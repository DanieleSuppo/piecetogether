"""Bounded, Core-owned model input; selection confers no semantic authority."""

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, replace
from time import monotonic
from typing import Any, Protocol

from . import view
from .contracts import DomainContract, finite_number
from .history import _candidate_supported, grounding_response, phrase_boundary, source_candidate
from .proposals import (
    ClaimOperation,
    ContextOperation,
    ContextRequestOperation,
    DisclosureOperation,
    EntityOperation,
    GroundingResolutionOperation,
    ReferenceResolutionOperation,
    RelationshipOperation,
    SemanticProposal,
    ValidationResult,
)


class BudgetExhausted(ValueError):
    """Operational exhaustion, never semantic rejection."""


@dataclass(frozen=True)
class SelectionConfig:
    adapter: str = 'deterministic'
    max_records: int = 32
    max_bytes: int = 65536
    catalogue_records: int = 128
    catalogue_bytes: int = 32768
    max_calls: int = 3
    max_tokens: int = 65536
    timeout_ms: int = 2000
    max_model_calls: int = 3
    model_id: str | None = None
    rubric_version: str = 'context-relevance-v1'
    secret_reference: str | None = None
    minimum_relevance: float | None = None

    def __post_init__(self) -> None:
        if self.adapter not in ('deterministic', 'jev'):
            raise ValueError('unknown semantic selector')
        for name, ceiling in (
            ('max_records', 256), ('max_bytes', 1048576), ('catalogue_records', 256),
            ('catalogue_bytes', 131072), ('max_calls', 16), ('max_tokens', 65536),
            ('timeout_ms', 30000), ('max_model_calls', 8),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f'{name} must be a positive bounded integer')
        if self.rubric_version != 'context-relevance-v1':
            raise ValueError('unsupported relevance rubric version')
        if self.adapter == 'jev' and (
            not isinstance(self.model_id, str)
            or not re.fullmatch(r'jev-\d+\.\d+\.\d+', self.model_id)
            or not isinstance(self.secret_reference, str) or not self.secret_reference.strip()
            or not finite_number(self.minimum_relevance)
            or self.minimum_relevance is None or not 0 < self.minimum_relevance < 1
        ):
            raise ValueError('Jev requires pinned model, secret reference and evaluated operating point')


@dataclass(frozen=True)
class ContextRecord:
    id: str
    kind: str
    summary: str
    revision: str
    payload_json: str
    reasons: tuple[str, ...] = ()
    disclosure_purposes: tuple[str, ...] = ()
    mandatory: bool = False


@dataclass(frozen=True)
class SelectionRequest:
    communication_json: str
    candidates: tuple[ContextRecord, ...]
    permitted_context: tuple[ContextRecord, ...]
    rubric_version: str
    remaining_calls: int
    remaining_tokens: int
    timeout_ms: int


@dataclass(frozen=True)
class Assessment:
    record_id: str
    primitive: str
    value: float


@dataclass(frozen=True)
class SelectionResult:
    outcome: str
    ranked_ids: tuple[str, ...]
    assessments: tuple[Assessment, ...]
    provider: str
    model: str
    rubric_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0


class SemanticSelector(Protocol):
    def select(self, request: SelectionRequest) -> SelectionResult: ...


class ReferenceSelector:
    def select(self, request: SelectionRequest) -> SelectionResult:
        text = json.loads(request.communication_json)['text'].casefold()
        words = set(re.findall(r'\w+', text))
        scores = [(record.id, len(words & set(re.findall(r'\w+', record.summary.casefold())))
                   + (100 if record.id in text else 0)) for record in request.candidates]
        ranked = tuple(record_id for record_id, score in sorted(scores, key=lambda item: -item[1]) if score)
        return SelectionResult('completed', ranked, (), 'deterministic', 'lexical-v1', request.rubric_version)


@dataclass(frozen=True)
class ContextPack:
    actor_id: str
    contract_version: str
    semantic_revision: int
    outcome: str = 'completed'
    records: tuple[ContextRecord, ...] = ()
    catalogue: tuple[ContextRecord, ...] = ()
    scope_revision: str = ''
    budget: SelectionConfig = SelectionConfig()
    used_bytes: int = 0
    catalogue_truncated: bool = False
    input_communication_id: str = ''
    contract_json: str = ''

    def captured(self) -> dict[str, Any]:
        return asdict(replace(self, catalogue=tuple(replace(record, payload_json='')
                                                   for record in self.catalogue)))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ContextPack':
        fields = dict(data)
        for name in ('records', 'catalogue'):
            fields[name] = tuple(ContextRecord(**{**record,
                'reasons': tuple(record['reasons']),
                'disclosure_purposes': tuple(record['disclosure_purposes'])}) for record in fields[name])
        fields['budget'] = SelectionConfig(**fields['budget'])
        return cls(**fields)


def model_view(pack: ContextPack) -> ContextPack:
    # Internal read permission is not permission to expose content to a response generator.
    # Keep undisclosable identities/revisions for reconciliation; never provide their values.
    def safe(record: ContextRecord, catalogue: bool = False) -> ContextRecord:
        return replace(record, payload_json='' if catalogue or not record.disclosure_purposes else record.payload_json,
                       summary=record.summary if record.disclosure_purposes else record.kind)
    return replace(pack, records=tuple(safe(record) for record in pack.records),
                   catalogue=tuple(safe(record, True) for record in pack.catalogue))


def retrieve(pack: ContextPack, requests: tuple[ContextRequestOperation, ...],
             evidence: list[dict[str, Any]]) -> tuple[ContextPack, ValidationResult]:
    known = {record.id: record for record in pack.catalogue}
    selected = {record.id: record for record in pack.records}
    used_bytes = pack.used_bytes
    for request in requests:
        if not set(request.scope_ids) <= known.keys():
            return pack, ValidationResult('rejected', ('context_scope_not_allowed',))
        for record_id in request.scope_ids:
            if record_id not in selected:
                record = known[record_id]
                used_bytes += len(record.payload_json.encode())
                selected[record_id] = replace(record, reasons=('additional_context',))
        if len(selected) > pack.budget.max_records or used_bytes > pack.budget.max_bytes:
            raise BudgetExhausted()
        evidence.append({'scope_ids': list(request.scope_ids), 'record_ids': list(request.scope_ids),
                         'purpose': request.purpose, 'budget': request.budget,
                         'revisions': {record_id: known[record_id].revision for record_id in request.scope_ids}})
    return replace(pack, records=tuple(selected.values()), used_bytes=used_bytes), ValidationResult('accepted')


def check_disclosure(proposal: SemanticProposal, pack: ContextPack | None,
                     inbound: dict[str, Any]) -> ValidationResult:
    records = {record.id: record for record in pack.records} if pack else {}
    if pack:
        local: set[str] = set()
        for operation in proposal.operations:
            if (isinstance(operation, ClaimOperation) or
                    isinstance(operation, (EntityOperation, ContextOperation)) and operation.action == 'create'):
                local.add(operation.id)
        accepted_items = {operation.item_id for operation in proposal.operations
                          if isinstance(operation, GroundingResolutionOperation)
                          and operation.outcome == 'accepted'}
        pending = {record.id: json.loads(record.payload_json)['candidate'] for record in pack.records
                   if record.kind == 'pending_grounding'}
        for record in pack.records:
            if record.kind == 'pending_grounding' and record.id in accepted_items:
                local.add(pending[record.id]['id'])
        for resolution in proposal.operations:
            if not isinstance(resolution, GroundingResolutionOperation) or resolution.outcome != 'corrected':
                continue
            candidate = pending.get(resolution.item_id)
            if candidate and candidate['kind'] == 'claim':
                for successor_id in resolution.successor_ids:
                    successor = next((operation for operation in proposal.operations
                                      if isinstance(operation, ClaimOperation) and operation.id == successor_id), None)
                    if successor and successor.target_id == candidate['target_id']:
                        local.add(successor.target_id)
        for operation in proposal.operations:
            references: tuple[str, ...] = ()
            if (isinstance(operation, EntityOperation) and operation.action == 'resolve'
                    or isinstance(operation, ContextOperation) and operation.action != 'create'):
                references = (operation.id,)
            elif isinstance(operation, ContextOperation):
                references = operation.entity_ids
            elif isinstance(operation, ClaimOperation):
                references = (operation.target_id,)
            elif isinstance(operation, RelationshipOperation):
                references = (operation.source_id, operation.target_id)
            elif isinstance(operation, ReferenceResolutionOperation) and operation.outcome == 'known':
                references = (operation.selected_id,) if operation.selected_id else ()
                expected_kinds = ({'canonical_concept', 'emergent_concept'} if operation.reference_kind == 'concept'
                                  else {operation.reference_kind})
                selected = records.get(operation.selected_id or '')
                if (selected is None or selected.kind not in expected_kinds
                        or operation.purpose not in selected.disclosure_purposes):
                    return ValidationResult('rejected', ('context_scope_not_allowed',))
            if any(reference not in local and (
                reference not in records or not records[reference].disclosure_purposes
            ) for reference in references):
                return ValidationResult('rejected', ('context_scope_not_allowed',))
    disclosures = [op for op in proposal.operations if isinstance(op, DisclosureOperation)]
    if len({op.record_id for op in disclosures}) != len(disclosures):
        return ValidationResult('rejected', ('duplicate_disclosure',))
    for operation in disclosures:
        disclosure_record = records.get(operation.record_id)
        if disclosure_record is None or operation.purpose not in disclosure_record.disclosure_purposes:
            return ValidationResult('rejected', ('disclosure_not_allowed',))
    if any(isinstance(op, ContextRequestOperation) for op in proposal.operations):
        return ValidationResult('rejected', ('context_request_not_resolved',))
    if len(response(proposal, inbound, pack)) > 65536:
        return ValidationResult('rejected', ('grounding_response_too_large',))
    return ValidationResult('accepted')


def response(proposal: SemanticProposal, inbound: dict[str, Any], pack: ContextPack | None) -> str:
    if proposal.response_intent == 'retrieval':
        return 'I cannot retrieve stored history through this acquisition channel.'
    # Historical references use Core rendering, not model-authored prose. A draft
    # remains usable for a memory-free acquisition; values withheld from model_view
    # cannot be smuggled from private history into its candidates either.
    draft = f'I understood: {inbound["text"]}' if pack and pack.records else proposal.draft_response
    rendered = grounding_response(replace(proposal, draft_response=draft))
    records = {record.id: record for record in pack.records} if pack else {}
    for operation in proposal.operations:
        if isinstance(operation, DisclosureOperation):
            record = records[operation.record_id]
            payload = json.loads(record.payload_json)
            content = payload.get('value', payload.get('candidate', payload.get('attributes', payload.get('text', record.summary))))
            rendered += f'\nFor {operation.purpose} ({record.id}): {encoded(content)}'
    return rendered


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def scope_revision(db: sqlite3.Connection, actor_id: str, communication_id: str) -> str:
    # Exposure/concept writes do not advance the semantic revision. Fence those too.
    values = [tuple(row) for row in db.execute(
        "SELECT g.id, i.id, t.id FROM exposed_groundings g JOIN pending_items i ON i.grounding_id=g.id "
        "LEFT JOIN trusted_grounding_items t ON t.id=i.id WHERE g.actor_id=? AND g.communication_id!=? ORDER BY i.id",
        (actor_id, communication_id))]
    concepts = [tuple(row) for row in db.execute(
        "SELECT name, communication_id FROM emergent_concepts "
        "WHERE json_extract(record, '$.provenance.actor_id')=? AND communication_id!=? ORDER BY name, communication_id",
        (actor_id, communication_id))]
    communications = db.execute(
        "SELECT count(*) FROM turns WHERE json_extract(inbound, '$.actor_id')=? AND status='completed' AND id!=?",
        (actor_id, communication_id)).fetchone()[0]
    return digest(encoded([values, concepts, communications]))


def assemble(db: sqlite3.Connection, inbound: dict[str, Any], contract_version: str,
             config: SelectionConfig, contract: DomainContract) -> ContextPack:
    actor_id = inbound['actor_id']
    revision = db.execute('SELECT revision FROM semantic_state').fetchone()['revision']
    records: list[ContextRecord] = []

    def add(record_id: str, kind: str, payload: Any, summary: str,
            mandatory: bool = False, purposes: tuple[str, ...] = ()) -> None:
        raw = encoded(payload)
        records.append(ContextRecord(record_id, kind, summary[:512], digest(raw), raw,
                                     ('structural',) if mandatory else (), purposes, mandatory))

    limit = config.catalogue_records + 1
    # ponytail: every Actor-owned pending item is mandatory; narrow structural scope if long backlogs demand it.
    pending = db.execute(
        "SELECT i.*, g.proposal, g.communication_id FROM pending_items i "
        "JOIN exposed_groundings g ON g.id=i.grounding_id "
        "LEFT JOIN trusted_grounding_items t ON t.id=i.id "
        "WHERE g.actor_id=? AND g.contract_version=? AND t.id IS NULL ORDER BY i.rowid LIMIT ?",
        (actor_id, contract_version, limit),
    ).fetchall()
    targets = []
    for row in pending:
        source = SemanticProposal.from_dict(json.loads(row['proposal']))
        targets.append(source_candidate(source, row['candidate_id']))

    target_matches = {index for index, target in enumerate(targets)
                      if _candidate_supported(target, inbound['text'])}

    for index, (row, target) in enumerate(zip(pending, targets)):
        payload = asdict(target)
        add(row['id'], 'pending_grounding', {'id': row['id'], 'grounding_id': row['grounding_id'],
            'policy': row['policy'], 'candidate': payload, 'source_communication_id': row['communication_id']},
            encoded(payload), mandatory=True, purposes=('grounding',) if inbound.get('reply_to') == row['grounding_id']
            or target_matches == {index} else ())

    for name, policy in contract.claim_concepts.items():
        explicit = phrase_boundary(inbound['text'], name)
        add('canonical:' + name, 'canonical_concept', {'name': name, 'policy': policy}, name,
            mandatory=False, purposes=('disambiguation', 'grounding') if explicit else ())

    trusted = [json.loads(row['record']) for row in db.execute(
        "SELECT record FROM trusted_records WHERE kind != 'context_transition' "
        "AND json_extract(record, '$.provenance.actor_id')=? "
        "ORDER BY rowid DESC LIMIT ?", (actor_id, limit))]
    current_contexts = view.contexts(db)
    trusted = [current_contexts[record['id']] if record['kind'] == 'context' else record for record in trusted]
    by_id = {record['id']: record for record in trusted}
    for record in trusted:
        summary = encoded(record.get('attributes', record.get('value', record['kind'])))
        if record['kind'] == 'context':
            summary += ' ' + ' '.join(encoded(by_id.get(member, {}).get('attributes', {}))
                                      for member in record['entity_ids'])
        explicit = record['id'] in inbound['text']
        add(record['id'], record['kind'], record, summary, mandatory=explicit,
            purposes=('continuity', 'disambiguation') if explicit else ())
    for row in db.execute(
        "SELECT r.id, r.record FROM reference_assertions r JOIN trusted_records t "
        "ON t.id=json_extract(r.record, '$.target_id') "
        "WHERE json_extract(t.record, '$.provenance.actor_id')=? "
        "AND NOT EXISTS (SELECT 1 FROM reference_assertions successor "
        "WHERE json_extract(successor.record, '$.supersedes_id')=r.id) "
        "ORDER BY r.rowid DESC LIMIT ?", (actor_id, limit),
    ):
        record = json.loads(row['record'])
        # Provider trust grants internal reference use, not sender-facing disclosure.
        add(record['id'], 'reference_assertion', record,
            record['concept'] + ' ' + encoded(record['value']), mandatory=record['id'] in inbound['text'])
    for row in db.execute(
        "SELECT record FROM emergent_concepts WHERE json_extract(record, '$.provenance.actor_id')=? "
        "ORDER BY rowid DESC LIMIT ?", (actor_id, limit),
    ):
        record = json.loads(row['record'])
        explicit = phrase_boundary(inbound['text'], record['name'])
        add('concept:' + digest(encoded([record['name'], record['provenance']['source_communication_id']])),
            'emergent_concept', record, record['name'] + ' ' + record['description'], mandatory=explicit,
            purposes=('disambiguation', 'grounding') if explicit else ())
    for row in db.execute(
        "SELECT id, inbound, proposal FROM turns WHERE json_extract(inbound, '$.actor_id')=? "
        "AND status='completed' ORDER BY rowid DESC LIMIT ?", (actor_id, limit),
    ):
        communication = json.loads(row['inbound'])
        structural = bool(inbound.get('thread_id') and inbound['thread_id'] == communication.get('thread_id'))
        add(row['id'], 'communication', communication, communication['text'], False,
            ('continuity',) if structural else ())
        if row['proposal']:
            for operation in json.loads(row['proposal']).get('operations', []):
                if operation['kind'] == 'artifact':
                    add('artifact:' + digest(encoded([row['id'], operation['id']])), 'artifact',
                        {**operation, 'source_communication_id': row['id'], 'status': 'candidate'},
                        communication['text'] + ' ' + operation['artifact_type'])
    mandatory = [record for record in records if record.mandatory]
    optional = [record for record in records if not record.mandatory]
    compact: list[ContextRecord] = []
    size = 0
    for record in mandatory + optional:
        cost = len(encoded([record.id, record.kind, record.summary, record.revision]).encode())
        if len(compact) >= config.catalogue_records or size + cost > config.catalogue_bytes:
            if record.mandatory:
                return ContextPack(actor_id, contract_version, revision, 'budget_exhausted',
                                   scope_revision=scope_revision(db, actor_id, inbound['id']), budget=config,
                                   input_communication_id=inbound['id'])
            continue
        compact.append(record)
        size += cost
    return ContextPack(actor_id, contract_version, revision, catalogue=tuple(compact),
                       scope_revision=scope_revision(db, actor_id, inbound['id']), budget=config,
                       input_communication_id=inbound['id'],
                       catalogue_truncated=len(compact) < len(records))


def validate_selection(result: SelectionResult, request: SelectionRequest) -> None:
    ids = {record.id for record in request.candidates}
    if (not isinstance(result, SelectionResult) or result.outcome not in ('completed', 'abstained', 'unavailable')
            or not isinstance(result.ranked_ids, tuple) or len(result.ranked_ids) > len(ids)
            or any(not isinstance(record_id, str) or record_id not in ids for record_id in result.ranked_ids)
            or len(set(result.ranked_ids)) != len(result.ranked_ids)
            or result.rubric_version != request.rubric_version
            or not isinstance(result.assessments, tuple) or len(result.assessments) > len(ids)
            or not isinstance(result.provider, str) or not result.provider
            or not isinstance(result.model, str) or not result.model
            or not finite_number(result.latency_ms) or result.latency_ms < 0
            or any(type(value) is not int or value < 0 for value in (result.input_tokens, result.output_tokens))):
        raise ValueError('invalid_selection')
    seen: set[str] = set()
    for item in result.assessments:
        if (not isinstance(item, Assessment) or item.record_id not in ids or item.record_id in seen
                or item.primitive != 'noul' or not finite_number(item.value)):
            raise ValueError('invalid_selection')
        seen.add(item.record_id)
        if not 0 <= item.value <= 1:
            raise ValueError('invalid_selection')


def select(pack: ContextPack, inbound: dict[str, Any], selector: SemanticSelector,
           evidence: list[dict[str, Any]]) -> ContextPack:
    if pack.outcome != 'completed':
        return pack
    config = pack.budget
    mandatory = tuple(record for record in pack.catalogue if record.mandatory)
    # The selector has summaries, never raw record content or access to the DB.
    candidates = tuple(replace(record, payload_json='', reasons=(), disclosure_purposes=())
                       for record in pack.catalogue if not record.mandatory)
    request = SelectionRequest(encoded(inbound), candidates,
                               tuple(replace(record, payload_json='') for record in mandatory),
                               config.rubric_version, config.max_calls, config.max_tokens, config.timeout_ms)
    input_size = len(encoded(asdict(request)).encode())
    mandatory_size = sum(len(record.payload_json.encode()) for record in mandatory)
    if (input_size > config.max_tokens or len(mandatory) > config.max_records
            or mandatory_size > config.max_bytes):
        evidence.append({'outcome': 'budget_exhausted', 'rubric_version': config.rubric_version,
                         'input_token_upper_bound': input_size})
        return replace(pack, outcome='budget_exhausted')
    started = monotonic()
    result: SelectionResult | None = None
    fallback: str | None = None
    try:
        result = selector.select(request)
        validate_selection(result, request)
        if result.outcome != 'completed':
            fallback = result.outcome
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        if isinstance(error, BudgetExhausted):
            evidence.append({'rubric_version': config.rubric_version, 'outcome': 'budget_exhausted',
                             'latency_ms': round((monotonic() - started) * 1000, 3),
                             'provider_metadata': error.args[0] if error.args and isinstance(error.args[0], dict) else None,
                             'candidate_revisions': {record.id: record.revision for record in candidates}})
            return replace(pack, outcome='budget_exhausted')
        fallback = 'invalid_selection' if not isinstance(error, OSError) else 'provider_failure'
    elapsed = round((monotonic() - started) * 1000, 3)
    entry: dict[str, Any] = {'input_communication_id': inbound['id'], 'rubric_version': config.rubric_version,
                            'provider': config.adapter, 'model': config.model_id or 'lexical-v1',
                            'input_token_upper_bound': input_size, 'calls_used': 1,
                            'mandatory_revisions': {record.id: record.revision for record in mandatory},
                            'candidate_revisions': {record.id: record.revision for record in candidates},
                            'latency_ms': elapsed, 'fallback_reason': fallback,
                            'response': asdict(result) if isinstance(result, SelectionResult) else None}
    # Malformed provider values must not poison durable trace serialization.
    try:
        encoded(entry)
    except (TypeError, ValueError, RecursionError):
        entry['response'] = None
        entry['response_capture'] = 'unserializable'
    evidence.append(entry)
    reported_tokens = (result.input_tokens if isinstance(result, SelectionResult)
                       and type(result.input_tokens) is int and result.input_tokens >= 0 else 0)
    if elapsed >= config.timeout_ms or reported_tokens > config.max_tokens:
        return replace(pack, outcome='budget_exhausted')
    if fallback:
        charged = max(input_size, reported_tokens)
        if config.max_calls < 2 or charged + input_size > config.max_tokens:
            return replace(pack, outcome='budget_exhausted')
        result = ReferenceSelector().select(replace(request, remaining_calls=config.max_calls - 1,
                                                   remaining_tokens=config.max_tokens - charged,
                                                   timeout_ms=max(1, config.timeout_ms - int(elapsed))))
        entry['fallback_response'] = asdict(result)
        entry['calls_used'] = 2
        if (monotonic() - started) * 1000 >= config.timeout_ms:
            return replace(pack, outcome='budget_exhausted')
    assert result is not None
    selected = list(mandatory)
    size = sum(len(record.payload_json.encode()) for record in mandatory)
    if len(selected) > config.max_records or size > config.max_bytes:
        return replace(pack, outcome='budget_exhausted')
    by_id = {record.id: record for record in pack.catalogue}
    for record_id in result.ranked_ids:
        record = by_id[record_id]
        cost = len(record.payload_json.encode())
        if len(selected) < config.max_records and size + cost <= config.max_bytes:
            selected.append(replace(record, reasons=('fallback' if fallback else 'ranking',)))
            size += cost
    return replace(pack, records=tuple(selected), used_bytes=size)


def current(db: sqlite3.Connection, pack: ContextPack) -> bool:
    return (db.execute('SELECT revision FROM semantic_state').fetchone()['revision'] == pack.semantic_revision
            and scope_revision(db, pack.actor_id, pack.input_communication_id) == pack.scope_revision)
