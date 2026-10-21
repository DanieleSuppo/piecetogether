"""Missing #31 combinations at the existing deterministic Core/replay seams."""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import test_context as fixtures
from piecetogether import context
from piecetogether.evaluation import ReplayCase, capture, replay
from piecetogether.proposals import (
    ClaimOperation, EmergentConceptOperation, EntityOperation, GroundingPlanOperation,
    GroundingResolutionOperation, ReferenceResolutionOperation,
)


DECISION = dict(decision_provider='captured', decision_model='decision-fixture-v1',
                decision_question_version='grounding-items-v1', decision_raw={'noul': .999})


class ScenarioCoverageTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.ContextTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        # Tripwires guard external effects/construction, never Core policy decisions.
        for target in ('http.client.HTTPSConnection', 'socket.create_connection',
                       'piecetogether.core.OpenAIResponsesModel', 'piecetogether.jev.JevSelector'):
            patcher = patch(target, side_effect=AssertionError('external inference forbidden'))
            tripwire = patcher.start()
            self.addCleanup(patcher.stop)
            self.addCleanup(tripwire.assert_not_called)

    def offline(self, case):
        # Exercise the serialized, versioned capture rather than sharing fixture adapters.
        restored = ReplayCase.from_dict(json.loads(json.dumps(case.to_dict())))
        with patch('piecetogether.core.DeterministicModel.propose',
                   side_effect=AssertionError('inference forbidden')) as inference:
            result = replay(restored)
        inference.assert_not_called()
        self.assertTrue(result.matched, result.mismatches)
        return result.actual

    def test_g1_wrong_confident_selection_retains_older_mandatory_items_and_replays(self):
        flow = self.flow
        older = flow.seed('terrazzo', commit=False)
        flow.seed('cucina')
        history = flow.seed('bagno')
        contexts = {entity['attributes']['label']: next(
            record['id'] for record in history['contexts'] if entity['id'] in record['entity_ids'])
            for entity in history['entities']}
        wrong = flow.seed('cantina')['claims'][-1]['id']
        foreign = flow.seed('privato', sender='s2')['entities'][-1]['id']
        mandatory = {item['id'] for item in older['grounding_items']}
        required = mandatory | {contexts['cucina'], contexts['bagno']}
        irrelevant = {wrong}

        class WrongSelector:
            def select(self, request):
                return context.SelectionResult('completed', (wrong, contexts['cucina']),
                    (context.Assessment(wrong, 'noul', .999),), 'captured', 'selector-fixture-v1',
                    request.rubric_version, raw_decisions={'wrong_candidate': wrong, 'noul': .999})

        flow.config = replace(flow.config, selection=context.SelectionConfig(max_records=len(mandatory) + 2))
        core = flow.core(fixtures.OperationsModel((), **DECISION), selector=WrongSelector())
        before = core.inspect_history(), core.current_view(), core.trusted_events()
        payload = flow.message(text='Torniamo alla cucina e al bagno, non alla cantina')
        case = capture(core, lambda recorder: recorder.accept(payload))
        for state in (case.expected, self.offline(case)):
            step = state['steps'][0]
            self.assertEqual(step['result']['status'], 'completed')
            pack = step['trace']['context_pack']
            selected = {record['id'] for record in pack['records']}
            catalogue = {record['id'] for record in pack['catalogue']}
            self.assertEqual(selected, mandatory | {wrong, contexts['cucina']})
            self.assertTrue(mandatory <= selected)
            self.assertTrue(required <= catalogue)
            self.assertEqual(required - selected, {contexts['bagno']})
            # Independent fixture labels: honest recall and wrong-selection counts.
            self.assertEqual((len(required & selected), len(required)), (4, 5))
            self.assertEqual((len(irrelevant & selected), len(selected)), (1, 5))
            self.assertNotIn(foreign, catalogue | selected)
            wrong_record = next(record for record in pack['records'] if record['id'] == wrong)
            self.assertEqual(wrong_record['disclosure_purposes'], [])
            self.assertNotIn('cantina segreto', step['result']['reply'])
            self.assertEqual((state['history'], state['view'], state['events']), before)
            self.assertEqual(step['grounding_items'], [])
        selection = case.steps[0]['selection'][0]
        self.assertEqual(selection['model'], 'selector-fixture-v1')
        self.assertEqual(selection['request']['rubric_version'], 'context-relevance-v1')
        self.assertEqual(selection['response']['assessments'][0]['value'], .999)
        self.assertIsNone(selection['fallback_reason'])
        proposal = case.steps[0]['generative_attempts'][0]['proposal']
        self.assertEqual(proposal['decision_model'], 'decision-fixture-v1')
        self.assertEqual(proposal['decision_question_version'], 'grounding-items-v1')
        self.assertEqual((core.inspect_history(), core.current_view(), core.trusted_events()), before)

    def test_g1_real_catalogue_compaction_separates_missing_required_records_and_replays(self):
        flow = self.flow
        older = flow.seed('terrazzo', commit=False)
        flow.seed('cucina')
        core = flow.core(fixtures.OperationsModel((), **DECISION))
        before = core.inspect_history(), core.current_view(), core.trusted_events()
        # Required identities come from real source state, before isolated capture.
        mandatory = {item['id'] for item in older['grounding_items']}
        required_contexts = {record['id'] for record in before[0]['contexts']}
        self.assertEqual(len(mandatory), 3)
        self.assertEqual(len(required_contexts), 1)
        required = mandatory | required_contexts

        class EmptySelector:
            def select(self, request):
                return context.SelectionResult('completed', (), (), 'captured',
                    'selector-fixture-v1', request.rubric_version)

        flow.config = replace(flow.config, selection=context.SelectionConfig(catalogue_records=len(mandatory)))
        core = flow.core(fixtures.OperationsModel((), **DECISION), selector=EmptySelector())
        case = capture(core, lambda recorder: recorder.accept(
            flow.message(text='Torniamo alla cucina, lasciando aperto il terrazzo')))
        for state in (case.expected, self.offline(case)):
            step = state['steps'][0]
            self.assertEqual(step['result']['status'], 'completed')
            pack = step['trace']['context_pack']
            self.assertIs(pack['catalogue_truncated'], True)
            self.assertEqual(pack['budget']['catalogue_records'], len(mandatory))
            catalogue = {record['id'] for record in pack['catalogue']}
            selected = {record['id'] for record in pack['records']}
            self.assertEqual(catalogue, mandatory)
            self.assertEqual(selected, mandatory)
            self.assertTrue(all(record['mandatory'] for record in pack['catalogue']))
            self.assertFalse(required_contexts & catalogue)
            required_available = required & catalogue
            missing = required - catalogue
            self.assertEqual(required_available, mandatory)
            self.assertEqual(missing, required_contexts)
            # Recall denominator excludes real source records lost to catalogue capacity.
            self.assertEqual((len(required_available & selected), len(required_available)), (3, 3))
            self.assertEqual(len(missing), 1)
            self.assertEqual(step['grounding_items'], [])
            self.assertEqual((state['history'], state['view'], state['events']), before)
        selection = case.steps[0]['selection'][0]
        self.assertEqual(selection['request']['candidates'], [])
        self.assertIsNone(selection['fallback_reason'])
        self.assertEqual((core.inspect_history(), core.current_view(), core.trusted_events()), before)

    def test_g2_missing_ambiguous_concepts_and_confidence_cannot_create_authority(self):
        flow = self.flow
        flow.seed('cucina')
        flow.seed('bagno')  # Same term, distinct legitimate per-source concept descriptions.
        core = flow.core()
        before = core.inspect_history(), core.current_view(), core.trusted_events(), core.inspect_concepts()
        self.assertEqual([concept['name'] for concept in before[3]], ['preferenza', 'preferenza'])
        cases = (
            ('missing', (ReferenceResolutionOperation('concept', 'known', 'concept:assente'),),
             'context_scope_not_allowed', {}),
            ('none', (ReferenceResolutionOperation('concept', 'none'),), None, {}),
            ('ambiguous', (ReferenceResolutionOperation('concept', 'ambiguous'),), None, {}),
            ('forced-ambiguous', (ReferenceResolutionOperation('concept', 'ambiguous', 'canonical:note'),),
             'invalid_reference_resolution', {}),
            ('invented-new', (ReferenceResolutionOperation('concept', 'new', candidate_ids=('assente',)),),
             'new_reference_requires_grounding', {}),
            ('promote', (EmergentConceptOperation('preferenza', 'Promuovi', 'authoritative'),),
             'emergent_authority_not_allowed', {}),
            ('contract', (EntityOperation('invented', 'Forbidden'),), 'entity_type_not_allowed', {}),
            ('identity', ({'kind': 'linkActor', 'sender': 's2', 'actor_id': 'a1'},),
             'unsupported_operation', {}),
            ('commit', lambda inbound: (GroundingResolutionOperation(
                'fabricated', 'p', inbound.id, 'explicit', 'accepted', confidence=.999),),
             'grounding_item_unavailable', {'intent': 'semantic_commit', 'semantic_revision': before[0]['revision']}),
        )
        for name, operations, reason, envelope in cases:
            with self.subTest(case=name):
                core = flow.core(fixtures.OperationsModel(operations, **DECISION, **envelope))
                payload = flow.message(text='Quella preferenza, non so quale opzione favorita', idempotency_key=name)
                result = core.accept(payload)
                self.assertEqual(result['status'], 'rejected' if reason else 'completed')
                inspected = core.inspect(result['communication_id'])
                self.assertEqual(inspected['trace']['validation_reasons'], [reason] if reason else [])
                self.assertEqual(inspected['trace']['grounding_decision']['raw'], {'noul': .999})
                self.assertEqual(sum(record['kind'] == 'emergent_concept'
                                     for record in inspected['trace']['context_pack']['records']), 2)
                self.assertEqual(inspected['grounding_items'], [])
                self.assertEqual((core.inspect_history(), core.current_view(), core.trusted_events(),
                                  core.inspect_concepts()), before)
                self.assertEqual(core.accept(payload), result)
                if reason:
                    self.assertIsNone(result['reply'])
                    self.assertIsNone(inspected['outbound'])
        self.assertNotIn('preferenza', flow.contract.claim_concepts)
        self.assertEqual(flow.config.identities, {'development:s1': 'a1', 'development:s2': 'a2'})

    def test_g3_italian_synonym_reuses_persisted_concept_through_grounding_and_view(self):
        flow = self.flow
        history = flow.seed('cucina')
        entity = history['entities'][0]['id']
        core = flow.core()  # Disposable provider; vocabulary comes from durable Core state.
        concepts = core.inspect_concepts()
        persisted = concepts[0]
        concept_id = 'concept:' + context.digest(context.encoded([
            persisted['name'], persisted['provenance']['source_communication_id']]))
        source_revision = history['revision']
        ids = {}

        def scenario(recorder):
            recorder.core.model = fixtures.OperationsModel((
                ClaimOperation('scelta', entity, 'preferenza', 'legno caldo', 'p'),
                GroundingPlanOperation('p', ('scelta',), 'explicit'),
            ), **{**DECISION, 'decision_raw': {'noul': .999, 'selected_concept_id': concept_id}})
            first = recorder.accept(flow.message(text=f"L'opzione favorita è il legno caldo per {entity}"))
            self.assertEqual(first['status'], 'completed')
            exposed = recorder.core.inspect(first['communication_id'])
            ids.update(source=first['communication_id'], exposure=exposed['outbound']['id'],
                       item=exposed['grounding_items'][0]['id'])
            selected = {record['id'] for record in exposed['trace']['context_pack']['records']}
            self.assertIn(concept_id, selected)
            self.assertEqual(recorder.core.inspect_history(), history)
            self.assertEqual(exposed['grounding_items'][0]['outcome'], 'pending')
            self.assertIn('legno caldo', first['reply'])
            recorder.restart()
            recorder.core.model = fixtures.OperationsModel(lambda inbound: (
                GroundingResolutionOperation(ids['item'], 'p', inbound.id, 'explicit', 'accepted', confidence=.999),
            ), intent='semantic_commit', semantic_revision=source_revision, **DECISION)
            accepted = recorder.accept(flow.message(text='Yes', reply_to=ids['exposure']))
            self.assertEqual(accepted['status'], 'completed')
            ids['evidence'] = accepted['communication_id']
            self.assertEqual(recorder.core.inspect_concepts(), concepts)

        case = capture(core, scenario)
        for state in (case.expected, self.offline(case)):
            self.assertEqual(state['history']['revision'], source_revision + 1)
            self.assertEqual(state['history']['claims'][:-1], history['claims'])
            claim = state['history']['claims'][-1]
            self.assertEqual((claim['target_id'], claim['concept'], claim['value']),
                             (entity, 'preferenza', 'legno caldo'))
            provenance = claim['provenance']
            self.assertEqual((provenance['source_communication_id'], provenance['exposure_communication_id'],
                              provenance['evidence_communication_id'], provenance['actor_id']),
                             (ids['source'], ids['exposure'], ids['evidence'], 'a1'))
            self.assertEqual(claim['contract_version'], flow.contract.version)
            group = next(group for group in state['view']['assertion_sets'] if group['concept'] == 'preferenza')
            self.assertEqual([head['id'] for head in group['heads']], [claim['id']])
            self.assertEqual(group['heads'][0]['provenance'], provenance)
            self.assertEqual(len(state['events']), len(history['events']) + 1)
            concept_record = next(record for record in state['steps'][0]['trace']['context_pack']['records']
                                  if record['id'] == concept_id)
            self.assertEqual(concept_record['kind'], 'emergent_concept')
            self.assertEqual(json.loads(concept_record['payload_json']), persisted)
            self.assertEqual(concept_record['disclosure_purposes'], [])
            self.assertNotIn('emergent_concept', {operation['kind'] for operation in state['steps'][0]['proposal']['operations']})
        self.assertEqual(persisted['status'], 'non_authoritative')
        self.assertNotIn('preferenza', flow.contract.claim_concepts)
        self.assertEqual(core.inspect_concepts(), concepts)
        self.assertEqual(core.inspect_history(), history)

    def test_g4_four_mixed_decisions_preserve_independent_transitions_retry_and_replay(self):
        flow = self.flow
        initial = flow.seed('cucina')
        entity = initial['entities'][0]['id']
        flow.contract = replace(flow.contract,
            grounding_policies={'p': {'acceptance': ['explicit', 'implicit']}},
            claim_concepts={**flow.contract.claim_concepts, **{
                name: {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'p'}
                for name in ('colore', 'misura', 'materiale', 'tempo')}})
        core = flow.core()
        ids = {}
        expected_outcomes = {'colore': 'accepted', 'misura': 'rejected',
                             'materiale': 'corrected', 'tempo': 'pending'}

        class Delivery:
            calls = 0
            def deliver(self, outbound):
                self.calls += 1
                return self.calls != 2  # Semantic commit succeeds; successor exposure needs retry.

        def scenario(recorder):
            recorder.core.model = fixtures.OperationsModel(tuple(
                ClaimOperation(name, entity, name, value, 'p') for name, value in
                (('colore', 'blu'), ('misura', 'grande'), ('materiale', 'legno'), ('tempo', 'domani'))
            ) + (GroundingPlanOperation('p', tuple(expected_outcomes), 'implicit'),), **DECISION)
            source = recorder.accept(flow.message(text=f'blu, grande, legno e domani per {entity}'))
            self.assertEqual(source['status'], 'completed')
            exposed = recorder.core.inspect(source['communication_id'])
            items = {item['candidate_id']: item['id'] for item in exposed['grounding_items']}
            ids.update(items=items, source=source['communication_id'], exposure=exposed['outbound']['id'])
            self.assertEqual(set(items), set(expected_outcomes))
            self.assertEqual(recorder.core.inspect_history(), initial)

            def mixed(inbound):
                return (
                    GroundingResolutionOperation(items['colore'], 'p', inbound.id, 'implicit', 'accepted',
                        rationale='blu sì', evidence_span='blu sì', confidence=.999),
                    GroundingResolutionOperation(items['misura'], 'p', inbound.id, 'implicit', 'rejected',
                        evidence_span='grande no', confidence=.999),
                    GroundingResolutionOperation(items['materiale'], 'p', inbound.id, 'implicit', 'corrected',
                        evidence_span='legno no metallo', successor_ids=('corretto',), confidence=.999),
                    GroundingResolutionOperation(items['tempo'], 'p', inbound.id, 'implicit', 'pending', confidence=.999),
                    ClaimOperation('corretto', entity, 'materiale', 'metallo', 'p'),
                    GroundingPlanOperation('p', ('corretto',), 'explicit'),
                )
            recorder.core.model = fixtures.OperationsModel(mixed, intent='semantic_commit',
                semantic_revision=initial['revision'], **DECISION)
            payload = flow.message(text='blu sì; grande no; legno no metallo; domani non so',
                                   reply_to=ids['exposure'])
            failed = recorder.accept(payload)
            self.assertEqual(failed['status'], 'retryable')
            ids['mixed'] = failed['communication_id']
            committed = recorder.core.inspect_history()
            self.assertEqual(committed['revision'], initial['revision'] + 1)
            self.assertEqual([(claim['concept'], claim['value']) for claim in committed['claims'][1:]],
                             [('colore', 'blu')])
            self.assertEqual(len(recorder.core.trusted_events()), len(initial['events']) + 1)
            observed = recorder.core.inspect(ids['source'])['grounding_items']
            self.assertEqual({item['candidate_id']: item['outcome'] for item in observed}, expected_outcomes)
            self.assertEqual(recorder.core.inspect(ids['mixed'])['grounding_items'], [])

            recorder.restart()
            recovered = recorder.accept(payload)
            self.assertEqual(recovered['status'], 'completed')
            self.assertEqual(recorder.core.inspect_history(), committed)
            correction = recorder.core.inspect(ids['mixed'])
            successor = correction['grounding_items'][0]
            self.assertEqual((successor['candidate_id'], successor['outcome']), ('corretto', 'pending'))
            self.assertIn('metallo', recovered['reply'])
            ids.update(successor=successor['id'], correction_exposure=correction['outbound']['id'])
            recorder.core.model = fixtures.OperationsModel(lambda inbound: (
                GroundingResolutionOperation(ids['successor'], 'p', inbound.id, 'explicit', 'accepted', confidence=.999),
            ), intent='semantic_commit', semantic_revision=committed['revision'], **DECISION)
            confirmation = flow.message(text='Yes', reply_to=ids['correction_exposure'])
            accepted = recorder.accept(confirmation)
            self.assertEqual(accepted['status'], 'completed')
            ids['evidence'] = accepted['communication_id']
            final = recorder.core.inspect_history(), recorder.core.current_view(), recorder.core.trusted_events()
            recorder.restart()
            self.assertEqual(recorder.accept(confirmation), accepted)
            self.assertEqual((recorder.core.inspect_history(), recorder.core.current_view(),
                              recorder.core.trusted_events()), final)

        case = capture(core, scenario, channel=Delivery())
        for index in (2, 4):
            self.assertEqual(case.steps[index]['generative_attempts'], [])
            self.assertEqual(case.steps[index]['selection'], [])
        self.assertEqual(case.steps[4]['channel'], [])
        proposal = case.steps[1]['generative_attempts'][0]['proposal']
        self.assertEqual(proposal['decision_question_version'], 'grounding-items-v1')
        self.assertEqual(proposal['decision_model'], 'decision-fixture-v1')
        self.assertEqual(proposal['decision_raw'], {'noul': .999})
        self.assertEqual([operation['confidence'] for operation in proposal['operations'][:4]], [.999] * 4)
        for state in (case.expected, self.offline(case)):
            self.assertEqual([step['result']['status'] for step in state['steps']],
                             ['completed', 'retryable', 'completed', 'completed', 'completed'])
            self.assertEqual(state['steps'][1]['trace']['commit_result'], 'committed')
            history = state['history']
            self.assertEqual(history['revision'], initial['revision'] + 2)
            self.assertEqual(history['claims'][:1], initial['claims'])
            claims = history['claims'][1:]
            self.assertEqual([(claim['concept'], claim['value']) for claim in claims],
                             [('colore', 'blu'), ('materiale', 'metallo')])
            self.assertEqual(len(history['commits']), len(initial['commits']) + 2)
            resolved = {item['id']: item for item in history['grounding_items']}
            for name, outcome in expected_outcomes.items():
                if outcome == 'pending':
                    self.assertNotIn(ids['items'][name], resolved)
                else:
                    self.assertEqual(resolved[ids['items'][name]]['outcome'], outcome)
            self.assertEqual(resolved[ids['items']['materiale']]['successor_candidate_ids'], ['corretto'])
            for claim, source, exposure, evidence in (
                (claims[0], ids['source'], ids['exposure'], ids['mixed']),
                (claims[1], ids['mixed'], ids['correction_exposure'], ids['evidence']),
            ):
                provenance = claim['provenance']
                self.assertEqual((provenance['source_communication_id'], provenance['exposure_communication_id'],
                                  provenance['evidence_communication_id'], provenance['actor_id']),
                                 (source, exposure, evidence, 'a1'))
                self.assertEqual(claim['target_id'], entity)
                self.assertEqual(claim['contract_version'], flow.contract.version)
            self.assertEqual(claims[1]['provenance']['corrected_grounding_item_id'], ids['items']['materiale'])
            groups = [group for group in state['view']['assertion_sets'] if group['concept'] != 'note']
            self.assertEqual({(group['concept'], head['value']) for group in groups for head in group['heads']},
                             {('colore', 'blu'), ('materiale', 'metallo')})
            self.assertEqual(len(state['events']), len(initial['events']) + 2)
            self.assertEqual([event['semantic_revision'] for event in state['events']], [1, 2, 3])
            self.assertEqual(len({event['id'] for event in state['events']}), 3)
            self.assertEqual(state['events'][1]['assertion_ids'], [claims[0]['id']])
            self.assertEqual(state['events'][2]['assertion_ids'], [claims[1]['id']])
            self.assertEqual(set(state['events'][1]['grounding_item_ids']),
                             {ids['items'][name] for name in ('colore', 'misura', 'materiale')})
            self.assertEqual(state['events'][2]['grounding_item_ids'], [ids['successor']])
            pending = next(record for record in state['steps'][3]['trace']['context_pack']['records']
                           if record['id'] == ids['items']['tempo'])
            self.assertEqual(pending['disclosure_purposes'], [])
            self.assertNotIn('domani', state['steps'][3]['result']['reply'])
            trusted = json.dumps([history, state['view'], state['events']])
            for metadata in ('decision_raw', 'decision-fixture-v1', 'noul', 'confidence'):
                self.assertNotIn(metadata, trusted)
        self.assertEqual(core.inspect_history(), initial)


if __name__ == '__main__':
    unittest.main()
