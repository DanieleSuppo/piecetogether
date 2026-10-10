"""Development Core: durable acquisition and authorized semantic history."""

import base64
import hashlib
import json
import math
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic, time
from typing import Any, ContextManager, Protocol, cast
from uuid import uuid4

from . import context, history, projections, references, view
from .application_api import ApplicationApiConfig
from .artifact_store import LocalArtifactStore, StagedContent
from .contracts import DeclarativeContractProvider, DomainContractProvider
from .email_channel import (
    CAPABILITIES as EMAIL_CAPABILITIES, DeliveryResult, EmailChannel, EmailConfig,
    ImapConfig, SmtpTransport, address, message_ids,
)
from .proposals import (
    CandidateClaim,
    ContextRequestOperation,
    GroundingResolutionOperation,
    ReferenceResolutionOperation,
    SemanticProposal,
    ValidationResult,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_local_env(path: Path) -> None:
    """Load local deployment secrets without overriding the process environment."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        name, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _imap_from_environment(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('imap must be a static acquisition object')
    configured = dict(data)
    text = {'PT_IMAP_HOST': 'host', 'PT_IMAP_SECURITY': 'security',
            'PT_IMAP_MAILBOX': 'mailbox', 'PT_IMAP_USERNAME': 'username'}
    for variable, field_name in text.items():
        if variable in os.environ:
            configured[field_name] = os.environ[variable]
    for variable, field_name, parser in (
        ('PT_IMAP_PORT', 'port', int),
        ('PT_IMAP_TIMEOUT_SECONDS', 'timeout_seconds', float),
        ('PT_IMAP_POLL_SECONDS', 'poll_seconds', float),
        ('PT_IMAP_BATCH_SIZE', 'batch_size', int),
    ):
        if variable in os.environ:
            try:
                configured[field_name] = parser(os.environ[variable])
            except ValueError as error:
                raise ValueError('invalid IMAP environment configuration') from error
    return configured


@dataclass(frozen=True)
class Bootstrap:
    database: Path
    identities: dict[str, str]
    channel: str = "development"
    model: str = "deterministic"
    contract_version: str = "development-v1"
    capabilities: tuple[str, ...] = ("receive", "reply")
    secret_references: dict[str, str] = field(default_factory=dict)
    contract: Path | None = None
    selection: context.SelectionConfig = field(default_factory=context.SelectionConfig)
    reference_state: references.ReferenceConfig | None = None
    artifact_store: Path | None = None
    projection: projections.ProjectionConfig | None = None
    application_api: ApplicationApiConfig | None = None
    email: EmailConfig | None = None
    imap: ImapConfig | None = None

    def __post_init__(self) -> None:
        if self.projection is not None and not isinstance(self.projection, projections.ProjectionConfig):
            raise ValueError('projection must be a static ProjectionConfig')
        if self.application_api is not None and not isinstance(self.application_api, ApplicationApiConfig):
            raise ValueError('application_api must be a static ApplicationApiConfig')
        if self.reference_state is not None and not isinstance(self.reference_state, references.ReferenceConfig):
            raise ValueError('reference_state must be a static ReferenceConfig')
        if not isinstance(self.selection, context.SelectionConfig):
            raise ValueError('selection must be a static SelectionConfig')
        if self.contract is not None and not isinstance(self.contract, Path):
            raise ValueError("contract must name a declarative file")
        if self.artifact_store is not None and not isinstance(self.artifact_store, Path):
            raise ValueError("artifact_store must name a static local directory")
        if self.channel not in ('development', 'email') or self.model != 'deterministic':
            raise ValueError('only static configured adapters are available')
        if (self.channel == 'email') != isinstance(self.email, EmailConfig):
            raise ValueError('Email channel requires a static Email transport configuration')
        if self.imap is not None and self.channel != 'email':
            raise ValueError('IMAP acquisition requires the Email channel')
        expected = EMAIL_CAPABILITIES if self.channel == 'email' else ('receive', 'reply')
        if set(self.capabilities) != set(expected):
            raise ValueError('enabled capabilities must match the static channel declaration')
        if (
            not isinstance(self.contract_version, str)
            or not self.contract_version.strip()
        ):
            raise ValueError("contract_version must be a nonempty string")
        if not isinstance(self.database, Path) or self.database.name == ":memory:":
            raise ValueError("database must name a persistent file")
        if (
            not isinstance(self.identities, dict)
            or not self.identities
            or any(
                not isinstance(identity, str)
                or not identity.startswith(f'{self.channel}:')
                or not identity.removeprefix(f'{self.channel}:').strip()
                or (self.channel == 'email' and not address(identity.removeprefix('email:')))
                or not isinstance(actor, str)
                or not actor.strip()
                for identity, actor in self.identities.items()
            )
        ):
            raise ValueError(
                "identities must map channel identities to nonempty Actors"
            )
        if not isinstance(self.secret_references, dict) or any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(reference, str)
            or not reference.strip()
            for name, reference in self.secret_references.items()
        ):
            raise ValueError(
                "secret_references must contain environment variable names"
            )
        if self.application_api is not None and any(
            credential.secret_reference not in self.secret_references
            for credential in self.application_api.credentials
        ):
            raise ValueError("application API credentials must use configured secret references")
        if (self.email is not None and self.email.password_secret_reference is not None
                and self.email.password_secret_reference not in self.secret_references):
            raise ValueError('SMTP credentials must use configured secret references')
        if (self.imap is not None
                and self.imap.password_secret_reference not in self.secret_references):
            raise ValueError('IMAP credentials must use configured secret references')

    @classmethod
    def from_file(cls, path: Path) -> "Bootstrap":
        _load_local_env(path.parent / '.env')
        data = json.loads(path.read_text())
        allowed = {
            "database",
            "identities",
            "channel",
            "model",
            "contract_version",
            "capabilities",
            "secret_references",
            "contract",
            "selection",
            "reference_state",
            "artifact_store",
            "projection",
            "application_api",
            "email",
            "imap",
        }
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("invalid bootstrap configuration")
        if not isinstance(data.get("database"), str) or not data["database"].strip():
            raise ValueError("database is required")
        if not isinstance(data.get("capabilities", ["receive", "reply"]), list) or any(
            not isinstance(value, str) for value in data.get("capabilities", [])
        ):
            raise ValueError("capabilities must be a string array")
        database = Path(data.pop("database"))
        if 'email' in data:
            data['email'] = EmailConfig.from_dict(data['email'])
        if 'imap' in data:
            data['imap'] = ImapConfig.from_dict(_imap_from_environment(data['imap']))
        if 'projection' in data:
            if not isinstance(data['projection'], dict):
                raise ValueError('projection must be an object')
            try:
                data['projection'] = projections.ProjectionConfig(**data['projection'])
            except TypeError as error:
                raise ValueError('invalid projection configuration') from error
        if 'application_api' in data:
            data['application_api'] = ApplicationApiConfig.from_dict(data['application_api'])
        if 'reference_state' in data:
            reference = data['reference_state']
            if (not isinstance(reference, dict) or set(reference) - {'provider_id', 'path'}
                    or not isinstance(reference.get('provider_id'), str)
                    or not isinstance(reference.get('path'), str) or not reference['path'].strip()):
                raise ValueError('reference_state requires a static provider_id and file path')
            data['reference_state'] = references.ReferenceConfig(
                reference['provider_id'], path.parent / reference['path'])
        if 'selection' in data:
            if not isinstance(data['selection'], dict):
                raise ValueError('selection must be an object')
            try:
                data['selection'] = context.SelectionConfig(**data['selection'])
            except TypeError as error:
                raise ValueError('invalid selector configuration') from error
        if "contract" in data:
            if not isinstance(data["contract"], str) or not data["contract"].strip():
                raise ValueError("contract must name a declarative file")
            data["contract"] = path.parent / data["contract"]
        if "artifact_store" in data:
            if not isinstance(data["artifact_store"], str) or not data["artifact_store"].strip():
                raise ValueError("artifact_store must name a static local directory")
            data["artifact_store"] = path.parent / data["artifact_store"]
        data["capabilities"] = tuple(data.get("capabilities", ["receive", "reply"]))
        config = cls(database=path.parent / database, **data)
        deferred = {config.imap.password_secret_reference} if config.imap is not None else set()
        if any(
            not os.environ.get(reference)
            for name, reference in config.secret_references.items()
            if name not in deferred
        ):
            raise ValueError("a deployment secret reference is unresolved")
        return config


@dataclass(frozen=True)
class Communication:
    id: str
    channel: str
    sender: str
    actor_id: str
    direction: str
    idempotency_key: str
    text: str
    sent_at: str
    received_at: str
    thread_id: str | None = None
    reply_to: str | None = None
    attachments: tuple[dict[str, Any], ...] = ()
    recipient: str | None = None
    transport_message_id: str | None = None
    transport_reply_to: str | None = None
    references: tuple[str, ...] = ()
    subject: str | None = None


class ModelProvider(Protocol):
    def propose(
        self, inbound: Communication, contract_version: str, context_pack: context.ContextPack
    ) -> SemanticProposal: ...


class ChannelPlugin(Protocol):
    capabilities: tuple[str, ...]
    def normalize(self, payload: Any) -> dict[str, Any]: ...
    def prepare(self, outbound: Communication, inbound: Communication) -> Communication: ...
    def deliver(self, outbound: Communication) -> bool | DeliveryResult: ...


class DeterministicModel:
    def propose(
        self, inbound: Communication, contract_version: str, context_pack: context.ContextPack
    ) -> SemanticProposal:
        return SemanticProposal(
            schema_version=1,
            contract_version=contract_version,
            communication_id=inbound.id,
            candidate_claims=(CandidateClaim(inbound.id, inbound.text),),
            draft_response=f"I understood: {inbound.text}",
        )


class DevelopmentChannel:
    """A durable development mailbox, not a real Email/Telegram transport."""

    capabilities: tuple[str, ...] = ('receive', 'reply')

    def __init__(self, database: Path):
        self.database = database

    def normalize(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError('expected a normalized Communication')
        return payload

    def prepare(self, outbound: Communication, inbound: Communication) -> Communication:
        return outbound

    def deliver(self, outbound: Communication) -> bool:
        with connect(self.database) as db:
            db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?)",
                (outbound.id, json.dumps(asdict(outbound))),
            )
        return True


@contextmanager
def connect(database: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def _normalize_attachments(value: Any) -> tuple[tuple[dict[str, Any], ...], dict[str, bytes]]:
    if value is None:
        return (), {}
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('attachments must be a bounded array')
    normalized: list[dict[str, Any]] = []
    content: dict[str, bytes] = {}
    for attachment in value:
        if not isinstance(attachment, dict) or set(attachment) - {'id', 'content_base64', 'media_type'}:
            raise ValueError('invalid attachment')
        attachment_id = attachment.get('id')
        encoded = attachment.get('content_base64')
        media_type = attachment.get('media_type', 'application/octet-stream')
        if (not isinstance(attachment_id, str) or not attachment_id.strip() or len(attachment_id) > 256
                or not isinstance(encoded, str) or len(encoded) > 1_398_104
                or not isinstance(media_type, str) or not media_type.strip() or len(media_type) > 256
                or attachment_id in content):
            raise ValueError('invalid attachment')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeEncodeError) as error:
            raise ValueError('invalid attachment content') from error
        if not raw or len(raw) > 1_048_576:
            raise ValueError('attachment content exceeds bounds')
        content[attachment_id] = raw
        normalized.append({'id': attachment_id, 'media_type': media_type,
                           'size': len(raw), 'checksum': hashlib.sha256(raw).hexdigest()})
    return tuple(normalized), content


def _snapshot(value: Any) -> tuple[Any, str | None]:
    try:
        return json.loads(json.dumps(value, allow_nan=False)), None
    except (TypeError, ValueError, RecursionError):
        return None, 'unserializable'


def _decision_trace(proposal: SemanticProposal | None, fallback: str | None = None) -> dict[str, Any]:
    value = {
        'provider': proposal.decision_provider if proposal else None,
        'model': proposal.decision_model if proposal else None,
        'question_version': proposal.decision_question_version if proposal else None,
        'raw': proposal.decision_raw if proposal else None,
        'fallback': fallback if fallback is not None else proposal.decision_fallback if proposal else None,
        'decisions': [asdict(operation) for operation in proposal.operations
                      if isinstance(operation, (GroundingResolutionOperation,
                                                ReferenceResolutionOperation))] if proposal else [],
    }
    snapshot, marker = _snapshot(value)
    if marker:
        return {'provider': None, 'model': None, 'question_version': None, 'raw': None,
                'fallback': fallback, 'decisions': [], 'capture': marker}
    assert isinstance(snapshot, dict)
    return snapshot


def _record_attempt(
    db: sqlite3.Connection, communication_id: str, proposal: str | None,
    outbound: str | None, trace: dict[str, Any], status: str,
) -> None:
    db.execute(
        "INSERT INTO processing_attempts VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(communication_id, attempt) DO UPDATE SET "
        "proposal=excluded.proposal, outbound=excluded.outbound, "
        "trace=excluded.trace, status=excluded.status",
        (communication_id, trace.get("attempt", 0), proposal, outbound, json.dumps(trace), status),
    )


def _checkpoint_result(row: sqlite3.Row) -> dict[str, Any]:
    return {'communication_id': row['id'],
            'status': 'retryable' if row['status'] == 'pending' else row['status'],
            'reply': json.loads(row['outbound'])['text'] if row['status'] == 'completed' else None}


class Core:
    def __init__(
        self,
        config: Bootstrap,
        model: ModelProvider | None = None,
        channel: ChannelPlugin | None = None,
        contract_provider: DomainContractProvider | None = None,
        selector: context.SemanticSelector | None = None,
        reference_provider: references.ReferenceStateProvider | None = None,
        artifact_store: LocalArtifactStore | None = None,
        projection_sink: projections.ProjectionSink | None = None,
        delivery_clock: Callable[[], float] = time,
    ):
        self.config = config
        provider = contract_provider or DeclarativeContractProvider(config.contract)
        self.contract = provider.get(config.contract_version)
        if self.contract.version != config.contract_version:
            raise ValueError("provider returned a different Contract version")
        if reference_provider is not None and config.reference_state is None:
            raise ValueError('Reference State Provider must be explicitly configured')
        self.reference_provider = (reference_provider or references.FileReferenceStateProvider(config.reference_state)
                                   if config.reference_state is not None else None)
        self.projection_sink = projection_sink or (projections.DevelopmentProjectionSink(config.database)
                                                  if config.projection is not None else None)
        self.delivery_clock = delivery_clock
        self.model = model or DeterministicModel()
        self.channel: ChannelPlugin = channel or (
            EmailChannel(config.database, config.email, SmtpTransport(config.email, config.secret_references))
            if config.email is not None else DevelopmentChannel(config.database)
        )
        if set(getattr(self.channel, 'capabilities', config.capabilities)) != set(config.capabilities):
            raise ValueError('ChannelPlugin capabilities do not match deployment configuration')
        self.artifact_store = artifact_store or (LocalArtifactStore(config.artifact_store)
                                                 if config.artifact_store is not None else None)
        if config.selection.adapter == 'jev':
            from .jev import JevSelector
            reference = config.secret_references.get(config.selection.secret_reference or '')
            api_key = os.environ.get(reference or '')
            if not reference or not api_key:
                raise ValueError('Jev secret reference is unresolved')
            self.selector: context.SemanticSelector = selector or JevSelector(config.selection, api_key)
        else:
            self.selector = selector or context.ReferenceSelector()
        config.database.parent.mkdir(parents=True, exist_ok=True)
        with connect(config.database) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    inbound TEXT NOT NULL,
                    proposal TEXT,
                    outbound TEXT,
                    trace TEXT,
                    status TEXT NOT NULL,
                    UNIQUE(channel, sender, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, outbound TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processing_attempts (
                    communication_id TEXT NOT NULL REFERENCES turns(id),
                    attempt INTEGER NOT NULL,
                    proposal TEXT,
                    outbound TEXT,
                    trace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(communication_id, attempt)
                );
            """)
            # Import the last available checkpoint from pre-history deployments once.
            legacy_rows = db.execute(
                "SELECT * FROM turns WHERE trace IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM processing_attempts WHERE communication_id=turns.id)"
            ).fetchall()
            for row in legacy_rows:
                _record_attempt(
                    db, row["id"], row["proposal"], row["outbound"],
                    json.loads(row["trace"]), row["status"],
                )
            history.initialize(db, self.contract)
            projections.initialize(db)
        if self.artifact_store is not None:
            with self._artifact_maintenance_lock():
                self._release_abandoned_artifact_cleanup()
            self.retry_artifact_deletion()
            self.retry_artifact_finalization()
        if self.reference_provider is not None:
            self.refresh_references()
        self._dispatch_events_after_commit()

    def refresh_references(self) -> dict[str, Any]:
        """Read the configured provider outside locks, then atomically append its assertions."""
        provider = self.reference_provider
        config = self.config.reference_state
        if provider is None or config is None or provider.provider_id != config.provider_id:
            raise ValueError('Reference State Provider is unavailable or has a different identity')
        assertions = provider.get()
        if (not isinstance(assertions, tuple) or len(assertions) > 256
                or any(not isinstance(assertion, references.ReferenceAssertion) for assertion in assertions)):
            raise ValueError('invalid Reference State Provider output')
        # Detach provider-owned mutable values before validation or acquiring the writer lock.
        assertions = tuple(references.ReferenceAssertion(**json.loads(json.dumps(asdict(assertion), allow_nan=False)))
                           for assertion in assertions)
        with connect(self.config.database) as db:
            db.execute('BEGIN IMMEDIATE')
            result = references.commit(db, assertions, config.provider_id, self.contract, now())
        self._dispatch_events_after_commit()
        return result

    def _artifact_maintenance_lock(self) -> ContextManager[None]:
        if self.artifact_store is None:
            return nullcontext()
        lock = getattr(self.artifact_store, 'maintenance_lock', None)
        return cast(ContextManager[None], lock()) if callable(lock) else nullcontext()

    def _release_abandoned_artifact_cleanup(self) -> None:
        """The caller holds the local maintenance lock; a crash cannot retain it."""
        with connect(self.config.database) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE artifact_stages SET state='staged' WHERE state='cleaning' "
                       "AND NOT EXISTS (SELECT 1 FROM artifact_publications "
                       "WHERE stage_ref=artifact_stages.stage_ref)")

    def _stage_attachments(self, inbound: Communication, content: dict[str, bytes]) -> None:
        if not content:
            return
        if self.artifact_store is None:
            raise OSError('ArtifactStore is not configured')
        with self._artifact_maintenance_lock():
            self._release_abandoned_artifact_cleanup()
            with connect(self.config.database) as db:
                if history.committed(db, inbound.id):
                    return
            for attachment in inbound.attachments:
                attachment_id = attachment['id']
                staged = self.artifact_store.stage(content[attachment_id],
                                                   f'{inbound.id}:{attachment_id}')
                if (not isinstance(staged.reference, str) or not re.fullmatch(r'[0-9a-f]{32}', staged.reference)
                        or staged.checksum != attachment['checksum'] or staged.size != attachment['size']):
                    raise OSError('ArtifactStore returned invalid staged evidence')
                with connect(self.config.database) as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("INSERT INTO artifact_stages VALUES (?, ?, ?, ?, ?, ?, 'staged') "
                               "ON CONFLICT(communication_id, attachment_id) DO NOTHING",
                               (inbound.id, attachment_id, inbound.actor_id, staged.reference,
                                staged.checksum, staged.size))
                    row = db.execute('SELECT * FROM artifact_stages WHERE communication_id=? AND attachment_id=?',
                                     (inbound.id, attachment_id)).fetchone()
                    if row is None or tuple(row[name] for name in ('actor_id', 'stage_ref', 'checksum', 'size')) != (
                        inbound.actor_id, staged.reference, staged.checksum, staged.size):
                        raise ValueError('conflicting staged artifact evidence')
                    if row['state'] == 'deleted':
                        db.execute("UPDATE artifact_stages SET state='staged' WHERE communication_id=? "
                                   "AND attachment_id=? AND state='deleted'", (inbound.id, attachment_id))
                    elif row['state'] == 'cleaning':
                        raise OSError('artifact staging cleanup is in progress')

    def retry_artifact_deletion(self) -> dict[str, str]:
        """Recover committed byte deletion outside semantic transactions."""
        if self.artifact_store is None:
            return {}
        outcomes = {}
        with self._artifact_maintenance_lock():
            with connect(self.config.database) as db:
                pending = db.execute('SELECT * FROM artifact_deletions ORDER BY rowid').fetchall()
            for job in pending:
                try:
                    self.artifact_store.remove(job['reference'])
                    with connect(self.config.database) as db:
                        db.execute('DELETE FROM artifact_deletions WHERE artifact_id=?', (job['artifact_id'],))
                    outcomes[job['artifact_id']] = 'deleted'
                except (OSError, ValueError, TypeError, AttributeError) as error:
                    with connect(self.config.database) as db:
                        failure = {'error_type': type(error).__name__, 'message': str(error)[:256], 'at': now()}
                        db.execute('UPDATE artifact_deletions SET failure=? WHERE artifact_id=?',
                                   (json.dumps(failure), job['artifact_id']))
                    outcomes[job['artifact_id']] = 'pending'
        return outcomes

    def retry_artifact_finalization(self) -> dict[str, str]:
        """Serialize publication with local byte removal; never resurrect deleted content."""
        with self._artifact_maintenance_lock():
            return self._finalize_artifacts()

    def _finalize_artifacts(self) -> dict[str, str]:
        if self.artifact_store is None:
            return {}
        with connect(self.config.database) as db:
            pending = db.execute("SELECT * FROM artifact_publications WHERE state='pending' ORDER BY rowid").fetchall()
        outcomes: dict[str, str] = {}
        for publication in pending:
            try:
                finalized = self.artifact_store.finalize(
                    StagedContent(publication['stage_ref'], publication['checksum'], publication['size']))
                if (not isinstance(finalized.reference, str) or finalized.reference != publication['stage_ref']
                        or not isinstance(finalized.checksum, str) or finalized.checksum != publication['checksum']):
                    raise OSError('ArtifactStore returned invalid finalization evidence')
                with connect(self.config.database) as db:
                    db.execute('BEGIN IMMEDIATE')
                    updated = db.execute("UPDATE artifact_publications SET state='available', storage_ref=? "
                                        "WHERE artifact_id=? AND state='pending' AND stage_ref=? AND checksum=?",
                                        (finalized.reference, publication['artifact_id'], publication['stage_ref'],
                                         publication['checksum']))
                    if updated.rowcount:
                        db.execute('DELETE FROM artifact_publication_failures WHERE artifact_id=?',
                                   (publication['artifact_id'],))
                outcomes[publication['artifact_id']] = 'available' if updated.rowcount else 'deleted'
            except (OSError, ValueError, TypeError, AttributeError) as error:
                with connect(self.config.database) as db:
                    db.execute('BEGIN IMMEDIATE')
                    if not db.execute("SELECT 1 FROM artifact_publications WHERE artifact_id=? AND state='pending'",
                                      (publication['artifact_id'],)).fetchone():
                        outcomes[publication['artifact_id']] = 'deleted'
                        continue
                    prior = db.execute('SELECT record FROM artifact_publication_failures WHERE artifact_id=?',
                                       (publication['artifact_id'],)).fetchone()
                    attempts = json.loads(prior['record'])['attempts'] + 1 if prior else 1
                    record = {'artifact_id': publication['artifact_id'], 'attempts': attempts,
                              'error_type': type(error).__name__, 'message': str(error)[:256], 'at': now()}
                    db.execute("INSERT INTO artifact_publication_failures VALUES (?, ?) "
                               "ON CONFLICT(artifact_id) DO UPDATE SET record=excluded.record",
                               (publication['artifact_id'], json.dumps(record)))
                outcomes[publication['artifact_id']] = 'pending'
        return outcomes

    def cleanup_artifact_staging(self, minimum_age_seconds: float = 3600) -> list[str]:
        """Sweep old unlinked stages; abandoned reservations are released before each run."""
        if self.artifact_store is None:
            return []
        if (type(minimum_age_seconds) not in (int, float)
                or not math.isfinite(minimum_age_seconds) or minimum_age_seconds < 0):
            raise ValueError('minimum_age_seconds must be a finite nonnegative number')
        with self._artifact_maintenance_lock():
            # A process crash releases the local lock but leaves this marker. No publication
            # can have been linked from a cleaning state, so it is safe to retry maintenance.
            self._release_abandoned_artifact_cleanup()
            with connect(self.config.database) as db:
                registered = {row['stage_ref'] for row in db.execute("SELECT stage_ref FROM artifact_stages")}
                candidates = [row['stage_ref'] for row in db.execute(
                    "SELECT stage_ref FROM artifact_stages WHERE state='staged' ORDER BY rowid")]
            sweep = getattr(self.artifact_store, 'cleanup', None)
            removed = sweep(registered, minimum_age_seconds) if callable(sweep) else []
            for reference in candidates:
                with connect(self.config.database) as db:
                    db.execute('BEGIN IMMEDIATE')
                    reserved = db.execute("UPDATE artifact_stages SET state='cleaning' WHERE stage_ref=? "
                                          "AND state='staged' AND NOT EXISTS (SELECT 1 FROM artifact_publications "
                                          "WHERE stage_ref=artifact_stages.stage_ref)", (reference,))
                if not reserved.rowcount:
                    continue
                outcome = None
                try:
                    outcome = self.artifact_store.remove_staged(reference, minimum_age_seconds)
                except OSError:
                    # Return the reservation to normal staging so a future cleanup or redelivery can recover.
                    outcome = None
                finally:
                    with connect(self.config.database) as db:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute("UPDATE artifact_stages SET state=? WHERE stage_ref=? AND state='cleaning'",
                                   ('deleted' if outcome is not None else 'staged', reference))
                if outcome:
                    removed.append(reference)
        return removed

    def artifact_bytes(self, artifact_id: str) -> bytes:
        """Core-only accessor for finalized local bytes; sender acquisition has no byte URL."""
        if self.artifact_store is None:
            raise KeyError(artifact_id)
        with self._artifact_maintenance_lock():
            with connect(self.config.database) as db:
                row = db.execute("SELECT storage_ref, checksum FROM artifact_publications "
                                 "WHERE artifact_id=? AND state='available'", (artifact_id,)).fetchone()
            if row is None:
                raise KeyError(artifact_id)
            content = self.artifact_store.read(row['storage_ref'])
            if hashlib.sha256(content).hexdigest() != row['checksum']:
                raise OSError('artifact content checksum mismatch')
            with connect(self.config.database) as db:
                if not db.execute("SELECT 1 FROM artifact_publications WHERE artifact_id=? AND state='available'",
                                  (artifact_id,)).fetchone():
                    raise KeyError(artifact_id)
            return content

    def accept_transport(self, payload: Any) -> dict[str, Any]:
        """Normalize through the configured first-party channel before durable ingress."""
        return self.accept(self.channel.normalize(payload))

    def accept(self, payload: Any) -> dict[str, Any]:
        result = self._accept(payload)
        self._dispatch_events_after_commit()
        return result

    def recover_email_pending(self, after_rowid: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], int]:
        """Resume bounded Email-only ingress without needing source MIME or development attachments."""
        if type(after_rowid) is not int or after_rowid < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Email recovery cursor must be bounded')
        with connect(self.config.database) as db:
            rows = db.execute("SELECT rowid, inbound FROM turns WHERE channel='email' "
                              "AND status NOT IN ('completed', 'rejected') AND rowid>? "
                              "ORDER BY rowid LIMIT ?", (after_rowid, limit)).fetchall()
        fields = {'channel', 'sender', 'idempotency_key', 'text', 'sent_at', 'thread_id', 'reply_to',
                  'transport_message_id', 'transport_reply_to', 'references', 'subject'}
        outcomes = []
        for row in rows:
            inbound = json.loads(row['inbound'])
            outcomes.append(self.accept({name: inbound[name] for name in fields if name in inbound
                                         and inbound[name] is not None}))
        return outcomes, rows[-1]['rowid'] if rows else 0

    def _accept(self, payload: Any) -> dict[str, Any]:
        required = {"channel", "sender", "idempotency_key", "text", "sent_at"}
        optional = {'thread_id', 'reply_to', 'attachments', 'transport_message_id',
                    'transport_reply_to', 'references', 'subject'}
        if (
            not isinstance(payload, dict)
            or not required <= payload.keys()
            or set(payload) - required - optional
        ):
            raise ValueError("expected a normalized Communication")
        attachments, attachment_content = _normalize_attachments(payload.get('attachments'))
        normalized_payload = {name: payload[name] for name in required | optional - {'attachments'}
                              if name in payload}
        normalized_payload['attachments'] = attachments
        if self.config.channel == 'email':
            if attachments:
                raise ValueError('Email attachments are not enabled')
            for name in ('transport_message_id', 'transport_reply_to'):
                value = normalized_payload.get(name)
                if value is not None and (not isinstance(value, str) or len(message_ids(value)) != 1):
                    raise ValueError('invalid Email message reference')
            if normalized_payload.get('transport_message_id') != normalized_payload.get('idempotency_key'):
                raise ValueError('Email ingress requires its transport Message-ID')
        elif any(name in payload for name in ('transport_message_id', 'transport_reply_to', 'references', 'subject')):
            raise ValueError('Email metadata requires the Email channel')
        refs = normalized_payload.get('references', ())
        if (not isinstance(refs, (tuple, list)) or len(refs) > 32
                or any(not isinstance(ref, str) or len(message_ids(ref)) != 1 for ref in refs)):
            raise ValueError('invalid Communication references')
        normalized_payload['references'] = tuple(refs)
        subject = normalized_payload.get('subject')
        if subject is not None and (not isinstance(subject, str) or len(subject) > 256
                                    or '\r' in subject or '\n' in subject):
            raise ValueError('invalid Communication subject')
        if any(
            not isinstance(normalized_payload[name], str)
            or not normalized_payload[name].strip()
            or len(normalized_payload[name]) > (32768 if name == "text" else 256)
            for name in required
        ):
            raise ValueError("Communication fields must be bounded nonempty strings")
        if any(
            normalized_payload.get(name) is not None
            and (
                not isinstance(normalized_payload[name], str)
                or not normalized_payload[name].strip()
                or len(normalized_payload[name]) > 256
            )
            for name in ("thread_id", "reply_to")
        ):
            raise ValueError("thread and reply references must be bounded strings")
        try:
            timestamp = datetime.fromisoformat(
                normalized_payload["sent_at"].replace("Z", "+00:00")
            )
        except ValueError:
            raise ValueError("sent_at must be an ISO-8601 timestamp") from None
        if timestamp.tzinfo is None:
            raise ValueError("sent_at must include a timezone")
        if normalized_payload["channel"] != self.config.channel:
            raise ValueError("channel is disabled")
        actor = self.config.identities.get(f"{normalized_payload['channel']}:{normalized_payload['sender']}")
        if actor is None:
            raise ValueError("unmapped channel identity")
        inbound = Communication(
            id=str(uuid4()),
            actor_id=actor,
            direction="inbound",
            received_at=now(),
            **normalized_payload,
        )
        # Ingress commits before calling the model or handing off a reply.
        with connect(self.config.database) as db:
            db.execute(
                "INSERT INTO turns (id, channel, sender, idempotency_key, inbound, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending') "
                "ON CONFLICT(channel, sender, idempotency_key) DO NOTHING",
                (
                    inbound.id,
                    inbound.channel,
                    inbound.sender,
                    inbound.idempotency_key,
                    json.dumps(asdict(inbound)),
                ),
            )
        # Attempt-storage failure must not undo durable ingress.
        with connect(self.config.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                "SELECT * FROM turns WHERE channel=? AND sender=? AND idempotency_key=?",
                (inbound.channel, inbound.sender, inbound.idempotency_key),
            ).fetchone()
            assert row is not None
            stored = json.loads(json.dumps(asdict(Communication(**json.loads(row['inbound'])))))
            incoming = json.loads(json.dumps(asdict(inbound)))
            if any(stored[name] != incoming[name] for name in incoming if name not in ('id', 'received_at')):
                raise ValueError('idempotency key already belongs to a different Communication')
            if row['status'] in ('completed', 'rejected'):
                return _checkpoint_result(row)
            # Reserve a unique attempt without holding the transaction during I/O.
            attempt = db.execute('SELECT COALESCE(MAX(attempt), 0) + 1 FROM processing_attempts '
                                 'WHERE communication_id=?', (row['id'],)).fetchone()[0]
            _record_attempt(db, row['id'], None, None, {'attempt': attempt, 'started_at': now()}, 'processing')
            # The turn checkpoint and its commit must come from the same transaction.
            prior_commit = history.committed(db, row['id'])
        inbound = Communication(**stored)
        if attachments and not prior_commit:
            with connect(self.config.database) as db:
                for attachment in attachments:
                    db.execute("INSERT OR IGNORE INTO artifact_ingress VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (inbound.id, attachment['id'], inbound.actor_id, attachment['media_type'],
                                attachment['checksum'], attachment['size'], now()))
        if row["status"] == "completed":
            return {
                "communication_id": inbound.id,
                "reply": json.loads(row["outbound"])["text"],
                "status": "completed",
            }
        previous_trace = json.loads(row["trace"]) if row["trace"] else {}
        if (
            row["status"] != "rejected"
            and (row["proposal"] or previous_trace.get("proposal_capture") == "unserializable")
            and previous_trace.get("validation_result") == "rejected"
            and not previous_trace.get("failure_stage")
        ):
            # Recover the pre-atomic #14 checkpoint without replacing its decision.
            with connect(self.config.database) as db:
                db.execute("UPDATE turns SET status='rejected' WHERE id=?", (inbound.id,))
                _record_attempt(
                    db, inbound.id, row["proposal"], row["outbound"], previous_trace, "rejected",
                )
            return {"communication_id": inbound.id, "reply": None, "status": "rejected"}
        if row["status"] == "rejected":
            return {"communication_id": inbound.id, "reply": None, "status": "rejected"}
        started = monotonic()
        outbound = (
            Communication(**json.loads(row["outbound"])) if row["outbound"] else None
        )
        trace: dict[str, Any] = {
            "input_communication_id": inbound.id,
            "contract_version": self.config.contract_version,
            "model_provider": self.config.model,
            "attempt": attempt,
            "started_at": now(),
            "commit_result": "not_requested",
            "delivery_result": "pending",
            "model_usage": None,
            "validation_result": None,
            "validation_reasons": [],
        }
        if prior_commit:
            trace.update(commit_result="committed", semantic_commit_id=prior_commit['id'],
                         semantic_revision=prior_commit['semantic_revision'],
                         contract_version=prior_commit['contract_version'])
        accepted = False
        status = "retryable"
        saved_outbound = row["outbound"]
        captured = row["proposal"] if outbound else None
        stage = "model"
        pack: context.ContextPack | None = None
        requests: tuple[ContextRequestOperation, ...] = ()
        preliminary = ValidationResult('accepted')
        publication_pending = False
        try:
            if inbound.attachments and not prior_commit:
                stage = 'artifact_stage'
                self._stage_attachments(inbound, attachment_content)
                stage = 'model'
            if outbound is None:
                with connect(self.config.database) as db:
                    db.execute('BEGIN')
                    pack = context.assemble(db, asdict(inbound), self.config.contract_version,
                                            self.config.selection, self.contract)
                    pack = replace(pack, contract_json=context.encoded(asdict(self.contract)))
                trace['selection'] = []
                pack = context.select(pack, asdict(inbound), self.selector, trace['selection'])
                trace['context_pack'] = pack.captured()
                if pack.outcome == 'budget_exhausted':
                    raise context.BudgetExhausted()
                trace['retrieval'] = []
                for model_call in range(self.config.selection.max_model_calls):
                    trace['model_calls'] = model_call + 1
                    model_pack = context.model_view(pack)
                    if len(context.encoded([asdict(inbound), asdict(model_pack)]).encode()) > pack.budget.max_bytes:
                        raise context.BudgetExhausted()
                    proposal = self.model.propose(inbound, self.config.contract_version, model_pack)
                    with connect(self.config.database) as db:
                        db.execute('BEGIN')
                        preliminary = history.check(db, proposal, asdict(inbound), self.contract)
                    requests = tuple(op for op in proposal.operations if isinstance(op, ContextRequestOperation))
                    if preliminary.outcome != 'accepted' or not requests:
                        break
                    pack, preliminary = context.retrieve(pack, requests, trace['retrieval'])
                    trace['context_pack'] = pack.captured()
                    if preliminary.outcome != 'accepted':
                        break
                else:
                    raise context.BudgetExhausted()
            else:
                stage = "validation"
                proposal = SemanticProposal.from_dict(json.loads(row["proposal"]))
                if previous_trace.get('context_pack'):
                    pack = context.ContextPack.from_dict(previous_trace['context_pack'])
                    for name in ('context_pack', 'selection', 'retrieval', 'model_calls'):
                        if name in previous_trace:
                            trace[name] = previous_trace[name]
            with connect(self.config.database) as db:
                db.execute('BEGIN')
                # A changed read set needs fresh interpretation, not a terminal policy rejection.
                if not prior_commit and pack is not None and not context.current(db, pack):
                    validation = ValidationResult('stale', ('context_revision_changed',))
                else:
                    validation = (ValidationResult('accepted') if prior_commit else
                                  history.check(db, proposal, asdict(inbound), self.contract))
                if validation.outcome == 'accepted' and not prior_commit:
                    if outbound is None and requests and preliminary.outcome != 'accepted':
                        validation = preliminary
                    else:
                        validation = context.check_disclosure(proposal, pack, asdict(inbound))
            if (validation.outcome == "accepted" and not prior_commit and outbound is not None
                    and outbound.text != context.response(proposal, asdict(inbound), pack)):
                validation = ValidationResult("stale", ("grounding_exposure_changed",))
            trace["validation_result"] = validation.outcome
            trace["validation_reasons"] = list(validation.reasons)
            trace["proposal_contract_version"] = proposal.contract_version
            trace["proposal_intent"] = proposal.intent
            trace["grounding_decision"] = _decision_trace(proposal)
            if proposal.intent == "semantic_commit":
                trace["commit_result"] = "committed" if prior_commit else validation.outcome
            status = {
                "accepted": "retryable",
                "rejected": "rejected",
                "stale": "reprocess_required",
            }[validation.outcome]
            try:
                captured = json.dumps(asdict(proposal), allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                captured = None
                trace["proposal_capture"] = "unserializable"
                if validation.outcome == "accepted":
                    raise ValueError("accepted proposal must be JSON serializable") from None
            if validation.outcome == "accepted":
                # Detach mutable provider values; exposure and commit use this captured snapshot.
                assert captured is not None
                proposal = SemanticProposal.from_dict(json.loads(captured))
                if outbound is None:
                    outbound = Communication(
                        id=str(uuid4()),
                        channel=inbound.channel,
                        sender=inbound.sender,
                        actor_id=inbound.actor_id,
                        direction="outbound",
                        idempotency_key=inbound.id,
                        text=context.response(proposal, asdict(inbound), pack),
                        sent_at=now(),
                        received_at=now(),
                        thread_id=inbound.thread_id,
                        reply_to=inbound.id,
                    )
                    prepare = getattr(self.channel, 'prepare', None)
                    if callable(prepare):
                        outbound = prepare(outbound, inbound)
            else:
                outbound = None
                trace["delivery_result"] = "not_attempted"
                trace["finished_at"] = now()
                trace["duration_ms"] = round((monotonic() - started) * 1000, 3)
            stage = "storage"
            with connect(self.config.database) as db:
                db.execute("BEGIN IMMEDIATE")
                raced_commit = history.committed(db, inbound.id)
                latest_attempt = db.execute('SELECT MAX(attempt) FROM processing_attempts '
                                            'WHERE communication_id=?', (inbound.id,)).fetchone()[0]
                if latest_attempt != attempt:
                    checkpoint = db.execute('SELECT * FROM turns WHERE id=?', (inbound.id,)).fetchone()
                    trace.update(recovery='superseded_attempt', finished_at=now(),
                                 duration_ms=round((monotonic() - started) * 1000, 3),
                                 delivery_result='not_attempted')
                    if raced_commit:
                        trace.update(commit_result='committed', semantic_commit_id=raced_commit['id'],
                                     semantic_revision=raced_commit['semantic_revision'])
                    _record_attempt(db, inbound.id, captured, None, trace, 'superseded')
                    return _checkpoint_result(checkpoint)
                if raced_commit and not prior_commit:
                    checkpoint = db.execute("SELECT * FROM turns WHERE id=?", (inbound.id,)).fetchone()
                    checkpoint_trace = json.loads(checkpoint['trace'])
                    trace.update(commit_result="committed", semantic_commit_id=raced_commit['id'],
                                 semantic_revision=raced_commit['semantic_revision'],
                                 contract_version=raced_commit['contract_version'],
                                 recovery="already_committed",
                                 discarded_proposal=json.loads(captured) if captured else None,
                                 validation_result="accepted", validation_reasons=[])
                    captured = checkpoint['proposal']
                    proposal = SemanticProposal.from_dict(json.loads(captured))
                    outbound = Communication(**json.loads(checkpoint['outbound']))
                    status = "retryable"
                    prior_commit = raced_commit
                    validation = ValidationResult("accepted")
                    if checkpoint['status'] == "completed":
                        trace['delivery_result'] = 'accepted'
                        trace['finished_at'] = now()
                        trace['duration_ms'] = round((monotonic() - started) * 1000, 3)
                        db.execute("UPDATE turns SET trace=? WHERE id=?", (json.dumps(trace), inbound.id))
                        _record_attempt(db, inbound.id, captured, checkpoint['outbound'], trace, 'completed')
                        return {"communication_id": inbound.id, "reply": outbound.text, "status": "completed"}
                if not prior_commit and validation.outcome == "accepted":
                    if pack is not None and not context.current(db, pack):
                        validation, semantic_commit = ValidationResult('stale', ('context_revision_changed',)), None
                    elif proposal.intent == 'semantic_commit' and not history.has_effect(proposal):
                        semantic_commit = None
                        trace['commit_result'] = 'pending'
                    else:
                        validation, semantic_commit = history.commit(
                            db, proposal, asdict(inbound), self.contract, now(),
                        )
                    trace["validation_result"] = validation.outcome
                    trace["validation_reasons"] = list(validation.reasons)
                    if semantic_commit:
                        proposal = history.normalized_successors(proposal, semantic_commit)
                        captured = json.dumps(asdict(proposal), allow_nan=False)
                        if outbound is not None:
                            outbound = replace(outbound, text=context.response(proposal, asdict(inbound), pack))
                        trace.update(commit_result="committed", semantic_commit_id=semantic_commit['id'],
                                     semantic_revision=semantic_commit['semantic_revision'])
                    elif proposal.intent == "semantic_commit" and trace['commit_result'] != 'pending':
                        trace["commit_result"] = validation.outcome
                    if validation.outcome != "accepted":
                        status = "rejected" if validation.outcome == "rejected" else "reprocess_required"
                        outbound = None
                        trace["delivery_result"] = "not_attempted"
                        trace["finished_at"] = now()
                        trace["duration_ms"] = round((monotonic() - started) * 1000, 3)
                if validation.outcome == "accepted" and not prior_commit:
                    history.remember_concepts(db, proposal, inbound.actor_id)
                encoded_outbound = json.dumps(asdict(outbound)) if outbound else None
                db.execute(
                    "UPDATE turns SET proposal=?, outbound=?, trace=?, status=? WHERE id=?",
                    (captured, encoded_outbound, json.dumps(trace), status, inbound.id),
                )
                _record_attempt(db, inbound.id, captured, encoded_outbound, trace, status)
            saved_outbound = encoded_outbound
            with connect(self.config.database) as finalization_db:
                committed_artifacts = (history.committed(finalization_db, inbound.id) or {}).get('artifact_ids', [])
            if committed_artifacts:
                stage = 'artifact_finalize'
                self.retry_artifact_deletion()
                self.retry_artifact_finalization()
                with connect(self.config.database) as finalization_db:
                    deletion_pending = [artifact_id for artifact_id in committed_artifacts if finalization_db.execute(
                        'SELECT 1 FROM artifact_deletions WHERE artifact_id=?', (artifact_id,)
                    ).fetchone()]
                    remaining = [artifact_id for artifact_id in committed_artifacts if finalization_db.execute(
                        "SELECT 1 FROM artifact_publications WHERE artifact_id=? AND state='pending' "
                        "UNION ALL SELECT 1 FROM artifact_deletions WHERE artifact_id=?", (artifact_id, artifact_id)
                    ).fetchone()]
                if remaining:
                    publication_pending = True
                    status = 'retryable'
                    trace.update(failure_stage='artifact_delete' if deletion_pending else 'artifact_finalize',
                                 error_type='ArtifactDeletionPending' if deletion_pending else 'ArtifactPublicationPending',
                                 artifact_publication_ids=[artifact_id for artifact_id in remaining if artifact_id not in deletion_pending],
                                 artifact_deletion_ids=deletion_pending, delivery_result='not_attempted')
            if outbound is None:
                return {"communication_id": inbound.id, "reply": None, "status": status}
            if outbound is not None and not publication_pending:
                stage = "channel"
                delivery = self.channel.deliver(outbound)
                accepted = delivery is True or delivery == 'success'
                trace['delivery_result'] = ('accepted' if accepted else delivery
                                            if delivery in ('failure', 'indeterminate') else 'retryable')
        except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error) as error:
            status = 'budget_exhausted' if isinstance(error, context.BudgetExhausted) else "retryable"
            if stage == "validation":
                saved_outbound = None
            trace["failure_stage"] = stage
            trace["error_type"] = type(error).__name__
            if stage == 'model' and 'grounding_decision' not in trace:
                trace['grounding_decision'] = _decision_trace(None, 'model_error')
            if trace.get("proposal_intent") == "semantic_commit" and stage == "storage" and not prior_commit:
                trace["commit_result"] = "retryable"
                trace.pop("semantic_commit_id", None)
                trace.pop("semantic_revision", None)
            trace["delivery_result"] = (
                "indeterminate" if stage == "channel" else "not_attempted"
            )
        trace["finished_at"] = now()
        trace["duration_ms"] = round((monotonic() - started) * 1000, 3)
        status = "completed" if accepted else status
        with connect(self.config.database) as db:
            db.execute("BEGIN IMMEDIATE")
            durable_commit = history.committed(db, inbound.id)
            latest_attempt = db.execute('SELECT MAX(attempt) FROM processing_attempts '
                                        'WHERE communication_id=?', (inbound.id,)).fetchone()[0]
            if latest_attempt != attempt or (durable_commit and trace.get('semantic_commit_id') != durable_commit['id']):
                checkpoint = db.execute('SELECT * FROM turns WHERE id=?', (inbound.id,)).fetchone()
                trace['recovery'] = 'superseded_attempt' if latest_attempt != attempt else 'already_committed'
                if durable_commit:
                    trace.update(commit_result='committed', semantic_commit_id=durable_commit['id'],
                                 semantic_revision=durable_commit['semantic_revision'])
                # Preserve the winning checkpoint; losing attempts retain only operational evidence.
                _record_attempt(db, inbound.id, captured, saved_outbound, trace, 'superseded')
                return _checkpoint_result(checkpoint)
            db.execute(
                "UPDATE turns SET status=?, trace=?, outbound=? WHERE id=?",
                (status, json.dumps(trace), saved_outbound, inbound.id),
            )
            _record_attempt(db, inbound.id, captured, saved_outbound, trace, status)
            if accepted and outbound is not None:
                history.expose(db, proposal, outbound.id, inbound.actor_id, trace['finished_at'])
        return {
            "communication_id": inbound.id,
            "reply": outbound.text if accepted and outbound else None,
            "status": status,
        }

    def inspect(self, communication_id: str) -> dict[str, Any]:
        """Operator-only in-process inspection; never part of sender replies."""
        with connect(self.config.database) as db:
            row = db.execute(
                "SELECT * FROM turns WHERE id=?", (communication_id,)
            ).fetchone()
            attempts = db.execute(
                "SELECT * FROM processing_attempts WHERE communication_id=? ORDER BY attempt",
                (communication_id,),
            ).fetchall()
            grounding_items = history.exposed_items(db, communication_id)
        if row is None:
            raise KeyError(communication_id)
        return {
            "status": row["status"],
            "grounding_items": grounding_items,
            "attempts": [
                {
                    "attempt": item["attempt"], "status": item["status"],
                    **{
                        name: json.loads(item[name]) if item[name] else None
                        for name in ("proposal", "outbound", "trace")
                    },
                }
                for item in attempts
            ],
            **{
                name: json.loads(row[name]) if row[name] else None
                for name in ("inbound", "proposal", "outbound", "trace")
            },
        }

    def inspect_email_acquisition(self) -> list[dict[str, str | None]]:
        """Operator-only Email acquisition checkpoints; never sender or State API data."""
        inspect = getattr(self.channel, 'acquisition_outcomes', None)
        if not callable(inspect):
            raise ValueError('Email acquisition is unavailable')
        return cast(list[dict[str, str | None]], inspect())

    def current_view(self) -> dict[str, Any]:
        """Trusted projection for local consumers; never a sender retrieval path."""
        with connect(self.config.database) as db:
            db.execute("BEGIN")
            return view.current_view(db)

    def inspect_history(self) -> dict[str, Any]:
        """Operator-only ledger inspection, separate from sender acquisition."""
        with connect(self.config.database) as db:
            db.execute("BEGIN")
            return history.inspect_history(db)

    def _dispatch_events_after_commit(self) -> None:
        try:
            self.dispatch_events()
        except sqlite3.Error:
            # The durable outbox/started attempt survives unavailable delivery bookkeeping.
            # Explicit maintenance surfaces the storage error; acquisition keeps its outcome.
            pass

    def dispatch_events(self) -> dict[str, str]:
        """Publish durable trusted events without semantic locks or model work."""
        if self.projection_sink is None:
            return {}
        config = self.config.projection or projections.ProjectionConfig()
        timestamp = self.delivery_clock()
        with connect(self.config.database) as db:
            pending = db.execute("SELECT o.* FROM semantic_outbox o LEFT JOIN event_delivery d "
                                 "ON d.event_id=o.id WHERE d.event_id IS NULL "
                                 "OR d.state='pending' AND d.next_attempt_at<=? "
                                 "OR d.state='delivering' AND d.lease_until<=? "
                                 "ORDER BY o.rowid LIMIT 100", (timestamp, timestamp)).fetchall()
        outcomes = {}
        for row in pending:
            with connect(self.config.database) as db:
                db.execute('BEGIN IMMEDIATE')
                attempt = projections.claim(db, row['id'], self.delivery_clock(), config)
            if attempt is None:
                continue
            error_type = None
            try:
                accepted = self.projection_sink.deliver(json.loads(row['record'])) is True
                if not accepted:
                    error_type = 'SinkNotAccepted'
            except Exception as error:
                accepted = False
                # Store classification, not arbitrary sink text which may contain secrets.
                error_type = type(error).__name__
            with connect(self.config.database) as db:
                db.execute('BEGIN IMMEDIATE')
                outcomes[row['id']] = projections.finish(
                    db, row['id'], attempt, self.delivery_clock(), config, accepted, error_type)
        return outcomes

    def recover_event_delivery(self, event_id: str) -> bool:
        """Operator resumes an exhausted delivery budget, keeping payload and attempt history."""
        with connect(self.config.database) as db:
            return bool(db.execute("UPDATE event_delivery SET state='pending', attempts_since_recovery=0, "
                                   "next_attempt_at=?, lease_until=NULL WHERE event_id=? AND state='failed'",
                                   (self.delivery_clock(), event_id)).rowcount)

    def inspect_event_delivery(self) -> list[dict[str, Any]]:
        """Operational delivery evidence, never trusted knowledge or sender replies."""
        with connect(self.config.database) as db:
            db.execute('BEGIN')
            return projections.inspect(db)

    def trusted_events(self) -> list[dict[str, Any]]:
        """Immutable public-event payloads, excluding delivery and processing internals."""
        with connect(self.config.database) as db:
            db.execute('BEGIN')
            return [json.loads(row['record']) for row in db.execute(
                'SELECT record FROM semantic_outbox ORDER BY rowid')]

    def inspect_concepts(self) -> list[dict[str, Any]]:
        """Non-authoritative vocabulary for later Core-owned reconciliation."""
        with connect(self.config.database) as db:
            return [json.loads(row['record']) for row in db.execute(
                "SELECT record FROM emergent_concepts ORDER BY rowid")]
