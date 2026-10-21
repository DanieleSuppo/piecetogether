"""Local ordinary expiry, legacy terminal retention and maintenance coordination.

Not a scheduler or selective subject-erasure API. ArtifactStore still owns bytes.
"""

import fcntl
import os
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Concatenate, ParamSpec, TypeVar

if TYPE_CHECKING:
    from .core import Core, DevelopmentChannel
    from .email_channel import EmailChannel
    from .telegram_channel import TelegramChannel


CATEGORIES = ('communications', 'semantic_history', 'evaluation_traces', 'current_view')
_ACTIONS = ('retain', 'redact', 'delete')


class RetentionUnavailable(RuntimeError):
    """Requested evidence is erased or withheld; never reconstruct it implicitly."""


class RetentionBusy(RuntimeError):
    """Maintenance conflicts with an active local Core operation; retry quiescently."""


@dataclass(frozen=True)
class RetentionPolicy:
    communications: str = 'retain'
    semantic_history: str = 'retain'
    evaluation_traces: str = 'retain'
    current_view: str = 'retain'

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or value not in _ACTIONS for value in asdict(self).values()):
            raise ValueError('retention actions must be retain, redact or delete')

    @classmethod
    def from_dict(cls, value: Any) -> 'RetentionPolicy':
        if not isinstance(value, dict) or set(value) - set(CATEGORIES):
            raise ValueError('retention must be a static category policy object')
        try:
            return cls(**value)
        except TypeError as error:
            raise ValueError('invalid retention policy') from error


@dataclass(frozen=True)
class ExpiryPolicy:
    """Static ordinary retention horizons; no scheduler or source-erasure policy."""

    trace_days: int = 30
    communication_days: int = 90

    def __post_init__(self) -> None:
        if any(type(value) is not int or not 1 <= value <= 365000
               for value in asdict(self).values()):
            raise ValueError('expiry horizons must be integer days between 1 and 365000')

    @classmethod
    def from_dict(cls, value: Any) -> 'ExpiryPolicy':
        if not isinstance(value, dict) or set(value) - {'trace_days', 'communication_days'}:
            raise ValueError('expiry must be a static horizon object')
        try:
            return cls(**value)
        except TypeError as error:
            raise ValueError('invalid expiry policy') from error


def initialize(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS retention_state (
        category TEXT PRIMARY KEY CHECK(category IN
            ('communications', 'semantic_history', 'evaluation_traces', 'current_view')),
        availability TEXT NOT NULL CHECK(availability IN ('redact', 'delete'))
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS record_expiry (
        communication_id TEXT PRIMARY KEY REFERENCES turns(id),
        trace_expired INTEGER NOT NULL CHECK(trace_expired IN (0, 1)),
        payload_expired INTEGER NOT NULL CHECK(payload_expired IN (0, 1)),
        settled_at TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS view_access (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        withheld INTEGER NOT NULL CHECK(withheld IN (0, 1))
    )""")


def availability(db: sqlite3.Connection) -> dict[str, str]:
    """Absent state means legacy/default retain; rows contain no record identifiers."""
    result = dict.fromkeys(CATEGORIES, 'retain')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='retention_state'").fetchone():
        result.update((row[0], row[1]) for row in db.execute(
            'SELECT category, availability FROM retention_state'))
    return result


def require(db: sqlite3.Connection, category: str) -> None:
    if availability(db)[category] != 'retain':
        raise RetentionUnavailable(f'{category} unavailable after retention')


def view_withheld(db: sqlite3.Connection) -> bool:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='view_access'").fetchone():
        return False
    return db.execute('SELECT 1 FROM view_access WHERE withheld=1').fetchone() is not None


def record_status(db: sqlite3.Connection, communication_id: str) -> tuple[bool, bool]:
    """Missing additive metadata is legacy retain, never a reset of terminal state."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='record_expiry'").fetchone():
        return False, False
    row = db.execute('SELECT trace_expired, payload_expired FROM record_expiry WHERE communication_id=?',
                     (communication_id,)).fetchone()
    return (bool(row[0]), bool(row[1])) if row else (False, False)


def require_payload(db: sqlite3.Connection, communication_id: str) -> None:
    require(db, 'communications')
    if record_status(db, communication_id)[1]:
        raise RetentionUnavailable('Communication payload unavailable after expiry')


def require_processing(db: sqlite3.Connection) -> None:
    if any(value != 'retain' for value in availability(db).values()):
        raise RetentionUnavailable('processing unavailable after retention; provision a fresh deployment')


@contextmanager
def operation(database: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Cross-process fence, held through I/O, never a long SQLite semantic lock.

    A sweep rejects rather than racing an in-flight model, channel or sink call.
    All Core instances for this deployment must use the same canonical DB path.
    """
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(database) + '.retention.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RetentionBusy('retention requires quiescent local Core operations') from error
        yield
    finally:
        os.close(descriptor)


P = ParamSpec('P')
R = TypeVar('R')


def coordinated(method: Callable[Concatenate['Core', P], R]) -> Callable[Concatenate['Core', P], R]:
    @wraps(method)
    def wrapped(self: 'Core', /, *args: P.args, **kwargs: P.kwargs) -> R:
        with operation(self.config.database):
            return method(self, *args, **kwargs)
    return wrapped


def processing(method: Callable[Concatenate['Core', P], R]) -> Callable[Concatenate['Core', P], R]:
    @wraps(method)
    def wrapped(self: 'Core', /, *args: P.args, **kwargs: P.kwargs) -> R:
        with operation(self.config.database):
            with closing(sqlite3.connect(self.config.database)) as db:
                require_processing(db)
            return method(self, *args, **kwargs)
    return wrapped


C = TypeVar('C', 'DevelopmentChannel', 'EmailChannel', 'TelegramChannel')


def channel_processing(method: Callable[Concatenate[C, P], R]) -> Callable[Concatenate[C, P], R]:
    """Fence checkpoint/handoff writes that acquisition workers make outside Core."""
    @wraps(method)
    def wrapped(self: C, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with operation(self.database):
            with closing(sqlite3.connect(self.database)) as db:
                require_processing(db)
            return method(self, *args, **kwargs)
    return wrapped


def apply(db: sqlite3.Connection, policy: RetentionPolicy) -> dict[str, str]:
    """Caller owns exclusive operation fence and BEGIN IMMEDIATE transaction.

    Redaction deliberately retains only the same non-identifying availability
    marker as deletion. No per-record tombstones, reasons or counts survive.
    Source erasure invalidates both source categories and every dependent copy.
    """
    previous = availability(db)
    effective = {category: max((previous[category], action), key=_ACTIONS.index)
                 for category, action in asdict(policy).items()}
    source_action = max((effective['communications'], effective['semantic_history']), key=_ACTIONS.index)
    if source_action != 'retain':
        if (db.execute('SELECT 1 FROM artifact_deletions LIMIT 1').fetchone()
                or db.execute("SELECT 1 FROM artifact_publications WHERE state='pending' LIMIT 1").fetchone()):
            raise RetentionBusy('complete pending Artifact publication/deletion before source retention')
        effective = {category: max((action, source_action), key=_ACTIONS.index)
                     for category, action in effective.items()}
        # All Core-owned evidence tables, including first-party acquisition/handoff
        # and local sink copies. Bytes are deliberately outside this transaction.
        tables = (
            'record_expiry', 'view_access',
            'processing_attempts', 'deliveries', 'turns', 'semantic_commits',
            'trusted_records', 'reference_assertions', 'reference_commits',
            'trusted_relationships', 'trusted_groundings', 'trusted_grounding_items',
            'exposed_groundings', 'pending_items', 'candidate_target_bindings',
            'event_delivery_attempts', 'event_delivery', 'semantic_outbox',
            'emergent_concepts', 'artifact_ingress',
            'artifact_publication_failures', 'artifact_publications', 'artifact_stages',
            'artifact_lifecycle', 'artifact_deletions', 'trusted_artifacts', 'semantic_state',
            'projection_deliveries',
            'email_handoffs', 'email_acquisition', 'telegram_handoffs', 'telegram_acquisition',
        )
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table in existing:
                db.execute(f'DELETE FROM {table}')
    elif effective['evaluation_traces'] != 'retain':
        for table in ('processing_attempts', 'candidate_target_bindings', 'pending_items', 'exposed_groundings'):
            db.execute(f'DELETE FROM {table}')
        # Pending exposure Proposals can contain decision diagnostics as well.
        # Discard working copies, not committed Grounding outcomes. Communications
        # and the trusted ledger remain independent, but retry is no longer safe.
        db.execute('UPDATE turns SET trace=NULL, proposal=NULL')
    for category, action in effective.items():
        if action != 'retain':
            db.execute('INSERT INTO retention_state VALUES (?, ?) '
                       'ON CONFLICT(category) DO UPDATE SET availability=excluded.availability',
                       (category, action))
    return effective


def expiry_reference(reference_time: datetime | None) -> datetime:
    reference = datetime.now(timezone.utc) if reference_time is None else reference_time
    if (not isinstance(reference, datetime) or reference.tzinfo is None
            or reference.utcoffset() is None or reference > datetime.now(timezone.utc)):
        raise ValueError('reference_time must be timezone-aware and not in the future')
    return reference


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError('missing Core retention timestamp')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Core retention timestamp must be timezone-aware')
    return result


def _strings(value: Any) -> set[str]:
    """Exact identifiers, including captured revision-map keys, not substring guesses."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set(value) | set().union(*(_strings(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_strings(item) for item in value))
    return set()


def _scrub_proposal(raw: str) -> str:
    proposal = json.loads(raw)
    for name in ('decision_provider', 'decision_model', 'decision_question_version',
                 'decision_raw', 'decision_fallback'):
        proposal.pop(name, None)
    return json.dumps(proposal)


def _references(db: sqlite3.Connection) -> dict[str, set[str | None]]:
    """Conservative evidence inventory, not a semantic dependency-closure algorithm.

    A foreign controlled copy protects its source. Own turn/attempt/mailbox copies
    are removable together. Unknown transport ownership is handled by protection.
    """
    result: dict[str, set[str | None]] = {}
    owners = {json.loads(row['outbound'])['id']: row['id'] for row in db.execute(
        'SELECT id, outbound FROM turns WHERE outbound IS NOT NULL')}
    tables = (
        'turns', 'processing_attempts', 'deliveries', 'semantic_commits', 'trusted_records',
        'reference_assertions', 'reference_commits', 'trusted_relationships', 'trusted_groundings',
        'trusted_grounding_items', 'exposed_groundings', 'pending_items', 'candidate_target_bindings',
        'semantic_outbox', 'emergent_concepts', 'artifact_ingress', 'artifact_stages',
        'trusted_artifacts', 'artifact_publications', 'artifact_publication_failures',
        'artifact_lifecycle', 'artifact_deletions', 'projection_deliveries',
        'email_handoffs', 'email_acquisition', 'telegram_handoffs', 'telegram_acquisition',
    )
    existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in tables:
        if table not in existing:
            continue
        for row in db.execute(f'SELECT * FROM {table}'):
            owner = (row['id'] if table == 'turns' else row['communication_id']
                     if table == 'processing_attempts' else owners.get(row['id'])
                     if table == 'deliveries' else None)
            for name in row.keys():
                value = row[name]
                if value is None:
                    continue
                if name in ('inbound', 'outbound', 'proposal', 'trace', 'record'):
                    value = json.loads(value)
                for identifier in _strings(value):
                    result.setdefault(identifier, set()).add(owner)
    return result


def expire(db: sqlite3.Connection, policy: ExpiryPolicy, reference: datetime) -> dict[str, int]:
    """Require a fully initialized Core schema, not a partial/optional-table snapshot.

    Caller owns the exclusive maintenance fence and transaction, including dry-run
    rollback. Inventory guards tolerate optional tables, not missing required state.
    Never alters trusted history, obligations or legacy terminal availability.
    A malformed stored timestamp/JSON aborts atomically rather than guessing age.
    """
    require_processing(db)
    report = dict.fromkeys(('traces_erased', 'traces_protected',
                            'communications_erased', 'communications_protected'), 0)
    # Unresolved byte/event work can have indirect dependencies. Protect all traces
    # until those existing owners resolve it; never manufacture job completion.
    obligations = bool(db.execute(
        "SELECT 1 FROM artifact_deletions UNION ALL "
        "SELECT 1 FROM artifact_publications WHERE state='pending' UNION ALL "
        "SELECT 1 FROM event_delivery WHERE state!='delivered' LIMIT 1").fetchone())
    report['obligations_pending'] = int(obligations)
    rows = db.execute('SELECT * FROM turns ORDER BY rowid').fetchall()
    activity: dict[str, datetime] = {}
    trace_copies: set[str] = set()
    for row in rows:
        trace_expired, payload_expired = record_status(db, row['id'])
        if payload_expired:
            continue
        inbound = json.loads(row['inbound'])
        settled = _timestamp(inbound['received_at'])
        if trace_expired:
            settled = max(settled, _timestamp(db.execute(
                'SELECT settled_at FROM record_expiry WHERE communication_id=?', (row['id'],)).fetchone()[0]))
        attempts = db.execute('SELECT * FROM processing_attempts WHERE communication_id=?',
                              (row['id'],)).fetchall()
        if row['trace'] is not None or attempts:
            trace_copies.add(row['id'])
        for attempt in [row, *attempts]:
            if attempt['trace']:
                trace = json.loads(attempt['trace'])
                for name in ('started_at', 'finished_at'):
                    if name in trace:
                        settled = max(settled, _timestamp(trace[name]))
        activity[row['id']] = settled
        # Any unfinished checkpoint protects all its copies, even past the horizon.
        active = (row['status'] not in ('completed', 'rejected') or bool(db.execute(
            "SELECT 1 FROM processing_attempts WHERE communication_id=? AND status='processing'",
            (row['id'],)).fetchone()))
        if not trace_expired and reference - settled >= timedelta(days=policy.trace_days):
            if active or obligations:
                report['traces_protected'] += 1
            else:
                db.execute('UPDATE turns SET trace=NULL, proposal=? WHERE id=?',
                           (_scrub_proposal(row['proposal']) if row['proposal'] else None, row['id']))
                db.execute('DELETE FROM processing_attempts WHERE communication_id=?', (row['id'],))
                for exposed in db.execute('SELECT id, proposal FROM exposed_groundings WHERE communication_id=?',
                                           (row['id'],)).fetchall():
                    db.execute('UPDATE exposed_groundings SET proposal=? WHERE id=?',
                               (_scrub_proposal(exposed['proposal']), exposed['id']))
                db.execute('INSERT INTO record_expiry VALUES (?, 1, 0, ?) '
                           'ON CONFLICT(communication_id) DO UPDATE SET trace_expired=1, settled_at=excluded.settled_at',
                           (row['id'], settled.isoformat()))
                report['traces_erased'] += int(row['id'] in trace_copies)
    # Inventory after planned trace removal also makes dry-run counts faithful.
    references = _references(db)
    for row in rows:
        if record_status(db, row['id'])[1]:
            continue
        received = _timestamp(json.loads(row['inbound'])['received_at'])
        if reference - received < timedelta(days=policy.communication_days):
            continue
        outbound_id = json.loads(row['outbound'])['id'] if row['outbound'] else None
        identifiers = {row['id']} | ({outbound_id} if outbound_id else set())
        protected = (row['status'] != 'completed' or row['channel'] != 'development' or obligations
                     or reference - activity[row['id']] < timedelta(days=policy.communication_days)
                     or any(owner != row['id'] for identifier in identifiers
                            for owner in references.get(identifier, set()))
                     or bool(db.execute("SELECT 1 FROM processing_attempts WHERE communication_id=? AND status='processing'",
                                        (row['id'],)).fetchone()))
        if protected:
            report['communications_protected'] += 1
            continue
        if not record_status(db, row['id'])[0]:
            report['traces_erased'] += int(row['id'] in trace_copies)
        db.execute('DELETE FROM processing_attempts WHERE communication_id=?', (row['id'],))
        if outbound_id:
            db.execute('DELETE FROM deliveries WHERE id=?', (outbound_id,))
        db.execute("UPDATE turns SET inbound='{}', outbound=NULL, proposal=NULL, trace=NULL WHERE id=?", (row['id'],))
        db.execute('INSERT INTO record_expiry VALUES (?, 1, 1, ?) '
                   'ON CONFLICT(communication_id) DO UPDATE SET trace_expired=1, payload_expired=1',
                   (row['id'], activity[row['id']].isoformat()))
        report['communications_erased'] += 1
    return report
