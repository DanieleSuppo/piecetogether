"""Core-owned exposure evidence and append-only trusted semantic commits."""

import json
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from . import view
from .contracts import DomainContract
from .proposals import (
    ArtifactOperation,
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
        CREATE TABLE IF NOT EXISTS reference_assertions (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reference_commits (
            id TEXT PRIMARY KEY, record TEXT NOT NULL
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
        CREATE TABLE IF NOT EXISTS artifact_ingress (
            communication_id TEXT NOT NULL, attachment_id TEXT NOT NULL,
            actor_id TEXT NOT NULL, media_type TEXT NOT NULL, checksum TEXT NOT NULL,
            size INTEGER NOT NULL, received_at TEXT NOT NULL,
            PRIMARY KEY(communication_id, attachment_id)
        );
        CREATE TABLE IF NOT EXISTS artifact_stages (
            communication_id TEXT NOT NULL, attachment_id TEXT NOT NULL,
            actor_id TEXT NOT NULL, stage_ref TEXT NOT NULL, checksum TEXT NOT NULL,
            size INTEGER NOT NULL, state TEXT NOT NULL,
            PRIMARY KEY(communication_id, attachment_id)
        );
        CREATE TABLE IF NOT EXISTS trusted_artifacts (
            id TEXT PRIMARY KEY, communication_id TEXT NOT NULL, attachment_id TEXT NOT NULL,
            record TEXT NOT NULL, UNIQUE(communication_id, attachment_id)
        );
        CREATE TABLE IF NOT EXISTS artifact_publications (
            artifact_id TEXT PRIMARY KEY REFERENCES trusted_artifacts(id),
            stage_ref TEXT NOT NULL, checksum TEXT NOT NULL, size INTEGER NOT NULL,
            state TEXT NOT NULL, storage_ref TEXT
        );
        CREATE TABLE IF NOT EXISTS artifact_lifecycle (
            id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE, record TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifact_deletions (
            artifact_id TEXT PRIMARY KEY, reference TEXT NOT NULL, failure TEXT
        );
        CREATE TABLE IF NOT EXISTS artifact_publication_failures (
            artifact_id TEXT PRIMARY KEY REFERENCES artifact_publications(artifact_id),
            record TEXT NOT NULL
        );
    """)
    db.execute("INSERT INTO semantic_state VALUES (1, 0, ?) "
               "ON CONFLICT(singleton) DO UPDATE SET contract_version=excluded.contract_version",
               (contract.version,))


def targets(db: sqlite3.Connection) -> dict[str, str]:
    result = {}
    for row in db.execute("SELECT id, kind, record FROM trusted_records WHERE kind != 'context_transition'"):
        result[row['id']] = (json.loads(row['record'])['entity_type']
                             if row['kind'] == 'entity' else '$' + row['kind'])
    return result


def concept_names(db: sqlite3.Connection) -> set[str]:
    return {row['name'] for row in db.execute("SELECT DISTINCT name FROM emergent_concepts")}


def artifact_binding(
    db: sqlite3.Connection, artifact_id: str, communication_id: str, actor_id: str,
) -> dict[str, Any] | None:
    """Resolve a Claim Artifact reference to an Actor-owned Artifact or source ingress."""
    artifact = db.execute("SELECT id FROM trusted_artifacts WHERE id=? "
                          "AND NOT EXISTS (SELECT 1 FROM artifact_lifecycle WHERE artifact_id=trusted_artifacts.id) "
                          "AND json_extract(record, '$.provenance.actor_id')=?",
                          (artifact_id, actor_id)).fetchone()
    if artifact is not None:
        return {'artifact_id': artifact['id']}
    ingress = db.execute("SELECT attachment_id, checksum, size FROM artifact_ingress "
                         "WHERE communication_id=? AND attachment_id=? AND actor_id=?",
                         (communication_id, artifact_id, actor_id)).fetchone()
    if ingress is None:
        return None
    return {'artifact_ingress': {'communication_id': communication_id,
                                 'attachment_id': ingress['attachment_id'],
                                 'checksum': ingress['checksum'], 'size': ingress['size']}}


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
            elif candidate.action in ('suspend', 'resume'):
                clauses.append(f'{candidate.action} context {candidate.id}.')
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
    deleting = {op.id for op in proposal.operations if isinstance(op, ArtifactOperation) and op.action == 'delete'}
    for proposal_operation in proposal.operations:
        if (isinstance(proposal_operation, ArtifactOperation) and proposal.intent == 'semantic_commit'
                and proposal_operation.action != 'delete'):
            ingress = db.execute("SELECT 1 FROM artifact_ingress WHERE communication_id=? AND attachment_id=? "
                                 "AND actor_id=?", (inbound['id'], proposal_operation.id, inbound['actor_id'])).fetchone()
            if ingress is None:
                return ValidationResult('rejected', ('invalid_artifact_provenance',))
        if (isinstance(proposal_operation, ArtifactOperation)
                and proposal_operation.action in ('supersede', 'delete')):
            target_id = (proposal_operation.predecessor_id if proposal_operation.action == 'supersede'
                         else proposal_operation.id)
            predecessor = db.execute("SELECT record FROM trusted_artifacts WHERE id=? "
                                     "AND NOT EXISTS (SELECT 1 FROM artifact_lifecycle WHERE artifact_id=?)",
                                     (target_id, target_id)).fetchone()
            previous = json.loads(predecessor['record']) if predecessor else {}
            if (previous.get('artifact_type') != proposal_operation.artifact_type
                    or previous.get('roles') != list(proposal_operation.roles)
                    or previous.get('provenance', {}).get('actor_id') != inbound['actor_id']):
                return ValidationResult('rejected', ('artifact_predecessor_unavailable'
                    if proposal_operation.action == 'supersede' else 'artifact_lifecycle_unavailable',))
        if isinstance(proposal_operation, ArtifactOperation) and proposal_operation.action in ('persist', 'supersede'):
            staged = db.execute("SELECT * FROM artifact_stages WHERE communication_id=? AND attachment_id=? "
                                "AND actor_id=? AND state='staged'",
                                (inbound['id'], proposal_operation.id, inbound['actor_id'])).fetchone()
            if staged is None:
                return ValidationResult('rejected', ('artifact_staging_unavailable',))
        if isinstance(proposal_operation, ClaimOperation) and proposal_operation.artifact_id is not None:
            if (proposal_operation.artifact_id in deleting
                    or artifact_binding(db, proposal_operation.artifact_id, proposal.communication_id,
                                        inbound['actor_id']) is None):
                return ValidationResult('rejected', ('invalid_artifact_provenance',))
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
    contexts = view.contexts(db)
    for context_operation in proposal.operations:
        if isinstance(context_operation, ContextOperation) and context_operation.action in ('suspend', 'resume'):
            expected = 'active' if context_operation.action == 'suspend' else 'suspended'
            current_context = contexts.get(context_operation.id)
            if current_context is None or current_context['status'] != expected:
                return ValidationResult('rejected', ('invalid_context_transition',))
    if proposal.intent == 'candidate':
        return result
    # ponytail: deployment-wide revision, scope revisions in #22 if contention matters.
    if proposal.semantic_revision != state['revision']:
        return ValidationResult('stale', ('semantic_revision_changed',))
    resolutions = tuple(op for op in proposal.operations if isinstance(op, GroundingResolutionOperation))
    artifacts = tuple(op for op in proposal.operations if isinstance(op, ArtifactOperation))
    if not resolutions and not artifacts:
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
                               ClaimOperation, GroundingPlanOperation, ArtifactOperation))
           for op in proposal.operations) or (candidate_ids != successor_ids or successor_ids != successor_plans) and candidate_ids:
        return ValidationResult('rejected', ('ungrounded_trusted_mutation',))
    resolution_ids = [op.item_id for op in resolutions]
    if len(resolution_ids) != len(set(resolution_ids)):
        return ValidationResult('rejected', ('duplicate_grounding_resolution',))
    for operation in resolutions:
        pending = pending_candidate(db, operation.item_id)
        if pending is None:
            return ValidationResult('rejected', ('grounding_item_unavailable',))
        row, source, candidate = pending
        if (operation.outcome == 'accepted' and isinstance(candidate, ContextOperation)
                and candidate.action in ('suspend', 'resume')):
            current = contexts.get(candidate.id)
            expected = 'active' if candidate.action == 'suspend' else 'suspended'
            if current is None or current['status'] != expected:
                return ValidationResult('stale', ('context_lifecycle_changed',))
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


def delete_artifact(
    db: sqlite3.Connection, operation: ArtifactOperation, inbound: dict[str, Any],
    contract: DomainContract, metadata: dict[str, Any],
) -> None:
    """Explicit retention is the only exception to immutable Artifact history."""
    row = db.execute('SELECT * FROM trusted_artifacts WHERE id=?', (operation.id,)).fetchone()
    original = json.loads(row['record'])
    retention = contract.artifact_types[operation.artifact_type]['retention']
    keep_metadata = retention['metadata'] == 'retain'
    keep_provenance = retention['provenance'] == 'retain'
    transition = {'id': str(uuid4()), 'kind': 'artifact_transition', 'artifact_id': operation.id,
                  'action': 'delete', 'reason': operation.reason, 'retention': retention, **metadata}
    if keep_provenance:
        transition['provenance'] = {'source_communication_id': inbound['id'], 'actor_id': inbound['actor_id']}
    if keep_metadata or keep_provenance:
        db.execute('INSERT INTO artifact_lifecycle VALUES (?, ?, ?)',
                   (transition['id'], operation.id, json.dumps(transition)))
    publication = db.execute('SELECT * FROM artifact_publications WHERE artifact_id=?', (operation.id,)).fetchone()
    if publication and retention['bytes'] == 'delete':
        db.execute('INSERT INTO artifact_deletions VALUES (?, ?, NULL)',
                   (operation.id, publication['stage_ref']))
    db.execute("UPDATE artifact_publications SET state='deleted' WHERE artifact_id=?", (operation.id,))
    db.execute('DELETE FROM artifact_publication_failures WHERE artifact_id=?', (operation.id,))
    if not keep_provenance:
        original.pop('provenance', None)
        db.execute('DELETE FROM artifact_ingress WHERE communication_id=? AND attachment_id=?',
                   (row['communication_id'], row['attachment_id']))
        db.execute('DELETE FROM artifact_stages WHERE communication_id=? AND attachment_id=?',
                   (row['communication_id'], row['attachment_id']))
        # Keep a unique tombstone key without preserving the source Communication/attachment pair.
        db.execute("UPDATE trusted_artifacts SET communication_id='', attachment_id=id WHERE id=?", (operation.id,))
    if not keep_metadata:
        original = {key: value for key, value in original.items() if key in ('id', 'kind', 'provenance')}
    if keep_metadata or keep_provenance:
        db.execute('UPDATE trusted_artifacts SET record=? WHERE id=?', (json.dumps(original), operation.id))
    else:
        db.execute('DELETE FROM artifact_publications WHERE artifact_id=?', (operation.id,))
        db.execute('DELETE FROM trusted_artifacts WHERE id=?', (operation.id,))
    for relation in db.execute('SELECT id, record FROM trusted_relationships').fetchall():
        record = json.loads(relation['record'])
        if operation.id not in (record['source_id'], record['target_id']):
            continue
        if not keep_metadata and not keep_provenance:
            db.execute('DELETE FROM trusted_relationships WHERE id=?', (relation['id'],))
        elif not keep_provenance and record['source_id'] == operation.id:
            record.pop('provenance', None)
            db.execute('UPDATE trusted_relationships SET record=? WHERE id=?', (json.dumps(record), relation['id']))


def has_effect(proposal: SemanticProposal) -> bool:
    return any(
        isinstance(operation, ArtifactOperation)
        or isinstance(operation, GroundingResolutionOperation) and operation.outcome != 'pending'
        for operation in proposal.operations
    )


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
    artifact_records: list[dict[str, Any]] = []
    publications: list[dict[str, Any]] = []
    artifact_relationships: list[dict[str, Any]] = []
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
        if isinstance(candidate, ClaimOperation) and candidate.artifact_id is not None:
            artifact_provenance = artifact_binding(
                db, candidate.artifact_id, row['communication_id'], row['actor_id'])
            if artifact_provenance is None:
                return ValidationResult('rejected', ('invalid_artifact_provenance',)), None
            provenance.update(artifact_provenance)
        if parent:
            provenance['corrected_grounding_item_id'] = parent.item_id
        record = {**asdict(candidate), **metadata, 'id': str(uuid4()),
                  'provenance': provenance, 'provenance_class': 'grounded'}
        is_resolution = isinstance(candidate, (EntityOperation, ContextOperation)) and candidate.action == 'resolve'
        is_transition = isinstance(candidate, ContextOperation) and candidate.action in ('suspend', 'resume')
        if is_transition:
            record.update(kind='context_transition', context_id=candidate.id)
        if operation.outcome == 'accepted' and not is_resolution:
            records[row['grounding_id'], row['candidate_id']] = record
        item = {'id': operation.item_id, **metadata, 'outcome': operation.outcome,
                'policy': operation.policy, 'acceptance_mode': operation.acceptance_mode,
                'rationale': operation.rationale, 'provenance': provenance,
                'target_id': candidate.id if is_resolution or is_transition or operation.outcome != 'accepted' else record['id'],
                'candidate_id': row['candidate_id'], 'exposed_at': row['exposed_at']}
        if operation.outcome == 'corrected':
            item['successor_candidate_ids'] = list(operation.successor_ids)
        items.append(item)

    deleted_artifact_ids = [op.id for op in proposal.operations
                            if isinstance(op, ArtifactOperation) and op.action == 'delete']
    if any(record['provenance'].get('artifact_id') in deleted_artifact_ids for record in records.values()):
        return ValidationResult('rejected', ('invalid_artifact_provenance',)), None
    for artifact_operation in (op for op in proposal.operations if isinstance(op, ArtifactOperation)):
        if artifact_operation.action == 'delete':
            continue
        ingress = db.execute("SELECT * FROM artifact_ingress WHERE communication_id=? AND attachment_id=? "
                             "AND actor_id=?", (inbound['id'], artifact_operation.id, inbound['actor_id'])).fetchone()
        if ingress is None:
            return ValidationResult('rejected', ('invalid_artifact_provenance',)), None
        artifact_id = str(uuid4())
        provenance = {'source_communication_id': inbound['id'], 'actor_id': inbound['actor_id'],
                      'attachment_checksum': ingress['checksum'], 'attachment_size': ingress['size']}
        record = {'id': artifact_id, 'kind': 'artifact', 'attachment_id': artifact_operation.id,
                  'artifact_type': artifact_operation.artifact_type, 'roles': list(artifact_operation.roles),
                  'action': artifact_operation.action, 'retention': artifact_operation.retention or contract.artifact_types[artifact_operation.artifact_type]['retention'],
                  'media_type': ingress['media_type'], **metadata, 'provenance': provenance}
        artifact_records.append(record)
        if artifact_operation.action == 'supersede':
            artifact_relationships.append({'id': str(uuid4()), 'kind': 'relationship',
                                          'relationship_type': 'supersedes', 'source_id': artifact_id,
                                          'target_id': artifact_operation.predecessor_id,
                                          **metadata, 'provenance': provenance})
        if artifact_operation.action in ('persist', 'supersede'):
            staged = db.execute("SELECT * FROM artifact_stages WHERE communication_id=? AND attachment_id=? "
                                "AND actor_id=? AND state='staged'", (inbound['id'], artifact_operation.id,
                                                                        inbound['actor_id'])).fetchone()
            if staged is None:
                return ValidationResult('rejected', ('artifact_staging_unavailable',)), None
            publications.append({'artifact_id': artifact_id, 'attachment_id': artifact_operation.id,
                                 'stage_ref': staged['stage_ref'], 'checksum': staged['checksum'], 'size': staged['size']})

    mapping = {(row['grounding_id'], row['candidate_id']): row['id'] for row in db.execute(
        "SELECT id, grounding_id, candidate_id FROM trusted_records WHERE kind != 'context_transition'")}
    mapping.update({key: record['id'] for key, record in records.items() if record['kind'] != 'context_transition'})
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
    relationships = artifact_relationships
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
    # Reserve stages only after every deterministic rejection check has passed.
    for publication in publications:
        linked = db.execute("UPDATE artifact_stages SET state='linked' WHERE communication_id=? "
                            "AND attachment_id=? AND state='staged' AND stage_ref=?",
                            (inbound['id'], publication['attachment_id'], publication['stage_ref']))
        if not linked.rowcount:
            raise sqlite3.IntegrityError('artifact staging changed inside semantic transaction')
    for deletion_operation in proposal.operations:
        if isinstance(deletion_operation, ArtifactOperation) and deletion_operation.action == 'delete':
            delete_artifact(db, deletion_operation, inbound, contract, metadata)
    for (grounding_id, candidate_id), record in records.items():
        db.execute("INSERT INTO trusted_records VALUES (?, ?, ?, ?, ?)",
                   (record['id'], record['kind'], json.dumps(record), grounding_id, candidate_id))
    for record in relationships:
        db.execute("INSERT INTO trusted_relationships VALUES (?, ?)",
                   (record['id'], json.dumps(record)))
    for record in artifact_records:
        db.execute("INSERT INTO trusted_artifacts VALUES (?, ?, ?, ?)",
                   (record['id'], inbound['id'], record['attachment_id'], json.dumps(record)))
    for publication in publications:
        db.execute("INSERT INTO artifact_publications VALUES (?, ?, ?, ?, 'pending', NULL)",
                   (publication['artifact_id'], publication['stage_ref'], publication['checksum'], publication['size']))
    for item in items:
        db.execute("INSERT INTO trusted_grounding_items VALUES (?, ?)",
                   (item['id'], json.dumps(item)))
    for grounding_id, source in sources.items():
        db.execute("INSERT OR IGNORE INTO trusted_groundings VALUES (?, ?)",
                   (grounding_id, json.dumps({'id': grounding_id, **metadata,
                                             'source_communication_id': source.communication_id})))
    summary = {'id': commit_id, **metadata, 'communication_id': inbound['id'],
               'record_ids': [record['id'] for record in records.values()],
               'artifact_ids': [record['id'] for record in artifact_records] + deleted_artifact_ids,
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
             'context_ids': list(dict.fromkeys(r['context_id'] if r['kind'] == 'context_transition' else r['id']
                                              for r in records.values() if r['kind'] in ('context', 'context_transition'))),
             'artifact_ids': summary['artifact_ids'], 'assertion_ids': [r['id'] for r in records.values() if r['kind'] == 'claim'],
             'grounding_item_ids': summary['grounding_item_ids']}
    # Currentness changes also affect existing targets and predecessor assertions.
    affected = [record.get('target_id') for record in records.values()]
    affected += [item['target_id'] for item in items if item['outcome'] == 'accepted']
    affected += [endpoint for relation in relationships for endpoint in (relation['source_id'], relation['target_id'])]
    affected += [entity_id for record in records.values() if record['kind'] == 'context'
                 for entity_id in record['entity_ids']]
    known = targets(db)
    artifact_ids = {row['id'] for row in db.execute('SELECT id FROM trusted_artifacts')}
    for affected_id in affected:
        if affected_id in artifact_ids:
            field = 'artifact_ids'
        elif affected_id in known:
            field = {'$context': 'context_ids', '$claim': 'assertion_ids'}.get(known[affected_id], 'entity_ids')
        else:
            continue
        if affected_id not in event[field]:
            event[field].append(affected_id)
    db.execute("INSERT INTO semantic_outbox VALUES (?, ?)", (event['id'], json.dumps(event)))
    return result, summary


def inspect_history(db: sqlite3.Connection) -> dict[str, Any]:
    state = db.execute("SELECT revision FROM semantic_state").fetchone()
    records = [json.loads(row['record']) for row in db.execute("SELECT record FROM trusted_records ORDER BY rowid")]
    artifacts = view.artifacts(db)
    result = {'revision': state['revision'],
              'artifact_deletions': [{'artifact_id': row['artifact_id'], 'state': 'pending',
                                      'failure': json.loads(row['failure']) if row['failure'] else None}
                                     for row in db.execute('SELECT artifact_id, failure FROM artifact_deletions ORDER BY rowid')],
              'entities': [r for r in records if r['kind'] == 'entity'],
              'artifacts': artifacts,
              'contexts': [r for r in records if r['kind'] == 'context'],
              'context_transitions': [r for r in records if r['kind'] == 'context_transition'],
              'claims': [r for r in records if r['kind'] == 'claim']}
    for name, table in (('reference_assertions', 'reference_assertions'), ('reference_commits', 'reference_commits'),
                        ('artifact_lifecycle', 'artifact_lifecycle'),
                        ('commits', 'semantic_commits'), ('relationships', 'trusted_relationships'),
                        ('groundings', 'trusted_groundings'), ('grounding_items', 'trusted_grounding_items'),
                        ('events', 'semantic_outbox')):
        result[name] = [json.loads(row['record']) for row in db.execute(f"SELECT record FROM {table} ORDER BY rowid")]
    return result
