import tempfile
import unittest
import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import SemanticProposal
from piecetogether.contracts import DomainContract
from piecetogether.proposals import (
    ArtifactOperation, ContextOperation, EmergentConceptOperation, EntityOperation,
    GroundingPlanOperation, GroundingResolutionOperation, ClaimOperation,
)


class ContractProvider:
    def __init__(self, contract):
        self.contract = contract

    def get(self, version):
        return self.contract


class OperationsModel:
    def __init__(self, operations, **envelope):
        self.operations = operations
        self.envelope = envelope

    def propose(self, inbound, version, context_pack):
        operations = self.operations(inbound) if callable(self.operations) else self.operations
        return SemanticProposal(1, version, inbound.id, (), 'A current interpretation',
                                operations, **self.envelope)


class PackModel:
    def __init__(self):
        self.packs = []

    def propose(self, inbound, version, context_pack):
        self.packs.append(context_pack)
        return SemanticProposal(1, version, inbound.id, (), 'A current interpretation')


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Bootstrap(Path(self.directory.name) / 'core.sqlite3',
                                {'development:s1': 'a1', 'development:s2': 'a2'})
        self.sequence = 0
        self.contract = DomainContract(
            'development-v1',
            entity_types={'Subject': {'creation': True, 'attributes': {'label': {'type': 'string'}}}},
            claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'},
                                     'grounding_policy': 'p'}},
            grounding_policies={'p': {'acceptance': ['explicit']}},
            emergent_concepts={'allowed': True, 'target_types': ['Subject'],
                              'value': {'type': 'string'}, 'grounding_policy': 'p'},
            artifact_types={'Evidence': {'roles': ['source-evidence'], 'persistence': 'forbidden',
                            'retention': {'metadata': 'retain', 'bytes': 'delete', 'provenance': 'retain'},
                            'supersession': False}},
        )

    def message(self, **changes):
        self.sequence += 1
        return {'channel': 'development', 'sender': 's1',
                'idempotency_key': f'm{self.sequence}', 'text': 'Una nuova interpretazione',
                'sent_at': '2026-10-06T12:00:00Z', **changes}

    def core(self, model=None, **kwargs):
        return Core(self.config, model=model or PackModel(),
                    contract_provider=ContractProvider(self.contract), **kwargs)

    def seed(self, label='cucina', sender='s1', commit=True):
        model = OperationsModel((
            EntityOperation('subject', 'Subject', {'label': label}),
            ContextOperation('context', ('subject',)),
            ClaimOperation('note', 'subject', 'note', f'{label} segreto', 'p'),
            GroundingPlanOperation('p', ('note',), 'explicit', ('subject', 'context')),
            EmergentConceptOperation('preferenza', 'opzione favorita'),
            ArtifactOperation('evidence', 'Evidence', ('source-evidence',)),
        ))
        core = self.core(model)
        result = core.accept(self.message(text=label, sender=sender))
        self.assertEqual(result['status'], 'completed')
        exposed = core.inspect(result['communication_id'])
        if not commit:
            return exposed
        revision = core.inspect_history()['revision']
        def accept(inbound):
            return tuple(GroundingResolutionOperation(item['id'], 'p', inbound.id,
                         'explicit', 'accepted') for item in exposed['grounding_items'])
        core = self.core(OperationsModel(accept, intent='semantic_commit', semantic_revision=revision))
        result = core.accept(self.message(text='Yes', sender=sender, reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'completed')
        return core.inspect_history()

    def test_model_receives_core_owned_pack_and_trace_retains_its_revision(self):
        model = PackModel()
        core = Core(self.config, model=model)
        result = core.accept(self.message())
        self.assertEqual(result['status'], 'completed')
        pack = model.packs[0]
        self.assertEqual(pack.actor_id, 'a1')
        self.assertEqual(pack.semantic_revision, 0)
        self.assertEqual(pack.contract_version, 'development-v1')
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(trace['context_pack']['semantic_revision'], 0)
        self.assertEqual(trace['context_pack']['outcome'], 'completed')
        self.assertNotIn('context_pack', result)

    def test_multi_context_catalogue_is_actor_scoped_and_retains_concepts_and_artifacts(self):
        self.seed('cucina')
        self.seed('bagno')
        foreign = self.seed('privato', sender='s2')
        model = PackModel()
        core = self.core(model)
        result = core.accept(self.message(text='cucina e bagno, opzione favorita'))
        self.assertEqual(result['status'], 'completed')
        records = model.packs[0].records
        self.assertEqual(sum(record.kind == 'context' for record in records), 2)
        self.assertIn('emergent_concept', {record.kind for record in records})
        self.assertIn('artifact', {record.kind for record in records})
        foreign_ids = {record['id'] for record in foreign['entities']
                       if record['provenance']['actor_id'] == 'a2'}
        self.assertFalse(foreign_ids & {record.id for record in records})
        self.assertTrue(all(record.revision for record in records))

    def test_mandatory_pending_grounding_survives_empty_ranking_and_has_explicit_budget_outcome(self):
        from piecetogether.context import SelectionConfig, SelectionResult
        exposed = self.seed(commit=False)
        class EmptySelector:
            def select(self, request):
                return SelectionResult('completed', (), (), 'fixture', 'fixture-v1', request.rubric_version)
        model = PackModel()
        core = self.core(model, selector=EmptySelector())
        result = core.accept(self.message(text='continuiamo', reply_to=exposed['outbound']['id']))
        self.assertEqual(result['status'], 'completed')
        pending_ids = {item['id'] for item in exposed['grounding_items']}
        self.assertTrue(pending_ids <= {record.id for record in model.packs[0].records})
        self.config = replace(self.config, selection=SelectionConfig(max_records=1))
        model = PackModel()
        exhausted = self.core(model, selector=EmptySelector()).accept(self.message(text='continuiamo'))
        self.assertEqual(exhausted['status'], 'budget_exhausted')
        self.assertIsNone(exhausted['reply'])
        self.assertEqual(model.packs, [])

    def test_unusable_selection_falls_back_without_semantic_rejection_or_trusted_effect(self):
        from piecetogether.context import Assessment, SelectionResult
        self.seed()
        for i, mode in enumerate(('unknown', 'duplicate', 'nan', 'range', 'abstained', 'timeout', 'shape')):
            class BadSelector:
                def select(self, request):
                    if mode == 'timeout':
                        raise TimeoutError('provider timed out')
                    if mode == 'shape':
                        return {'ranked_ids': ['invented']}
                    known = request.candidates[0].id
                    ids = ('invented',) if mode == 'unknown' else (known, known) if mode == 'duplicate' else (known,)
                    assessments = ((Assessment(known, 'noul', float('nan') if mode == 'nan' else 1.1),)
                                   if mode in ('nan', 'range') else ())
                    return SelectionResult('abstained' if mode == 'abstained' else 'completed',
                                           ids, assessments, 'fixture', 'fixture-v1', request.rubric_version)
            core = self.core(selector=BadSelector())
            before = core.inspect_history()
            result = core.accept(self.message(text='cucina', idempotency_key=f'bad-{i}'))
            self.assertEqual(result['status'], 'completed', mode)
            trace = core.inspect(result['communication_id'])['trace']
            self.assertIsNotNone(trace['selection'][0]['fallback_reason'])
            self.assertIn('fallback_response', trace['selection'][0])
            self.assertEqual(core.inspect_history(), before)

    def test_high_score_does_not_grant_disclosure_and_exhausted_fallback_is_explicit(self):
        from piecetogether.context import Assessment, SelectionConfig, SelectionResult
        self.seed()
        class AllSelector:
            def select(self, request):
                return SelectionResult('completed', tuple(record.id for record in request.candidates),
                    tuple(Assessment(record.id, 'noul', 1) for record in request.candidates),
                    'fixture', 'fixture-v1', request.rubric_version)
        core = self.core(selector=AllSelector())
        result = core.accept(self.message(text='Un altro argomento'))
        trace = core.inspect(result['communication_id'])['trace']
        claim = next(record for record in trace['context_pack']['records'] if record['kind'] == 'claim')
        self.assertEqual(claim['disclosure_purposes'], [])
        class Abstained:
            def select(self, request):
                return SelectionResult('abstained', (), (), 'fixture', 'fixture-v1', request.rubric_version)
        self.config = replace(self.config, selection=SelectionConfig(max_calls=1))
        exhausted = self.core(selector=Abstained()).accept(self.message())
        self.assertEqual(exhausted['status'], 'budget_exhausted')
        self.assertIsNone(exhausted['reply'])

    def test_additional_context_requests_are_scoped_purpose_labelled_and_cumulative(self):
        from piecetogether.proposals import ContextRequestOperation
        from piecetogether.context import SelectionResult
        history = self.seed()
        target = history['entities'][0]['id']
        class EmptySelector:
            def select(self, request):
                return SelectionResult('completed', (), (), 'fixture', 'fixture-v1', request.rubric_version)
        class RequestModel(PackModel):
            def propose(self, inbound, version, context_pack):
                self.packs.append(context_pack)
                if len(self.packs) == 1:
                    return SemanticProposal(1, version, inbound.id, (), 'not exposed',
                                            (ContextRequestOperation((target,), 'disambiguation', 2),))
                return SemanticProposal(1, version, inbound.id, (), 'ready')
        model = RequestModel()
        core = self.core(model, selector=EmptySelector())
        result = core.accept(self.message())
        self.assertEqual(result['status'], 'completed')
        self.assertIn(target, {record.id for record in model.packs[-1].records})
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(trace['retrieval'][0]['purpose'], 'disambiguation')
        self.assertEqual(trace['retrieval'][0]['record_ids'], [target])
        self.assertNotIn('not exposed', result['reply'])
        class LoopingModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'not exposed',
                                        (ContextRequestOperation((target,), 'grounding', 1),))
        exhausted = self.core(LoopingModel()).accept(self.message())
        self.assertEqual(exhausted['status'], 'budget_exhausted')
        self.assertIsNone(exhausted['reply'])

    def test_disclosure_is_core_rendered_and_ranking_or_request_cannot_authorize_it(self):
        from piecetogether.proposals import DisclosureOperation
        history = self.seed()
        claim = history['claims'][0]
        core = self.core(OperationsModel((DisclosureOperation(claim['id'], 'continuity'),)))
        denied = core.accept(self.message(text='Tell me everything'))
        self.assertEqual(denied['status'], 'rejected')
        self.assertIsNone(denied['reply'])
        self.assertEqual(core.inspect(denied['communication_id'])['trace']['validation_reasons'],
                         ['disclosure_not_allowed'])
        model = OperationsModel((DisclosureOperation(claim['id'], 'disambiguation'),))
        core = self.core(model)
        allowed = core.accept(self.message(text=f'For {claim["id"]}, is this the intended interpretation?'))
        self.assertEqual(allowed['status'], 'completed')
        self.assertIn('cucina segreto', allowed['reply'])
        self.assertIn('disambiguation', allowed['reply'])
        core = self.core(OperationsModel((), response_intent='retrieval'))
        refused = core.accept(self.message(text='List all the stored notes'))
        self.assertEqual(refused['status'], 'completed')
        self.assertNotIn('cucina segreto', refused['reply'])
        self.assertIn('cannot retrieve', refused['reply'])

    def test_model_cannot_disclose_internal_context_via_unchecked_prose_or_catalogue(self):
        history = self.seed()
        class LeakingModel(PackModel):
            def propose(self, inbound, version, context_pack):
                self.packs.append(context_pack)
                return SemanticProposal(1, version, inbound.id, (), 'cucina segreto')
        model = LeakingModel()
        core = self.core(model)
        result = core.accept(self.message(text='cucina'))
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('cucina segreto', result['reply'])
        self.assertTrue(all(not record.payload_json for record in model.packs[0].catalogue))
        self.assertTrue(all(not record.payload_json for record in model.packs[0].records
                            if not record.disclosure_purposes))
        self.assertNotIn(history['claims'][0]['value'], str(model.packs[0]))

    def test_revision_change_during_model_work_requires_reprocessing(self):
        self.seed()
        outer = self
        class RacingModel:
            def propose(self, inbound, version, context_pack):
                outer.seed('bagno')
                return SemanticProposal(1, version, inbound.id, (), 'stale')
        core = self.core(RacingModel())
        result = core.accept(self.message(text='cucina'))
        self.assertEqual(result['status'], 'reprocess_required')
        self.assertIsNone(result['reply'])
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['context_revision_changed'])

    def test_delivery_retry_restores_pack_without_retrieval_or_selector_calls(self):
        from piecetogether.proposals import DisclosureOperation
        claim = self.seed()['claims'][0]
        class RefusingChannel:
            def deliver(self, outbound):
                return False
        core = self.core(OperationsModel((DisclosureOperation(claim['id'], 'continuity'),)),
                         channel=RefusingChannel())
        payload = self.message(text=claim['id'])
        first = core.accept(payload)
        original = core.inspect(first['communication_id'])
        class MustNotRun:
            def select(self, request):
                raise AssertionError('cached selection must be restored')
            def propose(self, inbound, version, context_pack):
                raise AssertionError('cached model work must be restored')
        recovered = self.core(MustNotRun(), selector=MustNotRun()).accept(payload)
        self.assertEqual(recovered['status'], 'completed')
        self.assertIn('cucina segreto', recovered['reply'])
        trace = self.core().inspect(first['communication_id'])['trace']
        self.assertEqual(trace['context_pack'], original['trace']['context_pack'])

    def test_jev_bootstrap_requires_pinned_configuration_and_resolved_secret(self):
        from piecetogether.context import SelectionConfig
        for changes in ({'adapter': 'unknown'}, {'adapter': 'jev'},
                        {'adapter': 'jev', 'model_id': 'jev-latest', 'secret_reference': 'typesafe', 'minimum_relevance': .7},
                        {'max_calls': False}, {'rubric_version': 'unversioned'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SelectionConfig(**changes)
        config = replace(self.config, selection=SelectionConfig(adapter='jev', model_id='jev-1.13.0',
                         secret_reference='typesafe', minimum_relevance=.7))
        with self.assertRaises(ValueError):
            Core(config)
        config = replace(config, secret_references={'typesafe': 'PT_TEST_TYPESAFE_KEY'})
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            Core(config)

    def test_captured_jev_relevance_selects_multiple_contexts_without_live_credentials(self):
        from piecetogether.context import SelectionConfig
        self.seed('cucina')
        history = self.seed('bagno')
        expected = {record['id'] for record in history['contexts']}
        config = replace(self.config, selection=SelectionConfig(adapter='jev', model_id='jev-1.13.0',
                         secret_reference='typesafe', minimum_relevance=.7),
                         secret_references={'typesafe': 'PT_TEST_TYPESAFE_KEY'})
        requests = []
        class CapturedResponse:
            status = 200
            def read1(self, limit):
                body = self.body
                self.body = b''
                return body
        class CapturedConnection:
            sock = None
            def __init__(self, *args, **kwargs):
                pass
            def connect(self):
                pass
            def request(self, method, path, body, headers):
                requests.append(json.loads(body))
            def getresponse(self):
                response = CapturedResponse()
                response.body = json.dumps({'model': 'jev-1.13.0', 'answers': {
                    record_id: {'type': 'noul', 'noul': .93 if record_id in expected else .02}
                    for record_id in requests[-1]['questions']},
                    'usage': {'input_tokens': 1200, 'output_tokens': 40}}).encode()
                return response
            def close(self):
                pass
        model = PackModel()
        with patch.dict(os.environ, {'PT_TEST_TYPESAFE_KEY': 'fixture-only'}), \
                patch('http.client.HTTPSConnection', CapturedConnection):
            core = Core(config, model=model, contract_provider=ContractProvider(self.contract))
            result = core.accept(self.message(text='cucina e bagno'))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual({record.id for record in model.packs[0].records}, expected)
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(trace['selection'][0]['response']['model'], 'jev-1.13.0')
        self.assertEqual(trace['selection'][0]['response']['input_tokens'], 1200)
        self.assertIsNone(trace['selection'][0]['fallback_reason'])
        self.assertTrue(all(question['type'] == 'noul' for question in requests[0]['questions'].values()))
        self.assertNotIn('fixture-only', json.dumps(trace))

    def test_captured_jev_failures_fallback_and_exhausted_usage_is_explicit(self):
        from piecetogether.context import SelectionConfig
        self.seed('cucina')
        self.config = replace(self.config,
            selection=SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe',
                                      minimum_relevance=.7),
            secret_references={'typesafe': 'PT_TEST_TYPESAFE_KEY'})
        for mode in ('unknown', 'nan', 'boolean', 'wrong_model', 'duplicate', 'score', 'abstained',
                     '429', 'timeout', 'usage'):
            class Response:
                status = 429 if mode == '429' else 200
                def read1(self, limit):
                    body = self.body
                    self.body = b''
                    return body
            class Connection:
                sock = None
                def __init__(self, *args, **kwargs):
                    pass
                def connect(self):
                    if mode == 'timeout':
                        raise TimeoutError('captured timeout')
                def request(self, method, path, body, headers):
                    self.request_body = json.loads(body)
                def getresponse(self):
                    ids = list(self.request_body['questions'])
                    probability = float('nan') if mode == 'nan' else True if mode == 'boolean' else .5 if mode == 'abstained' else .93
                    data = {'model': 'jev-preview' if mode == 'wrong_model' else 'jev-1.13.0',
                            'answers': {record_id: {'type': 'score' if mode == 'score' else 'noul',
                                                   'noul': probability} for record_id in ids},
                            'usage': {'input_tokens': 65537 if mode == 'usage' else 1200, 'output_tokens': 40}}
                    if mode == 'unknown':
                        data['answers']['invented'] = data['answers'].pop(ids[0])
                    body = json.dumps(data)
                    if mode == 'duplicate':
                        body = body.replace('"input_tokens": 1200', '"input_tokens": 1200, "input_tokens": 1200')
                    response = Response()
                    response.body = body.encode()
                    return response
                def close(self):
                    pass
            with self.subTest(mode=mode), patch.dict(os.environ, {'PT_TEST_TYPESAFE_KEY': 'fixture-only'}), \
                    patch('http.client.HTTPSConnection', Connection):
                core = self.core()
                result = core.accept(self.message(text='cucina'))
                trace = core.inspect(result['communication_id'])['trace']
                self.assertEqual(result['status'], 'budget_exhausted' if mode == 'usage' else 'completed')
                if mode != 'usage':
                    self.assertIsNotNone(trace['selection'][0]['fallback_reason'])
                    self.assertIn('fallback_response', trace['selection'][0])
                else:
                    self.assertEqual(trace['selection'][0]['provider_metadata']['usage']['input_tokens'], 65537)

    def test_input_budget_is_enforced_before_invoking_a_selector(self):
        from piecetogether.context import SelectionConfig
        self.config = replace(self.config, selection=SelectionConfig(max_tokens=1))
        class MustNotRun:
            def select(self, request):
                raise AssertionError('input already exceeds the turn budget')
        result = self.core(selector=MustNotRun()).accept(self.message())
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertIsNone(result['reply'])

    def test_retrieval_refusal_cannot_create_unseen_grounding_or_vocabulary(self):
        model = OperationsModel((EntityOperation('hidden', 'Subject'),
            GroundingPlanOperation('p', (), 'explicit', ('hidden',))), response_intent='retrieval')
        core = self.core(model)
        result = core.accept(self.message(text='List everything'))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect(result['communication_id'])['grounding_items'], [])
        self.assertEqual(core.inspect_history()['entities'], [])
        self.assertIsNone(result['reply'])

    def test_request_must_fit_its_declared_budget_or_is_terminally_invalid(self):
        from piecetogether.proposals import ContextRequestOperation
        history = self.seed()
        ids = (history['entities'][0]['id'], history['contexts'][0]['id'])
        core = self.core(OperationsModel((ContextRequestOperation(ids, 'continuity', 1),)))
        payload = self.message()
        result = core.accept(payload)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['invalid_context_request'])
        self.assertEqual(core.accept(payload), result)

    def test_actual_rendered_reply_is_bounded_instead_of_unused_historical_draft(self):
        self.seed()
        class LongDraftModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'x' * 65536, (
                    EntityOperation('new', 'Subject'),
                    GroundingPlanOperation('p', (), 'explicit', ('new',)),
                ))
        core = self.core(LongDraftModel())
        result = core.accept(self.message(text='cucina'))
        self.assertEqual(result['status'], 'completed')
        self.assertLess(len(result['reply']), 65536)
        self.assertIn('new Subject', result['reply'])
        self.config = replace(self.config, database=Path(self.directory.name) / 'empty.sqlite3')
        core = self.core(LongDraftModel())
        oversized = core.accept(self.message())
        self.assertEqual(oversized['status'], 'rejected')
        self.assertIsNone(oversized['reply'])
        self.assertEqual(core.inspect(oversized['communication_id'])['trace']['validation_reasons'],
                         ['grounding_response_too_large'])

    def test_provider_cannot_bypass_actor_scope_using_a_known_foreign_entity_id(self):
        foreign = self.seed('privato', sender='s2')['entities'][0]['id']
        core = self.core(OperationsModel((
            EntityOperation(foreign, 'Subject', action='resolve'),
            GroundingPlanOperation('p', (), 'explicit', (foreign,)),
        )))
        result = core.accept(self.message(text=foreign))
        self.assertEqual(result['status'], 'rejected')
        self.assertIsNone(result['reply'])
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['context_scope_not_allowed'])


if __name__ == '__main__':
    unittest.main()
