"""Evidence-preserving projection of committed records, never Working State."""

import json
import sqlite3
from itertools import combinations
from typing import Any


REPLACEMENT_RELATIONS = ('corrects', 'supersedes')


def contexts(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Stable Context identities with append-only, grounded lifecycle evidence."""
    result = {}
    for row in db.execute("SELECT record FROM trusted_records WHERE kind IN ('context', 'context_transition') ORDER BY rowid"):
        record = json.loads(row['record'])
        if record['kind'] == 'context':
            result[record['id']] = {**record, 'status': 'active', 'lifecycle': []}
        else:
            current = result[record['context_id']]
            current['status'] = 'suspended' if record['action'] == 'suspend' else 'active'
            current['lifecycle'].append(record)
    return result


def artifacts(db: sqlite3.Connection) -> list[dict[str, Any]]:
    result = []
    for row in db.execute("SELECT a.record, p.state, l.record AS lifecycle, f.record AS failure "
                          "FROM trusted_artifacts a LEFT JOIN artifact_publications p ON p.artifact_id=a.id "
                          "LEFT JOIN artifact_lifecycle l ON l.artifact_id=a.id "
                          "LEFT JOIN artifact_publication_failures f ON f.artifact_id=a.id ORDER BY a.rowid"):
        record = {**json.loads(row['record']), 'availability':
                  'deleted' if row['lifecycle'] else row['state'] or 'metadata_only'}
        if row['lifecycle']:
            record['lifecycle'] = [json.loads(row['lifecycle'])]
        if row['failure']:
            record['publication_failure'] = json.loads(row['failure'])
        result.append(record)
    return result


def current_view(db: sqlite3.Connection) -> dict[str, Any]:
    """Derive the view inside the caller's read transaction; no stale cache."""
    revision = db.execute('SELECT revision FROM semantic_state').fetchone()['revision']
    records = [json.loads(row['record']) for row in db.execute(
        'SELECT record FROM trusted_records ORDER BY rowid')]
    records.extend(json.loads(row['record']) for row in db.execute(
        'SELECT record FROM reference_assertions ORDER BY rowid'))
    relationships = [json.loads(row['record']) for row in db.execute(
        'SELECT record FROM trusted_relationships ORDER BY rowid')]
    relationships.extend({
        'id': record['id'] + ':supersedes', 'kind': 'relationship',
        'relationship_type': 'supersedes', 'source_id': record['id'],
        'target_id': record['supersedes_id'],
        **{key: record[key] for key in ('semantic_commit_id', 'semantic_revision',
                                      'contract_version', 'committed_at', 'provenance')},
    } for record in records if record['kind'] == 'reference_assertion' and record.get('supersedes_id'))
    noncurrent = {relation['target_id'] for relation in relationships
                  if relation['relationship_type'] in REPLACEMENT_RELATIONS}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        if record['kind'] in ('claim', 'reference_assertion'):
            groups.setdefault((record['target_id'], record['concept']), []).append(record)
    assertion_sets = []
    # ponytail: derive from the ledger on read; materialize/index when ledger size demands it.
    for (target_id, concept), claims in groups.items():
        ids = {claim['id'] for claim in claims}
        relations = [relation for relation in relationships
                     if relation['source_id'] in ids and relation['target_id'] in ids]
        heads = []
        for claim in claims:
            if claim['id'] in noncurrent:
                continue
            ancestors = {claim['id']}
            frontier = [claim['id']]
            while frontier:
                source_id = frontier.pop()
                for relation in relations:
                    if (relation['source_id'] == source_id
                            and relation['relationship_type'] in REPLACEMENT_RELATIONS
                            and relation['target_id'] not in ancestors):
                        ancestors.add(relation['target_id'])
                        frontier.append(relation['target_id'])
            heads.append({**claim, 'current': True,
                          'lineage_ids': [record['id'] for record in claims if record['id'] in ancestors]})
        conflicts = []
        for first, second in combinations(heads, 2):
            pair = {first['id'], second['id']}
            contradictions = [relation['id'] for relation in relations
                              if relation['relationship_type'] == 'contradicts'
                              and {relation['source_id'], relation['target_id']} == pair]
            if first['value'] != second['value'] or contradictions:
                conflicts.append({'assertion_ids': [first['id'], second['id']],
                                  'relationship_ids': contradictions})
        assertion_sets.append({'target_id': target_id, 'concept': concept, 'heads': heads,
                               'lineage': [{**claim, 'current': False} for claim in claims
                                           if claim['id'] in noncurrent],
                               'relationships': relations, 'conflicts': conflicts})
    projected_artifacts = [{**record, 'available': record['availability'] == 'available',
                            'current': record['id'] not in noncurrent and record['availability'] != 'deleted'}
                           for record in artifacts(db)]
    return {'revision': revision,
            'entities': [record for record in records if record['kind'] == 'entity'],
            'contexts': list(contexts(db).values()),
            'artifacts': projected_artifacts,
            'assertion_sets': assertion_sets}
