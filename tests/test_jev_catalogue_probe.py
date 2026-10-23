"""Portable offline diagnostics proofs. Never use operator captures or credentials."""
from contextlib import redirect_stdout
from dataclasses import asdict, replace
import io
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from piecetogether import jev, jev_smoke
from piecetogether.context import Assessment, SelectionConfig, SelectionFailure, SelectionResult, encoded

CONFIG = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)


def fixture_capture():
    import test_context
    from piecetogether.evaluation import capture
    fixture = test_context.ContextTests('runTest')
    fixture.setUp()
    try:
        fixture.seed('cucina')
        fixture.seed('bagno')
        fixture.config = replace(fixture.config, selection=CONFIG)
        class Selector:
            provider_id = 'typesafe'
            model_id = 'jev-1.13.0'
            def select(self, req):
                scores = tuple(Assessment(r.id, 'noul', .44 if r.kind == 'context' else .4) for r in req.candidates)
                return SelectionResult('abstained', (), scores, self.provider_id, self.model_id,
                                       req.rubric_version, 4529, 759)
        return capture(fixture.core(test_context.OperationsModel(()), selector=Selector()),
                       lambda recorder: recorder.accept(fixture.message(text='cucina e bagno'))).to_dict()
    finally:
        fixture.doCleanups()


def worker(root, requests, plan_hash, start, queue, trust):
    from piecetogether.jev_smoke import FollowupJevSelector
    with patch.object(jev_smoke, '_ledger_root', return_value=Path(root)), \
            patch.object(jev.JevSelector, 'select', side_effect=lambda r: safe_result(r)):
        start.wait()
        try:
            FollowupJevSelector(CONFIG, 'fixture-secret', requests, plan_hash=plan_hash,
                               ack_no_off_credit_charges=True, trust_primary_group=trust).select(requests[0])
            queue.put('winner')
        except Exception:
            queue.put('refused')


def safe_result(req):
    return SelectionResult('abstained', (), tuple(Assessment(r.id, 'noul', .4) for r in req.candidates),
                           'typesafe', 'jev-1.13.0', req.rubric_version, 12, 3)


def crash_followup(root, requests, plan_hash):
    original = jev_smoke._write_all
    def interrupted(fd, body):
        if b'"slot": 1' in body:
            os.write(fd, body[:12])
            os._exit(23)
        original(fd, body)
    with patch.object(jev_smoke, '_ledger_root', return_value=Path(root)), \
            patch.object(jev_smoke, '_write_all', side_effect=interrupted):
        jev_smoke.FollowupJevSelector(CONFIG, 'fixture-secret', requests,
            plan_hash=plan_hash, ack_no_off_credit_charges=True).select(requests[0])


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = fixture_capture()

    def setUp(self):
        import jev_catalogue_probe as probe
        self.probe = probe
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from test_jev_smoke import temporary_account
        temporary_account(self, self.temp.name)
        self.root = Path(self.temp.name) / 'piecetogether/jev-usd5-smoke'
        self.env = Path(self.temp.name) / 'fixture.env'
        self.env.write_text('PT_TYPESAFE_API_KEY=fixture-secret\nOTHER_SECRET=must-not-load\n')
        for obj, name, value in ((jev_smoke, '_ledger_root', lambda: self.root),
                                 (probe, '_ENV_PATH', self.env)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.plan = probe.build_plan(self.capture)
        self.requests = tuple(probe._restore(case['request']) for case in self.plan['cases'])

    def reserve(self):
        jev_smoke.reserve_once()
        return (self.root / 'reservation.json').read_bytes()

    def selector(self, **kw):
        return jev_smoke.FollowupJevSelector(CONFIG, 'fixture-secret', self.requests,
            plan_hash=self.plan['plan_hash'], ack_no_off_credit_charges=True, **kw)

    def test_payload_wire_equal_and_five_frozen_distinct_bodies(self):
        self.assertEqual(self.plan, self.probe.build_plan(self.capture))
        self.assertEqual(len(self.requests), 5)
        bodies = [encoded(jev._payload(r, CONFIG.model_id)) for r in self.requests]
        self.assertEqual(len(set(bodies)), 5)
        original = self.probe._restore(self.capture['steps'][0]['selection'][0]['request'])
        def compact(r):
            return {'id': r.id, 'kind': r.kind, 'summary': r.summary, 'revision': r.revision}
        old = {'model': 'jev-1.13.0', 'state': {'communication': json.loads(original.communication_json),
               'candidates': [compact(r) for r in original.candidates],
               'permitted_context': [compact(r) for r in original.permitted_context]},
               'questions': {r.id: {'type': 'noul', 'instructions': {'question': jev.QUESTION, 'candidate_id': r.id},
                   'criteria': {'true': 'Needed to interpret this turn',
                                'false': 'Irrelevant, speculative, or general retrieval'}} for r in original.candidates}}
        self.assertEqual(encoded(jev._payload(original, 'jev-1.13.0')).encode(), encoded(old).encode())
        self.assertNotIn(encoded(old), bodies)
        for case, request in zip(self.plan['cases'], self.requests):
            body = json.loads(encoded(jev._payload(request, 'jev-1.13.0')))
            inverse = case['to_original']
            self.assertEqual([inverse[k] for k in body['questions']], sorted(r.id for r in original.candidates))
            self.assertEqual(request.minimum_relevance, .7)
            self.assertLessEqual(request.timeout_ms, 1000)
            self.assertEqual({v['instructions']['question'] for v in body['questions'].values()}, {jev.QUESTION})

    def test_closed_profile_rejects_real_or_malformed_inputs(self):
        import copy
        for change in ('text', 'attachments', 'actor', 'extra', 'summary', 'missing', 'permitted'):
            data = copy.deepcopy(self.capture)
            req = data['steps'][0]['selection'][0]['request']
            comm = json.loads(req['communication_json'])
            if change == 'text': comm['text'] = 'customer secret'
            if change == 'attachments': comm['attachments'] = ['secret']
            if change == 'actor': comm['actor_id'] = 'customer'
            if change == 'extra': comm['secret'] = 'customer'
            if change == 'summary': req['candidates'][0]['summary'] = 'customer'
            if change == 'missing': req['candidates'].pop()
            if change == 'permitted': req['permitted_context'] = [req['candidates'][0]]
            req['communication_json'] = encoded(comm)
            with self.subTest(change=change), self.assertRaises(ValueError): self.probe.build_plan(data)
        self.assertFalse(self.root.exists())

    def test_default_and_offline_plan_are_inert(self):
        capture_path = Path(self.temp.name) / 'capture.json'
        capture_path.write_text(encoded(self.capture))
        with patch.object(jev_smoke.pwd, 'getpwuid', side_effect=AssertionError('NSS')) as nss, \
                patch('piecetogether.core._load_local_env', side_effect=AssertionError('env')) as loader, \
                patch('http.client.HTTPSConnection', side_effect=AssertionError('network')) as http, \
                patch.object(jev_smoke, '_ledger_root', side_effect=AssertionError('ledger')) as root, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(self.probe.main([]), 0)
            self.assertEqual(self.probe.main(['--capture', str(capture_path)]), 0)
            self.assertEqual((nss.call_count, loader.call_count, http.call_count, root.call_count), (0, 0, 0, 0))

    def test_old_record_required_exact_and_never_created(self):
        for body in (None, b'', b'{', b'{}', b'{"reserved_cents":500}'):
            if body is not None:
                self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
                os.chmod(self.root.parent, 0o700)
                (self.root / 'reservation.json').write_bytes(body)
                os.chmod(self.root / 'reservation.json', 0o600)
            guard = self.selector()
            with patch.object(jev.JevSelector, 'select') as delegate, self.assertRaises(jev_smoke.ReservationRefused):
                guard.select(self.requests[0])
            self.assertEqual(delegate.call_count, 0)
            self.assertIsNone(guard.reserved_cents)
            self.assertFalse((self.root / 'catalogue-followup.json').exists())
        self.assertEqual((self.root / 'reservation.json').read_bytes(), b'{"reserved_cents":500}')

    def test_five_durable_slots_restart_and_old_smoke_refuse(self):
        before = self.reserve()
        guard = self.selector()
        def delegate(req):
            slot = guard.recorded_slots
            marker = self.root / f'catalogue-attempt-{slot}.json'
            self.assertTrue(marker.exists())
            self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
            return safe_result(req)
        with patch.object(jev.JevSelector, 'select', side_effect=delegate) as call:
            for req in self.requests: guard.select(req)
            with self.assertRaises(SelectionFailure): guard.select(self.requests[0])
            self.assertEqual(call.call_count, 5)
            with self.assertRaises(jev_smoke.ReservationRefused): self.selector().select(self.requests[0])
        claim = json.loads((self.root / 'catalogue-followup.json').read_text())
        self.assertEqual(claim['max_new_calls'], 5)
        self.assertEqual(claim['prior_recorded_attempts'], 1)
        self.assertNotIn('reserved_cents', claim)
        self.assertEqual(claim['plan_hash'], self.plan['plan_hash'])
        self.assertEqual((self.root / 'reservation.json').read_bytes(), before)
        with self.assertRaises(jev_smoke.ReservationRefused): jev_smoke.reserve_once()

    def test_unplanned_and_error_are_terminal(self):
        self.reserve()
        guard = self.selector()
        with patch.object(jev.JevSelector, 'select') as call:
            with self.assertRaises(SelectionFailure): guard.select(self.requests[1])
            with self.assertRaises(SelectionFailure): guard.select(self.requests[0])
            self.assertEqual(call.call_count, 0)
        self.assertFalse((self.root / 'catalogue-followup.json').exists())

    def test_threads_serialize_and_processes_cross_mode_one_winner(self):
        self.reserve()
        ctx = multiprocessing.get_context('fork')
        start, queue = ctx.Event(), ctx.Queue()
        children = [ctx.Process(target=worker, args=(str(self.root), self.requests, self.plan['plan_hash'], start, queue, mode))
                    for mode in (True, False, True, False)]
        for child in children: child.start()
        start.set()
        outcomes = [queue.get(timeout=10) for _ in children]
        for child in children:
            child.join(10)
            self.assertEqual(child.exitcode, 0)
        self.assertEqual(outcomes.count('winner'), 1)

    def connection(self, mode, calls):
        class Response:
            status = int(mode) if mode in ('401', '402', '429') else 200
            def read1(self, limit):
                chunk, self.body = self.body[:limit], self.body[limit:]
                return chunk
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs):
                self.timeout = kwargs['timeout']
            def connect(self): pass
            def request(self, method, path, body, headers):
                calls.append(json.loads(body))  # Count HTTP sends, never Core/delegate work.
                self.data = calls[-1]
                if mode == 'transport': raise OSError('Authorization: Bearer fixture-secret')
            def getresponse(self):
                records = self.data['state']['candidates']
                scores = {r['id']: (.4 if mode == 'abstained' else .93 if r['kind'] == 'context' else .02)
                          for r in records}
                if mode == 'position' and len(calls) in (2, 3):
                    scores = {r['id']: i / 20 for i, r in enumerate(records)}
                data = {'model': 'jev-1.14.0' if mode == 'model' else 'jev-1.13.0',
                        'answers': {k: {'type': 'noul', 'noul': v} for k, v in scores.items()},
                        'usage': {'input_tokens': 100, 'output_tokens': 10}}
                if mode == 'usage_missing': del data['usage']
                if mode == 'usage_invalid': data['usage']['input_tokens'] = True
                if mode == 'budget': data['usage']['input_tokens'] = 65537
                if mode == 'echo': data['Authorization'] = 'Bearer fixture-secret'
                result = Response()
                result.body = b'{"bad":"Bearer fixture-secret"' if mode == 'malformed' else encoded(data).encode()
                return result
            def close(self): pass
        return Connection

    def invoke(self, mode='success', output=None):
        calls = []
        output = output or Path(self.temp.name) / ('out-' + mode)
        with patch('http.client.HTTPSConnection', self.connection(mode, calls)), redirect_stdout(io.StringIO()):
            report = self.probe._run(self.plan, output)
        return report, calls, output

    def test_fake_live_five_http_posts_private_receipts_and_no_secret(self):
        before = self.reserve()
        report, calls, output = self.invoke()
        self.assertEqual(len(calls), 5)
        self.assertEqual(report['status'], 'completed_diagnostics_only')
        self.assertIsNone(report['position_tracking_suspected'])
        self.assertEqual(report['stop_rule'], 'not_triggered')
        self.assertEqual(report['invocation_recorded_slots'], 5)
        self.assertEqual(report['global_recorded_attempts'], 6)
        self.assertIsNone(report['actual_http_count'])
        self.assertEqual(report['reserved_cents'], 500)
        self.assertEqual(report['actual_cost'], 'UNRECONCILED')
        self.assertFalse(report['provider_account_independently_verified'])
        self.assertFalse(report['production_go'])
        self.assertEqual(report['cases'][0]['metrics']['required_selected'], 2)
        self.assertEqual(report['cases'][0]['metrics']['wrong_selected'], 0)
        self.assertIsNone(report['false_accept_rate'])
        self.assertEqual((self.root / 'reservation.json').read_bytes(), before)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for artifact in output.iterdir():
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
            text = artifact.read_text()
            for secret in ('fixture-secret', 'OTHER_SECRET', 'must-not-load', 'Authorization', 'Bearer '):
                self.assertNotIn(secret, text)
        refused, calls, _ = self.invoke(output=Path(self.temp.name) / 'second')
        self.assertEqual(calls, [])
        self.assertIsNone(refused['global_recorded_attempts'])
        self.assertEqual(refused['global_attempt_upper_bound'], 6)
        self.assertEqual(refused['invocation_recorded_slots'], 0)
        self.assertEqual(refused['reserved_cents'], 500)

    def test_direct_http_error_counters_stop_batch_and_preserve_hold(self):
        for mode in ('401', '402', '429', 'model', 'transport', 'usage_missing', 'usage_invalid',
                     'budget', 'echo', 'malformed'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    jev_smoke.reserve_once()
                    report, calls, output = self.invoke(mode, Path(temp) / 'out')
                self.assertEqual(len(calls), 1)
                self.assertEqual(report['status'], 'failed')
                self.assertIsNone(report['observed_usage'])
                self.assertEqual(report['reserved_cents'], 500)
                self.assertEqual(report['invocation_selector_delegations'], 1)
                for file in output.iterdir():
                    self.assertNotIn('fixture-secret', file.read_text())
                self.assertEqual(json.loads((root / 'reservation.json').read_text())['reserved_cents'], 500)

    def test_completed_empty_selection_reports_known_zero_counts(self):
        scores = {record.id: 0.0 for record in self.requests[0].candidates}
        result = SelectionResult('completed', (), (), 'typesafe', 'jev-1.13.0',
                                 'context-relevance-v1', 12, 3)
        metrics = self.probe._metrics(scores, self.plan['required_ids'], result)
        self.assertEqual(metrics['required_selected'], 0)
        self.assertEqual(metrics['required_recall'], 0.0)
        self.assertEqual(metrics['wrong_selected'], 0)
        self.assertIsNone(metrics['false_accept_rate'])

    def test_failed_before_comparison_reports_untested_position_rule(self):
        self.reserve()
        report, calls, _ = self.invoke('401')
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['cases'], [])
        self.assertIsNone(report['position_tracking_suspected'])
        self.assertEqual(report['stop_rule'], 'not_tested')

    def test_abstention_data_continues_frozen_plan_but_metrics_undefined(self):
        self.reserve()
        report, calls, _ = self.invoke('abstained')
        self.assertEqual(len(calls), 5)
        for case in report['cases']:
            self.assertEqual(case['outcome'], 'abstained')
            self.assertIsNone(case['metrics']['required_recall'])
            self.assertIsNone(case['metrics']['wrong_selected'])
        self.assertEqual([self.probe._hash(c) for c in calls],
                         [c['provider_body_digest'] for c in self.plan['cases']])

    def test_positional_tracking_stops_at_readable_pair(self):
        self.reserve()
        report, calls, _ = self.invoke('position')
        self.assertEqual(len(calls), 3)
        self.assertEqual(report['status'], 'stopped_position_tracking_suspected')
        self.assertTrue(report['position_tracking_suspected'])
        self.assertEqual(report['stop_rule'], 'triggered')
        self.assertFalse(report['production_go'])
        self.assertEqual(report['reserved_cents'], 500)

    def test_output_failure_stops_before_next_send(self):
        self.reserve()
        original = self.probe._write_private
        def write(directory, name, value):
            if name == 'case-1.json': raise OSError('Bearer fixture-secret')
            original(directory, name, value)
        with patch.object(self.probe, '_write_private', side_effect=write):
            report, calls, output = self.invoke()
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['reserved_cents'], 500)
        self.assertNotIn('fixture-secret', (output / 'receipt.json').read_text())

    def test_no_secret_or_claim_before_hash_profile_output_validation(self):
        capture_path = Path(self.temp.name) / 'capture.json'
        capture_path.write_text(encoded(self.capture))
        args = ['--live', '--capture', str(capture_path), '--output', str(Path(self.temp.name) / 'out'),
                '--expected-plan-hash', '0' * 64, '--ack-owner-prepaid-usd5', '--ack-no-off-credit-charges']
        with patch.object(self.probe, '_load_key', side_effect=AssertionError('secret')) as key, \
                patch('http.client.HTTPSConnection') as http, redirect_stdout(io.StringIO()):
            self.assertEqual(self.probe.main(args), 1)
            self.assertEqual((key.call_count, http.call_count), (0, 0))
            for missing in ('--ack-owner-prepaid-usd5', '--ack-no-off-credit-charges'):
                with self.assertRaises(SystemExit): self.probe.main([a for a in args if a != missing])
            with self.assertRaises(ValueError): self.probe._run(self.plan, Path(self.temp.name))
            self.assertEqual(key.call_count, 0)
        self.assertFalse(self.root.exists())

    def test_environment_loader_selects_only_temp_key_and_restores_environment(self):
        from piecetogether import core
        original = core._load_local_env
        seen = []
        def loader(path):
            self.assertEqual(path, self.env)
            original(path)
            seen.append(dict(os.environ))
        previous = os.environ
        with patch.object(core, '_load_local_env', side_effect=loader):
            self.assertEqual(self.probe._load_key(), 'fixture-secret')
        self.assertIs(os.environ, previous)
        self.assertEqual(seen, [{'PT_TYPESAFE_API_KEY': 'fixture-secret'}])

    def test_marker_write_fsync_errors_and_crash_are_nonresumable(self):
        for stage in ('claim_write', 'marker_write', 'claim_fsync', 'marker_fsync', 'directory_fsync'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(dir=self.temp.name) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    jev_smoke.reserve_once()
                    old = (root / 'reservation.json').read_bytes()
                    original_write, original_sync = jev_smoke._write_all, os.fsync
                    def write(fd, body):
                        marker = b'"slot": 1' in body
                        if stage == ('marker_write' if marker else 'claim_write'):
                            os.write(fd, body[:7])
                            raise OSError('fixture-secret')
                        original_write(fd, body)
                    def sync(fd):
                        import stat
                        node = os.fstat(fd)
                        is_dir = stat.S_ISDIR(node.st_mode)
                        marker = root / 'catalogue-attempt-1.json'
                        is_marker = marker.exists() and node.st_ino == marker.stat().st_ino
                        if ((stage == 'directory_fsync' and is_dir)
                                or stage == 'marker_fsync' and is_marker
                                or stage == 'claim_fsync' and not is_dir and not is_marker):
                            raise OSError('fixture-secret')
                        original_sync(fd)
                    guard = self.selector()
                    with patch.object(jev_smoke, '_write_all', side_effect=write), \
                            patch.object(jev_smoke.os, 'fsync', side_effect=sync), \
                            patch('http.client.HTTPSConnection') as http:
                        with self.assertRaises(jev_smoke.ReservationRefused): guard.select(self.requests[0])
                        self.assertEqual(http.call_count, 0)
                    with self.assertRaises(jev_smoke.ReservationRefused): self.selector().select(self.requests[0])
                    self.assertFalse(guard.prior_batch_attempts_known)
                    self.assertEqual((root / 'reservation.json').read_bytes(), old)
        self.reserve()
        child = multiprocessing.get_context('fork').Process(target=crash_followup,
            args=(str(self.root), self.requests, self.plan['plan_hash']))
        child.start(); child.join(10)
        self.assertEqual(child.exitcode, 23)
        self.assertEqual((self.root / 'catalogue-attempt-1.json').stat().st_size, 12)
        with self.assertRaises(jev_smoke.ReservationRefused): self.selector().select(self.requests[0])

    def test_original_private_nofollow_single_link_and_exact_bytes(self):
        for mode in ('symlink', 'hardlink', 'permissions', 'whitespace', 'missing', 'foreign_owner'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    jev_smoke.reserve_once()
                    old = root / 'reservation.json'
                    if mode == 'symlink':
                        old.rename(root / 'target'); old.symlink_to(root / 'target')
                    if mode == 'hardlink': os.link(old, root / 'other')
                    if mode == 'permissions': os.chmod(old, 0o640)
                    if mode == 'whitespace': old.write_bytes(old.read_bytes() + b' ')
                    if mode == 'missing': old.unlink()
                    fstat = os.fstat
                    def metadata(fd):
                        node = fstat(fd)
                        if mode == 'foreign_owner' and node.st_ino == old.stat().st_ino:
                            values = list(node); values[4] = os.getuid() + 1
                            return os.stat_result(values)
                        return node
                    with patch.object(jev_smoke.os, 'fstat', side_effect=metadata), \
                            patch('http.client.HTTPSConnection') as http:
                        with self.assertRaises(jev_smoke.ReservationRefused): self.selector().select(self.requests[0])
                        self.assertEqual(http.call_count, 0)
                    self.assertFalse((root / 'catalogue-followup.json').exists())

    def test_same_instance_thread_serialization_and_terminal_echo(self):
        self.reserve()
        guard = self.selector()
        entered, release = threading.Event(), threading.Event()
        results = []
        def delegate(req):
            entered.set(); release.wait(5)
            return safe_result(req)
        def select(req):
            try:
                guard.select(req); results.append('ok')
            except Exception: results.append('failed')
        with patch.object(jev.JevSelector, 'select', side_effect=delegate) as call:
            first = threading.Thread(target=select, args=(self.requests[0],))
            second = threading.Thread(target=select, args=(self.requests[1],))
            first.start(); self.assertTrue(entered.wait(5)); second.start()
            self.assertEqual(call.call_count, 1)
            release.set(); first.join(5); second.join(5)
            self.assertEqual(results, ['ok', 'ok'])
            self.assertEqual(call.call_count, 2)
        bad = replace(safe_result(self.requests[2]), raw_decisions={'note': '%66ixture%2Dsecret'})
        with patch.object(jev.JevSelector, 'select', return_value=bad) as call:
            with self.assertRaises(SelectionFailure) as caught: guard.select(self.requests[2])
            self.assertEqual(caught.exception.raw, {'classification': 'selector_unavailable'})
            with self.assertRaises(SelectionFailure): guard.select(self.requests[3])
            self.assertEqual(call.call_count, 1)
        self.assertEqual(guard.reserved_cents, 500)

    def test_claim_restarts_changed_plan_and_trust_modes_never_reopen(self):
        self.reserve()
        first = self.selector()
        with patch.object(jev.JevSelector, 'select', side_effect=safe_result):
            first.select(self.requests[0])
        old = (self.root / 'reservation.json').read_bytes()
        for trust in (True, False):
            for plan_hash in (self.plan['plan_hash'], 'f' * 64):
                guard = jev_smoke.FollowupJevSelector(CONFIG, 'fixture-secret', self.requests,
                    plan_hash=plan_hash, ack_no_off_credit_charges=True, trust_primary_group=trust)
                with patch('http.client.HTTPSConnection') as http:
                    with self.assertRaises(jev_smoke.ReservationRefused): guard.select(self.requests[0])
                    self.assertEqual(http.call_count, 0)
                self.assertFalse(guard.prior_batch_attempts_known)
                self.assertEqual(guard.reserved_cents, 500)
        claim = self.root / 'catalogue-followup.json'
        for partial in (b'', b'{', b'{}'):
            claim.write_bytes(partial)  # Fixture corruption only, never real state.
            with self.assertRaises(jev_smoke.ReservationRefused): self.selector().select(self.requests[0])
        self.assertEqual((self.root / 'reservation.json').read_bytes(), old)

    def test_mutated_original_claim_marker_and_ancestry_stop_next_attempt(self):
        for mode in ('old', 'claim', 'marker', 'marker_hardlink', 'ancestry', 'leaf_permissions'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as temp:
                root = Path(temp) / 'piecetogether/jev-usd5-smoke'
                with patch.object(jev_smoke, '_ledger_root', return_value=root), \
                        patch.object(jev.JevSelector, 'select', side_effect=safe_result) as delegate:
                    jev_smoke.reserve_once()
                    guard = self.selector(); guard.select(self.requests[0])
                    if mode == 'old': (root / 'reservation.json').write_text('{}')
                    if mode == 'claim': (root / 'catalogue-followup.json').write_text('{}')
                    if mode == 'marker': (root / 'catalogue-attempt-1.json').write_text('{}')
                    if mode == 'marker_hardlink': os.link(root / 'catalogue-attempt-1.json', root / 'alias')
                    if mode == 'ancestry':
                        root.rename(root.with_name('moved')); root.mkdir(mode=0o700)
                    if mode == 'leaf_permissions': os.chmod(root, 0o775)
                    with self.assertRaises(jev_smoke.ReservationRefused): guard.select(self.requests[1])
                    with self.assertRaises(SelectionFailure): guard.select(self.requests[2])
                    self.assertEqual(delegate.call_count, 1)
                    self.assertFalse(guard.prior_batch_attempts_known)

    def test_claim_and_marker_fsync_precede_every_http_send(self):
        self.reserve()
        synced = set()
        original_sync = os.fsync
        calls = []
        connection = self.connection('success', calls)
        original_request = connection.request
        def sync(fd):
            original_sync(fd)
            node = os.fstat(fd)
            synced.add((node.st_dev, node.st_ino))
        def request(instance, method, path, body, headers):
            slot = len(calls) + 1
            for node in (self.root, self.root / 'catalogue-followup.json', self.root / f'catalogue-attempt-{slot}.json'):
                self.assertIn((node.stat().st_dev, node.stat().st_ino), synced)
            original_request(instance, method, path, body, headers)
        with patch.object(jev_smoke.os, 'fsync', side_effect=sync), \
                patch.object(connection, 'request', request), \
                patch('http.client.HTTPSConnection', connection):
            report = self.probe._run(self.plan, Path(self.temp.name) / 'durable')
        self.assertEqual(len(calls), 5)
        self.assertEqual(report['status'], 'completed_diagnostics_only')

    def test_deadline_and_response_budget_exhaustion_are_terminal(self):
        from piecetogether.context import BudgetExhausted
        self.reserve()
        guard = self.selector()
        with patch.object(jev_smoke, 'monotonic', side_effect=(0, 0, 15)), \
                patch('http.client.HTTPSConnection') as http:
            with self.assertRaises(BudgetExhausted): guard.select(self.requests[0])
            self.assertEqual(http.call_count, 0)
        self.assertEqual(guard.recorded_slots, 1)
        self.assertEqual(guard.attempted_dispatches, 0)
        self.assertEqual(guard.reserved_cents, 500)
        with self.assertRaises(SelectionFailure): guard.select(self.requests[0])

    def test_constructor_requires_literal_financial_ack_and_distinct_five_pinned_inputs(self):
        for ack in (False, 1, 'true', None):
            with self.subTest(ack=ack), self.assertRaises(ValueError):
                jev_smoke.FollowupJevSelector(CONFIG, 'fixture-secret', self.requests,
                    plan_hash=self.plan['plan_hash'], ack_no_off_credit_charges=ack)
        for requests in (self.requests[:4], self.requests + self.requests[:1],
                         (self.requests[0],) * 5,
                         (replace(self.requests[0], minimum_relevance=.6),) + self.requests[1:],
                         (replace(self.requests[0], timeout_ms=1001),) + self.requests[1:]):
            with self.assertRaises(ValueError):
                jev_smoke.FollowupJevSelector(CONFIG, 'fixture-secret', requests,
                    plan_hash=self.plan['plan_hash'], ack_no_off_credit_charges=True)
        self.assertFalse(self.root.exists())

    def test_explicit_live_main_uses_child_temp_env_and_direct_shared_http_counter(self):
        self.reserve()
        count = multiprocessing.get_context('fork').Value('i', 0)
        calls = []
        connection = self.connection('success', calls)
        original = connection.request
        def request(instance, *args, **kwargs):
            with count.get_lock(): count.value += 1
            return original(instance, *args, **kwargs)
        capture_path = Path(self.temp.name) / 'capture.json'
        capture_path.write_text(encoded(self.capture))
        output = Path(self.temp.name) / 'child-output'
        with patch.object(connection, 'request', request), \
                patch('http.client.HTTPSConnection', connection), redirect_stdout(io.StringIO()) as stdout:
            code = self.probe.main(['--live', '--capture', str(capture_path), '--output', str(output),
                '--expected-plan-hash', self.plan['plan_hash'], '--ack-owner-prepaid-usd5',
                '--ack-no-off-credit-charges'])
        self.assertEqual(code, 0)
        self.assertEqual(count.value, 5)
        self.assertNotIn('fixture-secret', stdout.getvalue())
        self.assertEqual(json.loads((output / 'receipt.json').read_text())['reserved_cents'], 500)

    def test_original_and_followup_cross_mode_concurrency_share_one_envelope(self):
        self.reserve()
        ctx = multiprocessing.get_context('fork')
        start, queue = ctx.Event(), ctx.Queue()
        def old_worker():
            with patch.object(jev_smoke, '_ledger_root', return_value=self.root):
                start.wait()
                try:
                    jev_smoke.reserve_once(trust_primary_group=True)
                    queue.put('old-winner')
                except jev_smoke.ReservationRefused: queue.put('old-refused')
        children = [ctx.Process(target=old_worker)] + [ctx.Process(target=worker,
            args=(str(self.root), self.requests, self.plan['plan_hash'], start, queue, trust))
            for trust in (False, True)]
        for child in children: child.start()
        start.set()
        outcomes = [queue.get(timeout=10) for _ in children]
        for child in children:
            child.join(10); self.assertEqual(child.exitcode, 0)
        self.assertEqual(outcomes.count('winner'), 1)
        self.assertEqual(outcomes.count('old-refused'), 1)
        self.assertNotIn('old-winner', outcomes)

    def test_position_rule_uses_exact_decimal_boundaries(self):
        left = {'a': .2, 'b': .4}
        # Position error just above .01 must NOT satisfy the frozen rule.
        right = {'b': .2100000000001, 'a': .4}
        self.assertFalse(self.probe._alignment(['a', 'b'], left, ['b', 'a'], right)['position_tracking_suspected'])
        right = {'b': .21, 'a': .4}
        self.assertTrue(self.probe._alignment(['a', 'b'], left, ['b', 'a'], right)['position_tracking_suspected'])

    def test_position_rule_separates_content_and_position(self):
        ids = [r.id for r in self.requests[0].candidates]
        left = dict(zip(ids, [i / 20 for i in range(15)]))
        right = dict(zip(reversed(ids), left.values()))
        result = self.probe._alignment(list(reversed(ids)), left, ids, right)
        self.assertTrue(result['position_tracking_suspected'])
        self.assertTrue(all(v <= .01 for v in result['position_differences']))
        self.assertGreaterEqual(max(result['content_differences']), .1)
        self.assertFalse(self.probe._alignment(ids, left, list(reversed(ids)), left)['position_tracking_suspected'])


if __name__ == '__main__':
    unittest.main()
