"""Current Trusted View scenarios through real Grounding and Core commits."""

import json
import sqlite3
import unittest

import test_history as fixtures
from piecetogether.core import Core
from piecetogether.proposals import (
    ClaimOperation, ContextOperation, EntityOperation, GroundingPlanOperation,
    GroundingResolutionOperation, RelationshipOperation,
)


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.HistoryTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def test_candidates_and_operational_records_never_cross_view_boundary(self):
        exposed = self.flow.expose()
        core = self.flow.core()
        self.assertEqual(core.current_view(), {
            'revision': 0, 'entities': [], 'contexts': [], 'artifacts': [], 'assertion_sets': [],
        })
        self.assertTrue(core.inspect(exposed['inbound']['id'])['grounding_items'])

    def test_current_heads_retain_provenance_and_transitive_lineage(self):
        core, _, result = self.flow.accept_items(self.flow.expose())
        self.assertEqual(result['status'], 'completed')
        original = core.inspect_history()['claims'][0]
        for revision, relation in enumerate(('corrects', 'supersedes'), 1):
            exposed = self.flow.next_claim(core.inspect_history(), revision + 3, relation)
            core, _, result = self.flow.accept_items(exposed, revision=revision)
            self.assertEqual(result['status'], 'completed')
        history = core.inspect_history()
        view = core.current_view()
        assertions = view['assertion_sets'][0]
        self.assertEqual(view['revision'], 3)
        self.assertEqual(assertions['target_id'], history['entities'][0]['id'])
        self.assertEqual(assertions['concept'], 'count')
        self.assertEqual([head['id'] for head in assertions['heads']], [history['claims'][-1]['id']])
        self.assertEqual(assertions['heads'][0]['provenance'], history['claims'][-1]['provenance'])
        self.assertTrue(assertions['heads'][0]['current'])
        self.assertEqual(assertions['heads'][0]['lineage_ids'], [claim['id'] for claim in history['claims']])
        self.assertEqual([claim['id'] for claim in assertions['lineage']], [claim['id'] for claim in history['claims'][:-1]])
        self.assertTrue(all(not claim['current'] for claim in assertions['lineage']))
        self.assertEqual(assertions['lineage'][0]['provenance'], original['provenance'])
        self.assertEqual(assertions['relationships'], history['relationships'])
        self.assertEqual(assertions['conflicts'], [])
        self.assertEqual(self.flow.core().current_view(), view)

    def test_contradicting_heads_are_explicit_conflicts_not_a_scalar_winner(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        exposed = self.flow.next_claim(core.inspect_history(), 4, 'contradicts')
        core, _, result = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'completed')
        history = core.inspect_history()
        assertions = core.current_view()['assertion_sets'][0]
        ids = [claim['id'] for claim in history['claims']]
        self.assertEqual([head['id'] for head in assertions['heads']], ids)
        self.assertEqual(assertions['lineage'], [])
        self.assertEqual(assertions['conflicts'], [{
            'assertion_ids': ids, 'relationship_ids': [history['relationships'][0]['id']],
        }])
        # Superseding one branch preserves the other competing current head.
        entity = history['entities'][0]['id']
        exposed = self.flow.expose((
            EntityOperation(entity, 'Subject', action='resolve'),
            ClaimOperation('next', entity, 'count', 5, 'p'),
            RelationshipOperation('supersedes', 'next', ids[1]),
            RelationshipOperation('contradicts', 'next', ids[0]),
            GroundingPlanOperation('p', ('next',), 'explicit'),
        ))
        core, _, result = self.flow.accept_items(exposed, revision=2)
        self.assertEqual(result['status'], 'completed')
        assertions = core.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in assertions['heads']], [3, 5])
        self.assertEqual(len(assertions['conflicts']), 1)
        self.assertEqual([claim['value'] for claim in assertions['lineage']], [4])

    def test_equal_independent_assertions_remain_heads_without_inventing_conflict(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        exposed = self.flow.next_claim(core.inspect_history(), 3)
        core, _, result = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'completed')
        assertions = core.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in assertions['heads']], [3, 3])
        self.assertEqual(assertions['conflicts'], [])

    def test_explicit_contradiction_is_visible_even_when_current_values_are_equal(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        exposed = self.flow.next_claim(core.inspect_history(), 3, 'contradicts')
        core, _, result = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'completed')
        assertions = core.current_view()['assertion_sets'][0]
        self.assertEqual([head['value'] for head in assertions['heads']], [3, 3])
        self.assertEqual(len(assertions['conflicts']), 1)
        self.assertEqual(assertions['conflicts'][0]['relationship_ids'], [core.inspect_history()['relationships'][0]['id']])

    def lifecycle(self, action, context_id, revision, **changes):
        core = self.flow.core((
            ContextOperation(context_id, action=action),
            GroundingPlanOperation('p', (), 'explicit', resolution_ids=(context_id,)),
        ))
        result = core.accept(self.flow.message(text=f'{action} {context_id}', **changes))
        self.assertEqual(result['status'], 'completed')
        exposed = core.inspect(result['communication_id'])
        self.assertIn(action, exposed['outbound']['text'])
        self.assertEqual(core.current_view()['revision'], revision)
        return exposed

    def test_context_lifecycle_preserves_identity_members_and_provenance_across_threads(self):
        exposed = self.flow.expose((
            EntityOperation('first', 'Subject'), EntityOperation('second', 'Subject'),
            ContextOperation('scope', ('first', 'second')),
            GroundingPlanOperation('p', (), 'explicit', resolution_ids=('first', 'second', 'scope')),
        ))
        core, _, result = self.flow.accept_items(exposed)
        self.assertEqual(result['status'], 'completed')
        original = core.inspect_history()['contexts'][0]
        self.assertEqual(core.current_view()['contexts'][0]['status'], 'active')
        self.assertEqual(len(original['entity_ids']), 2)
        for revision, action, status in ((1, 'suspend', 'suspended'), (2, 'resume', 'active')):
            exposed = self.lifecycle(action, original['id'], revision, thread_id=f'thread-{revision}')
            core, payload, result = self.flow.accept_items(
                exposed, revision=revision, evidence={'thread_id': 'different-thread'})
            self.assertEqual(result['status'], 'completed')
            projected = core.current_view()['contexts'][0]
            self.assertEqual(projected['status'], status)
            self.assertEqual(projected['id'], original['id'])
            self.assertEqual(projected['entity_ids'], original['entity_ids'])
            self.assertEqual(projected['provenance'], original['provenance'])
            transition = projected['lifecycle'][-1]
            self.assertEqual(transition['action'], action)
            self.assertEqual(transition['provenance']['evidence_communication_id'], result['communication_id'])
            self.assertEqual(core.inspect_history()['contexts'], [original])
            self.assertEqual(core.inspect_history()['events'][-1]['context_ids'], [original['id']])
            before = core.current_view()
            self.assertEqual(core.accept(payload), result)
            self.assertEqual(self.flow.core().current_view(), before)
        self.assertEqual(len(projected['lifecycle']), 2)

    def test_lifecycle_rejects_unknown_context_membership_rewrite_and_invalid_transition(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        original = core.inspect_history()['contexts'][0]
        before = core.current_view()
        for operation in (
            ContextOperation('missing', action='suspend'),
            ContextOperation(core.inspect_history()['entities'][0]['id'], action='suspend'),
            ContextOperation(original['id'], action='resume'),
            ContextOperation(original['id'], ('missing',), action='suspend'),
        ):
            with self.subTest(operation=operation):
                result = self.flow.core((operation,)).accept(self.flow.message(text=operation.id))
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(core.current_view(), before)

    def test_transition_cannot_bypass_grounding_or_actor_disclosure_scope(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        before = core.current_view()
        operations = (ContextOperation(context_id, action='suspend'),
                      GroundingPlanOperation('p', (), 'explicit', resolution_ids=(context_id,)))
        other_actor = self.flow.core(operations)
        result = other_actor.accept(self.flow.message(text=context_id, sender='s2'))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(other_actor.inspect(result['communication_id'])['trace']['validation_reasons'], ['context_scope_not_allowed'])
        direct = Core(self.flow.config, model=fixtures.ProposalModel(
            operations, intent='semantic_commit', semantic_revision=1),
            contract_provider=fixtures.ContractProvider(self.flow.contract))
        result = direct.accept(self.flow.message(text=context_id))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.current_view(), before)

    def test_context_candidate_or_rejected_grounding_cannot_suspend_trusted_context(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        before = core.current_view()
        exposed = self.lifecycle('suspend', context_id, 1)
        core, _, rejected = self.flow.accept_items(exposed, revision=1, evidence={'text': 'No'})
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(core.current_view(), before)
        self.assertEqual(core.inspect(exposed['inbound']['id'])['grounding_items'][0]['outcome'], 'pending')

    def test_context_transition_rolls_back_with_failed_outbox_and_retries_once(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        exposed = self.lifecycle('suspend', context_id, 1)
        before = core.current_view()
        with sqlite3.connect(self.flow.config.database) as db:
            db.executescript("""
                CREATE TRIGGER fail_lifecycle_outbox BEFORE INSERT ON semantic_outbox
                BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END;
            """)
        core, payload, failed = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(failed['status'], 'retryable')
        self.assertEqual(core.current_view(), before)
        with sqlite3.connect(self.flow.config.database) as db:
            db.execute('DROP TRIGGER fail_lifecycle_outbox')
        self.assertEqual(core.accept(payload)['status'], 'completed')
        self.assertEqual(core.current_view()['contexts'][0]['status'], 'suspended')
        self.assertEqual(len(core.current_view()['contexts'][0]['lifecycle']), 1)

    def test_context_pack_contains_current_lifecycle_not_just_creation_record(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        exposed = self.lifecycle('suspend', context_id, 1)
        core, _, result = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'completed')
        result = self.flow.core().accept(self.flow.message(text=context_id))
        record = next(record for record in self.flow.core().inspect(result['communication_id'])['trace']['context_pack']['records']
                      if record['id'] == context_id)
        self.assertEqual(json.loads(record['payload_json'])['status'], 'suspended')

    def test_delayed_context_transition_uses_sender_evidence_without_thread_or_reply(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        exposed = self.lifecycle('suspend', context_id, 1, thread_id='old-thread')
        core = Core(self.flow.config, model=fixtures.ProposalModel(
            lambda inbound: (GroundingResolutionOperation(
                exposed['grounding_items'][0]['id'], 'p', inbound.id, 'explicit', 'accepted',
                evidence_span=f'{context_id} yes'),), intent='semantic_commit', semantic_revision=1),
            contract_provider=fixtures.ContractProvider(self.flow.contract))
        result = core.accept(self.flow.message(text=f'{context_id} yes'))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(core.current_view()['contexts'][0]['status'], 'suspended')

    def test_competing_transition_rechecks_context_lifecycle_even_with_fresh_revision(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        first = self.lifecycle('suspend', context_id, 1)
        second = self.lifecycle('suspend', context_id, 1)
        core, _, result = self.flow.accept_items(first, revision=1)
        self.assertEqual(result['status'], 'completed')
        before = core.current_view()
        core, _, result = self.flow.accept_items(second, revision=2)
        self.assertEqual(result['status'], 'reprocess_required')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'], ['context_lifecycle_changed'])
        self.assertEqual(core.current_view(), before)

    def test_context_scoped_claim_keeps_context_identity_when_committed_with_transition(self):
        core, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = core.inspect_history()['contexts'][0]['id']
        exposed = self.flow.expose((
            ContextOperation(context_id, action='suspend'),
            ClaimOperation('scoped', context_id, 'count', 8, 'p'),
            GroundingPlanOperation('p', ('scoped',), 'explicit', resolution_ids=(context_id,)),
        ))
        self.assertEqual(exposed['status'], 'completed')
        core, _, result = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'completed')
        view = core.current_view()
        self.assertEqual(view['contexts'][0]['status'], 'suspended')
        self.assertEqual(len(view['assertion_sets']), 2)
        assertions = next(group for group in view['assertion_sets'] if group['target_id'] == context_id)
        self.assertEqual([head['value'] for head in assertions['heads']], [8])
        self.assertEqual(core.inspect_history()['claims'][-1]['target_id'], context_id)


if __name__ == '__main__':
    unittest.main()
