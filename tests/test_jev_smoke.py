"""Offline-only proofs; every reservation uses a patched private temporary root."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from piecetogether import jev_smoke
from piecetogether.context import ContextRecord, SelectionRequest


def request():
    return SelectionRequest('{"text":"cucina e bagno"}',
        (ContextRecord('c1', 'context', 'cucina', 'r1', ''),), (),
        'context-relevance-v1', 3, 65536, 2000, .7)


def reserve_worker(root, start, results, *, trust_primary_group=False):
    with patch.object(jev_smoke, '_ledger_root', return_value=Path(root)):
        start.wait()
        try:
            jev_smoke.reserve_once(trust_primary_group=trust_primary_group)
            results.put('reserved')
        except jev_smoke.ReservationRefused:
            results.put('refused')


def crash_worker(root):
    def interrupted(fd, body):
        os.write(fd, body[:12])
        os._exit(23)
    with patch.object(jev_smoke, '_ledger_root', return_value=Path(root)), \
            patch.object(jev_smoke, '_write_all', side_effect=interrupted):
        jev_smoke.reserve_once()


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'private' / 'jev-usd5-smoke'
        self.patch = patch.object(jev_smoke, '_ledger_root', return_value=self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_refusal_reasons_distinguish_unsafe_held_and_uncertain_durability(self):
        from piecetogether.context import SelectionConfig
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        for mode, expected in (('unsafe', 'unsafe_ancestry'), ('empty', 'already_reserved'),
                               ('partial', 'already_reserved'), ('full', 'already_reserved'),
                               ('fsync', 'durability_failed')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                home = Path(directory) / 'home'
                home.mkdir(mode=0o700)
                root = home / '.local/state/piecetogether/jev-usd5-smoke'
                if mode == 'unsafe':
                    os.chmod(home, 0o775)  # Real group-writable account ancestry.
                elif mode in ('empty', 'partial', 'full'):
                    root.mkdir(parents=True, mode=0o700)
                    for parent in (root.parent, root.parent.parent, root.parent.parent.parent):
                        os.chmod(parent, 0o700)
                    (root / 'reservation.json').write_text({'empty': '', 'partial': '{',
                                                          'full': '{"reserved_cents":500}'}[mode])
                guarded = jev_smoke.GuardedJevSelector(config, 'fixture-secret')
                from contextlib import nullcontext
                fault = patch.object(jev_smoke.os, 'fsync', side_effect=OSError('Bearer fixture-secret /private')) if mode == 'fsync' else nullcontext()
                with patch.object(jev_smoke, '_ledger_root', return_value=root), fault, \
                        patch('http.client.HTTPSConnection', side_effect=AssertionError('network')) as http:
                    with self.assertRaises(jev_smoke.ReservationRefused) as caught:
                        guarded.select(request())
                    self.assertEqual(http.call_count, 0)
                self.assertEqual(str(caught.exception), 'reservation refused')
                self.assertEqual(getattr(caught.exception, 'reason', None), expected)
                self.assertEqual(getattr(guarded, 'reservation_refusal_reason', None), expected)
                self.assertIsNone(guarded.reserved_cents)
                self.assertEqual(guarded.attempted_dispatches, 0)
                if mode == 'fsync':
                    self.assertTrue((root / 'reservation.json').exists())

    def test_refusal_reason_rejects_external_secret_or_malformed_values(self):
        from piecetogether.context import SelectionConfig
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        class Malformed:
            def __str__(self):
                raise AssertionError('must not stringify external data')
        for value in ('Bearer fixture-secret', {'reason': 'fixture-secret'}, Malformed(), None):
            with self.subTest(kind=type(value).__name__):
                error = jev_smoke.ReservationRefused(value)
                self.assertEqual(str(error), 'reservation refused')
                self.assertEqual(error.reason, 'reservation_refused')
                # Even tampered external exception attributes cannot become receipts.
                error.reason = value
                guarded = jev_smoke.GuardedJevSelector(config, 'fixture-secret')
                with patch.object(jev_smoke, 'reserve_once', side_effect=error), \
                        patch('http.client.HTTPSConnection') as http:
                    with self.assertRaises(jev_smoke.ReservationRefused) as caught:
                        guarded.select(request())
                    self.assertEqual(http.call_count, 0)
                self.assertEqual(caught.exception.reason, 'reservation_refused')
                self.assertEqual(guarded.reservation_refusal_reason, 'reservation_refused')

    def test_check_only_echo_normalization_rejects_variants_without_mutating_safe_results(self):
        from piecetogether.context import SelectionConfig, SelectionFailure, SelectionResult
        from piecetogether.jev import JevSelector
        from urllib.parse import quote
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        key = 'fixture-secret'
        escaped = ''.join('\\u%04x' % ord(char) for char in key)
        variants = (key.upper(), escaped, ''.join('%%%02X' % ord(char) for char in key),
                    quote(escaped), 'aUtHoRiZaTiOn \t: redacted', 'bEaReR\tredacted',
                    'BEARER\r\nredacted')
        for index, text in enumerate(variants):
            with self.subTest(index=index), patch.object(jev_smoke, '_ledger_root',
                    return_value=Path(self.temp.name) / str(index) / 'jev-usd5-smoke'):
                result = SelectionResult('completed', ('c1',), (), 'typesafe', 'jev-1.13.0',
                                         'context-relevance-v1', 10, 0, raw_decisions={'note': text})
                with patch.object(JevSelector, 'select', return_value=result):
                    with self.assertRaises(SelectionFailure) as caught:
                        jev_smoke.GuardedJevSelector(config, key).select(request())
                self.assertEqual(caught.exception.raw, {'classification': 'selector_unavailable'})
        safe = SelectionResult('completed', ('c1',), (), 'typesafe', 'jev-1.13.0',
                               'context-relevance-v1', 10, 0,
                               raw_decisions={'note': 'Cost\\u0020is%20NOMINAL', 'number': 7})
        with patch.object(JevSelector, 'select', return_value=safe):
            actual = jev_smoke.GuardedJevSelector(config, key).select(request())
        self.assertIs(actual, safe)
        self.assertEqual(actual.raw_decisions, {'note': 'Cost\\u0020is%20NOMINAL', 'number': 7})

    def test_exact_five_dollars_is_permanent_including_restart_and_env_changes(self):
        jev_smoke.reserve_once()
        record = json.loads((self.root / 'reservation.json').read_text())
        self.assertEqual(record, {'experiment': 'jev-usd5-smoke-v1', 'currency': 'USD',
            'limit_cents': 500, 'reserved_cents': 500, 'reconciled_cents': 0,
            'attempted_dispatches': 1})
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'reservation.json').stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {'HOME': self.temp.name, 'XDG_STATE_HOME': '/different'}):
            with self.assertRaises(jev_smoke.ReservationRefused):
                jev_smoke.reserve_once()
        self.assertEqual(json.loads((self.root / 'reservation.json').read_text()), record)

    def test_os_account_identity_ignores_home_xdg_and_cwd(self):
        self.patch.stop()
        import pwd
        expected = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/piecetogether/jev-usd5-smoke'
        old = Path.cwd()
        try:
            os.chdir(self.temp.name)
            with patch.dict(os.environ, {'HOME': '/wrong', 'XDG_STATE_HOME': '/wrong'}):
                self.assertEqual(jev_smoke._ledger_root(), expected)
        finally:
            os.chdir(old)

    def test_concurrent_processes_have_one_winner(self):
        ctx = multiprocessing.get_context('fork')
        start, results = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=reserve_worker, args=(str(self.root), start, results)) for _ in range(4)]
        for worker in workers:
            worker.start()
        start.set()
        outcomes = [results.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(outcomes.count('reserved'), 1)
        self.assertEqual(outcomes.count('refused'), 3)
        with self.assertRaises(jev_smoke.ReservationRefused):
            jev_smoke.reserve_once()

    def test_existing_partial_corrupt_or_full_record_never_dispatches(self):
        self.root.mkdir(parents=True, mode=0o700)
        os.chmod(self.root.parent, 0o700)
        for body in ('', '{', '{}', '{"reserved_cents":500}'):
            with self.subTest(body=body):
                (self.root / 'reservation.json').write_text(body)
                with patch('piecetogether.jev.JevSelector.select', side_effect=AssertionError('dispatch')):
                    with self.assertRaises(jev_smoke.ReservationRefused):
                        jev_smoke.reserve_once()

    def test_process_death_during_write_leaves_nonspendable_partial_reservation(self):
        worker = multiprocessing.get_context('fork').Process(target=crash_worker, args=(str(self.root),))
        worker.start()
        worker.join(10)
        self.assertEqual(worker.exitcode, 23)
        self.assertEqual((self.root / 'reservation.json').read_bytes(), b'{"attempted_')
        with self.assertRaises(jev_smoke.ReservationRefused):
            jev_smoke.reserve_once()

    def test_unsafe_nodes_refused(self):
        for mode in ('symlink_dir', 'symlink_file', 'hardlink', 'permissions', 'file_directory'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                root = Path(directory) / 'jev-usd5-smoke'
                if mode == 'symlink_dir':
                    root.symlink_to(self.temp.name, target_is_directory=True)
                elif mode == 'file_directory':
                    root.write_text('unsafe')
                else:
                    root.mkdir(mode=0o755 if mode == 'permissions' else 0o700)
                    target = Path(directory) / 'target'
                    target.write_text('sentinel')
                    if mode == 'symlink_file':
                        (root / 'reservation.json').symlink_to(target)
                    if mode == 'hardlink':
                        os.link(target, root / 'reservation.json')
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    with self.assertRaises(jev_smoke.ReservationRefused):
                        jev_smoke.reserve_once()
                if mode in ('symlink_file', 'hardlink'):
                    self.assertEqual(target.read_text(), 'sentinel')

    def test_interrupted_write_or_fsync_retains_file_and_refuses_retry(self):
        for operation in ('write', 'fsync'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                root = Path(directory) / 'jev-usd5-smoke'
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    with patch('piecetogether.jev_smoke.os.' + operation, side_effect=OSError('secret')):
                        with self.assertRaisesRegex(jev_smoke.ReservationRefused, '^reservation refused$'):
                            jev_smoke.reserve_once()
                    self.assertTrue((root / 'reservation.json').exists())
                    with self.assertRaises(jev_smoke.ReservationRefused):
                        jev_smoke.reserve_once()

    def test_write_all_and_fsync_before_delegate_and_hold_after_success(self):
        from piecetogether.context import SelectionConfig
        from piecetogether.jev import JevSelector
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        original_write = os.write
        synced = []
        original_fsync = os.fsync
        def sync(fd):
            original_fsync(fd)
            synced.append(fd)
        def delegate(req):
            self.assertGreaterEqual(len(synced), 3)
            self.assertEqual(json.loads((self.root / 'reservation.json').read_text())['reserved_cents'], 500)
            from piecetogether.context import SelectionResult
            return SelectionResult('completed', ('c1',), (), 'typesafe', 'jev-1.13.0', req.rubric_version, 10, 0)
        with patch.object(JevSelector, 'select', side_effect=delegate), \
                patch.object(jev_smoke.os, 'write', side_effect=lambda fd, body: original_write(fd, body[:7])), \
                patch.object(jev_smoke.os, 'fsync', side_effect=sync):
            guarded = jev_smoke.GuardedJevSelector(config, 'fixture-secret')
            self.assertEqual(guarded.select(request()).ranked_ids, ('c1',))
        with self.assertRaises(jev_smoke.ReservationRefused):
            jev_smoke.reserve_once()

    def test_directory_fsync_and_path_substitution_fail_before_delegation(self):
        from piecetogether.context import SelectionConfig
        from piecetogether.jev import JevSelector
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        original_fsync = os.fsync
        for mode in ('directory_fsync', 'substitution', 'new_hardlink', 'file_permissions'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                root = Path(directory) / 'jev-usd5-smoke'
                calls = []
                def fsync(fd):
                    calls.append(fd)
                    original_fsync(fd)
                    if len(calls) == 2:
                        if mode == 'directory_fsync':
                            raise OSError('private failure')
                        if mode == 'substitution':
                            root.rename(root.with_name('original'))
                            root.mkdir(mode=0o700)
                        if mode == 'new_hardlink':
                            os.link(root / 'reservation.json', Path(directory) / 'second-link')
                        if mode == 'file_permissions':
                            os.chmod(root / 'reservation.json', 0o644)
                with patch.object(jev_smoke, '_ledger_root', return_value=root), \
                        patch.object(jev_smoke.os, 'fsync', side_effect=fsync), \
                        patch.object(JevSelector, 'select', side_effect=AssertionError('must not delegate')):
                    with self.assertRaises(jev_smoke.ReservationRefused):
                        jev_smoke.GuardedJevSelector(config, 'fixture-secret').select(request())

    def test_local_delegate_failure_still_counts_attempt_and_holds_full_amount(self):
        from piecetogether.context import BudgetExhausted, SelectionConfig
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        from dataclasses import replace
        guarded = jev_smoke.GuardedJevSelector(config, 'fixture-secret')
        with patch('http.client.HTTPSConnection', side_effect=AssertionError('no HTTP')) as http:
            with self.assertRaises(BudgetExhausted):
                guarded.select(replace(request(), remaining_calls=0))
            self.assertEqual(http.call_count, 0)
        self.assertEqual(guarded.attempted_dispatches, 1)
        self.assertEqual(guarded.reserved_cents, 500)
        self.assertEqual(json.loads((self.root / 'reservation.json').read_text())['reserved_cents'], 500)

    def test_exception_and_returned_secret_are_sanitized_before_core(self):
        from piecetogether.context import SelectionConfig, SelectionFailure, SelectionResult
        from piecetogether.jev import JevSelector
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        for value in (SelectionFailure({'Authorization': 'Bearer fixture-secret'}),
                      TimeoutError('fixture-secret'),
                      SelectionResult('completed', (), (), 'typesafe', 'fixture-secret', 'context-relevance-v1'),
                      SelectionResult('completed', (), (), 'typesafe', 'jev-1.13.0', 'context-relevance-v1',
                                      raw_decisions={'Authorization': 'Bearer fixture-secret'})):
            with self.subTest(value=type(value).__name__), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                with patch.object(jev_smoke, '_ledger_root', return_value=Path(directory) / 'jev-usd5-smoke'):
                    with patch.object(JevSelector, 'select', **({'side_effect': value} if isinstance(value, Exception) else {'return_value': value})):
                        with self.assertRaises(SelectionFailure) as caught:
                            jev_smoke.GuardedJevSelector(config, 'fixture-secret').select(request())
                        self.assertNotIn('fixture-secret', str(caught.exception.raw))
                        self.assertNotIn('Authorization', str(caught.exception.raw))


class PrimaryGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir(mode=0o700)
        self.root = self.home / 'piecetogether/jev-usd5-smoke'
        self.root_patch = patch.object(jev_smoke, '_ledger_root', return_value=self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_matching_primary_group_requires_explicit_opt_in_and_never_chmods(self):
        from types import SimpleNamespace
        os.chmod(self.home, 0o775)
        primary_gid = self.home.stat().st_gid
        with patch.object(jev_smoke.pwd, 'getpwuid', return_value=SimpleNamespace(pw_gid=primary_gid)), \
                patch('os.getgid', return_value=primary_gid + 1), \
                patch('os.chmod', side_effect=AssertionError('must not chmod existing nodes')) as chmod, \
                patch('os.fchmod', side_effect=AssertionError('must not fchmod')) as fchmod:
            with self.assertRaises(jev_smoke.ReservationRefused) as strict:
                jev_smoke.reserve_once()
            self.assertEqual(strict.exception.reason, 'unsafe_ancestry')
            self.assertFalse((self.root / 'reservation.json').exists())
            jev_smoke.reserve_once(trust_primary_group=True)
            self.assertEqual(chmod.call_count, 0)
            self.assertEqual(fchmod.call_count, 0)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o775)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        record = json.loads((self.root / 'reservation.json').read_text())
        self.assertEqual(record['experiment'], 'jev-usd5-smoke-v1')
        self.assertEqual(record['reserved_cents'], 500)
        self.assertEqual((self.root / 'reservation.json').stat().st_mode & 0o777, 0o600)

    def test_opt_in_rejects_foreign_uid_gid_and_world_write(self):
        from types import SimpleNamespace
        os.chmod(self.home, 0o775)
        node = self.home.stat()
        original_fstat = os.fstat
        for field in ('gid', 'uid', 'world_write'):
            os.chmod(self.home, 0o777 if field == 'world_write' else 0o775)
            def metadata(fd):
                result = original_fstat(fd)
                if (result.st_dev, result.st_ino) == (node.st_dev, node.st_ino):
                    values = list(result)
                    if field == 'gid':
                        values[5] = node.st_gid + 1
                    elif field == 'uid':
                        values[4] = os.getuid() + 1
                    return os.stat_result(values)
                return result
            with self.subTest(field=field), \
                    patch.object(jev_smoke.pwd, 'getpwuid', return_value=SimpleNamespace(pw_gid=node.st_gid)), \
                    patch.object(jev_smoke.os, 'fstat', side_effect=metadata), \
                    patch('http.client.HTTPSConnection') as http:
                with self.assertRaises(jev_smoke.ReservationRefused) as caught:
                    jev_smoke.reserve_once(trust_primary_group=True)
                self.assertEqual(caught.exception.reason, 'unsafe_ancestry')
                self.assertEqual(http.call_count, 0)
                self.assertFalse((self.root / 'reservation.json').exists())

    def test_truthy_nonbooleans_cannot_enable_group_trust(self):
        from piecetogether.context import SelectionConfig
        config = SelectionConfig(adapter='jev', model_id='jev-1.13.0', secret_reference='typesafe', minimum_relevance=.7)
        os.chmod(self.home, 0o775)
        class MustNotCoerce:
            def __bool__(self):
                raise AssertionError('must require literal True')
        for value in (1, 'true', {'trust': True}, MustNotCoerce()):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(jev_smoke.ReservationRefused):
                    jev_smoke.reserve_once(trust_primary_group=value)
                with self.assertRaises(ValueError):
                    jev_smoke._new_output(self.home / 'output', trust_primary_group=value)
                guarded = jev_smoke.GuardedJevSelector(config, 'fixture-secret', trust_primary_group=value)
                with patch('http.client.HTTPSConnection') as http:
                    with self.assertRaises(jev_smoke.ReservationRefused):
                        guarded.select(request())
                    self.assertEqual(http.call_count, 0)
        self.assertFalse((self.root / 'reservation.json').exists())

    def test_opt_in_preserves_private_directories_and_no_follow(self):
        for kind in ('private_parent', 'private_leaf', 'symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                root = Path(directory) / 'piecetogether/jev-usd5-smoke'
                root.parent.mkdir(mode=0o700)
                if kind == 'private_parent':
                    os.chmod(root.parent, 0o775)
                elif kind == 'private_leaf':
                    root.mkdir(mode=0o700)
                    os.chmod(root, 0o775)
                else:
                    root.symlink_to(self.home, target_is_directory=True)
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    with self.assertRaises(jev_smoke.ReservationRefused):
                        jev_smoke.reserve_once(trust_primary_group=True)
                self.assertFalse((root / 'reservation.json').exists())

    def test_output_ancestry_uses_same_opt_in_but_output_remains_private(self):
        os.chmod(self.home, 0o775)
        with self.assertRaises(ValueError):
            jev_smoke._new_output(self.home / 'strict')
        fd = jev_smoke._new_output(self.home / 'trusted', trust_primary_group=True)
        try:
            self.assertEqual(os.fstat(fd).st_mode & 0o777, 0o700)
            jev_smoke._write_private(fd, 'receipt.json', {'synthetic': True})
            self.assertEqual((self.home / 'trusted/receipt.json').stat().st_mode & 0o777, 0o600)
        finally:
            os.close(fd)
        original_mkdir = os.mkdir
        def unsafe_output(name, mode=0o777, *, dir_fd=None):
            original_mkdir(name, mode, dir_fd=dir_fd)
            os.chmod(name, 0o775, dir_fd=dir_fd)  # Simulate changed output permissions.
        with patch.object(jev_smoke.os, 'mkdir', side_effect=unsafe_output):
            with self.assertRaises(ValueError):
                jev_smoke._new_output(self.home / 'unsafe-output', trust_primary_group=True)

    def test_mixed_mode_processes_share_one_reservation_and_no_reset(self):
        ctx = multiprocessing.get_context('fork')
        for group_writable in (False, True):
            with self.subTest(group_writable=group_writable), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                home = Path(directory) / 'home'
                home.mkdir(mode=0o700)
                if group_writable:
                    os.chmod(home, 0o775)
                root = home / 'piecetogether/jev-usd5-smoke'
                start, results = ctx.Event(), ctx.Queue()
                workers = [ctx.Process(target=reserve_worker, args=(str(root), start, results),
                                       kwargs={'trust_primary_group': mode})
                           for mode in (False, True, False, True)]
                for worker in workers:
                    worker.start()
                start.set()
                outcomes = [results.get(timeout=10) for _ in workers]
                for worker in workers:
                    worker.join(10)
                    self.assertEqual(worker.exitcode, 0)
                self.assertEqual(outcomes.count('reserved'), 1)
                before = (root / 'reservation.json').read_bytes()
                for mode in (False, True):
                    with patch.object(jev_smoke, '_ledger_root', return_value=root):
                        with self.assertRaises(jev_smoke.ReservationRefused):
                            jev_smoke.reserve_once(trust_primary_group=mode)
                self.assertEqual((root / 'reservation.json').read_bytes(), before)


class SmokeCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'ledger' / 'jev-usd5-smoke'
        self.ledger_patch = patch.object(jev_smoke, '_ledger_root', return_value=self.root)
        self.ledger_patch.start()
        self.addCleanup(self.ledger_patch.stop)

    def connection(self, mode, requests):
        class Response:
            status = int(mode) if mode in ('402', '429') else 200
            def read1(self, limit):
                body, self.body = self.body, b''
                return body
        class Connection:
            sock = None
            def __init__(self, *args, **kwargs):
                pass
            def connect(self):
                pass
            def request(self, method, path, body, headers):
                requests.append(json.loads(body))
                self.body = requests[-1]
                if mode == 'timeout':
                    raise TimeoutError('Authorization: Bearer fixture-secret')
            def getresponse(self):
                candidates = {record['id']: record for record in self.body['state']['candidates']}
                data = {'model': 'jev-1.14.0' if mode == 'version' else 'jev-1.13.0',
                        'answers': {record_id: {'type': 'noul', 'noul':
                            .5 if mode == 'abstained' else .01 if mode == 'wrong_selection'
                            else .93 if record['kind'] == 'context' else .02}
                            for record_id, record in candidates.items()},
                        'usage': {'input_tokens': 1200, 'output_tokens': 40}}
                if mode == 'usage_missing':
                    del data['usage']
                if mode == 'usage_invalid':
                    data['usage']['input_tokens'] = True
                if mode == 'usage_exhausted':
                    data['usage']['input_tokens'] = 65537
                if mode == 'echo':
                    data['Authorization'] = 'Bearer fixture-secret'
                response = Response()
                response.body = (b'{"raw":"Authorization: Bearer fixture-secret"' if mode == 'malformed'
                                 else json.dumps(data).encode())
                return response
            def close(self):
                pass
        return Connection

    def invoke(self, mode='success'):
        import contextlib
        import io
        import jev_guarded_smoke
        requests, stdout, stderr = [], io.StringIO(), io.StringIO()
        output = Path(self.temp.name) / ('output-' + mode)
        with patch.dict(os.environ, {'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                patch('http.client.HTTPSConnection', self.connection(mode, requests)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5', '--output', str(output)])
        return code, output, requests, stdout.getvalue() + stderr.getvalue()

    def test_import_collection_and_default_preflight_are_inert_even_with_credentials(self):
        import importlib
        import jev_guarded_smoke
        original_get = os.environ.get
        def environment_get(name, default=None):
            if name == 'PT_TYPESAFE_API_KEY':
                raise AssertionError('secret lookup')
            return original_get(name, default)  # argparse/gettext legitimately reads locale.
        with patch.dict(os.environ, {'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                patch('os.environ.get', side_effect=environment_get), \
                patch.object(jev_smoke, 'reserve_once', side_effect=AssertionError('reservation')), \
                patch('http.client.HTTPSConnection', side_effect=AssertionError('network')) as http, \
                patch.object(jev_smoke.pwd, 'getpwuid', side_effect=AssertionError('account lookup')) as account, \
                patch('piecetogether.core._load_local_env', side_effect=AssertionError('env file')), \
                patch('piecetogether.core.Bootstrap.from_file', side_effect=AssertionError('real configuration')), \
                patch('piecetogether.core.Core', side_effect=AssertionError('fixture construction')):
            importlib.reload(jev_smoke)
            # Reload redefines the resolver; restore the test-only root before any
            # possible dispatch so even a regression cannot burn the real hold.
            with patch.object(jev_smoke, '_ledger_root', return_value=self.root):
                importlib.reload(jev_guarded_smoke)
                suite = unittest.defaultTestLoader.discover('tests', pattern='test_jev_smoke.py')
                # Collect/import only; never recursively execute the discovered suite.
                self.assertGreater(suite.countTestCases(), 0)
                self.assertEqual(jev_guarded_smoke.main([]), 0)
                self.assertEqual(http.call_count, 0)
                self.assertEqual(account.call_count, 0)
        self.assertFalse(self.root.exists())

    def test_primary_group_flag_requires_live_and_does_not_replace_account_consent(self):
        import contextlib
        import io
        import jev_guarded_smoke
        with patch('http.client.HTTPSConnection') as http, \
                patch.object(jev_smoke, 'reserve_once') as reserve, \
                contextlib.redirect_stderr(io.StringIO()):
            for arguments in (['--ack-trusted-primary-group'],
                              ['--live', '--ack-trusted-primary-group', '--output', self.temp.name]):
                with self.assertRaises(SystemExit) as caught:
                    jev_guarded_smoke.main(arguments)
                self.assertEqual(caught.exception.code, 2)
            self.assertEqual(http.call_count, 0)
            self.assertEqual(reserve.call_count, 0)
        self.assertFalse(self.root.exists())

    def test_primary_group_flag_reaches_output_guard_receipt_and_same_hold(self):
        import contextlib
        import io
        import jev_guarded_smoke
        group_home = Path(self.temp.name) / 'group-home'
        group_home.mkdir(mode=0o700)
        os.chmod(group_home, 0o775)
        root = group_home / 'piecetogether/jev-usd5-smoke'
        requests = []
        with patch.object(jev_smoke, '_ledger_root', return_value=root), \
                patch.dict(os.environ, {'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                patch('http.client.HTTPSConnection', self.connection('success', requests)), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5',
                '--output', str(group_home / 'strict-output')]), 1)
            self.assertEqual(len(requests), 0)
            self.assertFalse((root / 'reservation.json').exists())
            output = group_home / 'trusted-output'
            self.assertEqual(jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5',
                '--ack-trusted-primary-group', '--output', str(output)]), 0)
            self.assertEqual(len(requests), 1)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertIs(receipt['trust_primary_group'], True)
            self.assertEqual(receipt['reserved_cents'], 500)
            self.assertEqual(receipt['actual_cost'], 'UNRECONCILED')
            self.assertTrue(receipt['replay_independent_expectations_passed'])
            for mode in (False, True):
                arguments = ['--live', '--ack-owner-prepaid-usd5', '--output',
                             str(Path(self.temp.name) / ('second-' + str(mode)))]
                if mode:
                    arguments.append('--ack-trusted-primary-group')
                self.assertEqual(jev_guarded_smoke.main(arguments), 1)
                self.assertEqual(len(requests), 1)
        self.assertEqual(json.loads((root / 'reservation.json').read_text())['reserved_cents'], 500)
        self.assertEqual(group_home.stat().st_mode & 0o777, 0o775)

    def test_live_requires_explicit_matching_account_ack_and_new_output(self):
        import contextlib
        import io
        import jev_guarded_smoke
        for arguments in (['--live'], ['--live', '--output', str(Path(self.temp.name) / 'out')],
                          ['--ack-owner-prepaid-usd5']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                jev_guarded_smoke.main(arguments)
        self.assertFalse(self.root.exists())
        for kind in ('existing', 'symlink', 'unsafe_parent'):
            with self.subTest(kind=kind):
                output = Path(self.temp.name) / kind
                if kind == 'existing':
                    output.mkdir(mode=0o700)
                elif kind == 'symlink':
                    output.symlink_to(self.temp.name, target_is_directory=True)
                else:
                    output.mkdir(mode=0o777)
                    os.chmod(output, 0o777)
                    output /= 'new'
                with patch.dict(os.environ, {'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                        patch('http.client.HTTPSConnection', side_effect=AssertionError('network')) as http, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5',
                                                           '--output', str(output)]), 1)
                    self.assertEqual(http.call_count, 0)
                self.assertFalse(self.root.exists())

    def test_success_captures_and_replays_actual_independent_domain_expectations(self):
        from piecetogether.evaluation import ReplayCase, replay
        code, output, requests, console = self.invoke()
        self.assertEqual(code, 0, console)
        self.assertEqual(len(requests), 1)
        report = json.loads((output / 'receipt.json').read_text())
        self.assertEqual(report['status'], 'passed')
        self.assertIs(report['trust_primary_group'], False)
        self.assertEqual(report['sample_count'], 1)
        self.assertEqual(report['required_available'], 2)
        self.assertEqual(report['required_selected'], 2)
        self.assertEqual(report['wrong_selected'], 0)
        self.assertEqual(report['reserved_cents'], 500)
        self.assertEqual(report['actual_cost'], 'UNRECONCILED')
        self.assertEqual(report['nominal_usd'], '0.0000504')
        self.assertEqual(report['cap_evidence'], 'OWNER-CONFIRMED')
        self.assertFalse(report['provider_account_independently_verified'])
        case = ReplayCase.from_dict(json.loads((output / 'capture.json').read_text()))
        with patch('socket.socket', side_effect=AssertionError('network')), \
                patch('piecetogether.jev.JevSelector.select', side_effect=AssertionError('provider')):
            actual = replay(case).actual
        self.assertEqual(actual['history'], case.expected['history'])
        self.assertEqual(actual['steps'][0]['grounding_items'], [])
        self.assertEqual(len(actual['steps'][0]['selection'][0]['selected_ids']), 2)
        for artifact in output.iterdir():
            self.assertEqual(artifact.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(jev_smoke.ReservationRefused):
            jev_smoke.reserve_once()

    def test_failures_keep_full_hold_at_most_one_http_and_no_secret_in_any_artifact(self):
        import base64
        for mode in ('malformed', 'timeout', '402', '429', 'version', 'abstained',
                     'usage_missing', 'usage_invalid', 'usage_exhausted', 'wrong_selection', 'echo'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                root = Path(directory) / 'jev-usd5-smoke'
                with patch.object(jev_smoke, '_ledger_root', return_value=root):
                    code, output, requests, console = self.invoke(mode)
                self.assertEqual(code, 1, console)
                self.assertEqual(len(requests), 1)
                self.assertEqual(json.loads((root / 'reservation.json').read_text())['reserved_cents'], 500)
                report = json.loads((output / 'receipt.json').read_text())
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['actual_cost'], 'UNRECONCILED')
                self.assertEqual(report['attempted_selector_delegations'], 1)
                content = console + ''.join(artifact.read_text() for artifact in output.iterdir())
                case = json.loads((output / 'capture.json').read_text())
                database = base64.b64decode(case['initial_state'])
                self.assertNotIn(b'fixture-secret', database)
                self.assertNotIn('fixture-secret', content)
                self.assertNotIn('Authorization', content)
                self.assertNotIn('Bearer ', content)

    def test_second_output_restart_and_environment_cannot_buy_second_attempt(self):
        import contextlib
        import io
        import jev_guarded_smoke
        code, _, requests, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 1)
        second = Path(self.temp.name) / 'second-output'
        old = Path.cwd()
        try:
            os.chdir(self.temp.name)
            with patch.dict(os.environ, {'HOME': self.temp.name, 'XDG_STATE_HOME': self.temp.name,
                                        'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                    patch('http.client.HTTPSConnection', side_effect=AssertionError('second HTTP')) as http, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5',
                                                       '--output', str(second)]), 1)
                self.assertEqual(http.call_count, 0)
        finally:
            os.chdir(old)
        refused = json.loads((second / 'receipt.json').read_text())
        self.assertEqual(refused['attempted_selector_delegations'], 0)
        self.assertIsNone(refused['reserved_cents'])  # Refusal cannot assert a zero hold.
        self.assertEqual(refused['selector_failure'], 'reservation_refused')
        self.assertEqual(json.loads((self.root / 'reservation.json').read_text())['reserved_cents'], 500)

    def test_refusal_receipts_expose_only_closed_reasons_and_unknown_hold(self):
        import contextlib
        import io
        import jev_guarded_smoke
        original_reserve = jev_smoke.reserve_once
        for mode, expected in (('unsafe', 'unsafe_ancestry'), ('partial', 'already_reserved'),
                               ('fsync', 'durability_failed'), ('malformed', 'reservation_refused')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                home = Path(directory) / 'home'
                home.mkdir(mode=0o700)
                root = home / '.local/state/piecetogether/jev-usd5-smoke'
                if mode == 'unsafe':
                    os.chmod(home, 0o775)
                elif mode == 'partial':
                    root.mkdir(parents=True, mode=0o700)
                    for parent in (root.parent, root.parent.parent, root.parent.parent.parent):
                        os.chmod(parent, 0o700)
                    (root / 'reservation.json').write_text('{')
                def reserve(*, trust_primary_group=False):
                    if mode == 'fsync':
                        with patch.object(jev_smoke.os, 'fsync', side_effect=OSError('Bearer fixture-secret /private')):
                            original_reserve(trust_primary_group=trust_primary_group)
                    elif mode == 'malformed':
                        error = jev_smoke.ReservationRefused()
                        error.reason = {'Authorization': 'Bearer fixture-secret'}
                        raise error
                    else:
                        original_reserve(trust_primary_group=trust_primary_group)
                output = Path(directory) / 'output'
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(jev_smoke, '_ledger_root', return_value=root), \
                        patch.object(jev_smoke, 'reserve_once', side_effect=reserve), \
                        patch.dict(os.environ, {'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                        patch('http.client.HTTPSConnection', side_effect=AssertionError('network')) as http, \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5',
                                                           '--output', str(output)]), 1)
                    self.assertEqual(http.call_count, 0)
                receipt = json.loads((output / 'receipt.json').read_text())
                self.assertEqual(receipt.get('reservation_refusal_reason'), expected)
                self.assertIsNone(receipt['reserved_cents'])
                self.assertEqual(receipt['attempted_selector_delegations'], 0)
                content = stdout.getvalue() + stderr.getvalue() + ''.join(path.read_text() for path in output.iterdir())
                self.assertNotIn('fixture-secret', content)
                self.assertNotIn('Authorization', content)
                self.assertNotIn('/private', content)

    def test_metadata_echo_variants_never_reach_core_capture_receipt_or_console(self):
        import contextlib
        import io
        import jev_guarded_smoke
        from piecetogether.context import SelectionResult
        from piecetogether.evaluation import capture
        from piecetogether.jev import JevSelector
        observed = []
        def inspecting_capture(core, scenario):
            def inspect(recorder):
                scenario(recorder)
                observed.append(recorder.core.config.database.read_bytes())
                step = recorder.outcomes[0]
                observed.append(json.dumps(recorder.core.inspect(step['result']['communication_id'])).encode())
            return capture(core, inspect)
        # Inject SelectionResult metadata directly at the delegate seam. This is
        # defense in depth, not a schema-valid network exploit of pinned Jev.
        for text in ('FIXTURE-SECRET', r'fixture-\u0073ecret', '%66ixture%2Dsecret',
                     'BeArEr\tredacted', 'aUtHoRiZaTiOn \t: redacted'):
            with self.subTest(text=text), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                output = Path(directory) / 'output'
                stdout, stderr = io.StringIO(), io.StringIO()
                def delegate(req):
                    return SelectionResult('completed', (), (), 'typesafe', 'jev-1.13.0',
                                           req.rubric_version, 10, 0, raw_decisions={'note': text})
                with patch.object(jev_smoke, '_ledger_root', return_value=Path(directory) / 'jev-usd5-smoke'), \
                        patch.object(JevSelector, 'select', side_effect=delegate) as select, \
                        patch.object(jev_guarded_smoke, 'capture', side_effect=inspecting_capture), \
                        patch.dict(os.environ, {'PT_TYPESAFE_API_KEY': 'fixture-secret'}), \
                        patch('http.client.HTTPSConnection') as http, \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(jev_guarded_smoke.main(['--live', '--ack-owner-prepaid-usd5',
                                                           '--output', str(output)]), 1)
                    self.assertEqual(select.call_count, 1)
                    self.assertEqual(http.call_count, 0)
                content = stdout.getvalue() + stderr.getvalue() + ''.join(path.read_text() for path in output.iterdir())
                self.assertNotIn(text, content)
                receipt = json.loads((output / 'receipt.json').read_text())
                self.assertEqual(receipt['selector_failure'], 'selector_unavailable')
                self.assertEqual(receipt['reserved_cents'], 500)
        for value in observed:
            self.assertNotIn(b'fixture-secret', value.lower())
            self.assertNotIn(b'fixture-\\u0073ecret', value)
            self.assertNotIn(b'%66ixture%2Dsecret', value)
            self.assertNotIn(b'authorization', value.lower())
            self.assertNotIn(b'bearer', value.lower())

    def test_secret_echo_never_reaches_capture_core_trace_or_database(self):
        import jev_guarded_smoke
        from piecetogether.evaluation import capture
        observed = []
        def inspecting_capture(core, scenario):
            def inspect(recorder):
                scenario(recorder)
                observed.append(recorder.core.config.database.read_bytes())
                step = recorder.outcomes[0]
                observed.append(json.dumps(recorder.core.inspect(step['result']['communication_id'])).encode())
            return capture(core, inspect)
        for mode in ('echo', 'malformed', 'timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.temp.name) as directory:
                with patch.object(jev_smoke, '_ledger_root', return_value=Path(directory) / 'jev-usd5-smoke'), \
                        patch.object(jev_guarded_smoke, 'capture', side_effect=inspecting_capture):
                    code, _, _, console = self.invoke(mode)
                self.assertEqual(code, 1)
                self.assertNotIn('fixture-secret', console)
        self.assertEqual(len(observed), 6)
        for value in observed:
            self.assertNotIn(b'fixture-secret', value)
            self.assertNotIn(b'Authorization', value)
            self.assertNotIn(b'Bearer ', value)

    def test_replay_match_alone_cannot_hide_wrong_domain_outcome(self):
        import jev_guarded_smoke
        from piecetogether.evaluation import ReplayResult, replay
        def corrupted(case):
            result = replay(case)
            result.actual['steps'][0]['selection'][0]['selected_ids'] = []
            return ReplayResult(result.actual, ())
        with patch.object(jev_guarded_smoke, 'replay', side_effect=corrupted):
            code, output, _, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((output / 'receipt.json').read_text())['error'], 'domain_or_replay_failed')

    def test_optimized_python_still_rejects_incorrect_replay_outcome(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-O', '-m', 'unittest',
            'test_jev_smoke.SmokeCommandTests.test_replay_match_alone_cannot_hide_wrong_domain_outcome'],
            env={**os.environ, 'PYTHONPATH': 'tests'}, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_artifact_failure_never_releases_reservation(self):
        import jev_guarded_smoke
        with patch.object(jev_guarded_smoke, '_write_private', side_effect=OSError('fixture-secret')):
            code, _, requests, console = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(len(requests), 1)
        self.assertNotIn('fixture-secret', console)
        self.assertEqual(json.loads((self.root / 'reservation.json').read_text())['reserved_cents'], 500)


if __name__ == '__main__':
    unittest.main()
