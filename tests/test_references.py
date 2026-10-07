"""Authoritative reference scenarios at the Core boundary, without live providers."""

import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path

import test_history as fixtures
from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import ClaimOperation, EntityOperation, GroundingPlanOperation, RelationshipOperation
from piecetogether.references import ReferenceAssertion, ReferenceConfig


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.HistoryTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.core, _, result = self.flow.accept_items(self.flow.expose())
        self.assertEqual(result['status'], 'completed')
        self.entity_id = self.core.inspect_history()['entities'][0]['id']
        self.directory = Path(self.flow.directory.name)
        self.reference_file = self.directory / 'reference.json'
        self.config_file = self.directory / 'deployment.json'
        self.config_file.write_text(json.dumps({
            'database': 'core.sqlite3', 'identities': self.flow.config.identities,
            'reference_state': {'provider_id': 'application', 'path': 'reference.json'},
        }))

    def assertion(self, **changes):
        return {'target_id': self.entity_id, 'concept': 'count', 'value': 4,
                'source_reference': 'records/subject-1', 'observed_version': 'v1',
                **changes}

    def write_assertions(self, *assertions):
        self.reference_file.write_text(json.dumps(list(assertions)))

    def configured_core(self, **changes):
        return Core(Bootstrap.from_file(self.config_file),
                    contract_provider=fixtures.ContractProvider(self.flow.contract),
                    **changes)

    def test_configured_reference_keeps_distinct_provenance_and_explicit_conflict(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        assertions = core.current_view()['assertion_sets'][0]
        grounded, authoritative = assertions['heads']
        self.assertEqual([grounded['value'], authoritative['value']], [3, 4])
        self.assertEqual(grounded['provenance_class'], 'grounded')
        self.assertEqual(authoritative['provenance_class'], 'authoritative')
        self.assertEqual(authoritative['kind'], 'reference_assertion')
        self.assertEqual(authoritative['provenance'], {
            'provider_id': 'application', 'source_reference': 'records/subject-1',
            'observed_version': 'v1', 'observed_at': None,
        })
        self.assertEqual(authoritative['contract_version'], self.flow.contract.version)
        self.assertTrue(authoritative['semantic_commit_id'])
        self.assertEqual(assertions['conflicts'], [{
            'assertion_ids': [grounded['id'], authoritative['id']], 'relationship_ids': [],
        }])
        ledger = core.inspect_history()
        self.assertEqual(len(ledger['claims']), 1)
        self.assertEqual(len(ledger['reference_assertions']), 1)
        self.assertEqual(len(ledger['grounding_items']), 3)
        snapshot = core.current_view()
        self.assertEqual(self.configured_core().current_view(), snapshot)
        self.assertEqual(core.refresh_references()['assertion_ids'], [])
        self.assertEqual(core.current_view(), snapshot)

    def test_reference_updates_keep_own_lineage_without_retiring_grounded_heads(self):
        self.write_assertions(self.assertion(value=3))
        core = self.configured_core()
        first = core.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in first['heads']], [3, 3])
        self.assertEqual(first['conflicts'], [])
        original_id = first['heads'][1]['id']
        for version, value in (('v2', 4), ('v3', 5)):
            self.write_assertions(self.assertion(observed_version=version, value=value))
            core.refresh_references()
        assertions = core.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in assertions['heads']], [3, 5])
        self.assertEqual([record['value'] for record in assertions['lineage']], [3, 4])
        self.assertEqual(assertions['heads'][1]['lineage_ids'][0], original_id)
        self.assertEqual(len(assertions['heads'][1]['lineage_ids']), 3)
        self.assertEqual(len(assertions['conflicts']), 1)
        self.assertTrue(all(record['provenance_class'] == 'authoritative'
                            for record in assertions['lineage']))
        snapshot = core.current_view()
        self.write_assertions(self.assertion(value=3))
        self.assertEqual(core.refresh_references()['assertion_ids'], [])
        self.assertEqual(core.current_view(), snapshot)
        self.assertEqual(len(core.inspect_history()['reference_assertions']), 3)

    def test_one_source_version_cannot_be_rewritten_by_changing_observation_time(self):
        self.write_assertions(self.assertion(observed_at='2026-10-07T10:00:00Z'))
        core = self.configured_core()
        snapshot = core.current_view()
        self.write_assertions(self.assertion(value=7, observed_at='2026-10-07T11:00:00Z'))
        with self.assertRaisesRegex(ValueError, 'rewritten'):
            core.refresh_references()
        self.assertEqual(core.current_view(), snapshot)

    def test_time_only_observations_retain_time_and_reject_out_of_order_updates(self):
        self.write_assertions(self.assertion(observed_version=None, observed_at='2026-10-07T10:00:00Z'))
        core = self.configured_core()
        snapshot = core.current_view()
        reference = snapshot['assertion_sets'][0]['heads'][1]
        self.assertEqual(reference['provenance']['observed_at'], '2026-10-07T10:00:00Z')
        self.assertIsNone(reference['provenance']['observed_version'])
        self.write_assertions(self.assertion(value=7, observed_version=None, observed_at='2026-10-06T10:00:00Z'))
        with self.assertRaisesRegex(ValueError, 'older'):
            core.refresh_references()
        self.assertEqual(core.current_view(), snapshot)
        self.write_assertions(self.assertion(value=8, observed_version=None, observed_at='2026-10-07T11:00:00+00:00'))
        core.refresh_references()
        self.assertEqual([head['value'] for head in core.current_view()['assertion_sets'][0]['heads']], [3, 8])

    def test_reference_event_names_affected_target_without_grounding_items(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        event = core.inspect_history()['events'][-1]
        self.assertEqual(event['event_type'], 'reference_committed')
        self.assertEqual(event['entity_ids'], [self.entity_id])
        self.assertEqual(event['grounding_item_ids'], [])
        reference = core.current_view()['assertion_sets'][0]['heads'][1]
        self.assertEqual(event['assertion_ids'], [reference['id']])
        self.assertEqual(event['semantic_revision'], core.current_view()['revision'])
        self.assertEqual(event['semantic_commit_id'], reference['semantic_commit_id'])

    def test_invalid_reference_batches_never_change_trusted_state(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        snapshot = core.current_view()
        ledger = core.inspect_history()
        for changes in (
            {'target_id': 'missing'}, {'target_id': ledger['claims'][0]['id']},
            {'concept': 'unknown'}, {'value': True}, {'value': float('nan')},
            {'observed_version': None}, {'source_reference': ''},
            {'observed_at': 'yesterday'}, {'observed_at': '2026-10-07T10:00:00'},
            {'provider_id': 'spoofed'}, {'kind': 'claim'}, {'provenance_class': 'grounded'},
        ):
            with self.subTest(changes=changes):
                self.write_assertions(self.assertion(value=6, observed_version='v2'), self.assertion(**changes))
                with self.assertRaises(ValueError):
                    core.refresh_references()
                self.assertEqual(core.current_view(), snapshot)
                self.assertEqual(core.inspect_history(), ledger)

    def test_independent_reference_sources_and_providers_remain_competing_heads(self):
        self.write_assertions(self.assertion(), self.assertion(value=5, source_reference='other-record'))
        core = self.configured_core()
        self.config_file.write_text(json.dumps({
            'database': 'core.sqlite3', 'identities': self.flow.config.identities,
            'reference_state': {'provider_id': 'other-provider', 'path': 'reference.json'},
        }))
        self.write_assertions(self.assertion(value=6))
        other = self.configured_core()
        assertions = other.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in assertions['heads']], [3, 4, 5, 6])
        self.assertEqual([head['provenance'].get('provider_id') for head in assertions['heads']],
                         [None, 'application', 'application', 'other-provider'])
        self.assertEqual(len(assertions['conflicts']), 6)
        self.assertEqual(assertions['lineage'], [])
        self.assertEqual(len(core.inspect_history()['claims']), 1)

    def test_failed_reference_commit_rolls_back_history_revision_and_event_then_retries(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        snapshot = core.current_view()
        ledger = core.inspect_history()
        self.write_assertions(self.assertion(value=5, observed_version='v2'))
        with sqlite3.connect(self.flow.config.database) as db:
            db.executescript("""
                CREATE TRIGGER fail_reference_event BEFORE INSERT ON semantic_outbox
                BEGIN SELECT RAISE(ABORT, 'event unavailable'); END;
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            core.refresh_references()
        self.assertEqual(core.current_view(), snapshot)
        self.assertEqual(core.inspect_history(), ledger)
        with sqlite3.connect(self.flow.config.database) as db:
            db.execute('DROP TRIGGER fail_reference_event')
        core.refresh_references()
        self.assertEqual([head['value'] for head in core.current_view()['assertion_sets'][0]['heads']], [3, 5])
        self.assertEqual(len(core.inspect_history()['reference_assertions']), 2)

    def test_injected_provider_must_match_static_identity_and_runs_outside_transaction(self):
        test = self
        class Provider:
            provider_id = 'application'

            def get(self):
                # This separate Core writes during provider I/O; a held writer lock would fail.
                result = test.flow.core().accept(test.flow.message(text='An unrelated new contribution'))
                test.assertEqual(result['status'], 'completed')
                return (ReferenceAssertion(test.entity_id, 'count', 4, 'records/subject-1', 'v1'),)

        config = replace(self.flow.config, reference_state=ReferenceConfig('application'))
        provider = Provider()
        core = Core(config, contract_provider=fixtures.ContractProvider(self.flow.contract),
                    reference_provider=provider)
        self.assertEqual([head['value'] for head in core.current_view()['assertion_sets'][0]['heads']], [3, 4])
        snapshot = core.current_view()
        provider.provider_id = 'unconfigured'
        with self.assertRaises(ValueError):
            core.refresh_references()
        with self.assertRaises(ValueError):
            self.flow.core(reference_provider=provider)
        self.assertEqual(core.current_view(), snapshot)

    def test_grounded_correction_does_not_silently_resolve_authoritative_conflict(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        exposed = self.flow.next_claim(core.inspect_history(), 5, 'corrects')
        core, _, result = self.flow.accept_items(exposed, revision=2)
        self.assertEqual(result['status'], 'completed')
        assertions = core.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in assertions['heads']], [5, 4])
        self.assertEqual([record['value'] for record in assertions['lineage']], [3])
        self.assertEqual(len(assertions['conflicts']), 1)
        reference = assertions['heads'][1]
        exposed = self.flow.expose((
            EntityOperation(self.entity_id, 'Subject', action='resolve'),
            ClaimOperation('next', self.entity_id, 'count', 4, 'p'),
            RelationshipOperation('supersedes', 'next', reference['id']),
            GroundingPlanOperation('p', ('next',), 'explicit'),
        ))
        self.assertEqual(exposed['status'], 'rejected')
        self.assertEqual(core.current_view()['assertion_sets'][0], assertions)

    def test_reference_context_is_actor_scoped_and_does_not_authorize_disclosure(self):
        self.write_assertions(self.assertion(value=987654))
        core = self.configured_core()
        reference_id = core.current_view()['assertion_sets'][0]['heads'][1]['id']
        class Model:
            def __init__(self):
                self.pack = None

            def propose(self, inbound, version, context_pack):
                self.pack = context_pack
                return fixtures.ProposalModel(()).propose(inbound, version, context_pack)

        model = Model()
        core = self.configured_core(model=model)
        result = core.accept(self.flow.message(text=f'count {self.entity_id} {reference_id}'))
        self.assertEqual(result['status'], 'completed')
        captured = core.inspect(result['communication_id'])['trace']['context_pack']['records']
        reference = next(record for record in captured if record['id'] == reference_id)
        self.assertEqual(reference['kind'], 'reference_assertion')
        self.assertEqual(json.loads(reference['payload_json'])['provenance_class'], 'authoritative')
        self.assertEqual(reference['disclosure_purposes'], [])
        safe_reference = next(record for record in model.pack.records if record.id == reference_id)
        self.assertEqual(safe_reference.payload_json, '')
        self.assertNotIn('987654', result['reply'])
        other = core.accept(self.flow.message(sender='s2', text=f'count {reference_id}'))
        self.assertEqual(other['status'], 'completed')
        self.assertNotIn(reference_id, [record.id for record in model.pack.catalogue])

    def test_context_reference_uses_context_identity_and_event_scope(self):
        context_id = self.core.inspect_history()['contexts'][0]['id']
        self.write_assertions(self.assertion(target_id=context_id))
        core = self.configured_core()
        assertion_set = next(group for group in core.current_view()['assertion_sets']
                             if group['target_id'] == context_id)
        self.assertEqual([head['value'] for head in assertion_set['heads']], [4])
        self.assertEqual(assertion_set['heads'][0]['provenance_class'], 'authoritative')
        self.assertEqual(assertion_set['conflicts'], [])
        self.assertEqual(core.inspect_history()['events'][-1]['context_ids'], [context_id])
        self.assertEqual(core.inspect_history()['events'][-1]['entity_ids'], [])

    def test_reference_refresh_during_model_work_fences_stale_semantic_commit(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        exposed = self.flow.next_claim(core.inspect_history(), 5, 'corrects')
        self.write_assertions(self.assertion(value=6, observed_version='v2'))
        original = fixtures.ProposalModel(
            lambda inbound: tuple(fixtures.GroundingResolutionOperation(
                item['id'], 'p', inbound.id, 'explicit', 'accepted')
                for item in exposed['grounding_items']), intent='semantic_commit', semantic_revision=2)
        class Model:
            def propose(self, inbound, version, context_pack):
                core.refresh_references()
                return original.propose(inbound, version, context_pack)

        committing = Core(self.flow.config, model=Model(),
                          contract_provider=fixtures.ContractProvider(self.flow.contract))
        result = committing.accept(self.flow.message(reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'reprocess_required')
        self.assertEqual(len(core.inspect_history()['claims']), 1)
        self.assertEqual([head['value'] for head in core.current_view()['assertion_sets'][0]['heads']], [3, 6])
        self.assertEqual({item['outcome'] for item in committing.inspect(exposed['inbound']['id'])['grounding_items']},
                         {'pending'})

    def test_unavailable_or_malformed_provider_leaves_durable_reference_state_unchanged(self):
        self.write_assertions(self.assertion())
        core = self.configured_core()
        snapshot = core.current_view()
        self.reference_file.unlink()
        with self.assertRaises(OSError):
            core.refresh_references()
        self.assertEqual(core.current_view(), snapshot)
        for raw in ('{}', '[{"target_id":"x","target_id":"y"}]', '[' * 2000 + ']' * 2000):
            self.reference_file.write_text(raw)
            with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, RecursionError)):
                core.refresh_references()
            self.assertEqual(core.current_view(), snapshot)
        # Disabling the provider does not erase previously committed reference evidence.
        self.assertEqual(self.flow.core().current_view(), snapshot)


if __name__ == '__main__':
    unittest.main()
