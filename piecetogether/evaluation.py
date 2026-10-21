"""Operator-only deterministic replay. No provider or transport is constructed here."""
import base64
import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing, nullcontext
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, ContextManager

from .core import Bootstrap, Communication, Core, DevelopmentChannel, IdentitySource, MediaPolicy, connect
from .artifact_store import FinalizedContent, LocalArtifactStore, StagedContent
from .contracts import DomainContract, finite_number
from .email_channel import EmailConfig
from .openai_responses import GenerationResult
from .proposals import SemanticProposal
from .telegram_channel import TelegramConfig

from . import context, retention

ReplayUnavailable = context.ReplayUnavailable


class CapturedSelector:
    """Consume request-bound decisions through the normal Core selection policy."""
    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts = attempts
        self.index = 0
        self.replay_timing: dict[str, float] | None = None

    def select(self, request: context.SelectionRequest) -> context.SelectionResult:
        if self.index >= len(self.attempts):
            raise ReplayUnavailable('missing selector attempt')
        entry = self.attempts[self.index]
        required = {'request', 'request_id', 'selection_policy_version', 'task_version',
                    'raw_response', 'response', 'failure', 'latency_ms', 'fallback_reason'}
        if (not required <= entry.keys() or entry.get('capture') or entry.get('response_capture')
                or entry['selection_policy_version'] != context.SELECTION_POLICY_VERSION
                or entry['task_version'] != 'context-relevance-v1'
                or context.encoded(entry['request']) != context.encoded(asdict(request))
                or entry['request_id'] != context.digest(context.encoded(asdict(request)))):
            raise ReplayUnavailable('incompatible or unavailable selector evidence')
        fallback = entry['failure']
        if fallback is None and isinstance(entry['response'], dict):
            outcome = entry['response'].get('outcome')
            fallback = outcome if outcome in ('abstained', 'unavailable') else None
        if entry['fallback_reason'] != fallback:
            raise ReplayUnavailable('contradictory selector fallback evidence')
        if (not finite_number(entry['latency_ms']) or entry['latency_ms'] < 0
                or 'total_latency_ms' in entry and (not finite_number(entry['total_latency_ms'])
                                                   or entry['total_latency_ms'] < entry['latency_ms'])
                or entry['fallback_reason'] is not None and 'total_latency_ms' not in entry
                and entry.get('outcome') != 'budget_exhausted'):
            raise ReplayUnavailable('missing selector timing evidence')
        self.index += 1
        self.provider_id, self.model_id = entry.get('provider'), entry.get('model')
        self.replay_timing = {'latency_ms': entry['latency_ms'],
                              'total_latency_ms': entry.get('total_latency_ms', entry['latency_ms'])}
        if entry['failure'] == 'provider_failure':
            raise OSError('captured selector outage')
        if entry['failure'] == 'budget_exhausted':
            raise context.BudgetExhausted(entry['raw_response'])
        response = entry['response']
        if response is not None:
            try:
                types = entry['response_types']
                ids = tuple(response['ranked_ids']) if types['ranked_ids_tuple'] else response['ranked_ids']
                assessments = response['assessments']
                if types['assessment_items'] is not None:
                    if len(types['assessment_items']) != len(assessments):
                        raise ReplayUnavailable('assessment shape evidence differs')
                    assessments = [context.Assessment(**item) if typed else item
                                   for item, typed in zip(assessments, types['assessment_items'])]
                if types['assessments_tuple']:
                    assessments = tuple(assessments)
                return context.SelectionResult(**{**response, 'ranked_ids': ids, 'assessments': assessments})
            except (KeyError, TypeError, ValueError):
                raise ReplayUnavailable('unrestorable selector result') from None
        if entry['failure'] == 'invalid_selection':
            # A parsed but invalid raw response still takes the ordinary validation/fallback path.
            raise context.SelectionFailure(entry['raw_response'])
        raise ReplayUnavailable('missing selector result')

    def assert_consumed(self) -> None:
        if self.index != len(self.attempts):
            raise ReplayUnavailable('unused selector evidence')


@dataclass
class ReplayCase:
    initial_state: bytes
    state_digest: str
    initial_revision: int
    contract: dict[str, Any]
    configuration: dict[str, Any]
    identities: list[dict[str, str]]
    steps: list[dict[str, Any]]
    expected: dict[str, Any]
    artifact_versions: list[dict[str, Any]] | None
    artifact_operations: list[dict[str, Any]] = field(default_factory=list)
    policy_versions: dict[str, str] = field(default_factory=lambda: dict(context.POLICY_VERSIONS))
    schema_version: int = context.TRACE_SCHEMA_VERSION
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data['initial_state'] = base64.b64encode(self.initial_state).decode('ascii')
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ReplayCase':
        try:
            required = {item.name for item in fields(cls)} - {'diagnostics'}
            if not isinstance(data, dict) or not required <= data.keys():
                raise ReplayUnavailable('missing replay fixture evidence')
            return cls(**{**data, 'initial_state': base64.b64decode(data['initial_state'], validate=True)})
        except (ValueError, TypeError, KeyError):
            raise ReplayUnavailable('invalid replay fixture') from None


@dataclass(frozen=True)
class ReplayResult:
    actual: dict[str, Any]
    mismatches: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return not self.mismatches


class _RecordingIdentity(IdentitySource):
    def __init__(self) -> None:
        self.values: list[dict[str, str]] = []

    def id(self) -> str:
        value = super().id()
        self.values.append({'kind': 'id', 'value': value})
        return value

    def now(self) -> str:
        value = super().now()
        self.values.append({'kind': 'time', 'value': value})
        return value


class _CapturedIdentity(IdentitySource):
    def __init__(self, values: list[dict[str, str]]):
        self.values, self.index = values, 0

    def _next(self, kind: str) -> str:
        if self.index >= len(self.values) or self.values[self.index].get('kind') != kind:
            raise ReplayUnavailable('missing or mismatched identity allocation')
        value = self.values[self.index].get('value')
        if not isinstance(value, str) or not value:
            raise ReplayUnavailable('invalid identity allocation')
        self.index += 1
        return value

    def id(self) -> str:
        return self._next('id')

    def now(self) -> str:
        return self._next('time')

    def assert_consumed(self) -> None:
        if self.index != len(self.values):
            raise ReplayUnavailable('unused identity allocations')


class _ContractProvider:
    def __init__(self, contract: DomainContract):
        self.contract = contract

    def get(self, version: str) -> DomainContract:
        if version != self.contract.version:
            raise ReplayUnavailable('Contract version mismatch')
        return self.contract


class _CapturedModel:
    def __init__(self, attempts: list[dict[str, Any]]):
        self.attempts, self.index = attempts, 0

    def _metadata(self, name: str) -> Any:
        return self.attempts[self.index].get(name) if self.index < len(self.attempts) else None

    @property
    def provider_id(self) -> Any:
        return self._metadata('provider')

    @property
    def model_id(self) -> Any:
        return self._metadata('model')

    @property
    def instruction_version(self) -> Any:
        return self._metadata('instruction_version')

    def propose(self, inbound: Communication, contract_version: str,
                context_pack: context.ContextPack) -> SemanticProposal:
        return self.propose_with_evidence(inbound, contract_version, context_pack).proposal

    def propose_with_evidence(self, inbound: Communication, version: str,
                              pack: context.ContextPack) -> GenerationResult:
        if self.index >= len(self.attempts):
            raise ReplayUnavailable('missing generative attempt')
        entry = self.attempts[self.index]
        required = {'provider', 'model', 'instruction_version', 'task_version', 'input_communication_id',
                    'context_pack', 'context_pack_ref', 'proposal', 'usage', 'outcome', 'latency_ms'}
        if (not required <= entry.keys() or entry.get('capture')
                or entry['task_version'] != 'semantic-proposal-v1'
                or entry['input_communication_id'] != inbound.id
                or entry['context_pack_ref'] != context.digest(context.encoded(entry['context_pack']))):
            raise ReplayUnavailable('incompatible or missing generative evidence')
        captured_pack = dict(entry['context_pack'])
        # Perception is already captured in the Proposal. Verify immutable media metadata,
        # never reconstruct a provider or fetch the original bytes for inference.
        for media in captured_pack.get('media', []):
            attachment = next((item for item in inbound.attachments if item['id'] == media['attachment_id']), None)
            if attachment is None or any(attachment[key] != media[key] for key in ('checksum', 'size', 'media_type')):
                raise ReplayUnavailable('required Artifact version unavailable')
        captured_pack['media'] = []
        if context.encoded(pack.captured()) != context.encoded(captured_pack):
            raise ReplayUnavailable('generative input or revisions differ')
        self.index += 1
        if entry['outcome'] == 'provider_failure':
            raise OSError('captured generative outage')
        if entry['outcome'] != 'completed' or not isinstance(entry['proposal'], dict):
            raise ReplayUnavailable('unavailable Proposal')
        if entry.get('proposal_ref') != context.digest(context.encoded(entry['proposal'])):
            raise ReplayUnavailable('Proposal evidence changed')
        try:
            proposal = SemanticProposal.from_dict(entry['proposal'])
        except (ValueError, TypeError, KeyError):
            raise ReplayUnavailable('unrestorable Proposal') from None
        return GenerationResult(proposal, entry['provider'], entry['model'],
                                entry['instruction_version'], entry['usage'])

    def assert_consumed(self) -> None:
        if self.index != len(self.attempts):
            raise ReplayUnavailable('unused generative evidence')


class _RecordingChannel:
    def __init__(self, delegate: Any, capabilities: tuple[str, ...]):
        self.delegate = delegate
        self.capabilities: tuple[str, ...] = getattr(delegate, 'capabilities', capabilities)
        self.operations: list[dict[str, Any]] = []

    def normalize(self, payload: Any) -> dict[str, Any]:
        method = getattr(self.delegate, 'normalize', None)
        if not callable(method):
            raise ReplayUnavailable('fixture channel cannot normalize transport input')
        normalized = method(payload)
        if not isinstance(normalized, dict):
            raise ReplayUnavailable('fixture channel returned invalid normalized input')
        return normalized

    def prepare(self, outbound: Communication, inbound: Communication) -> Communication:
        method = getattr(self.delegate, 'prepare', None)
        prepared = method(outbound, inbound) if callable(method) else outbound
        self.operations.append({'operation': 'prepare', 'input': asdict(outbound), 'output': asdict(prepared)})
        return prepared

    def deliver(self, outbound: Communication) -> Any:
        entry = {'operation': 'deliver', 'input': asdict(outbound)}
        self.operations.append(entry)
        try:
            outcome = self.delegate.deliver(outbound)
            if outcome not in (True, False, 'success', 'failure', 'indeterminate', None):
                raise ReplayUnavailable('unsupported channel outcome')
            entry['outcome'] = outcome
            return outcome
        except OSError:
            entry['failure'] = 'provider_failure'
            raise


class _CapturedChannel:
    def __init__(self, operations: list[dict[str, Any]], capabilities: tuple[str, ...]):
        self.operations, self.index = operations, 0
        self.capabilities = capabilities

    def normalize(self, payload: Any) -> dict[str, Any]:
        raise ReplayUnavailable('replay requires captured normalized input')

    def _next(self, operation: str, outbound: Communication) -> dict[str, Any]:
        if self.index >= len(self.operations):
            raise ReplayUnavailable('missing channel evidence')
        entry = self.operations[self.index]
        if (entry.get('operation') != operation
                or context.encoded(entry.get('input')) != context.encoded(asdict(outbound))):
            raise ReplayUnavailable('channel input differs')
        self.index += 1
        return entry

    def prepare(self, outbound: Communication, inbound: Communication) -> Communication:
        entry = self._next('prepare', outbound)
        try:
            prepared = Communication(**entry['output'])
        except (KeyError, TypeError):
            raise ReplayUnavailable('missing prepared channel metadata') from None
        transport_fields = {'sender', 'recipient', 'subject', 'transport_message_id', 'transport_reply_to', 'references'}
        if any(context.encoded(value) != context.encoded(getattr(prepared, key))
               for key, value in asdict(outbound).items() if key not in transport_fields):
            raise ReplayUnavailable('channel evidence cannot replace computed semantic output')
        return replace(outbound, **{key: getattr(prepared, key) for key in transport_fields})

    def deliver(self, outbound: Communication) -> Any:
        entry = self._next('deliver', outbound)
        if entry.get('failure') == 'provider_failure':
            raise OSError('captured channel outage')
        if 'outcome' not in entry:
            raise ReplayUnavailable('missing channel outcome')
        return entry['outcome']

    def assert_consumed(self) -> None:
        if self.index != len(self.operations):
            raise ReplayUnavailable('unused channel evidence')


class _RecordingStore:
    def __init__(self, root: Path):
        self.delegate = LocalArtifactStore(root)
        self.operations: list[dict[str, Any]] = []

    def maintenance_lock(self) -> ContextManager[None]:
        return self.delegate.maintenance_lock()

    def stage(self, content: bytes, idempotency_key: str) -> StagedContent:
        return self.delegate.stage(content, idempotency_key)

    def read(self, reference: str) -> bytes:
        return self.delegate.read(reference)

    def read_staged(self, reference: str) -> bytes:
        return self.delegate.read_staged(reference)

    def remove_staged(self, reference: str, minimum_age_seconds: float) -> bool | None:
        return self.delegate.remove_staged(reference, minimum_age_seconds)

    def cleanup(self, preserved: set[str], minimum_age_seconds: float) -> list[str]:
        return self.delegate.cleanup(preserved, minimum_age_seconds)

    def finalize(self, staged: StagedContent) -> FinalizedContent:
        try:
            finalized = self.delegate.finalize(staged)
        except OSError:
            raise ReplayUnavailable('fixture lacks bytes required for Artifact publication') from None
        self.operations.append({'operation': 'finalize', 'input': asdict(staged), 'output': asdict(finalized)})
        return finalized

    def remove(self, reference: str) -> None:
        self.delegate.remove(reference)
        self.operations.append({'operation': 'remove', 'input': reference})


class _CapturedStore:
    def __init__(self, operations: list[dict[str, Any]]):
        self.operations, self.index = operations, 0

    def maintenance_lock(self) -> ContextManager[None]:
        return nullcontext()

    def stage(self, content: bytes, idempotency_key: str) -> StagedContent:
        raise ReplayUnavailable('replay cannot stage uncaptured Artifact bytes')

    def read(self, reference: str) -> bytes:
        raise ReplayUnavailable('replay has no captured Artifact bytes')

    def read_staged(self, reference: str) -> bytes:
        raise ReplayUnavailable('replay has no captured staged Artifact bytes')

    def remove_staged(self, reference: str, minimum_age_seconds: float) -> bool | None:
        raise ReplayUnavailable('replay has no captured staging cleanup outcome')

    def cleanup(self, preserved: set[str], minimum_age_seconds: float) -> list[str]:
        raise ReplayUnavailable('replay has no captured staging cleanup outcome')

    def _next(self, operation: str, value: Any) -> dict[str, Any]:
        if self.index >= len(self.operations):
            raise ReplayUnavailable('missing Artifact lifecycle outcome')
        entry = self.operations[self.index]
        if entry.get('operation') != operation or context.encoded(entry.get('input')) != context.encoded(value):
            raise ReplayUnavailable('Artifact lifecycle input differs')
        self.index += 1
        return entry

    def finalize(self, staged: StagedContent) -> FinalizedContent:
        entry = self._next('finalize', asdict(staged))
        try:
            finalized = FinalizedContent(**entry['output'])
        except (TypeError, KeyError):
            raise ReplayUnavailable('missing Artifact publication metadata') from None
        if finalized.reference != staged.reference or finalized.checksum != staged.checksum:
            raise ReplayUnavailable('Artifact publication version differs')
        return finalized

    def remove(self, reference: str) -> None:
        self._next('remove', reference)

    def assert_consumed(self) -> None:
        if self.index != len(self.operations):
            raise ReplayUnavailable('unused Artifact lifecycle evidence')


# Only deterministic Core state belongs in a fixture. Transport queues, delivery logs
# and optional prompt diagnostics do not.
_STATE_TABLES = {
    'retention_state', 'turns', 'processing_attempts', 'semantic_state', 'semantic_commits', 'trusted_records',
    'reference_assertions', 'reference_commits', 'trusted_relationships', 'trusted_groundings',
    'trusted_grounding_items', 'exposed_groundings', 'pending_items', 'candidate_target_bindings',
    'semantic_outbox', 'emergent_concepts', 'artifact_ingress', 'artifact_stages', 'trusted_artifacts',
    'artifact_publications', 'artifact_publication_failures', 'artifact_lifecycle', 'artifact_deletions',
}
_TRACE_FIELDS = {
    'schema_version', 'policy_versions', 'generative_attempts', 'selection', 'retrieval', 'context_pack',
    'input_communication_id', 'contract_version', 'attempt', 'proposal_capture', 'proposal_intent',
    'proposal_contract_version', 'validation_result', 'validation_reasons', 'commit_result',
    'semantic_commit_id', 'semantic_revision', 'delivery_result', 'failure_stage', 'error_type',
    'processing_outcome', 'event_ids', 'model_calls',
}


_GENERATION_FIELDS = {
    'provider', 'model', 'instruction_version', 'task_version', 'input_communication_id', 'context_pack',
    'context_pack_ref', 'candidate_revisions', 'usage', 'proposal', 'proposal_ref', 'outcome', 'capture', 'latency_ms',
}
_SELECTION_FIELDS = {
    'request', 'request_id', 'selection_policy_version', 'task_version', 'operating_point',
    'raw_response', 'failure', 'provider_metadata', 'input_communication_id', 'rubric_version',
    'provider', 'model', 'input_token_upper_bound', 'calls_used', 'mandatory_revisions',
    'candidate_revisions', 'latency_ms', 'fallback_reason', 'response', 'response_types',
    'response_capture', 'capture', 'outcome', 'fallback_response', 'total_latency_ms', 'selected_ids', 'context_pack_ref',
}


def _required_trace(raw: str) -> dict[str, Any]:
    trace = {key: value for key, value in json.loads(raw).items() if key in _TRACE_FIELDS}
    for name, permitted in (('generative_attempts', _GENERATION_FIELDS), ('selection', _SELECTION_FIELDS)):
        if name in trace:
            trace[name] = [{key: value for key, value in attempt.items() if key in permitted}
                           for attempt in trace[name]]
    return trace


def _snapshot(database: Path) -> bytes:
    with tempfile.TemporaryDirectory(prefix='piecetogether-snapshot-') as directory:
        snapshot = Path(directory) / 'snapshot.sqlite3'
        return _snapshot_to_file(database, snapshot)


def _snapshot_to_file(database: Path, snapshot: Path) -> bytes:
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(snapshot)) as fixture:
        source.backup(fixture)
        fixture.execute('PRAGMA secure_delete=ON')
        tables = [row[0] for row in fixture.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in tables:
            if name not in _STATE_TABLES and not name.startswith('sqlite_'):
                fixture.execute('DROP TABLE "' + name.replace('"', '""') + '"')
        for table in ('turns', 'processing_attempts'):
            for rowid, raw in fixture.execute(f'SELECT rowid, trace FROM {table}').fetchall():
                if raw:
                    trace = _required_trace(raw)
                    fixture.execute(f'UPDATE {table} SET trace=? WHERE rowid=?', (context.encoded(trace), rowid))
        fixture.commit()
        fixture.execute('VACUUM')
    return snapshot.read_bytes()


def _restore(state: bytes, database: Path) -> None:
    if not isinstance(state, bytes) or not state.startswith(b'SQLite format 3\x00'):
        raise ReplayUnavailable('invalid initial-state snapshot')
    database.write_bytes(state)


def _initial_revision(database: Path) -> int:
    """Check retained evidence before Core startup can initialize missing state."""
    try:
        with closing(sqlite3.connect(database)) as db:
            retention.require_processing(db)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # A missing retention table means legacy retain, not erased evidence.
            if _STATE_TABLES - {'retention_state'} - tables:
                raise ReplayUnavailable('missing initial-state evidence tables')
            rows = db.execute('SELECT revision FROM semantic_state WHERE singleton=1').fetchall()
            if len(rows) != 1 or type(rows[0][0]) is not int or rows[0][0] < 0:
                raise ReplayUnavailable('missing initial semantic revision')
            return int(rows[0][0])
    except (retention.RetentionUnavailable, sqlite3.Error) as error:
        raise ReplayUnavailable('initial-state evidence unavailable') from error


def _configuration(core: Core) -> dict[str, Any]:
    return {'identities': dict(core.config.identities), 'channel': core.config.channel,
            'capabilities': list(core.config.capabilities), 'contract_version': core.contract.version,
            'retention': asdict(core.config.retention),
            'selection': asdict(core.config.selection), 'media_policy': {
                key: asdict(value) for key, value in core.config.media_policy.items()},
            'telegram_bot_id': core.config.telegram.bot_id if core.config.telegram else None,
            'artifact_store_enabled': core.artifact_store is not None}


def _bootstrap(configuration: dict[str, Any], root: Path) -> Bootstrap:
    channel = configuration['channel']
    return Bootstrap(root / 'core.sqlite3', configuration['identities'], channel=channel,
        capabilities=tuple(configuration['capabilities']), contract_version=configuration['contract_version'],
        retention=retention.RetentionPolicy.from_dict(configuration['retention']),
        selection=context.SelectionConfig(**configuration['selection']),
        media_policy={key: MediaPolicy(value['artifact_type'], tuple(value['roles']))
                      for key, value in configuration['media_policy'].items()},
        artifact_store=root / 'artifacts' if configuration['artifact_store_enabled'] else None,
        email=EmailConfig('replay@example.invalid', 'localhost', security='plain') if channel == 'email' else None,
        telegram=TelegramConfig(configuration['telegram_bot_id'], 'replay') if channel == 'telegram' else None,
        secret_references={'replay': 'PT_REPLAY_UNUSED'} if channel == 'telegram' else {})


def _outcome(core: Core, result: dict[str, Any]) -> dict[str, Any]:
    inspected = core.inspect(result['communication_id'])
    trace = inspected['trace'] or {}
    return {'result': result, 'proposal': inspected['proposal'], 'outbound': inspected['outbound'],
            'grounding_items': inspected['grounding_items'],
            'selection': [{key: entry.get(key) for key in (
                'selection_policy_version', 'task_version', 'request_id', 'provider', 'model', 'rubric_version',
                'operating_point', 'candidate_revisions', 'mandatory_revisions', 'selected_ids',
                'fallback_reason', 'calls_used', 'outcome', 'context_pack_ref')}
                for entry in trace.get('selection', [])],
            'trace': {key: trace.get(key) for key in (
                'context_pack', 'retrieval', 'validation_result', 'validation_reasons', 'commit_result',
                'semantic_commit_id', 'semantic_revision', 'event_ids', 'processing_outcome', 'delivery_result')}}


def _semantic_state(core: Core, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {'steps': steps, 'history': core.inspect_history(), 'view': core.current_view(),
            'events': core.trusted_events()}


def _artifact_versions(core: Core) -> list[dict[str, Any]]:
    with connect(core.config.database) as db:
        return [dict(row) for row in db.execute('SELECT * FROM artifact_ingress ORDER BY rowid')]


class ReplayRecorder:
    """Explicit fixture scenario boundary. Its Core is isolated from the source database."""
    def __init__(self, core: Core, channel: _RecordingChannel):
        self.core, self.channel = core, channel
        self.steps: list[dict[str, Any]] = []
        self.outcomes: list[dict[str, Any]] = []
        self._restarted = False

    def restart(self) -> None:
        self.core = Core(self.core.config, model=self.core.model, channel=self.channel,
                         selector=self.core.selector, contract_provider=_ContractProvider(self.core.contract),
                         identity_source=self.core.identity_source, artifact_store=self.core.artifact_store)
        self._restarted = True

    def accept(self, payload: dict[str, Any]) -> dict[str, Any]:
        start = len(self.channel.operations)
        with connect(self.core.config.database) as db:
            before_attempts = db.execute('SELECT count(*) FROM processing_attempts').fetchone()[0]
        result = self.core.accept(payload)
        with connect(self.core.config.database) as db:
            new_attempt = db.execute('SELECT count(*) FROM processing_attempts').fetchone()[0] > before_attempts
        inspected = self.core.inspect(result['communication_id'])
        trace = inspected['trace']
        if trace is None:
            raise ReplayUnavailable('processing attempt has no durable trace')
        with connect(self.core.config.database) as db:
            stages = [dict(row) for row in db.execute(
                'SELECT * FROM artifact_stages WHERE communication_id=? ORDER BY rowid', (result['communication_id'],))]
        self.steps.append({'inbound': inspected['inbound'], 'restart': self._restarted,
                           'selection': (trace.get('selection', [])
                                         if new_attempt and not trace.get('restored_from_attempt') else []),
                           'generative_attempts': trace.get('generative_attempts', []) if new_attempt else [],
                           'artifact_stages': stages, 'channel': self.channel.operations[start:]})
        self._restarted = False
        self.outcomes.append(_outcome(self.core, result))
        return result


def capture(core: Core, scenario: Callable[[ReplayRecorder], None], *, channel: Any = None) -> ReplayCase:
    """Snapshot explicitly, then run a fixture scenario in an isolated local Core.

    Only supplied fixture adapters execute during capture. The default channel is local.
    Provider objects are reused for capture, never serialized or constructed during replay.
    """
    with retention.operation(core.config.database):
        _initial_revision(core.config.database)
        initial = _snapshot(core.config.database)
    identities = _RecordingIdentity()
    with tempfile.TemporaryDirectory(prefix='piecetogether-capture-') as directory:
        root = Path(directory)
        config = replace(core.config, database=root / 'core.sqlite3',
                         artifact_store=root / 'artifacts' if core.artifact_store is not None else None,
                         reference_state=None, projection=None, application_api=None, imap=None)
        _restore(initial, config.database)
        revision = _initial_revision(config.database)
        if channel is None:
            channel = DevelopmentChannel(config.database)
            channel.capabilities = config.capabilities
        recording_channel = _RecordingChannel(channel, config.capabilities)
        store = _RecordingStore(config.artifact_store) if config.artifact_store is not None else None
        fixture = Core(config, model=core.model, selector=core.selector, channel=recording_channel,
                       contract_provider=_ContractProvider(core.contract), identity_source=identities,
                       artifact_store=store)
        recorder = ReplayRecorder(fixture, recording_channel)
        scenario(recorder)
        expected = _semantic_state(recorder.core, recorder.outcomes)
        return ReplayCase(initial, hashlib.sha256(initial).hexdigest(), revision, asdict(core.contract),
                          _configuration(core), identities.values, recorder.steps, expected,
                          _artifact_versions(recorder.core), store.operations if store is not None else [])


def replay(case: ReplayCase) -> ReplayResult:
    """Compute outcomes using Core, not expected assertions. Never perform external I/O."""
    if (type(case.schema_version) is not int or case.schema_version != context.TRACE_SCHEMA_VERSION
            or case.policy_versions != context.POLICY_VERSIONS or not isinstance(case.initial_state, bytes)
            or case.artifact_versions is None or hashlib.sha256(case.initial_state).hexdigest() != case.state_digest):
        raise ReplayUnavailable('unsupported versions or missing initial/Artifact evidence')
    try:
        contract = DomainContract(**case.contract)
        identities = _CapturedIdentity(case.identities)
        with tempfile.TemporaryDirectory(prefix='piecetogether-replay-') as directory:
            root = Path(directory)
            config = _bootstrap(case.configuration, root)
            _restore(case.initial_state, config.database)
            if _initial_revision(config.database) != case.initial_revision:
                raise ReplayUnavailable('initial revision differs')
            model, selector, channel = (_CapturedModel([]), CapturedSelector([]),
                                        _CapturedChannel([], config.capabilities))
            store = _CapturedStore(case.artifact_operations) if config.artifact_store is not None else None
            if store is None and case.artifact_operations:
                raise ReplayUnavailable('ArtifactStore configuration differs')
            core = Core(config, model=model, selector=selector, channel=channel,
                        contract_provider=_ContractProvider(contract), identity_source=identities, artifact_store=store)
            if core.inspect_history()['revision'] != case.initial_revision:
                raise ReplayUnavailable('initial revision differs')
            outcomes = []
            for step in case.steps:
                if step['restart']:
                    core = Core(config, model=model, selector=selector, channel=channel,
                                contract_provider=_ContractProvider(contract), identity_source=identities, artifact_store=store)
                model = _CapturedModel(step['generative_attempts'])
                # Pre-call budget exhaustion has no selector call to consume.
                selector = CapturedSelector([entry for entry in step['selection'] if 'request' in entry])
                channel = _CapturedChannel(step['channel'], config.capabilities)
                core.model, core.selector, core.channel = model, selector, channel
                inbound = step['inbound']
                if inbound['attachments'] and store is not None:
                    stages = step['artifact_stages']
                    by_id = {row['attachment_id']: row for row in stages}
                    for attachment in inbound['attachments']:
                        row = by_id.get(attachment['id'])
                        if row is None or any(row[key] != attachment[key] for key in ('checksum', 'size')):
                            raise ReplayUnavailable('missing required Artifact staging evidence')
                        if row['communication_id'] != inbound['id'] or row['actor_id'] != inbound['actor_id']:
                            raise ReplayUnavailable('Artifact ownership differs')
                        with connect(config.database) as db:
                            db.execute("INSERT OR IGNORE INTO artifact_stages VALUES (?, ?, ?, ?, ?, ?, 'staged')",
                                       tuple(row[key] for key in ('communication_id', 'attachment_id', 'actor_id',
                                                                 'stage_ref', 'checksum', 'size')))
                fields = {'channel', 'sender', 'idempotency_key', 'text', 'sent_at', 'thread_id', 'reply_to',
                          'attachments', 'transport_message_id', 'transport_reply_to', 'references', 'subject'}
                payload = {key: value for key, value in inbound.items() if key in fields and value is not None}
                if config.channel == 'development':
                    payload = {key: value for key, value in payload.items()
                               if key not in ('transport_message_id', 'transport_reply_to', 'references', 'subject')}
                if config.channel == 'telegram':
                    payload = {key: value for key, value in payload.items() if key not in ('references', 'subject')}
                result = core._accept(payload, recovering=True)
                if result['communication_id'] != inbound['id']:
                    raise ReplayUnavailable('normalized input identity differs')
                model.assert_consumed()
                selector.assert_consumed()
                channel.assert_consumed()
                outcomes.append(_outcome(core, result))
            identities.assert_consumed()
            if store is not None:
                store.assert_consumed()
            if context.encoded(_artifact_versions(core)) != context.encoded(case.artifact_versions):
                raise ReplayUnavailable('Artifact versions differ')
            actual = _semantic_state(core, outcomes)
            mismatches = tuple(key for key in ('steps', 'history', 'view', 'events')
                               if context.encoded(actual[key]) != context.encoded(case.expected.get(key)))
            return ReplayResult(actual, mismatches)
    except (KeyError, TypeError, ValueError, sqlite3.Error, retention.RetentionUnavailable) as error:
        raise ReplayUnavailable('incomplete or unsupported replay evidence') from error
