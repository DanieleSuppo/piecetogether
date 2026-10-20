"""Operator-only trace and offline replay regression proofs."""
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from piecetogether import context
from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import ContextRequestOperation, SemanticProposal


class TraceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Bootstrap(Path(directory.name) / 'core.sqlite3', {'development:s': 'actor'})
        self.message = dict(channel='development', sender='s', idempotency_key='one',
                            text='First note', sent_at='2026-10-20T12:00:00Z')

    def test_each_generation_keeps_distinct_input_proposal_usage_and_failure(self):
        from piecetogether.openai_responses import GenerationResult
        core = Core(self.config)
        core.accept(self.message)
        class RetrievalModel:
            calls = 0
            def propose_with_evidence(self, inbound, version, pack):
                self.calls += 1
                operations = ((ContextRequestOperation((pack.catalogue[0].id,), 'continuity', 1),)
                              if self.calls == 1 else ())
                proposal = SemanticProposal(1, version, inbound.id, (), 'Final response', operations)
                return GenerationResult(proposal, 'fixture', 'pinned-v1', 'instructions-v2',
                                        {'input_tokens': self.calls * 10, 'output_tokens': 3})
        core = Core(self.config, model=RetrievalModel())
        result = core.accept({**self.message, 'idempotency_key': 'two', 'text': 'Unrelated'})
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(trace['model_calls'], 2)
        calls = trace['generative_attempts']
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]['context_pack_ref'], calls[1]['context_pack_ref'])
        self.assertEqual(calls[0]['proposal']['operations'][0]['kind'], 'context_request')
        self.assertEqual(calls[1]['proposal']['operations'], [])
        self.assertEqual(calls[1]['usage']['input_tokens'], 20)
        self.assertEqual(calls[0]['model'], 'pinned-v1')
        self.assertEqual(calls[0]['instruction_version'], 'instructions-v2')
        self.assertIn('validation', trace['policy_versions'])
        self.assertEqual(trace['processing_outcome'], 'completed')
        self.assertEqual(trace['event_ids'], [])
        class FailedModel:
            def propose(self, *args):
                raise OSError('https://user:password@secret.invalid')
        core = Core(self.config, model=FailedModel())
        result = core.accept({**self.message, 'idempotency_key': 'three'})
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(trace['generative_attempts'][0]['outcome'], 'provider_failure')
        self.assertIsNone(trace['generative_attempts'][0]['usage'])
        self.assertNotIn('password', json.dumps(trace))

    def test_credential_bearing_provider_metadata_is_not_persisted(self):
        from piecetogether.openai_responses import GenerationResult
        class Model:
            def propose_with_evidence(self, inbound, version, pack):
                return GenerationResult(SemanticProposal(1, version, inbound.id, (), 'safe'),
                    'https://user:password@private.invalid', 'model-v1', 'v1', {'input_tokens': 1})
        core = Core(self.config, model=Model())
        result = core.accept(self.message)
        self.assertEqual(result['status'], 'retryable')
        self.assertNotIn('password', json.dumps(core.inspect(result['communication_id'])))
        class FailedModel:
            provider_id = 'https://user:password@private.invalid'
            def propose(self, *args): raise OSError('unavailable')
        core.model = FailedModel()
        result = core.accept({**self.message, 'idempotency_key': 'failure'})
        self.assertEqual(result['status'], 'retryable')
        self.assertNotIn('password', json.dumps(core.inspect(result['communication_id'])))

    def test_unserializable_selector_metadata_falls_back_but_replay_fails_explicitly(self):
        from piecetogether.evaluation import CapturedSelector, ReplayUnavailable
        class Selector:
            def select(self, request):
                return context.SelectionResult('completed', (), (), object(), 'model-v1', request.rubric_version)
        core = Core(self.config, selector=Selector())
        result = core.accept(self.message)
        self.assertEqual(result['status'], 'completed')
        evidence = core.inspect(result['communication_id'])['trace']['selection']
        self.assertEqual(evidence[0]['response_capture'], 'unserializable')
        with self.assertRaises(ReplayUnavailable):
            CapturedSelector(evidence).select(None)

    def test_selector_keeps_request_raw_unknown_response_and_final_pack_reference(self):
        class Unknown:
            def select(self, request):
                return {'outcome': 'completed', 'ranked_ids': ['unknown'], 'noul': .91}
        core = Core(self.config, selector=Unknown())
        result = core.accept(self.message)
        trace = core.inspect(result['communication_id'])['trace']
        selection = trace['selection'][0]
        self.assertEqual(selection['raw_response']['noul'], .91)
        self.assertEqual(selection['fallback_reason'], 'invalid_selection')
        self.assertEqual(selection['request']['rubric_version'], 'context-relevance-v1')
        self.assertEqual(selection['context_pack_ref'], context.digest(context.encoded(trace['context_pack'])))
        self.assertEqual(selection['selection_policy_version'], context.SELECTION_POLICY_VERSION)
        self.assertNotIn('raw_response', result)


class SelectorReplayTests(unittest.TestCase):
    def pack(self, **budget):
        record = context.ContextRecord('r1', 'entity', 'kitchen', 'revision-1', '{"value":"kitchen"}')
        return context.ContextPack('actor', 'development-v1', 0, catalogue=(record,),
                                   budget=context.SelectionConfig(**budget))

    def test_captured_selector_replays_selection_failures_abstention_and_budget(self):
        from piecetogether.evaluation import CapturedSelector
        inbound = {'id': 'input', 'text': 'kitchen'}
        for mode in ('success', 'abstained', 'unavailable', 'unknown', 'malformed', 'list_ids', 'dict_assessment',
                     'outage', 'budget', 'timeout'):
            class Selector:
                def select(self, request):
                    if mode == 'outage':
                        raise OSError('do not retain provider errors')
                    if mode == 'malformed':
                        return {'unknown': 'shape'}
                    if mode == 'budget':
                        raise context.BudgetExhausted({'usage': {'input_tokens': 99999}})
                    return context.SelectionResult(mode if mode in ('abstained', 'unavailable') else 'completed',
                        ('unknown',) if mode == 'unknown' else ['r1'] if mode == 'list_ids' else ('r1',),
                        ({'record_id': 'r1', 'primitive': 'noul', 'value': .9},) if mode == 'dict_assessment' else (),
                        'fixture', 'model-v1',
                        request.rubric_version)
            with self.subTest(mode=mode):
                pack = self.pack(max_calls=1 if mode == 'abstained' else 3)
                evidence = []
                expected = context.select(pack, inbound, Selector(), evidence)
                if mode == 'timeout':
                    evidence[0]['latency_ms'] = pack.budget.timeout_ms + 1
                    expected = replace(pack, outcome='budget_exhausted')
                captured = CapturedSelector(evidence)
                actual = context.select(pack, inbound, captured, [])
                self.assertEqual(actual, expected)
                captured.assert_consumed()

    def test_selector_contradictory_fallback_evidence_fails_at_selection_gate(self):
        from piecetogether.evaluation import CapturedSelector, ReplayUnavailable
        pack, inbound = self.pack(), {'id': 'input', 'text': 'kitchen'}
        evidence = []
        context.select(pack, inbound, context.ReferenceSelector(), evidence)
        for failure, fallback in ((None, 'provider_failure'), ('provider_failure', None),
                                  ('invalid_selection', 'provider_failure')):
            capture = json.loads(json.dumps(evidence))
            capture[0].update(failure=failure, fallback_reason=fallback, total_latency_ms=10)
            capture[0]['latency_ms'] = 0
            with self.subTest(failure=failure, fallback=fallback), self.assertRaises(ReplayUnavailable):
                context.select(pack, inbound, CapturedSelector(capture), [])

    def test_selector_missing_changed_revisions_versions_and_extra_calls_fail_closed(self):
        from piecetogether.evaluation import CapturedSelector, ReplayUnavailable
        pack, inbound = self.pack(), {'id': 'input', 'text': 'kitchen'}
        evidence = []
        context.select(pack, inbound, context.ReferenceSelector(), evidence)
        for change in ('missing', 'revision', 'question', 'policy', 'redacted', 'input', 'operating_point', 'timing'):
            capture = json.loads(json.dumps(evidence))
            changed_pack = pack
            changed_input = inbound
            if change == 'missing': capture = []
            if change == 'revision':
                changed_pack = replace(pack, catalogue=(replace(pack.catalogue[0], revision='new'),))
            if change == 'question': capture[0]['request']['rubric_version'] = 'new'
            if change == 'policy': capture[0]['selection_policy_version'] = 'new'
            if change == 'redacted': capture[0]['capture'] = 'redacted'
            if change == 'input': changed_input = {**inbound, 'text': 'changed'}
            if change == 'operating_point':
                changed_pack = replace(pack, budget=replace(pack.budget, minimum_relevance=.8))
            if change == 'timing': capture[0]['latency_ms'] = float('nan')
            with self.subTest(change=change), self.assertRaises(ReplayUnavailable):
                context.select(changed_pack, changed_input, CapturedSelector(capture), [])
        captured = CapturedSelector(evidence)
        with self.assertRaises(ReplayUnavailable): captured.assert_consumed()
        context.select(pack, inbound, captured, [])
        with self.assertRaises(ReplayUnavailable): context.select(pack, inbound, captured, [])


class SemanticReplayTests(unittest.TestCase):
    def setUp(self):
        from piecetogether.contracts import DomainContract
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Bootstrap(Path(directory.name) / 'core.sqlite3', {'development:s': 'actor'})
        self.contract = DomainContract('development-v1',
            entity_types={'Subject': {'creation': True, 'attributes': {}}},
            claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'},
                                     'grounding_policy': 'p'}},
            grounding_policies={'p': {'acceptance': ['explicit']}})
        class Provider:
            def get(inner, version): return self.contract
        self.core = Core(self.config, contract_provider=Provider())
        self.message = dict(channel='development', sender='s', idempotency_key='one',
                            text='kitchen', sent_at='2026-10-20T12:00:00Z')

    def capture_case(self, rejected=False, retry=False):
        from piecetogether.evaluation import capture
        from piecetogether.proposals import (ClaimOperation, EntityOperation,
            GroundingPlanOperation, GroundingResolutionOperation)
        message = self.message
        class Candidate:
            def propose(self, inbound, version, pack):
                return SemanticProposal(1, version, inbound.id, (), 'Interpretation', (
                    EntityOperation('subject', 'Subject'),
                    ClaimOperation('claim', 'subject', 'note', 'kitchen', 'p'),
                    GroundingPlanOperation('p', ('claim',), 'explicit', ('subject',))),
                    decision_provider='fixture', decision_model='decision-v1',
                    decision_question_version='grounding-v1', decision_raw={'noul': .99})
        class Commit:
            def propose(self, inbound, version, pack):
                return SemanticProposal(1, version, inbound.id, (), 'Confirmed', tuple(
                    GroundingResolutionOperation(record.id, 'p', inbound.id, 'explicit', 'accepted')
                    for record in pack.records if record.kind == 'pending_grounding'),
                    intent='semantic_commit', semantic_revision=pack.semantic_revision)
        class Channel:
            calls = 0
            def deliver(self, outbound):
                self.calls += 1
                return False if retry and self.calls == 2 else True
        def scenario(recorder):
            recorder.core.model = Candidate()
            first = recorder.accept(message)
            exposed = recorder.core.inspect(first['communication_id'])
            recorder.core.model = Commit()
            followup = {**message, 'idempotency_key': 'two', 'text': 'maybe' if rejected else 'yes',
                        'reply_to': exposed['outbound']['id']}
            recorder.accept(followup)
            if retry:
                recorder.restart()
                recorder.accept(followup)
        return capture(self.core, scenario, channel=Channel())

    def test_real_core_replays_grounding_commit_provenance_events_without_inference(self):
        from piecetogether.evaluation import replay
        from unittest.mock import patch
        before = self.core.inspect_history()
        case = self.capture_case()
        self.assertEqual(self.core.inspect_history(), before)
        self.assertEqual(len(case.expected['history']['claims']), 1)
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('network forbidden')), \
             patch('piecetogether.core.DeterministicModel.propose', side_effect=AssertionError('inference forbidden')):
            result = replay(case)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(result.actual['history']['claims'][0]['value'], 'kitchen')
        self.assertEqual(len(result.actual['events']), 1)
        trusted = json.dumps([result.actual['history'], result.actual['view'], result.actual['events']])
        self.assertNotIn('noul', trusted)
        self.assertNotIn('decision_raw', trusted)
        self.assertEqual(self.core.inspect_history(), before)

    def test_rejected_and_delivery_only_retry_after_restart_compute_real_outcomes(self):
        from piecetogether.evaluation import replay
        rejected = self.capture_case(rejected=True)
        result = replay(rejected)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(result.actual['history']['claims'], [])
        self.assertEqual(result.actual['steps'][-1]['result']['status'], 'rejected')
        retried = self.capture_case(retry=True)
        self.assertEqual(retried.steps[-1]['generative_attempts'], [])
        result = replay(retried)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(len(result.actual['history']['commits']), 1)

    def test_offline_replay_preserves_all_selector_fallback_and_budget_outcomes(self):
        from piecetogether.evaluation import capture, replay
        for mode in ('success', 'abstained', 'unknown', 'malformed', 'outage', 'budget'):
            class Selector:
                def select(self, request):
                    if mode == 'outage': raise OSError('outage with private diagnostics')
                    if mode == 'malformed': return {'arbitrary': ['invalid']}
                    if mode == 'budget': raise context.BudgetExhausted()
                    return context.SelectionResult('abstained' if mode == 'abstained' else 'completed',
                        ('invented',) if mode == 'unknown' else (), (), 'fixture', 'pinned-v1', request.rubric_version)
            self.core.selector = Selector()
            with self.subTest(mode=mode):
                case = capture(self.core, lambda recorder: recorder.accept(self.message))
                result = replay(case)
                self.assertTrue(result.matched, result.mismatches)
                self.assertEqual(result.actual['steps'][0]['result']['status'],
                                 'budget_exhausted' if mode == 'budget' else 'completed')
                recomputed = result.actual['steps'][0]['selection'][0]
                recorded = case.steps[0]['selection'][0]
                for key in ('fallback_reason', 'calls_used', 'provider', 'model', 'candidate_revisions'):
                    self.assertEqual(recomputed[key], recorded[key])

    def test_provider_outage_retry_and_multi_proposal_retrieval_replay_without_provider(self):
        from piecetogether.evaluation import capture, replay
        class Model:
            calls = 0
            def propose(self, inbound, version, pack):
                self.calls += 1
                if self.calls == 1: raise OSError('private provider failure')
                operations = ((ContextRequestOperation((pack.catalogue[0].id,), 'continuity', 1),)
                              if inbound.idempotency_key == 'two' and self.calls == 3 else ())
                return SemanticProposal(1, version, inbound.id, (), 'Current interpretation', operations)
        self.core.model = Model()
        def scenario(recorder):
            recorder.accept(self.message)
            recorder.restart()
            recorder.accept(self.message)
            recorder.accept({**self.message, 'idempotency_key': 'two', 'text': 'unrelated'})
        case = capture(self.core, scenario)
        result = replay(case)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(case.expected['steps'][0]['result']['status'], 'retryable')
        self.assertEqual(len(case.steps[2]['generative_attempts']), 2)

    def test_snapshot_preserves_initial_revision_and_does_not_export_optional_diagnostics_or_secrets(self):
        import os
        import sqlite3
        from unittest.mock import patch
        from piecetogether.evaluation import capture, replay
        from piecetogether.proposals import EntityOperation, GroundingPlanOperation, GroundingResolutionOperation
        class Model:
            def propose(self, inbound, version, pack):
                pending = [record for record in pack.records if record.kind == 'pending_grounding']
                if pending:
                    return SemanticProposal(1, version, inbound.id, (), 'Accepted', tuple(
                        GroundingResolutionOperation(record.id, 'p', inbound.id, 'explicit', 'accepted')
                        for record in pending), intent='semantic_commit', semantic_revision=pack.semantic_revision)
                return SemanticProposal(1, version, inbound.id, (), 'Candidate', (
                    EntityOperation('subject', 'Subject'), GroundingPlanOperation('p', (), 'explicit', ('subject',))))
        self.core.model = Model()
        first = self.core.accept(self.message)
        exposed = self.core.inspect(first['communication_id'])
        self.core.accept({**self.message, 'idempotency_key': 'two', 'text': 'yes',
                          'reply_to': exposed['outbound']['id']})
        with sqlite3.connect(self.config.database) as db:
            trace = json.loads(db.execute('SELECT trace FROM turns LIMIT 1').fetchone()[0])
            trace['diagnostics'] = {'prompt': 'operator-private-prompt'}
            trace['generative_attempts'][0]['full_prompt'] = 'operator-private-prompt'
            trace['selection'][0]['diagnostics'] = {'transport': 'operator-private-prompt'}
            db.execute('UPDATE turns SET trace=? WHERE id=?', (json.dumps(trace), first['communication_id']))
        before = self.config.database.read_bytes()
        with patch.dict(os.environ, {'PT_EVALUATION_TEST_SECRET': 'do-not-export-credential'}):
            self.core.config = replace(self.config, secret_references={'test': 'PT_EVALUATION_TEST_SECRET'})
            case = capture(self.core, lambda recorder: recorder.accept(
                {**self.message, 'idempotency_key': 'three', 'text': 'new information'}))
        self.assertEqual(case.initial_revision, 1)
        self.assertNotIn(b'operator-private-prompt', case.initial_state)
        self.assertNotIn('do-not-export-credential', json.dumps(case.to_dict()))
        result = replay(case)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(self.config.database.read_bytes(), before)

    def test_channel_capture_cannot_replace_the_computed_semantic_reply(self):
        from piecetogether.evaluation import replay, ReplayUnavailable
        case = self.capture_case()
        replacement = 'A replacement assertion supplied by channel evidence'
        case.steps[0]['channel'][0]['output']['text'] = replacement
        case.steps[0]['channel'][1]['input']['text'] = replacement
        case.expected['steps'][0]['outbound']['text'] = replacement
        case.expected['steps'][0]['result']['reply'] = replacement
        with self.assertRaises(ReplayUnavailable): replay(case)

    def test_terminal_idempotency_replay_consumes_no_new_decisions_or_deliveries(self):
        from piecetogether.evaluation import capture, replay
        def scenario(recorder):
            recorder.accept(self.message)
            recorder.accept(self.message)
        case = capture(self.core, scenario)
        self.assertEqual(case.steps[1]['generative_attempts'], [])
        self.assertEqual(case.steps[1]['selection'], [])
        self.assertEqual(case.steps[1]['channel'], [])
        self.assertTrue(replay(case).matched)

    def test_capture_preserves_missing_artifact_store_operational_failure(self):
        from piecetogether.evaluation import capture, replay
        payload = {**self.message, 'attachments': [{'id': 'a', 'content_base64': 'YQ=='}]}
        self.assertIsNone(self.core.artifact_store)
        case = capture(self.core, lambda recorder: recorder.accept(payload))
        self.assertEqual(case.expected['steps'][0]['result']['status'], 'retryable')
        self.assertTrue(replay(case).matched)

    def test_snapshot_preserves_initial_artifact_deletion_failure(self):
        import sqlite3
        from unittest.mock import patch
        from piecetogether.artifact_store import LocalArtifactStore
        from piecetogether.evaluation import capture, replay
        config = replace(self.config, artifact_store=self.config.database.parent / 'artifacts')
        source = Core(config)
        with sqlite3.connect(config.database) as db:
            db.execute('INSERT INTO artifact_deletions VALUES (?, ?, NULL)', ('artifact', 'a' * 32))
        with patch.object(LocalArtifactStore, 'remove', side_effect=OSError('fixture deletion failure')):
            self.assertEqual(source.retry_artifact_deletion(), {'artifact': 'pending'})
        # A deployment without an ArtifactStore cannot retry this pending deletion.
        source = Core(replace(config, artifact_store=None))
        history = source.inspect_history()
        self.assertEqual(history['artifact_deletions'][0]['failure']['message'], 'fixture deletion failure')
        before = config.database.read_bytes()
        case = capture(source, lambda recorder: None)
        self.assertEqual(case.expected['history'], history)
        result = replay(case)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(result.actual['history'], history)
        self.assertEqual(config.database.read_bytes(), before)

    def test_parallel_replays_do_not_share_identity_or_clock_state(self):
        from concurrent.futures import ThreadPoolExecutor
        from piecetogether.evaluation import replay
        case = self.capture_case()
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(replay, [case] * 3))
        self.assertTrue(all(result.matched for result in results))
        identifiers = [result.actual['events'][0]['id'] for result in results]
        self.assertEqual(len(set(identifiers)), 1)
        ordinary = self.core.accept(self.message)
        self.assertNotEqual(ordinary['communication_id'], case.steps[0]['inbound']['id'])

    def test_artifact_metadata_replays_real_persistence_without_external_bytes(self):
        import base64
        import copy
        from unittest.mock import patch
        from piecetogether.artifact_store import LocalArtifactStore
        from piecetogether.contracts import DomainContract
        from piecetogether.evaluation import capture, replay, ReplayUnavailable, ReplayCase
        from piecetogether.proposals import ArtifactOperation
        contract = DomainContract('development-v1', artifact_types={'Document': {
            'roles': ['persistent-domain-artifact'], 'persistence': 'required',
            'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
            'supersession': False}})
        class Provider:
            def get(self, version): return contract
        class Model:
            def propose(self, inbound, version, pack):
                return SemanticProposal(1, version, inbound.id, (), 'Stored', (
                    ArtifactOperation('document', 'Document', ('persistent-domain-artifact',), action='persist'),),
                    intent='semantic_commit', semantic_revision=pack.semantic_revision)
        config = replace(self.config, artifact_store=self.config.database.parent / 'artifacts')
        source = Core(config, model=Model(), contract_provider=Provider())
        payload = {**self.message, 'attachments': [{'id': 'document', 'media_type': 'application/pdf',
                   'content_base64': base64.b64encode(b'private document content').decode()}]}
        case = capture(source, lambda recorder: recorder.accept(payload))
        self.assertNotIn('private document content', json.dumps(case.to_dict()))
        case = ReplayCase.from_dict(json.loads(json.dumps(case.to_dict())))
        with patch.object(LocalArtifactStore, 'read', side_effect=AssertionError('no byte fetch')), \
             patch.object(LocalArtifactStore, 'stage', side_effect=AssertionError('no byte staging')), \
             patch('http.client.HTTPSConnection', side_effect=AssertionError('no network')):
            result = replay(case)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(result.actual['history']['artifacts'][0]['availability'], 'available')
        broken = copy.deepcopy(case)
        broken.steps[0]['artifact_stages'] = []
        with self.assertRaises(ReplayUnavailable): replay(broken)
        broken = copy.deepcopy(case)
        broken.artifact_versions[0]['checksum'] = '0' * 64
        with self.assertRaises(ReplayUnavailable): replay(broken)

        # Initial publication failures must survive capture, not disappear from both sides.
        with patch.object(LocalArtifactStore, 'finalize', side_effect=OSError('fixture publication failure')):
            source.accept(payload)
        failure = source.current_view()['artifacts'][0]['publication_failure']
        self.assertEqual(failure['message'], 'fixture publication failure')
        with self.assertRaises(ReplayUnavailable):
            capture(source, lambda recorder: None)  # Pending publication needs unavailable source bytes.
        source.retry_artifact_finalization()
        # Reproduce the review's available-Artifact failure row to isolate snapshot fidelity.
        import sqlite3
        with sqlite3.connect(config.database) as db:
            db.execute('INSERT INTO artifact_publication_failures VALUES (?, ?)',
                       (failure['artifact_id'], json.dumps(failure)))
        source_view = source.current_view()
        before = config.database.read_bytes()
        case = capture(source, lambda recorder: None)
        self.assertEqual(case.expected['view'], source_view)
        self.assertEqual(case.expected['history'], source.inspect_history())
        result = replay(case)
        self.assertTrue(result.matched, result.mismatches)
        self.assertEqual(result.actual['view'], source_view)
        self.assertEqual(config.database.read_bytes(), before)

    def test_missing_evidence_versions_revisions_and_tampering_never_infer_or_restore_assertions(self):
        import copy
        from piecetogether.evaluation import replay, ReplayUnavailable
        original = self.capture_case()
        for change in ('policy', 'question', 'revision', 'proposal', 'missing', 'artifact', 'initial_state'):
            case = copy.deepcopy(original)
            if change == 'policy': case.policy_versions['validation'] = 'new'
            if change == 'question': case.steps[0]['selection'][0]['request']['rubric_version'] = 'new'
            if change == 'revision': case.steps[1]['generative_attempts'][0]['context_pack']['semantic_revision'] = 99
            if change == 'proposal': case.steps[1]['generative_attempts'][0]['proposal']['operations'] = []
            if change == 'missing': case.steps[0]['generative_attempts'] = []
            if change == 'artifact': case.artifact_versions = None
            if change == 'initial_state': case.initial_state = None
            with self.subTest(change=change), self.assertRaises(ReplayUnavailable): replay(case)
        case = copy.deepcopy(original)
        case.expected['history']['claims'][0]['value'] = 'not the computed assertion'
        result = replay(case)
        self.assertFalse(result.matched)
        self.assertEqual(result.actual['history']['claims'][0]['value'], 'kitchen')
        self.assertIn('history', result.mismatches)
        from piecetogether.evaluation import ReplayCase
        serialized = original.to_dict()
        for field in ('schema_version', 'policy_versions', 'artifact_operations'):
            missing = {key: value for key, value in serialized.items() if key != field}
            with self.subTest(field=field), self.assertRaises(ReplayUnavailable):
                ReplayCase.from_dict(missing)
        original.diagnostics = {'optional': 'remove me'}
        original.diagnostics.clear()
        self.assertTrue(replay(original).matched)


if __name__ == '__main__':
    unittest.main()
