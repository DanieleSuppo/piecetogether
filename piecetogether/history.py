"""Core-owned exposure evidence and append-only trusted semantic commits."""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import uuid4

from .contracts import DomainContract
from .proposals import (
    ClaimOperation, ContextOperation, EmergentConceptOperation, EntityOperation,
    GroundingPlanOperation, GroundingResolutionOperation, RelationshipOperation,
    SemanticProposal, ValidationResult, validate,
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
        "CASE WHEN t.id IS NULL THEN 'pending' ELSE 'accepted' END AS outcome "
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
    if proposal.intent != 'candidate':
        return
    plans = [op for op in proposal.operations if isinstance(op, GroundingPlanOperation)]
    if not plans:
        return
    db.execute("INSERT OR IGNORE INTO exposed_groundings VALUES (?, ?, ?, ?, ?, ?)",
               (outbound_id, proposal.communication_id, actor_id,
                proposal.contract_version, exposed_at, json.dumps(asdict(proposal))))
    for plan in plans:
        for candidate_id in plan.claim_ids + plan.resolution_ids:
            db.execute("INSERT OR IGNORE INTO pending_items VALUES (?, ?, ?, ?)",
                       (str(uuid4()), outbound_id, candidate_id, plan.policy))


def committed(db: sqlite3.Connection, communication_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT record FROM semantic_commits WHERE communication_id=?",
                     (communication_id,)).fetchone()
    return json.loads(row['record']) if row else None


def check(
    db: sqlite3.Connection, proposal: SemanticProposal, inbound: dict[str, Any],
    contract: DomainContract,
) -> ValidationResult:
    result = validate(proposal, inbound['id'], contract, targets(db), concept_names(db))
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
        if len(grounding_response(proposal)) > 65536:
            return ValidationResult('rejected', ('grounding_response_too_large',))
        return result
    # ponytail: deployment-wide revision, scope revisions in #22 if contention matters.
    if proposal.semantic_revision != state['revision']:
        return ValidationResult('stale', ('semantic_revision_changed',))
    if not proposal.operations or any(not isinstance(op, GroundingResolutionOperation)
                                      for op in proposal.operations):
        return ValidationResult('rejected', ('ungrounded_trusted_mutation',))
    resolution_ids = [op.item_id for op in proposal.operations if isinstance(op, GroundingResolutionOperation)]
    if len(resolution_ids) != len(set(resolution_ids)):
        return ValidationResult('rejected', ('duplicate_grounding_resolution',))
    for operation in proposal.operations:
        assert isinstance(operation, GroundingResolutionOperation)
        row = db.execute(
            "SELECT i.*, g.actor_id, g.contract_version, g.exposed_at "
            "FROM pending_items i JOIN exposed_groundings g ON g.id=i.grounding_id "
            "WHERE i.id=?", (operation.item_id,)).fetchone()
        if row is None:
            return ValidationResult('rejected', ('grounding_item_unavailable',))
        if db.execute("SELECT 1 FROM trusted_grounding_items WHERE id=?",
                      (operation.item_id,)).fetchone():
            return ValidationResult('stale', ('grounding_item_already_resolved',))
        if row['contract_version'] != contract.version:
            return ValidationResult('stale', ('grounding_contract_changed',))
        if (row['actor_id'] != inbound['actor_id'] or row['policy'] != operation.policy
                or inbound['reply_to'] != row['grounding_id']
                or datetime.fromisoformat(inbound['received_at']) <= datetime.fromisoformat(row['exposed_at'])):
            return ValidationResult('rejected', ('invalid_grounding_evidence',))
        if operation.acceptance_mode != 'explicit':
            # #17 supplies policy-governed conversational evidence; a rationale is not proof.
            return ValidationResult('rejected', ('implicit_grounding_unavailable',))
        forms = contract.grounding_policies[operation.policy].get('confirmation_forms', ['yes'])
        if inbound['text'].strip().casefold() not in {form.strip().casefold() for form in forms}:
            return ValidationResult('rejected', ('explicit_confirmation_required',))
    return result


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
    commit_id = str(uuid4())
    revision = proposal.semantic_revision
    assert revision is not None
    metadata = {'semantic_commit_id': commit_id, 'semantic_revision': revision + 1,
                'contract_version': contract.version, 'committed_at': timestamp}
    for operation in proposal.operations:
        assert isinstance(operation, GroundingResolutionOperation)
        row = db.execute(
            "SELECT i.*, g.communication_id, g.proposal, g.exposed_at "
            "FROM pending_items i JOIN exposed_groundings g ON g.id=i.grounding_id WHERE i.id=?",
            (operation.item_id,)).fetchone()
        source = SemanticProposal.from_dict(json.loads(row['proposal']))
        result = validate(source, row['communication_id'], contract, targets(db), concept_names(db))
        if result.outcome != 'accepted':
            return result, None
        sources[row['grounding_id']] = source
        candidate = next(op for op in source.operations
                         if isinstance(op, (EntityOperation, ContextOperation, ClaimOperation))
                         and op.id == row['candidate_id'])
        provenance = {'source_communication_id': row['communication_id'],
                      'exposure_communication_id': row['grounding_id'],
                      'evidence_communication_id': inbound['id'], 'actor_id': inbound['actor_id'],
                      'grounding_id': row['grounding_id'], 'grounding_item_id': operation.item_id}
        record = {**asdict(candidate), **metadata, 'id': str(uuid4()),
                  'provenance': provenance, 'provenance_class': 'grounded'}
        is_resolution = isinstance(candidate, (EntityOperation, ContextOperation)) and candidate.action == 'resolve'
        if not is_resolution:
            records[row['grounding_id'], row['candidate_id']] = record
        items.append({'id': operation.item_id, **metadata, 'outcome': 'accepted',
                      'policy': operation.policy, 'acceptance_mode': operation.acceptance_mode,
                      'rationale': operation.rationale, 'provenance': provenance,
                      'target_id': candidate.id if is_resolution else record['id'],
                      'exposed_at': row['exposed_at']})

    mapping = {(row['grounding_id'], row['candidate_id']): row['id'] for row in db.execute(
        "SELECT id, grounding_id, candidate_id FROM trusted_records")}
    mapping.update({key: record['id'] for key, record in records.items()})
    existing = targets(db)

    def resolve(grounding_id: str, candidate_id: str) -> str | None:
        return mapping.get((grounding_id, candidate_id),
                           candidate_id if candidate_id in existing else None)

    for (grounding_id, candidate_id), record in records.items():
        if record['kind'] == 'claim':
            target = resolve(grounding_id, record['target_id'])
            if target is None:
                return ValidationResult('rejected', ('ungrounded_dependency',)), None
            record['target_id'] = target
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
    for grounding_id in sources:
        db.execute("INSERT OR IGNORE INTO trusted_groundings VALUES (?, ?)",
                   (grounding_id, json.dumps({'id': grounding_id, **metadata,
                                             'source_communication_id': sources[grounding_id].communication_id})))
    summary = {'id': commit_id, **metadata, 'communication_id': inbound['id'],
               'record_ids': [record['id'] for record in records.values()],
               'grounding_item_ids': [item['id'] for item in items]}
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
