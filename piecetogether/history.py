"""Core-owned exposure evidence and append-only trusted semantic commits."""

import json
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from .contracts import DomainContract
from .proposals import (
    ClaimOperation,
    ContextOperation,
    EmergentConceptOperation,
    EntityOperation,
    GroundingPlanOperation,
    GroundingResolutionOperation,
    RelationshipOperation,
    SemanticProposal,
    ValidationResult,
    validate,
)


def initialize(db: sqlite3.Connection, contract: DomainContract) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS semantic_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            revision INTEGER NOT NULL, contract_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_commits (
            id TEXT PRIMARY KEY, communication_id TEXT NOT NULL UNIQUE,
            record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trusted_records (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, record TEXT NOT NULL,
            grounding_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
            UNIQUE(grounding_id, candidate_id)
        );
        CREATE TABLE IF NOT EXISTS trusted_relationships (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trusted_groundings (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trusted_grounding_items (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exposed_groundings (
            id TEXT PRIMARY KEY, communication_id TEXT NOT NULL,
            actor_id TEXT NOT NULL, contract_version TEXT NOT NULL,
            exposed_at TEXT NOT NULL, proposal TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_items (
            id TEXT PRIMARY KEY, grounding_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL, policy TEXT NOT NULL,
            UNIQUE(grounding_id, candidate_id)
        );
        CREATE TABLE IF NOT EXISTS candidate_target_bindings (
            grounding_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
            target_grounding_id TEXT NOT NULL, target_candidate_id TEXT NOT NULL,
            PRIMARY KEY(grounding_id, candidate_id)
        );
        CREATE TABLE IF NOT EXISTS semantic_outbox (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS emergent_concepts (
            name TEXT NOT NULL, communication_id TEXT NOT NULL,
            record TEXT NOT NULL, PRIMARY KEY(name, communication_id)
        );
    """)
    db.execute("INSERT INTO semantic_state VALUES (1, 0, ?) "
               "ON CONFLICT(singleton) DO UPDATE SET contract_version=excluded.contract_version",
               (contract.version,))


def targets(db: sqlite3.Connection) -> dict[str, str]:
    result = {}
    for row in db.execute("SELECT id, kind, record FROM trusted_records"):
        result[row['id']] = (json.loads(row['record'])['entity_type']
                             if row['kind'] == 'entity' else '$' + row['kind'])
    return result


def concept_names(db: sqlite3.Connection) -> set[str]:
    return {row['name'] for row in db.execute("SELECT DISTINCT name FROM emergent_concepts")}


def remember_concepts(db: sqlite3.Connection, proposal: SemanticProposal, actor_id: str) -> None:
    for operation in proposal.operations:
        if isinstance(operation, EmergentConceptOperation):
            record = {**asdict(operation), 'contract_version': proposal.contract_version,
                      'provenance': {'source_communication_id': proposal.communication_id,
                                     'actor_id': actor_id}}
            db.execute("INSERT OR IGNORE INTO emergent_concepts VALUES (?, ?, ?)",
                       (operation.name, proposal.communication_id, json.dumps(record)))


def exposed_items(db: sqlite3.Connection, communication_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        "SELECT i.id, i.grounding_id, i.candidate_id, i.policy, "
        "COALESCE(json_extract(t.record, '$.outcome'), 'pending') AS outcome "
        "FROM pending_items i JOIN exposed_groundings g ON g.id=i.grounding_id "
        "LEFT JOIN trusted_grounding_items t ON t.id=i.id "
        "WHERE g.communication_id=? ORDER BY i.rowid", (communication_id,))]


def grounding_response(proposal: SemanticProposal) -> str:
    """Bind planned interpretations to observable content, independent of model prose."""
    candidates = {op.id: op for op in proposal.operations
                  if isinstance(op, (EntityOperation, ContextOperation, ClaimOperation))}
    clauses = []
    planned: set[str] = set()
    for plan in proposal.operations:
        if not isinstance(plan, GroundingPlanOperation):
            continue
        for target_id in plan.claim_ids + plan.resolution_ids:
            candidate = candidates[target_id]
            planned.add(target_id)
            if isinstance(candidate, ClaimOperation):
                clauses.append(f'For {candidate.target_id}, {candidate.concept} is '
                               f'{json.dumps(candidate.value, ensure_ascii=False, allow_nan=False)}.')
            elif isinstance(candidate, EntityOperation):
                if candidate.action == 'create':
                    clauses.append(f'A new {candidate.entity_type} ({candidate.id}) with '
                                   f'{json.dumps(candidate.attributes, ensure_ascii=False, allow_nan=False)}.')
                else:
                    clauses.append(f'The existing {candidate.entity_type} ({candidate.id}).')
            else:
                clauses.append(f'The {"new" if candidate.action == "create" else "existing"} '
                               f'context {candidate.id}' +
                               (f' involving {", ".join(candidate.entity_ids)}.' if candidate.entity_ids else '.'))
    for operation in proposal.operations:
        if isinstance(operation, RelationshipOperation) and operation.source_id in planned:
            clauses.append(f'{operation.source_id} {operation.relationship_type} {operation.target_id}.')
    if not clauses:
        return proposal.draft_response
    return proposal.draft_response + '\n\nI understood:\n' + '\n'.join(clauses) + '\nIs that right?'


def expose(
    db: sqlite3.Connection, proposal: SemanticProposal, outbound_id: str,
    actor_id: str, exposed_at: str,
) -> None:
    plans = [op for op in proposal.operations if isinstance(op, GroundingPlanOperation)]
    if proposal.intent not in ('candidate', 'semantic_commit'):
        return
    if not plans:
        return
    db.execute("INSERT OR IGNORE INTO exposed_groundings VALUES (?, ?, ?, ?, ?, ?)",
               (outbound_id, proposal.communication_id, actor_id,
                proposal.contract_version, exposed_at, json.dumps(asdict(proposal))))
    for plan in plans:
        for candidate_id in plan.claim_ids + plan.resolution_ids:
            db.execute("INSERT OR IGNORE INTO pending_items VALUES (?, ?, ?, ?)",
                       (str(uuid4()), outbound_id, candidate_id, plan.policy))
    for resolution in proposal.operations:
        if not isinstance(resolution, GroundingResolutionOperation) or resolution.outcome != 'corrected':
            continue
        pending = pending_candidate(db, resolution.item_id)
        if pending is None:
            continue
        row, _, candidate = pending
        if not isinstance(candidate, ClaimOperation):
            continue
        binding = claim_target_binding(db, row['grounding_id'], candidate)
        if binding is None:
            continue
        for successor_id in resolution.successor_ids:
            db.execute("INSERT OR IGNORE INTO candidate_target_bindings VALUES (?, ?, ?, ?)",
                       (outbound_id, successor_id, *binding))


def phrase_boundary(text: str, phrase: str) -> bool:
    """Whole-token Core boundary; never a model-defined semantic classifier."""
    return bool(re.search(r'(?<!\w)' + re.escape(phrase.casefold()) + r'(?!\w)', text.casefold()))


CandidateOperation = EntityOperation | ContextOperation | ClaimOperation


def source_candidate(source: SemanticProposal, candidate_id: str) -> CandidateOperation:
    return next(operation for operation in source.operations
                if isinstance(operation, (EntityOperation, ContextOperation, ClaimOperation))
                and operation.id == candidate_id)


def pending_candidate(
    db: sqlite3.Connection, item_id: str,
) -> tuple[sqlite3.Row, SemanticProposal, CandidateOperation] | None:
    row = db.execute(
        "SELECT i.*, g.communication_id, g.actor_id, g.contract_version, g.exposed_at, g.proposal "
        "FROM pending_items i JOIN exposed_groundings g ON g.id=i.grounding_id WHERE i.id=?", (item_id,),
    ).fetchone()
    if row is None:
        return None
    source = SemanticProposal.from_dict(json.loads(row['proposal']))
    return row, source, source_candidate(source, row['candidate_id'])


def claim_target_binding(
    db: sqlite3.Connection, grounding_id: str, candidate: ClaimOperation,
) -> tuple[str, str] | None:
    binding = db.execute(
        "SELECT target_grounding_id, target_candidate_id FROM candidate_target_bindings "
        "WHERE grounding_id=? AND candidate_id=?", (grounding_id, candidate.id),
    ).fetchone()
    if binding:
        return binding['target_grounding_id'], binding['target_candidate_id']
    if db.execute("SELECT 1 FROM trusted_records WHERE id=?", (candidate.target_id,)).fetchone():
        return None
    return grounding_id, candidate.target_id


def bound_target(
    db: sqlite3.Connection, grounding_id: str, candidate: ClaimOperation,
) -> tuple[tuple[str, str], str] | None:
    binding = claim_target_binding(db, grounding_id, candidate)
    if binding is None:
        return None
    row = db.execute("SELECT proposal FROM exposed_groundings WHERE id=?", (binding[0],)).fetchone()
    if row is None:
        return None
    target = source_candidate(SemanticProposal.from_dict(json.loads(row['proposal'])), binding[1])
    if isinstance(target, EntityOperation):
        return binding, target.entity_type
    if isinstance(target, ContextOperation):
        return binding, '$context'
    return None


def _candidate_terms(candidate: CandidateOperation) -> tuple[str, ...]:
    if isinstance(candidate, ClaimOperation) and isinstance(candidate.value, (str, int, float)):
        return (candidate.id, str(candidate.value))
    return (candidate.id,)


def _candidate_supported(candidate: CandidateOperation, text: str) -> bool:
    return any(phrase_boundary(text, term) for term in _candidate_terms(candidate))


def _relevant_clauses(text: str, candidate: CandidateOperation) -> tuple[str, ...]:
    return tuple(clause.strip() for clause in re.split(r'[;.!?]', text)
                 if clause.strip() and _candidate_supported(candidate, clause))


# ponytail: finite evidence grammar; extend supported composition when domain fixtures require it.
def _clause_supported(outcome: str, candidate: CandidateOperation,
                      clause: str, successors: tuple[CandidateOperation, ...]) -> bool:
    if any(character in clause for character in ('"', "'", '?')):
        return False
    terms = _candidate_terms(candidate)
    positives = ('yes', 'sì', 'si', 'va bene', 'correct', 'exactly', 'esatto')
    negatives = ('no', 'non', 'wrong', 'not')
    for term in terms:
        quoted = re.escape(term)
        if outcome == 'accepted' and any(
            re.fullmatch(r'\s*' + quoted + r'\s+' + re.escape(marker) + r'(?:\s*,\s*va bene)?\s*',
                         clause, flags=re.IGNORECASE) for marker in positives
        ):
            return True
        if outcome == 'rejected' and any(
            re.fullmatch(r'\s*' + quoted + r'\s+' + re.escape(marker) + r'\s*',
                         clause, flags=re.IGNORECASE) for marker in negatives
        ):
            return True
        if outcome == 'corrected' and len(successors) == 1 and any(
            re.fullmatch(r'\s*' + quoted + r'\s+' + re.escape(marker) + r'\s*,?\s*' +
                         re.escape(successor_term) + r'\s*', clause, flags=re.IGNORECASE)
            for marker in negatives for successor_term in _candidate_terms(successors[0])
        ):
            return True
    return False


def _whole_text_evidence_ambiguous(span: str, inbound_text: str) -> bool:
    # Unlabelled negation has an ambiguous antecedent; never discard it as unrelated.
    return (f'"{span}"' in inbound_text or f"'{span}'" in inbound_text
            or bool(re.search(re.escape(span.strip()) + r'\s*\?', inbound_text, re.IGNORECASE))
            or any(re.match(r'\s*(?:no|nope|not|non)\b', clause, re.IGNORECASE)
                   for clause in re.split(r'[;.!?]', inbound_text)))


def _outcome_supported(outcome: str, candidate: CandidateOperation,
                       span: str, inbound_text: str,
                       successors: tuple[CandidateOperation, ...]) -> bool:
    clauses = _relevant_clauses(inbound_text, candidate)
    return (not _whole_text_evidence_ambiguous(span, inbound_text) and len(clauses) == 1
            and span.strip().casefold() == clauses[0].casefold()
            and _clause_supported(outcome, candidate, clauses[0], successors))


def _explicit_acceptance_supported(candidate: CandidateOperation,
                                  span: str, inbound_text: str, forms: list[str]) -> bool:
    if _whole_text_evidence_ambiguous(span, inbound_text):
        return False
    clauses = _relevant_clauses(inbound_text, candidate)
    if len(clauses) != 1 or span.strip().casefold() != clauses[0].casefold():
        return False
    return any(
        any(re.fullmatch(r'\s*' + re.escape(term) + r'\s+' + re.escape(form) + r'\s*',
                         clauses[0], flags=re.IGNORECASE) for term in _candidate_terms(candidate))
        for form in forms
    )


def _target_unique(db: sqlite3.Connection, actor_id: str, contract_version: str,
                   span: str) -> bool:
    matches = 0
    rows = db.execute(
        "SELECT i.candidate_id, g.proposal FROM pending_items i "
        "JOIN exposed_groundings g ON g.id=i.grounding_id "
        "LEFT JOIN trusted_grounding_items t ON t.id=i.id "
        "WHERE g.actor_id=? AND g.contract_version=? AND t.id IS NULL",
        (actor_id, contract_version),
    )
    for row in rows:
        source = SemanticProposal.from_dict(json.loads(row['proposal']))
        if _candidate_supported(source_candidate(source, row['candidate_id']), span):
            matches += 1
    return matches == 1


def committed(db: sqlite3.Connection, communication_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT record FROM semantic_commits WHERE communication_id=?",
                     (communication_id,)).fetchone()
    return json.loads(row['record']) if row else None


def check(
    db: sqlite3.Connection, proposal: SemanticProposal, inbound: dict[str, Any],
    contract: DomainContract,
) -> ValidationResult:
    available = targets(db)
    if isinstance(proposal, SemanticProposal) and proposal.intent == 'semantic_commit':
        pending_resolutions = [
            (operation, *pending)
            for operation in proposal.operations if isinstance(operation, GroundingResolutionOperation)
            if (pending := pending_candidate(db, operation.item_id)) is not None
        ]
        accepted_targets = {
            (row['grounding_id'], candidate.id)
            for operation, row, _, candidate in pending_resolutions
            if operation.outcome == 'accepted' and isinstance(candidate, (EntityOperation, ContextOperation))
        }
        for operation, row, _, candidate in pending_resolutions:
            if operation.outcome == 'accepted' and isinstance(candidate, EntityOperation):
                available[candidate.id] = candidate.entity_type
                continue
            if operation.outcome == 'accepted' and isinstance(candidate, ContextOperation):
                available[candidate.id] = '$context'
                continue
            if operation.outcome not in ('accepted', 'corrected') or not isinstance(candidate, ClaimOperation):
                continue
            target = bound_target(db, row['grounding_id'], candidate)
            if target is None:
                continue
            binding, target_type = target
            target_is_trusted = db.execute(
                "SELECT 1 FROM trusted_records WHERE grounding_id=? AND candidate_id=?", binding,
            ).fetchone() is not None
            if operation.outcome == 'corrected' or target_is_trusted or binding in accepted_targets:
                available[candidate.target_id] = target_type
    result = validate(proposal, inbound['id'], contract, available, concept_names(db))
    if result.outcome != 'accepted':
        return result
    state = db.execute("SELECT * FROM semantic_state").fetchone()
    if state['contract_version'] != contract.version:
        return ValidationResult('stale', ('contract_version_changed',))
    planned: set[str] = set()
    local = {op.id for op in proposal.operations
             if isinstance(op, (EntityOperation, ContextOperation, ClaimOperation))}
    for op in proposal.operations:
        if isinstance(op, GroundingPlanOperation):
            ids = op.claim_ids + op.resolution_ids
            if planned.intersection(ids):
                return ValidationResult('rejected', ('duplicate_grounding_target',))
            if not set(ids) <= local:
                return ValidationResult('rejected', ('grounding_target_not_allowed',))
            planned.update(ids)
    if proposal.intent == 'candidate':
        return result
    # ponytail: deployment-wide revision, scope revisions in #22 if contention matters.
    if proposal.semantic_revision != state['revision']:
        return ValidationResult('stale', ('semantic_revision_changed',))
    resolutions = tuple(op for op in proposal.operations if isinstance(op, GroundingResolutionOperation))
    if not resolutions:
        return ValidationResult('rejected', ('ungrounded_trusted_mutation',))
    successor_list = [successor_id for operation in resolutions for successor_id in operation.successor_ids]
    if any(operation.outcome == 'corrected' and len(operation.successor_ids) != 1
           for operation in resolutions) or len(successor_list) != len(set(successor_list)):
        return ValidationResult('rejected', ('invalid_correction_successor',))
    successor_ids = set(successor_list)
    candidate_ids = {op.id for op in proposal.operations
                     if isinstance(op, (EntityOperation, ContextOperation, ClaimOperation))}
    successor_plans = {target_id for operation in proposal.operations
                       if isinstance(operation, GroundingPlanOperation)
                       for target_id in operation.claim_ids + operation.resolution_ids}
    if any(not isinstance(op, (GroundingResolutionOperation, EntityOperation, ContextOperation,
                               ClaimOperation, GroundingPlanOperation))
           for op in proposal.operations) or candidate_ids != successor_ids or successor_ids != successor_plans:
        return ValidationResult('rejected', ('ungrounded_trusted_mutation',))
    resolution_ids = [op.item_id for op in resolutions]
    if len(resolution_ids) != len(set(resolution_ids)):
        return ValidationResult('rejected', ('duplicate_grounding_resolution',))
    for operation in resolutions:
        pending = pending_candidate(db, operation.item_id)
        if pending is None:
            return ValidationResult('rejected', ('grounding_item_unavailable',))
        row, source, candidate = pending
        if db.execute("SELECT 1 FROM trusted_grounding_items WHERE id=?",
                      (operation.item_id,)).fetchone():
            return ValidationResult('stale', ('grounding_item_already_resolved',))
        if row['contract_version'] != contract.version:
            return ValidationResult('stale', ('grounding_contract_changed',))
        if (row['actor_id'] != inbound['actor_id'] or row['policy'] != operation.policy
                or datetime.fromisoformat(inbound['received_at']) <= datetime.fromisoformat(row['exposed_at'])):
            return ValidationResult('rejected', ('invalid_grounding_evidence',))
        successors = tuple(op for op in proposal.operations
                           if isinstance(op, (EntityOperation, ContextOperation, ClaimOperation))
                           and op.id in operation.successor_ids)
        if operation.outcome == 'corrected' and (len(successors) != 1 or type(successors[0]) is not type(candidate)):
            return ValidationResult('rejected', ('invalid_correction_successor',))
        if operation.outcome == 'corrected' and isinstance(candidate, ClaimOperation):
            successor = successors[0]
            assert isinstance(successor, ClaimOperation)
            prior_target = candidate.target_id
            mapped = db.execute(
                "SELECT id FROM trusted_records WHERE grounding_id=? AND candidate_id=?",
                (row['grounding_id'], prior_target),
            ).fetchone()
            if successor.concept != candidate.concept or successor.target_id not in {
                prior_target, mapped['id'] if mapped else prior_target,
            }:
                return ValidationResult('rejected', ('invalid_correction_successor',))
        span = operation.evidence_span
        delayed = inbound['reply_to'] != row['grounding_id']
        if operation.outcome == 'pending':
            continue
        if ((operation.acceptance_mode == 'implicit' or operation.outcome != 'accepted' or delayed)
                and (not span or span.casefold() not in inbound['text'].casefold())):
            return ValidationResult('rejected', ('grounding_evidence_span_required',))
        evidence = span or ''
        if (span or delayed) and not _target_unique(db, inbound['actor_id'], contract.version, evidence):
            return ValidationResult('rejected', ('grounding_target_ambiguous',))
        if operation.acceptance_mode == 'implicit':
            if (operation.outcome == 'accepted' and (
                    not operation.rationale or evidence.casefold() not in operation.rationale.casefold())):
                return ValidationResult('rejected', ('implicit_rationale_unsupported',))
            if not _outcome_supported(operation.outcome, candidate, evidence, inbound['text'], successors):
                return ValidationResult('rejected', ('implicit_evidence_unsupported',))
        elif operation.acceptance_mode == 'explicit':
            if operation.outcome == 'accepted':
                forms = contract.grounding_policies[operation.policy].get('confirmation_forms', ['yes'])
                if delayed or span:
                    if not _explicit_acceptance_supported(candidate, evidence, inbound['text'], forms):
                        return ValidationResult('rejected', ('explicit_confirmation_required',))
                elif inbound['text'].strip().casefold() not in {form.strip().casefold() for form in forms}:
                    return ValidationResult('rejected', ('explicit_confirmation_required',))
            elif not _outcome_supported(operation.outcome, candidate, evidence, inbound['text'], successors):
                return ValidationResult('rejected', ('explicit_evidence_unsupported',))
        else:
            return ValidationResult('rejected', ('invalid_grounding_evidence',))
    return result


def normalized_successors(proposal: SemanticProposal, summary: dict[str, Any]) -> SemanticProposal:
    targets = summary.get('successor_target_ids', {})
    if not targets:
        return proposal
    return replace(proposal, operations=tuple(
        replace(operation, target_id=targets[operation.id])
        if isinstance(operation, ClaimOperation) and operation.id in targets else operation
        for operation in proposal.operations
    ))


def has_effect(proposal: SemanticProposal) -> bool:
    return any(isinstance(operation, GroundingResolutionOperation)
               and operation.outcome != 'pending' for operation in proposal.operations)


def commit(
    db: sqlite3.Connection, proposal: SemanticProposal, inbound: dict[str, Any],
    contract: DomainContract, timestamp: str,
) -> tuple[ValidationResult, dict[str, Any] | None]:
    result = check(db, proposal, inbound, contract)
    if result.outcome != 'accepted' or proposal.intent != 'semantic_commit':
        return result, None
    records: dict[tuple[str, str], dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    sources: dict[str, SemanticProposal] = {}
    successor_bindings: dict[str, tuple[str, str]] = {}
    commit_id = str(uuid4())
    revision = proposal.semantic_revision
    assert revision is not None
    metadata = {'semantic_commit_id': commit_id, 'semantic_revision': revision + 1,
                'contract_version': contract.version, 'committed_at': timestamp}
    for operation in (op for op in proposal.operations if isinstance(op, GroundingResolutionOperation)
                      and op.outcome != 'pending'):
        pending = pending_candidate(db, operation.item_id)
        assert pending is not None
        row, source, candidate = pending
        source_targets = targets(db)
        for source_operation in source.operations:
            if not isinstance(source_operation, ClaimOperation):
                continue
            binding_target = bound_target(db, row['grounding_id'], source_operation)
            if binding_target is not None and binding_target[0][0] != row['grounding_id']:
                source_targets[source_operation.target_id] = binding_target[1]
        result = validate(source, row['communication_id'], contract, source_targets, concept_names(db))
        if result.outcome != 'accepted':
            return result, None
        sources[row['grounding_id']] = source
        if operation.outcome == 'corrected' and isinstance(candidate, ClaimOperation):
            binding = claim_target_binding(db, row['grounding_id'], candidate)
            if binding is not None:
                for successor_id in operation.successor_ids:
                    successor_bindings[successor_id] = binding
        parent = next((resolution for resolution in source.operations
                       if isinstance(resolution, GroundingResolutionOperation)
                       and row['candidate_id'] in resolution.successor_ids), None)
        provenance = {'source_communication_id': row['communication_id'],
                      'exposure_communication_id': row['grounding_id'],
                      'evidence_communication_id': inbound['id'], 'actor_id': inbound['actor_id'],
                      'grounding_id': row['grounding_id'], 'grounding_item_id': operation.item_id}
        if parent:
            provenance['corrected_grounding_item_id'] = parent.item_id
        record = {**asdict(candidate), **metadata, 'id': str(uuid4()),
                  'provenance': provenance, 'provenance_class': 'grounded'}
        is_resolution = isinstance(candidate, (EntityOperation, ContextOperation)) and candidate.action == 'resolve'
        if operation.outcome == 'accepted' and not is_resolution:
            records[row['grounding_id'], row['candidate_id']] = record
        item = {'id': operation.item_id, **metadata, 'outcome': operation.outcome,
                'policy': operation.policy, 'acceptance_mode': operation.acceptance_mode,
                'rationale': operation.rationale, 'provenance': provenance,
                'target_id': candidate.id if is_resolution or operation.outcome != 'accepted' else record['id'],
                'candidate_id': row['candidate_id'], 'exposed_at': row['exposed_at']}
        if operation.outcome == 'corrected':
            item['successor_candidate_ids'] = list(operation.successor_ids)
        items.append(item)

    mapping = {(row['grounding_id'], row['candidate_id']): row['id'] for row in db.execute(
        "SELECT id, grounding_id, candidate_id FROM trusted_records")}
    mapping.update({key: record['id'] for key, record in records.items()})
    existing = targets(db)

    def resolve(grounding_id: str, candidate_id: str) -> str | None:
        return mapping.get((grounding_id, candidate_id),
                           candidate_id if candidate_id in existing else None)

    def resolve_claim_target(grounding_id: str, candidate_id: str, target_id: str) -> str | None:
        binding = db.execute(
            "SELECT target_grounding_id, target_candidate_id FROM candidate_target_bindings "
            "WHERE grounding_id=? AND candidate_id=?", (grounding_id, candidate_id),
        ).fetchone()
        if binding:
            return resolve(binding['target_grounding_id'], binding['target_candidate_id'])
        return resolve(grounding_id, target_id)

    for (grounding_id, candidate_id), record in records.items():
        if record['kind'] == 'claim':
            resolved_target = resolve_claim_target(grounding_id, candidate_id, record['target_id'])
            if resolved_target is None:
                return ValidationResult('rejected', ('ungrounded_dependency',)), None
            record['target_id'] = resolved_target
        elif record['kind'] == 'context':
            entity_ids = [resolve(grounding_id, entity_id) for entity_id in record['entity_ids']]
            if None in entity_ids:
                return ValidationResult('rejected', ('ungrounded_dependency',)), None
            record['entity_ids'] = entity_ids
    relationships = []
    for grounding_id, source in sources.items():
        for op in source.operations:
            if op.kind != 'relationship':
                continue
            assert isinstance(op, RelationshipOperation)
            if (grounding_id, op.source_id) not in records:
                continue
            source_id = resolve(grounding_id, op.source_id)
            target_id = resolve(grounding_id, op.target_id)
            if target_id is None:
                return ValidationResult('rejected', ('ungrounded_dependency',)), None
            if op.relationship_type in ('corrects', 'supersedes', 'contradicts'):
                origin = records[grounding_id, op.source_id]
                previous = db.execute("SELECT record FROM trusted_records WHERE id=?", (target_id,)).fetchone()
                predecessor = (json.loads(previous['record']) if previous else
                               next((r for r in records.values() if r['id'] == target_id), None))
                if (origin['kind'] != 'claim' or predecessor is None or predecessor['kind'] != 'claim'
                        or origin['target_id'] != predecessor['target_id']
                        or origin['concept'] != predecessor['concept'] or source_id == target_id
                        or op.relationship_type in ('corrects', 'supersedes') and previous is None):
                    return ValidationResult('rejected', ('invalid_claim_history_relationship',)), None
            relationships.append({**asdict(op), **metadata, 'id': str(uuid4()),
                                  'source_id': source_id, 'target_id': target_id,
                                  'provenance': records[grounding_id, op.source_id]['provenance']})
    # ponytail: scan lineage history; index semantic keys when ledger size matters.
    previous_claims = [json.loads(row['record']) for row in db.execute(
        "SELECT record FROM trusted_records WHERE kind='claim'")]
    previous_relations = [json.loads(row['record']) for row in db.execute(
        "SELECT record FROM trusted_relationships")]
    noncurrent = {r['target_id'] for r in previous_relations if r['relationship_type'] in ('corrects', 'supersedes')}
    for record in records.values():
        if record['kind'] != 'claim':
            continue
        for head in previous_claims + list(records.values()):
            if (head['kind'] == 'claim' and head['id'] != record['id'] and head['id'] not in noncurrent
                    and head['target_id'] == record['target_id'] and head['concept'] == record['concept']
                    and head['value'] != record['value'] and not any(
                        relation['relationship_type'] in ('corrects', 'supersedes', 'contradicts')
                        and {relation['source_id'], relation['target_id']} == {record['id'], head['id']}
                        for relation in relationships)):
                return ValidationResult('rejected', ('claim_history_relationship_required',)), None
    for (grounding_id, candidate_id), record in records.items():
        db.execute("INSERT INTO trusted_records VALUES (?, ?, ?, ?, ?)",
                   (record['id'], record['kind'], json.dumps(record), grounding_id, candidate_id))
    for record in relationships:
        db.execute("INSERT INTO trusted_relationships VALUES (?, ?)",
                   (record['id'], json.dumps(record)))
    for item in items:
        db.execute("INSERT INTO trusted_grounding_items VALUES (?, ?)",
                   (item['id'], json.dumps(item)))
    for grounding_id, source in sources.items():
        db.execute("INSERT OR IGNORE INTO trusted_groundings VALUES (?, ?)",
                   (grounding_id, json.dumps({'id': grounding_id, **metadata,
                                             'source_communication_id': source.communication_id})))
    summary = {'id': commit_id, **metadata, 'communication_id': inbound['id'],
               'record_ids': [record['id'] for record in records.values()],
               'grounding_item_ids': [item['id'] for item in items],
               'successor_target_ids': {
                   operation.id: normalized_target
                   for operation in proposal.operations if isinstance(operation, ClaimOperation)
                   and operation.id in successor_bindings
                   if (normalized_target := resolve(*successor_bindings[operation.id])) is not None
               }}
    db.execute("INSERT INTO semantic_commits VALUES (?, ?, ?)",
               (commit_id, inbound['id'], json.dumps(summary)))
    db.execute("UPDATE semantic_state SET revision=?", (revision + 1,))
    event = {'id': str(uuid4()), **metadata, 'event_type': 'semantic_committed',
             'timestamp': timestamp, 'entity_ids': [r['id'] for r in records.values() if r['kind'] == 'entity'],
             'context_ids': [r['id'] for r in records.values() if r['kind'] == 'context'],
             'artifact_ids': [], 'assertion_ids': [r['id'] for r in records.values() if r['kind'] == 'claim'],
             'grounding_item_ids': summary['grounding_item_ids']}
    db.execute("INSERT INTO semantic_outbox VALUES (?, ?)", (event['id'], json.dumps(event)))
    return result, summary


def inspect_history(db: sqlite3.Connection) -> dict[str, Any]:
    state = db.execute("SELECT revision FROM semantic_state").fetchone()
    records = [json.loads(row['record']) for row in db.execute("SELECT record FROM trusted_records ORDER BY rowid")]
    result = {'revision': state['revision'],
              'entities': [r for r in records if r['kind'] == 'entity'],
              'contexts': [r for r in records if r['kind'] == 'context'],
              'claims': [r for r in records if r['kind'] == 'claim']}
    for name, table in (('commits', 'semantic_commits'), ('relationships', 'trusted_relationships'),
                        ('groundings', 'trusted_groundings'), ('grounding_items', 'trusted_grounding_items'),
                        ('events', 'semantic_outbox')):
        result[name] = [json.loads(row['record']) for row in db.execute(f"SELECT record FROM {table} ORDER BY rowid")]
    return result
