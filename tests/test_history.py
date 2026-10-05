import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from piecetogether.contracts import DomainContract
from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import (
    ClaimOperation, ContextOperation, EmergentConceptOperation, EntityOperation,
    GroundingPlanOperation, GroundingResolutionOperation, RelationshipOperation,
    SemanticProposal,
)


class ContractProvider:
    def __init__(self, contract):
        self.contract = contract

    def get(self, version):
        return self.contract


class ProposalModel:
    def __init__(self, operations, **envelope):
        self.operations = operations
        self.envelope = envelope

    def propose(self, inbound, version):
        operations = self.operations(inbound) if callable(self.operations) else self.operations
        return SemanticProposal(1, version, inbound.id, (), 'An interpretation',
                                operations, **self.envelope)


class MustNotRunModel:
    def propose(self, inbound, version):
        raise AssertionError('durable work must not invoke the model again')


class RefusingChannel:
    def deliver(self, outbound):
        return False


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Bootstrap(Path(self.directory.name) / 'core.sqlite3',
                                {'development:s1': 'a1', 'development:s2': 'a2'})
        self.contract = DomainContract(
            self.config.contract_version,
            entity_types={'Subject': {'creation': True, 'attributes': {}}},
            relationship_types={name: {'source_types': ['$claim'], 'target_types': ['$claim']}
                                for name in ('corrects', 'supersedes', 'contradicts')},
            grounding_policies={'p': {'acceptance': ['explicit', 'implicit']}},
            claim_concepts={'count': {'target_types': ['Subject', '$context'],
                                     'value': {'type': 'integer'}, 'grounding_policy': 'p'}},
            emergent_concepts={'allowed': True, 'target_types': ['Subject'],
                              'value': {'type': 'string'}, 'grounding_policy': 'p'},
        )
        self.sequence = 0

    def core(self, operations=(), **kwargs):
        return Core(self.config, model=ProposalModel(operations),
                    contract_provider=ContractProvider(self.contract), **kwargs)

    def message(self, **changes):
        self.sequence += 1
        return {'channel': 'development', 'sender': 's1',
                'idempotency_key': f'm{self.sequence}', 'text': 'Yes',
                'sent_at': '2026-10-05T12:00:00Z', **changes}

    def expose(self, operations=None, channel=None):
        operations = operations if operations is not None else (
            EntityOperation('subject', 'Subject'),
            ContextOperation('context', ('subject',)),
            ClaimOperation('count', 'subject', 'count', 3, 'p'),
            GroundingPlanOperation('p', ('count',), 'explicit',
                                  resolution_ids=('subject', 'context')),
        )
        core = self.core(operations, channel=channel)
        result = core.accept(self.message(text='There are three'))
        return core.inspect(result['communication_id'])

    def accept_items(self, exposed, items=None, revision=0, evidence=None, **kwargs):
        items = items if items is not None else exposed['grounding_items']
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id,
                                                     'explicit', 'accepted')
                         for item in items)
        core = Core(self.config, model=ProposalModel(
            operations, intent='semantic_commit', semantic_revision=revision),
            contract_provider=ContractProvider(self.contract), **kwargs)
        payload = self.message(**{'reply_to': exposed['outbound']['id'], **(evidence or {})})
        return core, payload, core.accept(payload)

    def test_authorized_commit_atomically_records_objects_claim_and_provenance(self):
        exposed = self.expose()
        core, _, result = self.accept_items(exposed)
        self.assertEqual(result['status'], 'completed')
        history = core.inspect_history()
        self.assertEqual(history['revision'], 1)
        self.assertEqual(len(history['entities']), 1)
        self.assertEqual(len(history['contexts']), 1)
        self.assertEqual(history['contexts'][0]['entity_ids'], [history['entities'][0]['id']])
        claim = history['claims'][0]
        self.assertEqual(claim['value'], 3)
        self.assertEqual(claim['target_id'], history['entities'][0]['id'])
        self.assertEqual(claim['provenance']['source_communication_id'], exposed['inbound']['id'])
        self.assertEqual(claim['provenance']['exposure_communication_id'], exposed['outbound']['id'])
        self.assertEqual(claim['provenance']['evidence_communication_id'], result['communication_id'])
        self.assertEqual(claim['provenance']['actor_id'], 'a1')
        self.assertEqual(claim['contract_version'], self.contract.version)
        self.assertEqual({item['outcome'] for item in history['grounding_items']}, {'accepted'})
        self.assertEqual(len(history['commits']), 1)
        self.assertEqual(history['events'][0]['semantic_commit_id'], history['commits'][0]['id'])
        self.assertEqual(core.inspect(result['communication_id'])['trace']['commit_result'], 'committed')
        self.assertEqual(self.core().inspect_history(), history)

    def test_model_cannot_turn_sender_rejection_into_explicit_acceptance(self):
        exposed = self.expose()
        items = exposed['grounding_items']
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id,
                                                     'explicit', 'accepted') for item in items)
        core = Core(self.config, model=ProposalModel(operations, intent='semantic_commit',
                                                   semantic_revision=0),
                    contract_provider=ContractProvider(self.contract))
        before = core.inspect_history()
        result = core.accept(self.message(text='No, that is wrong', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect_history(), before)

    def next_claim(self, history, value, relation=None):
        entity_id = history['entities'][0]['id']
        operations = (EntityOperation(entity_id, 'Subject', action='resolve'),
                      ClaimOperation('next', entity_id, 'count', value, 'p'),
                      GroundingPlanOperation('p', ('next',), 'explicit'))
        if relation:
            operations += (RelationshipOperation(relation, 'next', history['claims'][-1]['id']),)
        return self.expose(operations)

    def test_changed_claim_requires_history_relationship(self):
        core, _, _ = self.accept_items(self.expose())
        before = core.inspect_history()
        exposed = self.next_claim(before, 4)
        core, _, result = self.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['claim_history_relationship_required'])

    def test_accepting_existing_entity_resolution_preserves_its_identity(self):
        core, _, _ = self.accept_items(self.expose())
        original = core.inspect_history()['entities']
        exposed = self.expose((
            EntityOperation(original[0]['id'], 'Subject', action='resolve'),
            GroundingPlanOperation('p', (), 'explicit', resolution_ids=(original[0]['id'],)),
        ))
        core, _, result = self.accept_items(exposed, revision=1)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(core.inspect_history()['entities'], original)
        self.assertEqual(core.inspect_history()['grounding_items'][-1]['target_id'], original[0]['id'])

    def test_corrections_supersessions_and_contradictions_preserve_history(self):
        core, _, _ = self.accept_items(self.expose())
        original = core.inspect_history()['claims'][0]
        for revision, relation in enumerate(('corrects', 'supersedes', 'contradicts'), 1):
            before = core.inspect_history()
            exposed = self.next_claim(before, revision + 3, relation)
            core, _, result = self.accept_items(exposed, revision=revision)
            self.assertEqual(result['status'], 'completed')
            history = core.inspect_history()
            self.assertEqual(history['claims'][:-1], before['claims'])
            self.assertEqual(history['claims'][0], original)
            self.assertEqual(history['relationships'][-1]['relationship_type'], relation)
            self.assertEqual(history['relationships'][-1]['source_id'], history['claims'][-1]['id'])
            self.assertEqual(history['relationships'][-1]['target_id'], before['claims'][-1]['id'])

    def test_claim_does_not_authorize_unaccepted_entity_or_context(self):
        exposed = self.expose()
        item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'count')
        core, _, result = self.accept_items(exposed, [item])
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect_history()['revision'], 0)
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['ungrounded_dependency'])

    def test_partial_acceptance_can_complete_dependencies_in_a_later_commit(self):
        exposed = self.expose()
        entity_item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        core, _, first = self.accept_items(exposed, [entity_item])
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(len(core.inspect_history()['entities']), 1)
        self.assertEqual(core.inspect_history()['claims'], [])
        remaining = [item for item in exposed['grounding_items'] if item != entity_item]
        core, _, second = self.accept_items(exposed, remaining, revision=1)
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(len(core.inspect_history()['claims']), 1)
        self.assertEqual(len(core.inspect_history()['grounding_items']), 3)

    def test_direct_model_mutations_and_fabricated_items_leave_history_unchanged(self):
        for ops in ((EntityOperation('invented', 'Subject'),),
                    (ContextOperation('invented'),),
                    lambda inbound: (GroundingResolutionOperation('invented', 'p', inbound.id,
                                                                  'explicit', 'accepted'),)):
            core = Core(self.config, model=ProposalModel(ops, intent='semantic_commit', semantic_revision=0),
                        contract_provider=ContractProvider(self.contract))
            before = core.inspect_history()
            result = core.accept(self.message())
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(core.inspect_history(), before)

    def test_failed_or_indeterminate_exposure_and_silence_create_no_groundable_items(self):
        class IndeterminateChannel:
            def deliver(self, outbound):
                raise OSError('receipt missing')
        for channel in (RefusingChannel(), IndeterminateChannel()):
            exposed = self.expose(channel=channel)
            self.assertEqual(exposed['grounding_items'], [])
            self.assertEqual(self.core().inspect_history()['revision'], 0)
        exposed = self.expose()
        self.assertEqual({i['outcome'] for i in self.core().inspect(exposed['inbound']['id'])['grounding_items']},
                         {'pending'})
        self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_sender_identity_and_evidence_reference_cannot_be_forged(self):
        exposed = self.expose()
        for changes in ({'sender': 's2'}, {'reply_to': 'wrong-output'}):
            def operations(inbound):
                return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id,
                                                         'explicit', 'accepted')
                             for item in exposed['grounding_items'])
            core = Core(self.config, model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                        contract_provider=ContractProvider(self.contract))
            result = core.accept(self.message(reply_to=exposed['outbound']['id'], **changes)
                                 if 'reply_to' not in changes else self.message(**changes))
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(core.inspect_history()['revision'], 0)

    def test_stale_commit_and_reprocessing_preserve_previous_decision(self):
        first_exposed = self.expose()
        second_exposed = self.expose()
        self.accept_items(first_exposed)
        before = self.core().inspect_history()
        core, payload, stale = self.accept_items(second_exposed, revision=0)
        self.assertEqual(stale['status'], 'reprocess_required')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.inspect(stale['communication_id'])['trace']['validation_result'], 'stale')
        def fresh(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id,
                                                     'explicit', 'accepted') for item in second_exposed['grounding_items'])
        fresh_core = Core(self.config, model=ProposalModel(fresh, intent='semantic_commit', semantic_revision=1),
                          contract_provider=ContractProvider(self.contract))
        self.assertEqual(fresh_core.accept(payload)['status'], 'completed')
        self.assertEqual([a['trace']['validation_result'] for a in fresh_core.inspect(stale['communication_id'])['attempts']],
                         ['stale', 'accepted'])

    def test_storage_failure_rolls_back_history_grounding_outcomes_and_outbox(self):
        exposed = self.expose()
        before = self.core().inspect_history()
        with sqlite3.connect(self.config.database) as db:
            db.executescript("""
                CREATE TRIGGER fail_outbox BEFORE INSERT ON semantic_outbox
                BEGIN SELECT RAISE(ABORT, 'outbox storage unavailable'); END;
            """)
        core, payload, failed = self.accept_items(exposed)
        self.assertEqual(failed['status'], 'retryable')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.inspect(failed['communication_id'])['trace']['commit_result'], 'retryable')
        self.assertEqual({i['outcome'] for i in core.inspect(exposed['inbound']['id'])['grounding_items']}, {'pending'})
        with sqlite3.connect(self.config.database) as db:
            db.execute('DROP TRIGGER fail_outbox')
        self.assertEqual(core.accept(payload)['status'], 'completed')
        self.assertEqual(len(core.inspect_history()['commits']), 1)

    def test_committed_delivery_retry_survives_restart_and_contract_change(self):
        exposed = self.expose()
        core, payload, failed = self.accept_items(exposed, channel=RefusingChannel())
        self.assertEqual(failed['status'], 'retryable')
        before = core.inspect_history()
        self.assertEqual(before['revision'], 1)
        new_config = replace(self.config, contract_version='v2')
        restarted = Core(new_config, model=MustNotRunModel())
        completed = restarted.accept(payload)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(restarted.inspect_history(), before)
        self.assertEqual(restarted.inspect(completed['communication_id'])['trace']['commit_result'], 'committed')
        self.assertEqual(restarted.accept(payload), completed)
        self.assertEqual(restarted.inspect_history(), before)

    def test_commit_checkpoint_failure_cannot_leave_trusted_effects(self):
        exposed = self.expose()
        before = self.core().inspect_history()
        with sqlite3.connect(self.config.database) as db:
            db.executescript("""
                CREATE TRIGGER fail_checkpoint BEFORE UPDATE OF proposal ON turns
                BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END;
            """)
        core, _, result = self.accept_items(exposed)
        self.assertEqual(result['status'], 'retryable')
        self.assertEqual(core.inspect_history(), before)

    def test_emergent_concept_survives_restart_and_is_reusable_without_promotion(self):
        core = self.core((EmergentConceptOperation('preference', 'A reusable preference'),))
        declared = core.accept(self.message())
        self.assertEqual(declared['status'], 'completed')
        concepts = self.core().inspect_concepts()
        self.assertEqual(len(concepts), 1)
        self.assertEqual(concepts[0]['name'], 'preference')
        self.assertEqual(concepts[0]['status'], 'non_authoritative')
        self.assertEqual(concepts[0]['provenance']['source_communication_id'], declared['communication_id'])
        self.assertEqual(concepts[0]['provenance']['actor_id'], 'a1')
        self.assertEqual(concepts[0]['contract_version'], self.contract.version)
        exposed = self.expose((
            EntityOperation('subject', 'Subject'),
            ClaimOperation('claim', 'subject', 'preference', 'Earlier option', 'p'),
            GroundingPlanOperation('p', ('claim',), 'explicit', resolution_ids=('subject',)),
        ))
        core, _, accepted = self.accept_items(exposed)
        self.assertEqual(accepted['status'], 'completed')
        self.assertEqual(core.inspect_history()['claims'][0]['concept'], 'preference')
        self.assertEqual(core.inspect_concepts(), concepts)
        self.assertNotIn('preference', core.contract.claim_concepts)
        self.assertNotIn('preference', core.contract.entity_types)

    def test_emergent_concept_reconciliation_preserves_each_source_description(self):
        first_core = self.core((EmergentConceptOperation('preference', 'Earlier wording'),))
        first = first_core.accept(self.message())
        original = first_core.inspect_concepts()
        second_core = self.core((EmergentConceptOperation('preference', 'Later wording'),))
        second = second_core.accept(self.message())
        self.assertEqual(second['status'], 'completed')
        concepts = self.core().inspect_concepts()
        self.assertEqual(concepts[:1], original)
        self.assertEqual([c['description'] for c in concepts], ['Earlier wording', 'Later wording'])
        self.assertEqual([c['provenance']['source_communication_id'] for c in concepts],
                         [first['communication_id'], second['communication_id']])
        self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_commit_completed_by_another_worker_is_recovered_as_one_effect(self):
        exposed = self.expose()
        payload = self.message(reply_to=exposed['outbound']['id'])
        completed = []
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id,
                                                     'explicit', 'accepted')
                         for item in exposed['grounding_items'])
        class RacingModel:
            def propose(model, inbound, version):
                other = Core(self.config, model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                             contract_provider=ContractProvider(self.contract))
                completed.append(other.accept(payload))
                return SemanticProposal(1, version, inbound.id, (), 'Different stale draft',
                                        operations(inbound), intent='semantic_commit', semantic_revision=0)
        core = Core(self.config, model=RacingModel(), contract_provider=ContractProvider(self.contract))
        result = core.accept(payload)
        self.assertEqual(result, completed[0])
        self.assertEqual(len(core.inspect_history()['commits']), 1)
        self.assertEqual(core.inspect(result['communication_id'])['trace']['commit_result'], 'committed')

    def test_contract_confirmation_forms_are_checked_independently_of_model_acceptance(self):
        self.contract = replace(self.contract, grounding_policies={
            'p': {'acceptance': ['explicit'], 'confirmation_forms': ['Confermo']}})
        exposed = self.expose()
        core, _, rejected = self.accept_items(exposed)
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(core.inspect_history()['revision'], 0)
        core, _, accepted = self.accept_items(exposed, evidence={'text': ' CONFERMO '})
        self.assertEqual(accepted['status'], 'completed')
        self.assertEqual(core.inspect_history()['revision'], 1)
        for forms in ([], 'yes', [False], [' ']):
            with self.subTest(forms=forms), self.assertRaises(ValueError):
                replace(self.contract, grounding_policies={'p': {'acceptance': ['explicit'], 'confirmation_forms': forms}})

    def test_unverified_implicit_rationale_cannot_authorize_a_commit(self):
        exposed = self.expose()
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id, 'implicit',
                                                     'accepted', rationale='The sender continued')
                         for item in exposed['grounding_items'])
        core = Core(self.config, model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                    contract_provider=ContractProvider(self.contract))
        result = core.accept(self.message(text='Another subject', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect_history()['revision'], 0)

    def test_active_contract_change_fences_old_worker_and_old_grounding(self):
        exposed = self.expose()
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                         for item in exposed['grounding_items'])
        old = Core(self.config, model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                   contract_provider=ContractProvider(self.contract))
        new_contract = replace(self.contract, version='v2')
        new = Core(replace(self.config, contract_version='v2'),
                   model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                   contract_provider=ContractProvider(new_contract))
        for worker in (old, new):
            result = worker.accept(self.message(reply_to=exposed['outbound']['id']))
            self.assertEqual(result['status'], 'reprocess_required')
            self.assertEqual(worker.inspect_history()['revision'], 0)

    def test_persisted_emergent_names_still_obey_active_policy_and_closed_entity_types(self):
        self.core((EmergentConceptOperation('preference', 'A preference'),)).accept(self.message())
        concepts = self.core().inspect_concepts()
        self.contract = replace(self.contract, emergent_concepts={'allowed': False})
        operations = (EntityOperation('e', 'Subject'),
                      ClaimOperation('c', 'e', 'preference', 'An option', 'p'))
        core = self.core(operations)
        result = core.accept(self.message())
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['emergent_concepts_not_allowed'])
        self.assertEqual(core.inspect_concepts(), concepts)
        result = self.core((EntityOperation('e', 'preference'),)).accept(self.message())
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect_history()['revision'], 0)

    def test_rejected_proposals_never_add_emergent_vocabulary(self):
        core = self.core((EmergentConceptOperation('preference', 'A preference'), EntityOperation('e', 'Forbidden')))
        result = core.accept(self.message())
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect_concepts(), [])
        self.assertEqual(core.inspect_history()['revision'], 0)

    def test_delivery_retry_preserves_one_set_of_exposed_item_identifiers(self):
        exposed = self.expose(channel=RefusingChannel())
        payload = {key: exposed['inbound'][key] for key in
                   ('channel', 'sender', 'idempotency_key', 'text', 'sent_at', 'thread_id', 'reply_to')}
        restarted = Core(self.config, model=MustNotRunModel(), contract_provider=ContractProvider(self.contract))
        result = restarted.accept(payload)
        self.assertEqual(result['status'], 'completed')
        original_items = restarted.inspect(result['communication_id'])['grounding_items']
        self.assertEqual(len(original_items), 3)
        self.assertEqual(restarted.accept(payload), result)
        self.assertEqual(restarted.inspect(result['communication_id'])['grounding_items'], original_items)

    def test_exposure_uses_durable_proposal_not_mutable_provider_objects(self):
        self.contract = replace(self.contract, entity_types={
            'Subject': {'creation': True, 'attributes': {'label': {'type': 'string'}}}})
        attributes = {'label': 'Original'}
        class MutatingChannel:
            def deliver(self, outbound):
                attributes['label'] = 4
                return True
        exposed = self.expose((
            EntityOperation('subject', 'Subject', attributes),
            GroundingPlanOperation('p', (), 'explicit', resolution_ids=('subject',)),
        ), channel=MutatingChannel())
        core, _, result = self.accept_items(exposed)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(core.inspect_history()['entities'][0]['attributes'], {'label': 'Original'})

    def test_failed_losing_model_preserves_committed_checkpoint_and_restart_recovery(self):
        exposed = self.expose()
        payload = self.message(reply_to=exposed['outbound']['id'])
        completed = []
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                         for item in exposed['grounding_items'])
        class FailedRacingModel:
            def propose(model, inbound, version):
                other = Core(self.config, model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                             contract_provider=ContractProvider(self.contract))
                completed.append(other.accept(payload))
                raise ValueError('losing model failed')
        core = Core(self.config, model=FailedRacingModel(), contract_provider=ContractProvider(self.contract))
        result = core.accept(payload)
        self.assertEqual(result, completed[0])
        checkpoint = core.inspect(result['communication_id'])
        self.assertIsNotNone(checkpoint['outbound'])
        self.assertEqual(checkpoint['trace']['commit_result'], 'committed')
        restarted = Core(self.config, model=MustNotRunModel(), contract_provider=ContractProvider(self.contract))
        self.assertEqual(restarted.accept(payload), result)
        self.assertEqual(len(restarted.inspect_history()['commits']), 1)
        self.assertEqual(checkpoint['attempts'][-1]['trace']['failure_stage'], 'model')

    def test_core_exposes_every_groundable_interpretation_even_with_unrelated_model_draft(self):
        class UnrelatedDraft(ProposalModel):
            def propose(model, inbound, version):
                return replace(super().propose(inbound, version), draft_response='Would you like to continue?')
        operations = (
            EntityOperation('subject', 'Subject'),
            ClaimOperation('count', 'subject', 'count', 999, 'p'),
            GroundingPlanOperation('p', ('count',), 'explicit', resolution_ids=('subject',)),
        )
        core = Core(self.config, model=UnrelatedDraft(operations), contract_provider=ContractProvider(self.contract))
        result = core.accept(self.message(text='Hello'))
        self.assertEqual(result['status'], 'completed')
        self.assertIn('Subject', result['reply'])
        self.assertIn('count', result['reply'])
        self.assertIn('999', result['reply'])
        exposed = core.inspect(result['communication_id'])
        core, _, accepted = self.accept_items(exposed)
        self.assertEqual(accepted['status'], 'completed')
        self.assertEqual(core.inspect_history()['claims'][0]['value'], 999)

    def test_commit_after_ingress_snapshot_cannot_erase_winning_checkpoint(self):
        exposed = self.expose()
        payload = self.message(reply_to=exposed['outbound']['id'])
        def operations(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                         for item in exposed['grounding_items'])
        winner = Core(self.config, model=ProposalModel(operations, intent='semantic_commit', semantic_revision=0),
                      contract_provider=ContractProvider(self.contract))
        class FailedModel:
            def propose(model, inbound, version):
                raise ValueError('losing model failed')
        loser = Core(self.config, model=FailedModel(), contract_provider=ContractProvider(self.contract))
        completed = []
        original_connect = sqlite3.connect
        class SchedulingConnection(sqlite3.Connection):
            def close(db):
                super().close()
                if not completed:
                    completed.append(None)  # Prevent reentrant scheduling in the real SQLite adapter.
                    completed[0] = winner.accept(payload)
        def scheduled_connection(*args, **kwargs):
            return original_connect(*args, **kwargs, factory=SchedulingConnection)
        # Schedule at an external storage boundary, after ingress releases its transaction.
        with patch('sqlite3.connect', side_effect=scheduled_connection):
            result = loser.accept(payload)
        self.assertEqual(result, completed[0])
        self.assertIsNotNone(loser.inspect(result['communication_id'])['outbound'])
        restarted = Core(self.config, model=MustNotRunModel(), contract_provider=ContractProvider(self.contract))
        self.assertEqual(restarted.accept(payload), result)
        self.assertEqual(len(restarted.inspect_history()['commits']), 1)


if __name__ == '__main__':
    unittest.main()
