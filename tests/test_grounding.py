import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from piecetogether.contracts import DomainContract
from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import (
    ClaimOperation,
    ContextOperation,
    EmergentConceptOperation,
    EntityOperation,
    GroundingPlanOperation,
    GroundingResolutionOperation,
    ReferenceResolutionOperation,
    SemanticProposal,
)


class ContractProvider:
    def __init__(self, contract):
        self.contract = contract

    def get(self, version):
        return self.contract


class CapturedModel:
    def __init__(self, operations, **envelope):
        self.operations = operations
        self.envelope = envelope

    def propose(self, inbound, version, context_pack):
        operations = self.operations(inbound) if callable(self.operations) else self.operations
        return SemanticProposal(1, version, inbound.id, (), 'Interpretazione', operations, **self.envelope)


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Bootstrap(Path(self.directory.name) / 'core.sqlite3', {'development:s1': 'a1'})
        self.contract = DomainContract(
            self.config.contract_version,
            entity_types={'Subject': {'creation': True, 'attributes': {}}},
            grounding_policies={'p': {'acceptance': ['explicit', 'implicit']}},
            claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'},
                                     'grounding_policy': 'p'}},
        )
        self.sequence = 0

    def message(self, **changes):
        self.sequence += 1
        return {'channel': 'development', 'sender': 's1', 'idempotency_key': f'm{self.sequence}',
                'text': 'Yes', 'sent_at': '2026-10-07T12:00:00Z', **changes}

    def core(self, operations=(), **envelope):
        return Core(self.config, CapturedModel(operations, **envelope),
                    contract_provider=ContractProvider(self.contract))

    def expose(self):
        core = self.core((
            EntityOperation('subject', 'Subject'),
            ClaimOperation('note', 'subject', 'note', 'blue', 'p'),
            GroundingPlanOperation('p', ('note',), 'explicit', ('subject',)),
        ))
        result = core.accept(self.message(text='Blu'))
        self.assertEqual(result['status'], 'completed')
        return core.inspect(result['communication_id'])

    def test_italian_implicit_acceptance_requires_supported_later_evidence(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        def accept_subject(inbound):
            return (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),)
        first = self.core(accept_subject, intent='semantic_commit', semantic_revision=0).accept(
            self.message(reply_to=exposed['outbound']['id']))
        self.assertEqual(first['status'], 'completed')
        def accept_note(inbound):
            return (GroundingResolutionOperation(
                note['id'], 'p', inbound.id, 'implicit', 'accepted',
                rationale='Il mittente ha detto: blue sì, va bene', evidence_span='blue sì, va bene'),)
        result = self.core(accept_note, intent='semantic_commit', semantic_revision=1).accept(
            self.message(text='blue sì, va bene', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'completed')
        history = self.core().inspect_history()
        self.assertEqual(history['claims'][0]['value'], 'blue')
        accepted = next(record for record in history['grounding_items'] if record['id'] == note['id'])
        self.assertEqual(accepted['rationale'], 'Il mittente ha detto: blue sì, va bene')

    def test_implicit_evidence_requires_an_exact_item_specific_span(self):
        exposed = self.expose()
        item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        def unrelated(inbound):
            return (GroundingResolutionOperation(
                item['id'], 'p', inbound.id, 'implicit', 'accepted',
                rationale='The sender said: sì, parliamo di altro', confidence=.99,
            ),)
        result = self.core(unrelated, intent='semantic_commit', semantic_revision=0).accept(
            self.message(text='sì, parliamo di altro', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_implicit_evidence_rejects_substrings_questions_and_conflicting_clauses(self):
        for text, span in (
            ('subject sistemiamo', 'subject sistemiamo'),
            ('subject incorrect', 'subject incorrect'),
            ('subject yes; subject no', 'subject yes'),
            ('subject yes, but not that', 'subject yes'),
            ('subject yes?', 'subject yes'),
            ('subject yes ?', 'subject yes'),
            ('subject yes. No!', 'subject yes'),
            ('subject yes! Not that.', 'subject yes'),
            ('subject sì. Non è quello.', 'subject sì'),
            ('"subject yes"', 'subject yes'),
        ):
            with self.subTest(text=text):
                self.config = replace(self.config, database=Path(self.directory.name) / f'{self.sequence}.sqlite3')
                exposed = self.expose()
                item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
                rejected = self.core(
                    lambda inbound, item_id=item['id'], evidence=span: (GroundingResolutionOperation(
                        item_id, 'p', inbound.id, 'implicit', 'accepted',
                        rationale=evidence, evidence_span=evidence,
                    ),), intent='semantic_commit', semantic_revision=0,
                ).accept(self.message(text=text, reply_to=exposed['outbound']['id']))
                self.assertEqual(rejected['status'], 'rejected')
                self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_confident_but_conflicting_implicit_decision_stays_pending_with_trace(self):
        exposed = self.expose()
        item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        def mistaken(inbound):
            return (GroundingResolutionOperation(
                item['id'], 'p', inbound.id, 'implicit', 'accepted',
                rationale='The sender said: No, non è quello', confidence=.99,
            ),)
        core = self.core(mistaken, intent='semantic_commit', semantic_revision=0)
        result = core.accept(self.message(text='No, non è quello', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'rejected')
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(trace['grounding_decision']['decisions'][0]['confidence'], .99)
        self.assertIsNone(trace['grounding_decision']['fallback'])
        self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_implicit_acceptance_requires_rationale_bound_to_its_evidence(self):
        for rationale in (None, 'different evidence'):
            with self.subTest(rationale=rationale):
                self.config = replace(self.config, database=Path(self.directory.name) / f'rationale-{self.sequence}.sqlite3')
                exposed = self.expose()
                item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
                result = self.core(
                    lambda inbound, item_id=item['id'], explanation=rationale: (GroundingResolutionOperation(
                        item_id, 'p', inbound.id, 'implicit', 'accepted', rationale=explanation,
                        evidence_span='subject yes',
                    ),), intent='semantic_commit', semantic_revision=0,
                ).accept(self.message(text='subject yes', reply_to=exposed['outbound']['id']))
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_duplicate_value_items_remain_ambiguous_even_in_a_reply(self):
        core = self.core((
            EntityOperation('subject', 'Subject'),
            ClaimOperation('first', 'subject', 'note', 'blue', 'p'),
            ClaimOperation('second', 'subject', 'note', 'blue', 'p'),
            GroundingPlanOperation('p', ('first', 'second'), 'implicit', ('subject',)),
        ))
        exposed = core.accept(self.message(text='due note'))
        turn = core.inspect(exposed['communication_id'])
        first = next(item for item in turn['grounding_items'] if item['candidate_id'] == 'first')
        rejected = self.core(
            lambda inbound: (GroundingResolutionOperation(
                first['id'], 'p', inbound.id, 'implicit', 'accepted',
                rationale='blue yes', evidence_span='blue yes',
            ),), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='blue yes', reply_to=turn['outbound']['id']))
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(self.core().inspect_history()['revision'], 0)

    def test_reference_choices_are_scoped_and_new_requires_ordinary_grounding(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        def accept_subject(inbound):
            return (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),)
        self.assertEqual(self.core(accept_subject, intent='semantic_commit', semantic_revision=0).accept(
            self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        subject_id = self.core().inspect_history()['entities'][0]['id']
        before = self.core().inspect_history()
        unselected = self.core((ReferenceResolutionOperation('entity', 'known', subject_id),)).accept(
            self.message(text='quello', idempotency_key='unselected'))
        self.assertEqual(unselected['status'], 'rejected')
        known = self.core((ReferenceResolutionOperation('entity', 'known', subject_id),)).accept(
            self.message(text=subject_id, idempotency_key='known'))
        self.assertEqual(known['status'], 'completed')
        self.assertEqual(self.core().inspect_history(), before)
        unknown = self.core((ReferenceResolutionOperation('entity', 'known', 'invented'),)).accept(
            self.message(text='quello', idempotency_key='unknown'))
        self.assertEqual(unknown['status'], 'rejected')
        new = self.core((ReferenceResolutionOperation('entity', 'new', candidate_ids=('candidate',)),)).accept(
            self.message(text='un altro', idempotency_key='new'))
        self.assertEqual(new['status'], 'rejected')
        ambiguous = self.core((ReferenceResolutionOperation('entity', 'ambiguous'),)).accept(
            self.message(text='quale?', idempotency_key='ambiguous'))
        self.assertEqual(ambiguous['status'], 'completed')
        created = self.core((
            ReferenceResolutionOperation('entity', 'new', candidate_ids=('new_subject',)),
            EntityOperation('new_subject', 'Subject'),
            GroundingPlanOperation('p', (), 'explicit', ('new_subject',)),
        )).accept(self.message(text='un altro', idempotency_key='grounded-new'))
        self.assertEqual(created['status'], 'completed')
        self.assertEqual(self.core().inspect(created['communication_id'])['grounding_items'][0]['outcome'], 'pending')
        self.assertEqual(self.core().inspect_history(), before)

    def test_explicit_forms_support_targeted_delayed_and_mixed_items(self):
        self.contract = replace(self.contract, grounding_policies={
            'p': {'acceptance': ['explicit'], 'confirmation_forms': ['Confermo']},
        })
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        delayed = self.core(
            lambda inbound: (GroundingResolutionOperation(
                subject['id'], 'p', inbound.id, 'explicit', 'accepted', evidence_span='subject Confermo',
            ),), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='subject Confermo'))
        self.assertEqual(delayed['status'], 'completed')

        self.config = replace(self.config, database=Path(self.directory.name) / 'wrong-explicit.sqlite3')
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        wrong_form = self.core(
            lambda inbound: (GroundingResolutionOperation(
                subject['id'], 'p', inbound.id, 'explicit', 'accepted', evidence_span='subject yes',
            ),), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='subject yes'))
        self.assertEqual(wrong_form['status'], 'rejected')

        self.config = replace(self.config, database=Path(self.directory.name) / 'mixed-explicit.sqlite3')
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        mixed = self.core(
            lambda inbound: (
                GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted',
                                            evidence_span='subject Confermo'),
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'rejected',
                                            evidence_span='blue no'),
            ), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='subject Confermo; blue no', reply_to=exposed['outbound']['id']))
        self.assertEqual(mixed['status'], 'completed')
        self.assertEqual({item['outcome'] for item in self.core().inspect_history()['grounding_items']},
                         {'accepted', 'rejected'})

    def test_unrelated_italian_elision_does_not_poison_a_supported_sibling(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        accepted = self.core(
            lambda inbound: (GroundingResolutionOperation(
                subject['id'], 'p', inbound.id, 'implicit', 'accepted',
                rationale='subject sì', evidence_span='subject sì', confidence=.99,
            ),), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text="l'ho visto; un' altra domanda? dell'altro; subject sì",
                              reply_to=exposed['outbound']['id']))
        self.assertEqual(accepted['status'], 'completed')

    def test_external_semantic_references_must_be_selected_from_the_context_pack(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        self.assertEqual(self.core(
            lambda inbound: (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),),
            intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        subject_id = self.core().inspect_history()['entities'][0]['id']

        unselected = self.core((EntityOperation(subject_id, 'Subject', action='resolve'),)).accept(
            self.message(text='ordinary reference', idempotency_key='unselected-resolve'))
        self.assertEqual(unselected['status'], 'rejected')
        selected = self.core((EntityOperation(subject_id, 'Subject', action='resolve'),)).accept(
            self.message(text=subject_id, idempotency_key='selected-resolve'))
        self.assertEqual(selected['status'], 'completed')
        claim = self.core((
            ClaimOperation('updated-note', subject_id, 'note', 'green', 'p'),
            GroundingPlanOperation('p', ('updated-note',), 'explicit'),
        )).accept(self.message(text=subject_id, idempotency_key='selected-claim'))
        self.assertEqual(claim['status'], 'completed')

        self.config = replace(self.config, database=Path(self.directory.name) / 'context-scope.sqlite3')
        core = self.core((
            EntityOperation('subject', 'Subject'),
            ContextOperation('context', ('subject',)),
            GroundingPlanOperation('p', (), 'explicit', ('subject', 'context')),
        ))
        staged = core.accept(self.message(text='candidate', idempotency_key='stage-context'))
        pending = core.inspect(staged['communication_id'])['grounding_items']
        committed = self.core(
            lambda inbound: tuple(GroundingResolutionOperation(
                item['id'], 'p', inbound.id, 'explicit', 'accepted'
            ) for item in pending), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(reply_to=core.inspect(staged['communication_id'])['outbound']['id'],
                              idempotency_key='commit-context'))
        self.assertEqual(committed['status'], 'completed')
        context_id = self.core().inspect_history()['contexts'][0]['id']
        resolved_context = self.core((ContextOperation(context_id, action='resolve'),)).accept(
            self.message(text=context_id, idempotency_key='selected-context'))
        self.assertEqual(resolved_context['status'], 'completed')

    def test_new_reference_rejects_existing_resolution_action(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        self.assertEqual(self.core(
            lambda inbound: (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),),
            intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        subject_id = self.core().inspect_history()['entities'][0]['id']
        rejected = self.core((
            ReferenceResolutionOperation('entity', 'new', candidate_ids=(subject_id,)),
            EntityOperation(subject_id, 'Subject', action='resolve'),
            GroundingPlanOperation('p', (), 'explicit', (subject_id,)),
        )).accept(self.message(text=subject_id, idempotency_key='new-resolve'))
        self.assertEqual(rejected['status'], 'rejected')

    def test_optional_decision_evidence_is_snapshot_safe_and_channel_requires_true(self):
        rejected = self.core((EntityOperation('x', 'Forbidden'),), decision_raw=object()).accept(
            self.message(text='ordinary', idempotency_key='bad-raw-rejected'))
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(self.core().inspect(rejected['communication_id'])['trace']['grounding_decision']['capture'],
                         'unserializable')
        retryable = self.core((), decision_raw=float('nan')).accept(
            self.message(text='ordinary', idempotency_key='bad-raw-accepted'))
        self.assertEqual(retryable['status'], 'retryable')
        deep = []
        for _ in range(2000):
            deep = [deep]
        deep_result = self.core((), decision_raw=deep).accept(
            self.message(text='ordinary', idempotency_key='deep-raw'))
        self.assertEqual(deep_result['status'], 'retryable')
        self.assertEqual(self.core().inspect(deep_result['communication_id'])['trace']['grounding_decision']['capture'],
                         'unserializable')
        class MalformedChannel:
            def __init__(channel, receipt):
                channel.receipt = receipt

            def deliver(channel, outbound):
                return channel.receipt

        for receipt in ('accepted', None):
            core = Core(self.config, CapturedModel((
                EntityOperation('subject', 'Subject'),
                GroundingPlanOperation('p', (), 'explicit', ('subject',)),
            )), channel=MalformedChannel(receipt), contract_provider=ContractProvider(self.contract))
            result = core.accept(self.message(text='candidate', idempotency_key=f'receipt-{receipt}'))
            self.assertEqual(result['status'], 'retryable')
            self.assertEqual(core.inspect(result['communication_id'])['grounding_items'], [])

    def test_delayed_unique_pending_evidence_survives_restart_but_ambiguous_stays_untrusted(self):
        exposed = self.expose()
        item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        packs = []
        class DelayedModel:
            def propose(model, inbound, version, pack):
                packs.append(pack)
                return SemanticProposal(1, version, inbound.id, (), 'Interpretazione', (
                    GroundingResolutionOperation(item['id'], 'p', inbound.id, 'implicit', 'accepted',
                                                rationale='subject sì', evidence_span='subject sì'),
                ), intent='semantic_commit', semantic_revision=0)
        restarted = Core(self.config, DelayedModel(), contract_provider=ContractProvider(self.contract))
        delayed = restarted.accept(self.message(text='subject sì', thread_id='later-session'))
        self.assertEqual(delayed['status'], 'completed')
        pending = [record for record in packs[0].records if record.kind == 'pending_grounding']
        self.assertTrue(any(record.payload_json for record in pending))
        self.assertEqual(restarted.inspect_history()['entities'][0]['entity_type'], 'Subject')

        self.config = replace(self.config, database=Path(self.directory.name) / 'ambiguous.sqlite3')
        first = self.expose()
        second = self.expose()
        item = first['grounding_items'][0]
        ambiguous = self.core(
            lambda inbound: (GroundingResolutionOperation(item['id'], 'p', inbound.id, 'implicit', 'accepted',
                                                          evidence_span='subject sì'),),
            intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='subject sì'))
        self.assertEqual(ambiguous['status'], 'rejected')
        self.assertEqual(self.core().inspect_history()['revision'], 0)
        self.assertEqual(len(second['grounding_items']), 2)

    def test_selected_canonical_and_emergent_concepts_are_known_references(self):
        canonical = self.core((ReferenceResolutionOperation('concept', 'known', 'canonical:note',
                                                             purpose='grounding'),)).accept(
            self.message(text='note', idempotency_key='canonical'))
        self.assertEqual(canonical['status'], 'completed')
        self.contract = replace(self.contract, emergent_concepts={
            'allowed': True, 'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'p',
        })
        self.core((EmergentConceptOperation('preference', 'A preference'),)).accept(
            self.message(text='preference', idempotency_key='emergent-declaration'))
        packs = []
        class ObservedConceptModel:
            def propose(model, inbound, version, pack):
                packs.append(pack)
                concept_id = next(record.id for record in pack.records
                                  if record.kind == 'emergent_concept')
                return SemanticProposal(1, version, inbound.id, (), 'Interpretazione', (
                    ReferenceResolutionOperation('concept', 'known', concept_id, purpose='grounding'),
                ))
        emergent = Core(self.config, ObservedConceptModel(),
                        contract_provider=ContractProvider(self.contract)).accept(
            self.message(text='preference', idempotency_key='emergent'))
        self.assertEqual(emergent['status'], 'completed')
        self.assertEqual(packs[0].actor_id, 'a1')

    def test_new_emergent_concept_uses_a_grounded_candidate_without_promotion(self):
        self.contract = replace(self.contract, emergent_concepts={
            'allowed': True, 'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'p',
        })
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        self.assertEqual(self.core(
            lambda inbound: (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),),
            intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        subject_id = self.core().inspect_history()['entities'][0]['id']
        created = self.core((
            ReferenceResolutionOperation('concept', 'new', candidate_ids=('style_claim',)),
            EmergentConceptOperation('style', 'A non-authoritative style term'),
            ClaimOperation('style_claim', subject_id, 'style', 'warm', 'p'),
            GroundingPlanOperation('p', ('style_claim',), 'implicit'),
        )).accept(self.message(text=f'stile caldo {subject_id}', idempotency_key='new-concept'))
        self.assertEqual(created['status'], 'completed')
        self.assertEqual(self.core().inspect(created['communication_id'])['grounding_items'][0]['outcome'], 'pending')
        self.assertNotIn('style', self.contract.claim_concepts)

    def test_implicit_mixed_italian_outcomes_are_independent(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        def mixed(inbound):
            return (
                GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'implicit', 'accepted',
                                            rationale='subject sì', evidence_span='subject sì'),
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'implicit', 'corrected',
                                            evidence_span='blue no green', successor_ids=('green_note',)),
                ClaimOperation('green_note', 'subject', 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('green_note',), 'implicit'),
            )
        result = self.core(mixed, intent='semantic_commit', semantic_revision=0).accept(
            self.message(text='subject sì; blue no green', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'completed')
        turn = self.core().inspect(result['communication_id'])
        self.assertEqual(turn['grounding_items'][0]['candidate_id'], 'green_note')
        history = self.core().inspect_history()
        self.assertEqual({item['outcome'] for item in history['grounding_items']}, {'accepted', 'corrected'})
        self.assertEqual(history['claims'], [])
        successor = turn['grounding_items'][0]
        accepted = self.core(
            lambda inbound: (GroundingResolutionOperation(successor['id'], 'p', inbound.id, 'explicit', 'accepted'),),
            intent='semantic_commit', semantic_revision=1,
        ).accept(self.message(reply_to=turn['outbound']['id']))
        self.assertEqual(accepted['status'], 'completed')
        claim = self.core().inspect_history()['claims'][0]
        self.assertEqual(claim['target_id'], self.core().inspect_history()['entities'][0]['id'])

    def test_trace_retains_captured_decision_metadata_and_model_error_fallback(self):
        traced = self.core((), decision_provider='captured', decision_model='fixture-v1',
                           decision_question_version='grounding-v2',
                           decision_raw={'outcome': 'abstained'}, decision_fallback='pending').accept(
            self.message(text='nessuna decisione'))
        trace = self.core().inspect(traced['communication_id'])['trace']['grounding_decision']
        self.assertEqual(trace['provider'], 'captured')
        self.assertEqual(trace['question_version'], 'grounding-v2')
        self.assertEqual(trace['raw'], {'outcome': 'abstained'})
        self.assertEqual(trace['fallback'], 'pending')
        class FailingModel:
            def propose(model, inbound, version, pack):
                raise OSError('captured outage')
        failed_core = Core(self.config, FailingModel(), contract_provider=ContractProvider(self.contract))
        failed = failed_core.accept(self.message(text='errore'))
        self.assertEqual(failed['status'], 'retryable')
        self.assertEqual(failed_core.inspect(failed['communication_id'])['trace']['grounding_decision']['fallback'],
                         'model_error')

    def test_pending_resolution_has_no_semantic_effect(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        pending = self.core(
            lambda inbound: (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'implicit', 'pending'),),
            intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='non so', reply_to=exposed['outbound']['id']))
        self.assertEqual(pending['status'], 'completed')
        history = self.core().inspect_history()
        self.assertEqual(history['revision'], 0)
        self.assertEqual(history['events'], [])
        self.assertEqual(self.core().inspect(exposed['inbound']['id'])['grounding_items'][0]['outcome'], 'pending')

    def test_corrected_claim_can_retain_a_pending_entity_target_until_later_acceptance(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')

        correction = self.core(
            lambda inbound: (
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='blue no green', successor_ids=('green_note',)),
                ClaimOperation('green_note', 'subject', 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('green_note',), 'explicit'),
            ), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='blue no green', reply_to=exposed['outbound']['id']))
        self.assertEqual(correction['status'], 'completed')
        successor_turn = self.core().inspect(correction['communication_id'])
        successor = successor_turn['grounding_items'][0]

        restarted = Core(
            self.config,
            CapturedModel(
                lambda inbound: (GroundingResolutionOperation(
                    subject['id'], 'p', inbound.id, 'explicit', 'accepted',
                ),),
                intent='semantic_commit', semantic_revision=1,
            ),
            contract_provider=ContractProvider(self.contract),
        )
        self.assertEqual(restarted.accept(
            self.message(reply_to=exposed['outbound']['id'])
        )['status'], 'completed')
        accepted = Core(
            self.config,
            CapturedModel(
                lambda inbound: (GroundingResolutionOperation(
                    successor['id'], 'p', inbound.id, 'explicit', 'accepted',
                ),),
                intent='semantic_commit', semantic_revision=2,
            ),
            contract_provider=ContractProvider(self.contract),
        ).accept(self.message(reply_to=successor_turn['outbound']['id']))
        self.assertEqual(accepted['status'], 'completed')
        history = self.core().inspect_history()
        self.assertEqual(history['claims'][0]['target_id'], history['entities'][0]['id'])

    def test_pending_target_binding_cannot_use_another_groundings_local_id(self):
        first = self.expose()
        second_core = self.core((
            EntityOperation('subject', 'Subject'),
            GroundingPlanOperation('p', (), 'explicit', ('subject',)),
        ))
        second_result = second_core.accept(self.message(text='another subject'))
        second = second_core.inspect(second_result['communication_id'])
        first_note = next(item for item in first['grounding_items'] if item['candidate_id'] == 'note')
        second_subject = next(item for item in second['grounding_items'] if item['candidate_id'] == 'subject')
        correction = self.core(
            lambda inbound: (
                GroundingResolutionOperation(first_note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='blue no green', successor_ids=('green_note',)),
                ClaimOperation('green_note', 'subject', 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('green_note',), 'explicit'),
            ), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='blue no green', reply_to=first['outbound']['id']))
        self.assertEqual(correction['status'], 'completed')
        successor_turn = self.core().inspect(correction['communication_id'])
        successor = successor_turn['grounding_items'][0]

        wrong_root = self.core(
            lambda inbound: (
                GroundingResolutionOperation(second_subject['id'], 'p', inbound.id, 'explicit', 'accepted',
                                            evidence_span='subject yes'),
                GroundingResolutionOperation(successor['id'], 'p', inbound.id, 'explicit', 'accepted',
                                            evidence_span='green yes'),
            ), intent='semantic_commit', semantic_revision=1,
        ).accept(self.message(text='subject yes; green yes', reply_to=successor_turn['outbound']['id']))
        self.assertEqual(wrong_root['status'], 'rejected')
        self.assertEqual(self.core().inspect_history()['entities'], [])

    def test_corrected_claim_and_pending_entity_can_be_accepted_together(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        correction = self.core(
            lambda inbound: (
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='blue no green', successor_ids=('green_note',)),
                ClaimOperation('green_note', 'subject', 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('green_note',), 'explicit'),
            ), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='blue no green', reply_to=exposed['outbound']['id']))
        self.assertEqual(correction['status'], 'completed')
        successor_turn = self.core().inspect(correction['communication_id'])
        successor = successor_turn['grounding_items'][0]
        accepted = self.core(
            lambda inbound: (
                GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted',
                                            evidence_span='subject yes'),
                GroundingResolutionOperation(successor['id'], 'p', inbound.id, 'explicit', 'accepted',
                                            evidence_span='green yes'),
            ), intent='semantic_commit', semantic_revision=1,
        ).accept(self.message(text='subject yes; green yes', reply_to=successor_turn['outbound']['id']))
        self.assertEqual(accepted['status'], 'completed')
        history = self.core().inspect_history()
        self.assertEqual(history['claims'][0]['target_id'], history['entities'][0]['id'])

    def test_correction_chain_retains_the_original_pending_target_binding(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        green = self.core(
            lambda inbound: (
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='blue no green', successor_ids=('green_note',)),
                ClaimOperation('green_note', 'subject', 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('green_note',), 'explicit'),
            ), intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(text='blue no green', reply_to=exposed['outbound']['id']))
        green_turn = self.core().inspect(green['communication_id'])
        green_item = green_turn['grounding_items'][0]
        red = self.core(
            lambda inbound: (
                GroundingResolutionOperation(green_item['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='green no red', successor_ids=('red_note',)),
                ClaimOperation('red_note', 'subject', 'note', 'red', 'p'),
                GroundingPlanOperation('p', ('red_note',), 'explicit'),
            ), intent='semantic_commit', semantic_revision=1,
        ).accept(self.message(text='green no red', reply_to=green_turn['outbound']['id']))
        self.assertEqual(red['status'], 'completed')
        red_turn = self.core().inspect(red['communication_id'])
        red_item = red_turn['grounding_items'][0]
        self.assertEqual(self.core(
            lambda inbound: (GroundingResolutionOperation(
                subject['id'], 'p', inbound.id, 'explicit', 'accepted',
            ),), intent='semantic_commit', semantic_revision=2,
        ).accept(self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        self.assertEqual(self.core(
            lambda inbound: (GroundingResolutionOperation(
                red_item['id'], 'p', inbound.id, 'explicit', 'accepted',
            ),), intent='semantic_commit', semantic_revision=3,
        ).accept(self.message(reply_to=red_turn['outbound']['id']))['status'], 'completed')
        self.assertEqual(self.core().inspect_history()['claims'][0]['value'], 'red')

    def test_multi_successor_source_revalidation_keeps_each_pending_target_bound(self):
        for mode in ('subset', 'joint', 'corrected'):
            with self.subTest(mode=mode):
                self.config = replace(self.config, database=Path(self.directory.name) / f'multi-{mode}.sqlite3')
                exposed = self.core((
                    EntityOperation('first_subject', 'Subject'),
                    EntityOperation('second_subject', 'Subject'),
                    ClaimOperation('first_note', 'first_subject', 'note', 'blue', 'p'),
                    ClaimOperation('second_note', 'second_subject', 'note', 'yellow', 'p'),
                    GroundingPlanOperation('p', ('first_note', 'second_note'), 'explicit',
                                           ('first_subject', 'second_subject')),
                )).accept(self.message(text='two subjects'))
                first_turn = self.core().inspect(exposed['communication_id'])
                first_items = {item['candidate_id']: item for item in first_turn['grounding_items']}
                corrected = self.core(
                    lambda inbound: (
                        GroundingResolutionOperation(first_items['first_note']['id'], 'p', inbound.id,
                                                    'explicit', 'corrected', evidence_span='blue no green',
                                                    successor_ids=('green_note',)),
                        GroundingResolutionOperation(first_items['second_note']['id'], 'p', inbound.id,
                                                    'explicit', 'corrected', evidence_span='yellow no red',
                                                    successor_ids=('red_note',)),
                        ClaimOperation('green_note', 'first_subject', 'note', 'green', 'p'),
                        ClaimOperation('red_note', 'second_subject', 'note', 'red', 'p'),
                        GroundingPlanOperation('p', ('green_note', 'red_note'), 'explicit'),
                    ), intent='semantic_commit', semantic_revision=0,
                ).accept(self.message(text='blue no green; yellow no red', reply_to=first_turn['outbound']['id']))
                self.assertEqual(corrected['status'], 'completed')
                successor_turn = self.core().inspect(corrected['communication_id'])
                successors = {item['candidate_id']: item for item in successor_turn['grounding_items']}
                accepted_entities = self.core(
                    lambda inbound: (
                        GroundingResolutionOperation(first_items['first_subject']['id'], 'p', inbound.id,
                                                    'explicit', 'accepted'),
                        GroundingResolutionOperation(first_items['second_subject']['id'], 'p', inbound.id,
                                                    'explicit', 'accepted'),
                    ), intent='semantic_commit', semantic_revision=1,
                ).accept(self.message(text='Yes', reply_to=first_turn['outbound']['id']))
                self.assertEqual(accepted_entities['status'], 'completed')
                entity_ids = [entity['id'] for entity in self.core().inspect_history()['entities']]

                if mode == 'corrected':
                    result = self.core(
                        lambda inbound: (
                            GroundingResolutionOperation(successors['green_note']['id'], 'p', inbound.id,
                                                        'explicit', 'corrected', evidence_span='green no teal',
                                                        successor_ids=('teal_note',)),
                            ClaimOperation('teal_note', 'first_subject', 'note', 'teal', 'p'),
                            GroundingPlanOperation('p', ('teal_note',), 'explicit'),
                        ), intent='semantic_commit', semantic_revision=2,
                    ).accept(self.message(text='green no teal', reply_to=successor_turn['outbound']['id']))
                    self.assertEqual(result['status'], 'completed')
                    follow_up = self.core().inspect(result['communication_id'])['grounding_items']
                    self.assertEqual(follow_up[0]['candidate_id'], 'teal_note')
                    self.assertEqual(self.core().inspect_history()['claims'], [])
                    continue

                resolved = ('green_note',) if mode == 'subset' else ('green_note', 'red_note')
                result = self.core(
                    lambda inbound: tuple(GroundingResolutionOperation(
                        successors[candidate_id]['id'], 'p', inbound.id, 'explicit', 'accepted',
                        evidence_span=f'{"green" if candidate_id == "green_note" else "red"} yes',
                    ) for candidate_id in resolved),
                    intent='semantic_commit', semantic_revision=2,
                ).accept(self.message(
                    text='green yes' if mode == 'subset' else 'green yes; red yes',
                    reply_to=successor_turn['outbound']['id'],
                ))
                self.assertEqual(result['status'], 'completed')
                claims = self.core().inspect_history()['claims']
                self.assertEqual(
                    {(claim['value'], claim['target_id']) for claim in claims},
                    {('green', entity_ids[0])} if mode == 'subset'
                    else {('green', entity_ids[0]), ('red', entity_ids[1])},
                )

    def test_correction_creates_an_exposed_successor_with_immutable_lineage(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        def accept_subject(inbound):
            return (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),)
        self.assertEqual(self.core(accept_subject, intent='semantic_commit', semantic_revision=0).accept(
            self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        subject_id = self.core().inspect_history()['entities'][0]['id']
        def correct(inbound):
            return (
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='blue no, green', successor_ids=('corrected_note',)),
                ClaimOperation('corrected_note', subject_id, 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('corrected_note',), 'explicit'),
            )
        correction = self.core(correct, intent='semantic_commit', semantic_revision=1).accept(
            self.message(text=f'blue no, green; {subject_id}', reply_to=exposed['outbound']['id']))
        self.assertEqual(correction['status'], 'completed')
        correction_turn = self.core().inspect(correction['communication_id'])
        successor = correction_turn['grounding_items'][0]
        self.assertEqual(successor['outcome'], 'pending')
        def accept_successor(inbound):
            return (GroundingResolutionOperation(successor['id'], 'p', inbound.id, 'explicit', 'accepted'),)
        completed = self.core(accept_successor, intent='semantic_commit', semantic_revision=2).accept(
            self.message(reply_to=correction_turn['outbound']['id']))
        self.assertEqual(completed['status'], 'completed')
        history = self.core().inspect_history()
        self.assertEqual(history['claims'][0]['value'], 'green')
        claim = history['claims'][0]
        self.assertEqual(claim['provenance']['corrected_grounding_item_id'], note['id'])
        correction_item = next(record for record in history['grounding_items'] if record['id'] == note['id'])
        self.assertEqual(correction_item['outcome'], 'corrected')
        self.assertEqual(correction_item['successor_candidate_ids'], ['corrected_note'])

    def test_correction_successor_is_not_exposed_before_accepted_retry(self):
        exposed = self.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        self.assertEqual(self.core(
            lambda inbound: (GroundingResolutionOperation(subject['id'], 'p', inbound.id, 'explicit', 'accepted'),),
            intent='semantic_commit', semantic_revision=0,
        ).accept(self.message(reply_to=exposed['outbound']['id']))['status'], 'completed')
        subject_id = self.core().inspect_history()['entities'][0]['id']
        def correction(inbound):
            return (
                GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                            evidence_span='blue no green', successor_ids=('green_note',)),
                ClaimOperation('green_note', subject_id, 'note', 'green', 'p'),
                GroundingPlanOperation('p', ('green_note',), 'explicit'),
            )
        class RefusingChannel:
            def deliver(channel, outbound):
                return False
        core = Core(self.config, CapturedModel(correction, intent='semantic_commit', semantic_revision=1),
                    channel=RefusingChannel(), contract_provider=ContractProvider(self.contract))
        payload = self.message(text=f'blue no green; {subject_id}', reply_to=exposed['outbound']['id'])
        failed = core.accept(payload)
        self.assertEqual(failed['status'], 'retryable')
        self.assertEqual(core.inspect(failed['communication_id'])['grounding_items'], [])
        self.assertEqual(core.inspect_history()['revision'], 2)
        class MustNotRun:
            def propose(model, inbound, version, pack):
                raise AssertionError('correction retry must not reinfer')
        restarted = Core(self.config, MustNotRun(), contract_provider=ContractProvider(self.contract))
        retried = restarted.accept(payload)
        self.assertEqual(retried['status'], 'completed')
        successor = restarted.inspect(retried['communication_id'])['grounding_items']
        self.assertEqual(len(successor), 1)
        self.assertEqual(successor[0]['candidate_id'], 'green_note')

    def test_explicit_rejection_is_a_durable_independent_grounding_outcome(self):
        exposed = self.expose()
        item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        def reject(inbound):
            return (GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'rejected',
                                                evidence_span='blue no'),)
        result = self.core(reject, intent='semantic_commit', semantic_revision=0).accept(
            self.message(text='blue no', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'completed')
        history = self.core().inspect_history()
        rejected = next(record for record in history['grounding_items'] if record['id'] == item['id'])
        self.assertEqual(rejected['outcome'], 'rejected')
        self.assertEqual(history['claims'], [])


if __name__ == '__main__':
    unittest.main()
