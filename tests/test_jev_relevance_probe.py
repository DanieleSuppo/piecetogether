"""Versioned Jev proofs. Synthetic data, fake HTTP and temporary ledgers only."""
import copy
from dataclasses import asdict, replace
import json
import math
import unittest
from unittest.mock import patch

from piecetogether import context, jev
from piecetogether.context import SelectionConfig, SelectionFailure, encoded
from test_jev_catalogue_probe import CONFIG, fixture_capture

V2 = 'context-relevance-v2'


def connection(scores, calls):
    class Response:
        status = 200
        def read1(self, limit):
            chunk, self.body = self.body[:limit], self.body[limit:]
            return chunk
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs): pass
        def connect(self): pass
        def request(self, method, path, body, headers):
            calls.append(body)
            self.payload = json.loads(body)
        def getresponse(self):
            response = Response()
            ids = self.payload['questions']
            values = scores(self.payload) if callable(scores) else dict(zip(ids, scores))
            response.body = encoded({'model': 'jev-1.13.0', 'answers': {
                k: {'type': 'noul', 'noul': v} for k, v in values.items()},
                'usage': {'input_tokens': 100, 'output_tokens': 10}}).encode()
            return response
        def close(self): pass
    return Connection


class DecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import jev_catalogue_probe
        cls.capture = fixture_capture()
        cls.original = jev_catalogue_probe._restore(cls.capture['steps'][0]['selection'][0]['request'])

    def test_v2_inline_compact_kind_rules_without_raw_payload(self):
        request = replace(self.original, rubric_version=V2,
            candidates=tuple(replace(r, payload_json='NEVER SEND RAW') for r in self.original.candidates))
        payload = jev._payload(request, 'jev-1.13.0')
        self.assertNotIn('NEVER SEND RAW', encoded(payload))
        for record in request.candidates:
            question = payload['questions'][record.id]
            self.assertIn('candidate', question['instructions'])
            self.assertEqual(question['instructions']['candidate'], {
                'id': record.id, 'kind': record.kind, 'summary': record.summary, 'revision': record.revision})
            self.assertIn('selection_rule', question['instructions'])
            self.assertEqual(question['instructions']['question'],
                'Does `candidate` satisfy `selection_rule` for inclusion among the optional records '
                'used to interpret `state.communication.text`?')
            self.assertEqual(question['criteria'], {
                'true': "This kind's selection_rule applies to this record",
                'false': "This kind's selection_rule does not apply to this record; "
                         'topic overlap alone is insufficient for non-context records'})
            self.assertNotIn('candidate_id', question['instructions'])
        entity = next(r for r in request.candidates if r.kind == 'entity')
        self.assertIn('object', payload['questions'][entity.id]['instructions']['selection_rule'])

    def test_v2_probability_bands_and_positive_with_ambiguity(self):
        config = replace(CONFIG, rubric_version=V2)
        request = replace(self.original, rubric_version=V2, candidates=self.original.candidates[:1])
        for value, outcome, selected in ((0, 'completed', False), (.1, 'completed', False),
                (.3, 'completed', False), (math.nextafter(.3, 1), 'abstained', False),
                (.5, 'abstained', False), (math.nextafter(.7, 0), 'abstained', False),
                (.7, 'completed', True), (.9, 'completed', True), (1, 'completed', True)):
            calls = []
            with self.subTest(value=value), patch('http.client.HTTPSConnection', connection([value], calls)):
                result = jev.JevSelector(config, 'fixture-secret').select(request)
            self.assertEqual(result.outcome, outcome)
            self.assertEqual(result.ranked_ids, (request.candidates[0].id,) if selected else ())
            self.assertEqual(result.raw_decisions['answers'][request.candidates[0].id]['noul'], value)
        request = replace(request, candidates=self.original.candidates[:3])
        for values, outcome, ranks in (([.9, .5, .1], 'abstained', (request.candidates[0].id,)),
                ([.7, .9, .3], 'completed', (request.candidates[1].id, request.candidates[0].id)),
                ([.3, .1, 0], 'completed', ())):
            scores = {r.id: value for r, value in zip(request.candidates, values)}
            with patch('http.client.HTTPSConnection', connection(lambda p: scores, [])):
                result = jev.JevSelector(config, 'fixture-secret').select(request)
            self.assertEqual((result.outcome, result.ranked_ids), (outcome, ranks))

    def test_v2_version_threshold_and_unknown_kind_refuse_before_http(self):
        with self.assertRaises(ValueError): replace(CONFIG, rubric_version=V2, minimum_relevance=.6)
        config = replace(CONFIG, rubric_version=V2)
        base = replace(self.original, rubric_version=V2)
        for request in (self.original, replace(base, minimum_relevance=.6),
                replace(base, candidates=(replace(base.candidates[0], kind='unknown'),))):
            with patch('http.client.HTTPSConnection', side_effect=AssertionError('network')):
                with self.assertRaises(ValueError): jev.JevSelector(config, 'fixture-secret').select(request)
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('network')):
            with self.assertRaises(ValueError): jev.JevSelector(CONFIG, 'fixture-secret').select(base)

    def test_v1_actual_wire_matches_base_implementation(self):
        import subprocess
        namespace = {'__name__': 'piecetogether._base_jev', '__package__': 'piecetogether'}
        source = subprocess.check_output(['git', 'show', 'a7b032dc80e51c0d619990432ff6dafc6b098cc5:piecetogether/jev.py'])
        exec(compile(source, '<base jev>', 'exec'), namespace)
        calls = []
        with patch('http.client.HTTPSConnection', connection([.1] * 15, calls)):
            jev.JevSelector(CONFIG, 'fixture-secret').select(self.original)
        self.assertEqual(calls[0], encoded(namespace['_payload'](self.original, 'jev-1.13.0')).encode())
        # Legacy .1 still routes to application abstention, not v2 strong no.
        with patch('http.client.HTTPSConnection', connection([.1] * 15, [])):
            self.assertEqual(jev.JevSelector(CONFIG, 'fixture-secret').select(self.original).outcome, 'abstained')

    def test_v2_core_capture_replay_exact_version_no_trusted_mutation(self):
        import test_context
        from piecetogether.evaluation import CapturedSelector, ReplayUnavailable, capture, replay
        fixture = test_context.ContextTests('runTest')
        fixture.setUp()
        try:
            fixture.seed('cucina'); fixture.seed('bagno')
            fixture.config = replace(fixture.config, selection=replace(CONFIG, rubric_version=V2))
            core = fixture.core(test_context.OperationsModel(()), selector=jev.JevSelector(fixture.config.selection, 'fixture-secret'))
            before = core.inspect_history()
            scores = lambda p: {r['id']: .9 if r['kind'] == 'context' else .1 for r in p['state']['candidates']}
            with patch('http.client.HTTPSConnection', connection(scores, [])):
                case = capture(core, lambda recorder: recorder.accept(fixture.message(text='cucina e bagno')))
            entry = case.to_dict()['steps'][0]['selection'][0]
            self.assertEqual(entry['task_version'], V2)
            self.assertEqual(entry['selection_policy_version'], 'context-selection-v1')
            self.assertEqual(before['contexts'], core.inspect_history()['contexts'])
            with patch('http.client.HTTPSConnection', side_effect=AssertionError('network')):
                self.assertTrue(replay(case).matched)
            import jev_catalogue_probe
            request = jev_catalogue_probe._restore(entry['request'])
            self.assertEqual(CapturedSelector([entry]).select(request).rubric_version, V2)
            for change in ('task', 'response', 'unknown'):
                bad = copy.deepcopy(entry)
                if change == 'task': bad['task_version'] = 'context-relevance-v1'
                if change == 'response': bad['response']['rubric_version'] = 'context-relevance-v1'
                if change == 'unknown': bad['task_version'] = 'context-relevance-v3'
                with self.subTest(change=change), self.assertRaises(ReplayUnavailable):
                    CapturedSelector([bad]).select(request)
        finally:
            fixture.doCleanups()

    def test_v2_core_keeps_pending_mandatory_and_fallback_owns_ambiguity(self):
        import test_context
        fixture = test_context.ContextTests('runTest'); fixture.setUp()
        try:
            fixture.seed('cucina', commit=False)
            fixture.config = replace(fixture.config, selection=replace(CONFIG, rubric_version=V2))
            for value, fallback in ((.1, None), (.5, 'abstained')):
                model = test_context.PackModel()
                core = fixture.core(model, selector=jev.JevSelector(fixture.config.selection, 'fixture-secret'))
                calls = []
                scores = lambda p: {r['id']: value for r in p['state']['candidates']}
                with patch('http.client.HTTPSConnection', connection(scores, calls)):
                    result = core.accept(fixture.message(text='Sì, confermo.'))
                trace = core.inspect(result['communication_id'])['trace']
                entry = trace['selection'][0]
                self.assertEqual(entry['fallback_reason'], fallback)
                mandatory = {r['id'] for r in json.loads(calls[0])['state']['permitted_context']}
                self.assertTrue(mandatory)
                self.assertTrue(mandatory <= {r.id for r in model.packs[0].records})
                self.assertFalse(mandatory & set(json.loads(calls[0])['questions']))
                self.assertNotIn('payload_json', calls[0].decode())
                self.assertNotIn('disclosure_purposes', calls[0].decode())
                self.assertEqual(core.inspect_history()['claims'], [])
        finally:
            fixture.doCleanups()


class RelevanceProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = fixture_capture()

    def setUp(self):
        import builtins
        from pathlib import Path
        from types import SimpleNamespace
        import tempfile
        import jev_catalogue_probe as legacy
        import jev_relevance_probe as probe
        from piecetogether import jev_smoke
        self.probe, self.legacy, self.smoke = probe, legacy, jev_smoke
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / 'piecetogether/jev-usd5-smoke'
        self.env = self.home / 'fixture.env'
        self.env.write_text('PT_TYPESAFE_API_KEY=fixture-secret\nOTHER_SECRET=not-selected\n')
        forbidden = {Path.cwd() / '.env', Path.cwd() / 'config/.env'}
        original_open, path_open = builtins.open, Path.open
        def checked_open(path, *args, **kw):
            if isinstance(path, (str, Path)) and Path(path).absolute() in forbidden:
                raise AssertionError('real dotenv forbidden')
            return original_open(path, *args, **kw)
        def checked_path(path, *args, **kw):
            if path.absolute() in forbidden: raise AssertionError('real dotenv forbidden')
            return path_open(path, *args, **kw)
        for p in (patch.object(jev_smoke, '_ledger_root', return_value=self.root),
                patch.object(legacy, '_ENV_PATH', self.env),
                patch.object(jev_smoke.pwd, 'getpwuid', return_value=SimpleNamespace(
                    pw_dir=str(self.home), pw_gid=self.home.stat().st_gid)),
                patch.dict('os.environ', {}, clear=True),
                patch('builtins.open', checked_open), patch.object(Path, 'open', checked_path),
                patch('http.client.HTTPSConnection', side_effect=AssertionError('unfaked HTTP'))):
            p.start(); self.addCleanup(p.stop)

    def plan(self):
        self.assertTrue(hasattr(self.probe, 'build_plan'), 'fixed v2 compiler missing')
        return self.probe.build_plan(self.capture)

    def requests(self, plan):
        return tuple(self.legacy._restore(c['request']) for c in plan['cases'])

    def seed_prior(self, plan):
        import os
        prior = self.legacy.build_plan(self.capture)
        self.smoke.reserve_once()
        def write(name, value):
            (self.root / name).write_bytes((json.dumps(value, sort_keys=True) + '\n').encode())
            os.chmod(self.root / name, 0o600)
        import hashlib
        write('catalogue-followup.json', {'experiment': 'jev-catalogue-followup-v1',
            'plan_hash': prior['plan_hash'], 'max_new_calls': 5, 'prior_recorded_attempts': 1,
            'original_record_sha256': hashlib.sha256((self.root / 'reservation.json').read_bytes()).hexdigest(),
            'request_digests': [c['request_digest'] for c in prior['cases']]})
        for i, c in enumerate(prior['cases']):
            write(f'catalogue-attempt-{i + 1}.json', {'plan_hash': prior['plan_hash'], 'slot': i + 1,
                                                  'request_digest': c['request_digest']})
        return {p.name: p.read_bytes() for p in self.root.iterdir()}

    def guard(self, plan, **kw):
        self.assertTrue(hasattr(self.smoke, 'JevV2BoundedSelector'), 'fixed v2 guard missing')
        return self.smoke.JevV2BoundedSelector(replace(CONFIG, rubric_version=V2), 'fixture-secret',
            self.requests(plan), plan_hash=plan['plan_hash'], prior_plan_hash=plan['prior_plan_hash'],
            prior_request_digests=tuple(plan['prior_request_digests']), ack_no_off_credit_charges=True, **kw)

    def run_plan(self, plan, name='out', scores=None):
        calls = []
        scores = scores or (lambda p: {r['id']: .9 if r['kind'] == 'context' else .1 for r in p['state']['candidates']})
        with patch('http.client.HTTPSConnection', connection(scores, calls)):
            report = self.probe._run(plan, self.home / name)
        return report, calls

    def test_five_literal_cases_frozen_expectations_and_prior_binding(self):
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        requests = self.requests(plan)
        self.assertEqual([json.loads(r.communication_json)['text'] for r in requests], [
            'cucina e bagno', 'Riprendiamo il contesto cucina, non quello bagno.', 'Buongiorno, grazie.',
            "Mi riferisco all'entità chiamata bagno, non al contesto della conversazione.", 'Sì, confermo.'])
        ids = {(r.kind, r.summary): r.id for r in requests[0].candidates if r.kind in ('context', 'entity')}
        self.assertEqual([set(c['expected_positive_ids']) for c in plan['cases']], [
            {ids['context', '"context" {"label": "cucina"}'], ids['context', '"context" {"label": "bagno"}']},
            {ids['context', '"context" {"label": "cucina"}']}, set(), {ids['entity', '{"label": "bagno"}']}, set()])
        self.assertEqual(len({c['provider_body_digest'] for c in plan['cases']}), 5)
        self.assertEqual(len(requests[-1].permitted_context), 1)
        self.assertTrue(requests[-1].permitted_context[0].mandatory)
        self.assertNotIn(requests[-1].permitted_context[0].id, {r.id for r in requests[-1].candidates})
        prior = self.legacy.build_plan(self.capture)
        self.assertEqual(plan['prior_plan_hash'], prior['plan_hash'])
        self.assertEqual(plan['prior_request_digests'], [c['request_digest'] for c in prior['cases']])
        changed = copy.deepcopy(self.capture)
        response = changed['steps'][0]['selection'][0]['response']
        for a in response['assessments']: a['value'] = .99
        self.assertEqual([c['expected_positive_ids'] for c in self.probe.build_plan(changed)['cases']],
                         [c['expected_positive_ids'] for c in plan['cases']])
        self.assertFalse(self.root.exists())

    def test_offline_default_and_hash_mismatch_are_inert(self):
        from contextlib import redirect_stdout
        import io
        plan = self.plan()
        capture = self.home / 'capture.json'; capture.write_text(encoded(self.capture))
        with patch.object(self.legacy, '_load_key', side_effect=AssertionError('key')), \
                patch.object(self.smoke.pwd, 'getpwuid', side_effect=AssertionError('NSS')), \
                patch.object(self.smoke, '_ledger_root', side_effect=AssertionError('ledger')), redirect_stdout(io.StringIO()):
            self.assertEqual(self.probe.main([]), 0)
            self.assertEqual(self.probe.main(['--capture', str(capture)]), 0)
            self.assertEqual(self.probe.main(['--live', '--capture', str(capture), '--output', str(self.home / 'out'),
                '--expected-plan-hash', '0' * 64, '--ack-owner-prepaid-usd5', '--ack-no-off-credit-charges']), 1)
        bad = copy.deepcopy(self.capture)
        bad['steps'][0]['selection'][0]['request']['candidates'][0]['payload_json'] = 'raw'
        with self.assertRaises(ValueError): self.probe.build_plan(bad)
        bad_plan = copy.deepcopy(plan); bad_plan['cases'][0]['expected_positive_ids'] = []
        with self.assertRaises(ValueError): self.probe._run(bad_plan, self.home / 'tampered')
        self.assertFalse(self.root.exists())

    def test_five_dispatches_bound_exact_prior_bytes_and_no_sixth_or_resume(self):
        import hashlib
        plan = self.plan(); before = self.seed_prior(plan); guard = self.guard(plan)
        calls = []
        with patch('http.client.HTTPSConnection', connection([.1] * 15, calls)):
            for request in self.requests(plan): guard.select(request)
            with self.assertRaises(SelectionFailure): guard.select(self.requests(plan)[0])
            with self.assertRaises(self.smoke.ReservationRefused): self.guard(plan).select(self.requests(plan)[0])
        self.assertEqual(len(calls), 5)
        for name, body in before.items(): self.assertEqual((self.root / name).read_bytes(), body)
        claim = json.loads((self.root / 'jev-v2-followup.json').read_text())
        self.assertEqual(claim['prior_claim_sha256'], hashlib.sha256(before['catalogue-followup.json']).hexdigest())
        self.assertEqual(claim['prior_recorded_attempts'], 6)
        self.assertNotIn('reserved_cents', claim)
        self.assertEqual(guard.recorded_slots, 5)
        with self.assertRaises(self.smoke.ReservationRefused): self.smoke.reserve_once()

    def test_all_prior_records_exact_before_each_dispatch_and_unknown_counts(self):
        import tempfile
        for name in ('reservation.json', 'catalogue-followup.json') + tuple(f'catalogue-attempt-{i}.json' for i in range(1, 6)):
            for body in (None, b'', b'{}', b'{'):
                with self.subTest(name=name, body=body), tempfile.TemporaryDirectory(dir=self.home) as temp:
                    from pathlib import Path
                    with patch.object(self.smoke, '_ledger_root', return_value=Path(temp) / 'piecetogether/jev-usd5-smoke'):
                        old_root = self.root; self.root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                        try:
                            plan = self.plan(); self.seed_prior(plan)
                            target = self.root / name
                            if body is None: target.unlink()
                            else: target.write_bytes(body)
                            guard = self.guard(plan)
                            with self.assertRaises(self.smoke.ReservationRefused): guard.select(self.requests(plan)[0])
                            self.assertFalse(guard.prior_batch_attempts_known)
                            self.assertIsNone(guard.verified_prior_recorded_attempts)
                            self.assertFalse((self.root / 'jev-v2-followup.json').exists())
                        finally: self.root = old_root
        plan = self.plan(); self.seed_prior(plan); guard = self.guard(plan)
        with patch('http.client.HTTPSConnection', connection([.1] * 15, [])):
            guard.select(self.requests(plan)[0])
            (self.root / 'catalogue-attempt-5.json').write_bytes(b'{}')
            with self.assertRaises(self.smoke.ReservationRefused): guard.select(self.requests(plan)[1])
        self.assertEqual(guard.attempted_dispatches, 1)
        self.assertFalse(guard.prior_batch_attempts_known)

    def test_lost_ancestry_clears_stale_reservation_evidence(self):
        import os
        plan = self.plan(); self.seed_prior(plan); guard = self.guard(plan)
        with patch('http.client.HTTPSConnection', connection([.1] * 15, [])):
            guard.select(self.requests(plan)[0])
            os.chmod(self.root, 0o775)
            with self.assertRaises(self.smoke.ReservationRefused): guard.select(self.requests(plan)[1])
        self.assertIsNone(guard.reserved_cents)
        self.assertIsNone(guard.verified_prior_recorded_attempts)

    def test_shared_guard_inert_constructor_strict_input_and_fixed_namespace(self):
        plan = self.plan()
        with patch.object(self.smoke, '_ledger_root', side_effect=AssertionError('ledger')), \
                patch.object(self.smoke.pwd, 'getpwuid', side_effect=AssertionError('NSS')):
            self.guard(plan)
        requests = self.requests(plan)
        for changes in ({'prior_plan_hash': 'bad'}, {'prior_request_digests': ('0' * 64,) * 5},
                {'planned_requests': requests[:4]}, {'planned_requests': (replace(requests[0], timeout_ms=2001),) + requests[1:]},
                {'planned_requests': (replace(requests[0], rubric_version='context-relevance-v1'),) + requests[1:]},
                {'ack_no_off_credit_charges': 1}, {'namespace': 'other'}):
            args = dict(plan_hash=plan['plan_hash'], prior_plan_hash=plan['prior_plan_hash'],
                prior_request_digests=tuple(plan['prior_request_digests']), planned_requests=requests,
                ack_no_off_credit_charges=True)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.smoke.JevV2BoundedSelector(replace(CONFIG, rubric_version=V2), 'fixture-secret', **args)

    def test_native_scores_adapter_routes_receipt_counts_and_semantic_failure_continues(self):
        plan = self.plan(); before = self.seed_prior(plan)
        report, calls = self.run_plan(plan)
        self.assertEqual(len(calls), 5)
        self.assertEqual(report['status'], 'completed_diagnostics_only')
        self.assertEqual(report['required_prior_recorded_attempts'], 6)
        self.assertEqual(report['prior_recorded_attempts'], 6)
        self.assertEqual(report['global_recorded_attempts'], 11)
        self.assertEqual(report['global_attempt_upper_bound'], 11)
        self.assertIsNone(report['actual_http_count'])
        self.assertEqual(report['reserved_cents'], 500)
        self.assertEqual(report['actual_cost'], 'UNRECONCILED')
        self.assertFalse(report['production_go'])
        self.assertFalse(report['provider_account_independently_verified'])
        self.assertFalse(report['cases'][2]['expected_set_match'])
        for case in report['cases']:
            self.assertIn('native_probabilities', case)
            self.assertEqual(case['routing_policy'], 'adapter yes>=0.7; no<=0.3; any middle -> application fallback')
        for name, body in before.items(): self.assertEqual((self.root / name).read_bytes(), body)
        for path in (self.home / 'out').iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('fixture-secret', path.read_text())
        second, calls = self.run_plan(plan, 'second')
        self.assertEqual(calls, [])
        self.assertIsNone(second['global_recorded_attempts'])
        self.assertIsNone(second['prior_recorded_attempts'])

    def test_strong_no_and_ambiguous_receipts_do_not_invent_native_abstention(self):
        import tempfile
        from pathlib import Path
        for value in (.1, .5):
            with self.subTest(value=value), tempfile.TemporaryDirectory(dir=self.home) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(self.smoke, '_ledger_root', return_value=root):
                    old = self.root; self.root = root
                    try:
                        plan = self.plan(); self.seed_prior(plan); calls = []
                        with patch('http.client.HTTPSConnection', connection([value] * 15, calls)):
                            report = self.probe._run(plan, Path(temp) / 'out')
                        self.assertEqual(len(calls), 5)
                        for case in report['cases']:
                            self.assertEqual(case['adapter_outcome'], 'completed' if value == .1 else 'abstained')
                            self.assertEqual(case['application_fallback'], value == .5)
                            self.assertEqual(len(case['ambiguous_ids']), 0 if value == .1 else 15)
                            self.assertNotIn('abstained', case['native_provider_response'])
                        self.assertEqual(report['cases'][2]['expected_set_match'], True if value == .1 else None)
                        self.assertEqual(report['cases'][4]['expected_set_match'], True if value == .1 else None)
                        self.assertEqual(len(report['cases'][4]['mandatory_ids_supplied']), 1)
                    finally: self.root = old

    def test_final_receipt_failure_retains_claim_and_forbids_restart(self):
        plan = self.plan(); before = self.seed_prior(plan); calls = []
        original = self.probe._write_private
        def fail(directory, name, value):
            if name == 'receipt.json': raise OSError('Bearer fixture-secret')
            original(directory, name, value)
        with patch.object(self.probe, '_write_private', side_effect=fail), \
                patch('http.client.HTTPSConnection', connection([.1] * 15, calls)):
            with self.assertRaises(OSError): self.probe._run(plan, self.home / 'receipt-error')
        self.assertEqual(len(calls), 5)
        with self.assertRaises(self.smoke.ReservationRefused): self.guard(plan).select(self.requests(plan)[0])
        for name, body in before.items(): self.assertEqual((self.root / name).read_bytes(), body)

    def test_terminal_http_errors_and_output_failure(self):
        import tempfile
        from pathlib import Path
        from test_jev_catalogue_probe import ProbeTests
        for mode in ('401', '402', '429', 'model', 'transport', 'usage_missing', 'usage_invalid', 'budget', 'echo', 'malformed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.home) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(self.smoke, '_ledger_root', return_value=root):
                    old = self.root; self.root = root
                    try:
                        plan = self.plan(); self.seed_prior(plan); calls = []
                        with patch('http.client.HTTPSConnection', ProbeTests.connection(self, mode, calls)):
                            report = self.probe._run(plan, Path(temp) / 'out')
                        self.assertEqual(len(calls), 1)
                        self.assertEqual(report['status'], 'failed')
                        self.assertIsNone(report['observed_usage'])
                        self.assertEqual(report['reserved_cents'], 500)
                    finally: self.root = old
        plan = self.plan(); self.seed_prior(plan)
        original = self.probe._write_private
        def fail(directory, name, value):
            if name == 'case-1.json': raise OSError('Bearer fixture-secret')
            original(directory, name, value)
        with patch.object(self.probe, '_write_private', side_effect=fail):
            report, calls = self.run_plan(plan)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['observed_usage']['input_tokens'], 100)

    def test_process_race_one_winner(self):
        import multiprocessing
        import threading
        plan = self.plan(); self.seed_prior(plan)
        ctx = multiprocessing.get_context('fork'); start, queue = ctx.Event(), ctx.Queue()
        def worker(trust):
            with patch('http.client.HTTPSConnection', connection([.1] * 15, [])):
                start.wait()
                try:
                    self.guard(plan, trust_primary_group=trust).select(self.requests(plan)[0]); queue.put('winner')
                except Exception: queue.put('refused')
        children = [ctx.Process(target=worker, args=(trust,)) for trust in (False, True, False)]
        for child in children: child.start()
        start.set(); results = [queue.get(timeout=10) for _ in children]
        for child in children: child.join(10); self.assertEqual(child.exitcode, 0)
        self.assertEqual(results.count('winner'), 1)

    def test_crash_partial_claim_fsync_and_fd_substitution_burn_without_dispatch(self):
        import os
        import tempfile
        from pathlib import Path
        for mode in ('claim_write', 'marker_write', 'claim_fsync', 'marker_fsync', 'directory_fsync', 'binding'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.home) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(self.smoke, '_ledger_root', return_value=root):
                    old = self.root; self.root = root
                    try:
                        plan = self.plan(); before = self.seed_prior(plan); guard = self.guard(plan)
                        write, sync, binding = self.smoke._write_all, os.fsync, self.smoke._check_binding
                        def interrupted(fd, body):
                            marker = b'"slot": 1' in body
                            if mode == ('marker_write' if marker else 'claim_write'):
                                os.write(fd, body[:7]); raise OSError('secret')
                            write(fd, body)
                        def fsync(fd):
                            import stat
                            node = os.fstat(fd); directory = stat.S_ISDIR(node.st_mode)
                            marker = root / 'jev-v2-attempt-1.json'
                            is_marker = marker.exists() and marker.stat().st_ino == node.st_ino
                            if ((mode == 'directory_fsync' and directory) or
                                    mode == 'marker_fsync' and is_marker or
                                    mode == 'claim_fsync' and not directory and not is_marker): raise OSError('secret')
                            sync(fd)
                        def bound(parent, name, fd):
                            if mode == 'binding' and name == 'jev-v2-attempt-1.json':
                                (root / name).rename(root / 'moved')
                                (root / name).write_bytes(b'{}')
                            binding(parent, name, fd)
                        with patch.object(self.smoke, '_write_all', side_effect=interrupted), \
                                patch.object(self.smoke.os, 'fsync', side_effect=fsync), \
                                patch.object(self.smoke, '_check_binding', side_effect=bound):
                            with self.assertRaises(self.smoke.ReservationRefused): guard.select(self.requests(plan)[0])
                        with self.assertRaises(self.smoke.ReservationRefused): self.guard(plan).select(self.requests(plan)[0])
                        self.assertEqual(guard.attempted_dispatches, 0)
                        self.assertFalse(guard.prior_batch_attempts_known)
                        for name, body in before.items(): self.assertEqual((root / name).read_bytes(), body)
                    finally: self.root = old

    def test_v2_nodes_strict_group_ancestry_and_prior_private_records(self):
        import os
        import tempfile
        from pathlib import Path
        for mode in ('symlink', 'hardlink', 'permissions', 'whitespace', 'group', 'world', 'private_group'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.home) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(self.smoke, '_ledger_root', return_value=root):
                    old = self.root; self.root = root
                    try:
                        plan = self.plan(); self.seed_prior(plan)
                        marker = root / 'catalogue-attempt-5.json'
                        if mode == 'symlink': marker.rename(root / 'target'); marker.symlink_to(root / 'target')
                        if mode == 'hardlink': os.link(marker, root / 'alias')
                        if mode == 'permissions': os.chmod(marker, 0o640)
                        if mode == 'whitespace': marker.write_bytes(marker.read_bytes() + b' ')
                        if mode == 'group': os.chmod(temp, 0o775)
                        if mode == 'world': os.chmod(temp, 0o777)
                        if mode == 'private_group': os.chmod(root, 0o775)
                        with self.assertRaises(self.smoke.ReservationRefused): self.guard(plan).select(self.requests(plan)[0])
                        with patch('http.client.HTTPSConnection', connection([.1] * 15, [])):
                            if mode == 'group':
                                self.guard(plan, trust_primary_group=True).select(self.requests(plan)[0])
                            else:
                                with self.assertRaises(self.smoke.ReservationRefused):
                                    self.guard(plan, trust_primary_group=True).select(self.requests(plan)[0])
                    finally: self.root = old

    def test_current_claim_and_all_markers_rechecked_and_old_protocols_stay_closed(self):
        import tempfile
        from pathlib import Path
        for name in ('jev-v2-followup.json', 'jev-v2-attempt-1.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=self.home) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(self.smoke, '_ledger_root', return_value=root):
                    old = self.root; self.root = root
                    try:
                        plan = self.plan(); self.seed_prior(plan); guard = self.guard(plan); calls = []
                        with patch('http.client.HTTPSConnection', connection([.1] * 15, calls)):
                            guard.select(self.requests(plan)[0]); (root / name).write_bytes(b'{}')
                            with self.assertRaises(self.smoke.ReservationRefused): guard.select(self.requests(plan)[1])
                            with self.assertRaises(SelectionFailure): guard.select(self.requests(plan)[2])
                        self.assertEqual(len(calls), 1)
                        prior = self.legacy.build_plan(self.capture)
                        requests = tuple(self.legacy._restore(c['request']) for c in prior['cases'])
                        with self.assertRaises(self.smoke.ReservationRefused):
                            self.smoke.FollowupJevSelector(CONFIG, 'fixture-secret', requests,
                                plan_hash=prior['plan_hash'], ack_no_off_credit_charges=True).select(requests[0])
                        with self.assertRaises(self.smoke.ReservationRefused):
                            self.smoke.GuardedJevSelector(CONFIG, 'fixture-secret').select(requests[0])
                    finally: self.root = old

    def test_durable_records_precede_http_and_crash_never_resumes(self):
        import multiprocessing
        import os
        plan = self.plan(); before = self.seed_prior(plan)
        ctx = multiprocessing.get_context('fork')
        def crash():
            original = self.smoke._write_all
            def write(fd, body):
                if b'"slot": 1' in body:
                    os.write(fd, body[:12]); os._exit(23)
                original(fd, body)
            with patch.object(self.smoke, '_write_all', side_effect=write):
                self.guard(plan).select(self.requests(plan)[0])
        child = ctx.Process(target=crash); child.start(); child.join(10)
        self.assertEqual(child.exitcode, 23)
        self.assertEqual((self.root / 'jev-v2-attempt-1.json').stat().st_size, 12)
        with self.assertRaises(self.smoke.ReservationRefused): self.guard(plan).select(self.requests(plan)[0])
        for name, body in before.items(): self.assertEqual((self.root / name).read_bytes(), body)

    def test_same_guard_threads_serialize_native_dispatch(self):
        import threading
        plan = self.plan(); self.seed_prior(plan); guard = self.guard(plan)
        entered, release = threading.Event(), threading.Event()
        calls, outcomes = [], []
        base = connection([.1] * 15, calls)
        class Blocking(base):
            def getresponse(self):
                entered.set(); release.wait(5)
                return super().getresponse()
        def select(request):
            try: guard.select(request); outcomes.append('ok')
            except Exception: outcomes.append('failed')
        requests = self.requests(plan)
        with patch('http.client.HTTPSConnection', Blocking):
            first = threading.Thread(target=select, args=(requests[0],))
            second = threading.Thread(target=select, args=(requests[1],))
            first.start(); self.assertTrue(entered.wait(5)); second.start()
            self.assertEqual(len(calls), 1)
            release.set(); first.join(5); second.join(5)
        self.assertEqual(outcomes, ['ok', 'ok'])
        self.assertEqual(guard.recorded_slots, 2)

    def test_deadline_burns_slot_and_echo_failure_is_terminal(self):
        from piecetogether.context import BudgetExhausted
        plan = self.plan(); self.seed_prior(plan); guard = self.guard(plan)
        with patch.object(self.smoke, 'monotonic', side_effect=(0, 0, 15)):
            with self.assertRaises(BudgetExhausted): guard.select(self.requests(plan)[0])
        self.assertEqual(guard.recorded_slots, 1)
        self.assertEqual(guard.attempted_dispatches, 0)
        with self.assertRaises(SelectionFailure): guard.select(self.requests(plan)[0])

    def test_interrupt_during_second_marker_cannot_report_exact_global_count(self):
        import os
        plan = self.plan(); self.seed_prior(plan); guard = self.guard(plan)
        requests = self.requests(plan)
        with patch('http.client.HTTPSConnection', connection([.1] * 15, [])):
            guard.select(requests[0])
        self.assertTrue(guard.prior_batch_attempts_known)
        original = self.smoke._write_all
        def interrupt(fd, body):
            if b'"slot": 2' in body:
                os.write(fd, body[:7]); raise KeyboardInterrupt()
            original(fd, body)
        with patch.object(self.smoke, '_write_all', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt): guard.select(requests[1])
        self.assertFalse(guard.prior_batch_attempts_known)
        with self.assertRaises(SelectionFailure): guard.select(requests[1])
        self.assertEqual(guard.attempted_dispatches, 1)

    def test_batch_deadline_includes_final_response_not_only_next_dispatch(self):
        plan = self.plan(); self.seed_prior(plan)
        now, calls = [0], []
        base = connection([.1] * 15, calls)
        class LateFinal(base):
            def getresponse(self):
                response = super().getresponse()
                if len(calls) == 5: now[0] = 16
                return response
        with patch.object(self.probe, 'monotonic', side_effect=lambda: now[0]), \
                patch('http.client.HTTPSConnection', LateFinal):
            report = self.probe._run(plan, self.home / 'late-final')
        self.assertEqual(len(calls), 5)
        self.assertEqual(report['status'], 'failed')

    def test_explicit_child_only_reads_temp_selected_key_and_fsyncs_before_posts(self):
        import multiprocessing
        import os
        from contextlib import redirect_stdout
        import io
        plan = self.plan(); before = self.seed_prior(plan)
        ctx = multiprocessing.get_context('fork'); count = ctx.Value('i', 0)
        calls = []; synced = set(); sync = os.fsync
        base = connection([.1] * 15, calls)
        def fsync(fd):
            sync(fd); node = os.fstat(fd); synced.add((node.st_dev, node.st_ino))
        class Verified(base):
            def request(instance, method, path, body, headers):
                self.assertEqual(headers['Authorization'], 'Bearer fixture-secret')
                with count.get_lock(): count.value += 1; slot = count.value
                for name in ('jev-v2-followup.json', f'jev-v2-attempt-{slot}.json'):
                    node = (self.root / name).stat()
                    self.assertIn((node.st_dev, node.st_ino), synced)
                node = self.root.stat(); self.assertIn((node.st_dev, node.st_ino), synced)
                super().request(method, path, body, headers)
        loader = self.legacy._load_key; parent = os.getpid()
        def key():
            self.assertNotEqual(os.getpid(), parent)
            return loader()
        capture = self.home / 'capture.json'; capture.write_text(encoded(self.capture))
        output = self.home / 'child-output'
        with patch('http.client.HTTPSConnection', Verified), patch.object(self.smoke.os, 'fsync', side_effect=fsync), \
                patch.object(self.legacy, '_load_key', side_effect=key), redirect_stdout(io.StringIO()):
            code = self.probe.main(['--live', '--capture', str(capture), '--output', str(output),
                '--expected-plan-hash', plan['plan_hash'], '--ack-owner-prepaid-usd5', '--ack-no-off-credit-charges'])
        self.assertEqual(code, 0)
        self.assertEqual(count.value, 5)
        report = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(report['global_recorded_attempts'], 11)
        for name, body in before.items(): self.assertEqual((self.root / name).read_bytes(), body)
