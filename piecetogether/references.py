"""Static trusted reference input, kept separate from conversational Grounding."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .contracts import DomainContract, unique_record, valid_name, value_matches


@dataclass(frozen=True)
class ReferenceConfig:
    provider_id: str
    path: Path | None = None

    def __post_init__(self) -> None:
        if not valid_name(self.provider_id) or self.path is not None and not isinstance(self.path, Path):
            raise ValueError('invalid static Reference State Provider configuration')


@dataclass(frozen=True)
class ReferenceAssertion:
    target_id: str
    concept: str
    value: Any
    source_reference: str
    observed_version: str | None = None
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if (any(not valid_name(value) for value in (self.target_id, self.concept, self.source_reference))
                or self.observed_version is not None and not valid_name(self.observed_version)
                or self.observed_version is None and self.observed_at is None):
            raise ValueError('reference assertions require source and observation provenance')
        if self.observed_at is not None:
            if not valid_name(self.observed_at):
                raise ValueError('invalid reference observation time')
            try:
                timestamp = datetime.fromisoformat(self.observed_at.replace('Z', '+00:00'))
            except ValueError:
                raise ValueError('invalid reference observation time') from None
            if timestamp.tzinfo is None:
                raise ValueError('reference observation time requires a timezone')
        if len(json.dumps(self.value, allow_nan=False).encode()) > 65536:
            raise ValueError('reference value exceeds limit')


class ReferenceStateProvider(Protocol):
    provider_id: str

    def get(self) -> tuple[ReferenceAssertion, ...]: ...


class FileReferenceStateProvider:
    """A bootstrap-selected local JSON array, never a runtime plugin or URL."""

    def __init__(self, config: ReferenceConfig):
        if config.path is None:
            raise ValueError('file Reference State Provider requires a path')
        self.provider_id = config.provider_id
        self.path = config.path

    def get(self) -> tuple[ReferenceAssertion, ...]:
        with self.path.open() as source:
            raw = source.read(1048577)
        if len(raw.encode()) > 1048576:
            raise ValueError('reference input exceeds limit')
        data = json.loads(raw, object_pairs_hook=unique_record)
        if not isinstance(data, list) or len(data) > 256 or any(not isinstance(item, dict) for item in data):
            raise ValueError('reference input must be a bounded assertion array')
        try:
            return tuple(ReferenceAssertion(**item) for item in data)
        except TypeError as error:
            raise ValueError('invalid reference assertion fields') from error


def commit(db: sqlite3.Connection, assertions: tuple[ReferenceAssertion, ...],
           provider_id: str, contract: DomainContract, timestamp: str) -> dict[str, Any]:
    state = db.execute('SELECT revision, contract_version FROM semantic_state').fetchone()
    if state['contract_version'] != contract.version:
        raise ValueError('reference Contract version changed')
    revision = state['revision']
    commit_id = str(uuid4())
    metadata = {'semantic_commit_id': commit_id, 'semantic_revision': revision + 1,
                'contract_version': contract.version, 'committed_at': timestamp}
    ids = []
    affected: dict[str, list[str]] = {'entity': [], 'context': []}
    previous = [json.loads(row['record']) for row in db.execute(
        'SELECT record FROM reference_assertions ORDER BY rowid')]
    for assertion in assertions:
        target = db.execute('SELECT kind, record FROM trusted_records WHERE id=?',
                            (assertion.target_id,)).fetchone()
        target_type = (json.loads(target['record'])['entity_type'] if target and target['kind'] == 'entity'
                       else '$context' if target and target['kind'] == 'context' else None)
        policy = contract.claim_concepts.get(assertion.concept)
        if (policy is None or target_type not in policy['target_types']
                or not value_matches(assertion.value, policy['value'])):
            raise ValueError('reference assertion violates the Domain Contract')
        payload = asdict(assertion)
        lineage = [record for record in previous
                   if record['provenance']['provider_id'] == provider_id
                   and all(record[key] == payload[key] for key in ('target_id', 'concept', 'source_reference'))]
        observation_key = 'observed_version' if assertion.observed_version is not None else 'observed_at'
        existing = next((record for record in lineage
                         if record[observation_key] == payload[observation_key]
                         and (assertion.observed_version is not None or record['observed_version'] is None)), None)
        if existing is not None:
            if json.dumps(existing['value'], sort_keys=True) != json.dumps(assertion.value, sort_keys=True):
                raise ValueError('reference observation cannot be rewritten')
            continue
        # ponytail: scan reference lineages; index provider/source/semantic key when history grows.
        predecessor = lineage[-1] if lineage else None
        if predecessor and predecessor['observed_at'] is not None and assertion.observed_at is not None:
            if (datetime.fromisoformat(assertion.observed_at.replace('Z', '+00:00'))
                    <= datetime.fromisoformat(predecessor['observed_at'].replace('Z', '+00:00'))):
                raise ValueError('reference observation is older than or equal to the current head')
        record = {**payload, **metadata, 'id': str(uuid4()),
                  'supersedes_id': predecessor['id'] if predecessor else None,
                  'kind': 'reference_assertion', 'provenance_class': 'authoritative',
                  'provenance': {'provider_id': provider_id,
                                 'source_reference': assertion.source_reference,
                                 'observed_version': assertion.observed_version,
                                 'observed_at': assertion.observed_at}}
        previous.append(record)
        ids.append(record['id'])
        assert target is not None
        if assertion.target_id not in affected[target['kind']]:
            affected[target['kind']].append(assertion.target_id)
        db.execute('INSERT INTO reference_assertions VALUES (?, ?)',
                   (record['id'], json.dumps(record, allow_nan=False)))
    if ids:
        db.execute('UPDATE semantic_state SET revision=?', (revision + 1,))
        summary = {'id': commit_id, **metadata, 'provider_id': provider_id, 'assertion_ids': ids}
        db.execute('INSERT INTO reference_commits VALUES (?, ?)', (commit_id, json.dumps(summary)))
        event = {'id': str(uuid4()), **metadata, 'event_type': 'reference_committed',
                 'timestamp': timestamp, 'entity_ids': affected['entity'],
                 'context_ids': affected['context'], 'artifact_ids': [],
                 'assertion_ids': ids, 'grounding_item_ids': []}
        db.execute('INSERT INTO semantic_outbox VALUES (?, ?)', (event['id'], json.dumps(event)))
    return {'revision': revision + bool(ids), 'assertion_ids': ids,
            'semantic_commit_id': commit_id if ids else None}
