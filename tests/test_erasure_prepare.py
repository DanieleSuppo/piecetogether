"""R2a preparation is durable, fail closed, and never removes temporary test bytes."""
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from piecetogether import evaluation, retention
from piecetogether.artifact_store import LocalArtifactStore
from piecetogether.core import Bootstrap, Core, DevelopmentChannel
from piecetogether.projections import DevelopmentProjectionSink


class ErasurePrepareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Bootstrap(self.root / 'core.sqlite3', {'development:sender': 'actor'},
                                artifact_store=self.root / 'artifacts')
        self.core = Core(self.config)
        self.store = self.core.artifact_store
        self.payload = {'channel': 'development', 'sender': 'sender', 'idempotency_key': 'key',
                        'text': 'retained-secret', 'sent_at': '2026-10-21T12:00:00Z'}

    def files(self):
        return {str(path.relative_to(self.root / 'artifacts')): path.read_bytes()
                for path in (self.root / 'artifacts').rglob('*') if path.is_file()}

    def jobs(self):
        with sqlite3.connect(self.config.database) as db:
            return db.execute('SELECT relative_path, kind, size, device, inode FROM erasure_jobs '
                              'ORDER BY relative_path').fetchall()

    def test_inventory_covers_publication_stage_keys_partial_and_orphans_without_removing_bytes(self):
        published = self.store.stage(b'published-secret', 'publication-key')
        self.store.finalize(published)
        stage = self.store.stage(b'stage-secret', 'stage-key')
        (self.store.staging / ('a' * 32 + '.tmp')).write_bytes(b'partial-secret')
        (self.store.keys / ('b' * 32 + '.tmp')).write_bytes(b'partial-key-secret')
        (self.store.persistent / ('c' * 32)).write_bytes(b'orphan-secret')
        before = self.files()
        status = self.core.erasure_preflight()
        self.assertEqual(status['phase'], 'unprepared')
        self.assertEqual(status['inventory'], 'verified')
        self.assertEqual(status['pending_files'], 8)  # 7 byte/key files plus identifying owner evidence
        self.assertFalse(status['complete'])
        self.assertEqual(self.files(), before)
        self.assertEqual(self.jobs(), [])
        prepared = self.core.prepare_erasure()
        self.assertEqual(prepared['phase'], 'prepared')
        self.assertEqual(prepared['pending_files'], 8)
        self.assertEqual(self.files(), before)
        jobs = self.jobs()
        self.assertEqual({row[0] for row in jobs}, set(before) - {'.artifact-maintenance.lock'})
        self.assertTrue(all(row[2] >= 0 and row[3] >= 0 and row[4] > 0 for row in jobs))
        restarted = Core(self.config)
        self.assertEqual(restarted.erasure_status(), prepared)
        self.assertEqual(restarted.prepare_erasure(), prepared)
        self.assertEqual(self.jobs(), jobs)
        self.assertEqual(self.files(), before)
        self.assertTrue((self.store.staging / stage.reference).exists())
        self.assertNotIn('secret', repr(prepared))
        self.assertNotIn(str(self.root), repr(prepared))
        self.assertTrue(all(item['status'] == 'unverified' for item in prepared['outside_core_local']))

    def test_prepare_freezes_existing_new_core_direct_channels_sinks_store_and_replay(self):
        result = self.core.accept(self.payload)
        outbound = self.core.inspect(result['communication_id'])['outbound']
        from piecetogether.core import Communication
        staged = self.store.stage(b'stage-secret', 'stage-key')
        self.core.prepare_erasure()
        before = self.files()
        with sqlite3.connect(self.config.database) as db:
            before_dump = '\n'.join(db.iterdump())
        for core in (self.core, Core(self.config)):
            for action in (lambda: core.accept(self.payload), core.current_view, core.inspect_history,
                           core.trusted_events, lambda: core.inspect(result['communication_id']),
                           core.dispatch_events, core.retry_artifact_finalization, core.retry_artifact_deletion,
                           core.cleanup_artifact_staging, core.recover_channel_pending,
                           core.expire_records, core.apply_retention,
                           lambda: core.set_view_withheld(False),
                           lambda: core.artifact_bytes('unknown')):
                with self.subTest(action=action), self.assertRaises(retention.RetentionUnavailable):
                    action()
        for action in (lambda: DevelopmentChannel(self.config.database).deliver(Communication(**outbound)),
                       lambda: DevelopmentProjectionSink(self.config.database).deliver(
                           {'id': 'event', 'semantic_commit_id': 'commit'}),
                       lambda: self.store.stage(b'new', 'new-key'),
                       lambda: self.store.finalize(staged), lambda: self.store.read_staged(staged.reference),
                       lambda: self.store.remove(staged.reference),
                       lambda: self.store.cleanup(set(), 0)):
            with self.subTest(action=action), self.assertRaises(retention.RetentionUnavailable):
                action()
        with self.assertRaises(evaluation.ReplayUnavailable):
            evaluation.capture(self.core, lambda recorder: recorder.accept(self.payload))
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual('\n'.join(db.iterdump()), before_dump)
        self.assertEqual(self.files(), before)

    def test_initial_operator_prepare_bypasses_normal_startup_recovery_and_provider_construction(self):
        from piecetogether import erasure
        stage = self.store.stage(b'never-publish-secret', 'stage-key')
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO artifact_publications VALUES ('artifact', ?, ?, ?, 'pending', NULL)",
                       (stage.reference, stage.checksum, stage.size))
            db.execute("INSERT INTO artifact_deletions VALUES ('old-artifact', ?, NULL)", ('d' * 32,))
        before = self.files()
        with patch.object(Core, '__init__', side_effect=AssertionError('no Core/provider construction')), \
             patch.object(LocalArtifactStore, 'finalize', side_effect=AssertionError('never publish')), \
             patch.object(LocalArtifactStore, 'remove', side_effect=AssertionError('never remove')):
            preflight = erasure.preflight_local(self.config.database, store_root=self.config.artifact_store)
            self.assertEqual(preflight['phase'], 'unprepared')
            self.assertEqual(preflight['inventory'], 'verified')
            self.assertEqual(self.jobs(), [])
            status = erasure.prepare_local(self.config.database, store_root=self.config.artifact_store)
        self.assertEqual(status['inventory'], 'verified')
        self.assertFalse(status['complete'])
        self.assertEqual(self.files(), before)
        self.assertEqual(self.core.erasure_status(), status)
        self.assertEqual(self.core.prepare_erasure(), status)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT state FROM artifact_publications').fetchone()[0], 'pending')
            self.assertEqual(db.execute('SELECT count(*) FROM artifact_deletions').fetchone()[0], 1)
        missing = self.root / 'not-a-core.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):
            erasure.prepare_local(missing, store_root=None)
        self.assertFalse(missing.exists())
        legacy = replace(self.config, database=self.root / 'legacy-local.sqlite3', artifact_store=None)
        Core(legacy)
        with sqlite3.connect(legacy.database) as db:
            for table in ('artifact_store_owner', 'erasure_state', 'erasure_jobs'):
                db.execute(f'DROP TABLE {table}')
        self.assertEqual(erasure.preflight_local(legacy.database, store_root=None)['phase'], 'unprepared')
        self.assertEqual(erasure.prepare_local(legacy.database, store_root=None)['phase'], 'prepared')
        partial = self.root / 'partial.sqlite3'
        with sqlite3.connect(partial) as db:
            db.execute('CREATE TABLE unrelated (value TEXT)')
        with self.assertRaises(ValueError):
            erasure.prepare_local(partial, store_root=None)
        with sqlite3.connect(partial) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                             [('unrelated',)])

    def test_pending_publication_deletion_survive_prepare_and_startup_never_publishes(self):
        stage = self.store.stage(b'never-publish-secret', 'stage-key')
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO artifact_publications VALUES ('artifact', ?, ?, ?, 'pending', NULL)",
                       (stage.reference, stage.checksum, stage.size))
            db.execute("INSERT INTO artifact_deletions VALUES ('old-artifact', ?, NULL)", ('d' * 32,))
        before = self.files()
        self.core.prepare_erasure()
        jobs = self.jobs()
        restarted = Core(self.config)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.jobs(), jobs)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT state FROM artifact_publications').fetchone()[0], 'pending')
            self.assertEqual(db.execute('SELECT count(*) FROM artifact_deletions').fetchone()[0], 1)
        self.assertFalse((self.store.persistent / stage.reference).exists())
        self.assertFalse(restarted.erasure_status()['complete'])

    def test_unsafe_symlink_unknown_and_hardlinked_entries_block_without_touching_unrelated_files(self):
        unrelated = self.root / 'unrelated-secret'
        unrelated.write_bytes(b'outside-secret')
        for kind in ('symlink', 'hardlink', 'foreign_name', 'directory'):
            with self.subTest(kind=kind):
                path = self.store.staging / ('a' * 32)
                if kind == 'symlink':
                    path.symlink_to(unrelated)
                elif kind == 'hardlink':
                    path.hardlink_to(unrelated)
                elif kind == 'directory':
                    path.mkdir()
                else:
                    path = self.store.root / 'foreign-file'
                    path.write_bytes(b'foreign-secret')
                try:
                    status = self.core.erasure_preflight()
                    self.assertEqual(status['inventory'], 'blocked')
                    self.assertEqual(status['pending_files'], 0)
                    self.assertEqual(self.jobs(), [])
                    self.assertEqual(unrelated.read_bytes(), b'outside-secret')
                finally:
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink()
        self.assertEqual(self.core.erasure_preflight()['inventory'], 'verified')

    def test_new_core_store_construction_cannot_race_exclusive_prepare(self):
        original = LocalArtifactStore.__init__
        def construct(store, *args, **kwargs):
            with self.assertRaises(retention.RetentionBusy):
                self.core.prepare_erasure()
            original(store, *args, **kwargs)
        with patch.object(LocalArtifactStore, '__init__', construct):
            Core(self.config)
        self.assertEqual(self.core.erasure_status()['phase'], 'unprepared')

    def test_inventory_io_holds_no_sqlite_writer_transaction(self):
        from piecetogether import erasure
        original = erasure.inventory
        phases = []
        def inspect(database, store):
            with sqlite3.connect(database, timeout=0) as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT phase FROM erasure_state').fetchone()
                phases.append(row[0] if row else 'unprepared')
                db.rollback()
            return original(database, store)
        with patch.object(erasure, 'inventory', side_effect=inspect):
            self.assertEqual(self.core.prepare_erasure()['inventory'], 'verified')
        self.assertEqual(phases, ['unprepared', 'prepared'])

    def test_full_inventory_blockers_leave_first_prepare_unprepared_and_source_usable(self):
        from piecetogether import erasure
        for kind in ('stray-root', 'nested', 'unknown-name', 'nonempty-lock', 'stat-failure'):
            with self.subTest(kind=kind):
                if kind == 'stray-root':
                    path = self.store.root / 'stray'
                    path.write_bytes(b'untouched')
                elif kind == 'nested':
                    path = self.store.staging / ('a' * 32)
                    path.mkdir()
                elif kind == 'unknown-name':
                    path = self.store.keys / 'unknown'
                    path.write_bytes(b'untouched')
                else:
                    path = self.store.root / '.artifact-maintenance.lock'
                    if kind == 'nonempty-lock':
                        path.write_bytes(b'untouched')
                before = self.files()
                with sqlite3.connect(self.config.database) as db:
                    dump = '\n'.join(db.iterdump())
                regular = erasure._regular
                def stat_entry(entry):
                    if entry == path:
                        raise OSError('private-path-secret')
                    return regular(entry)
                if kind == 'stat-failure':
                    with patch.object(erasure, '_regular', side_effect=stat_entry):
                        result = self.core.prepare_erasure()
                else:
                    result = self.core.prepare_erasure()
                self.assertEqual(result['phase'], 'unprepared')
                self.assertEqual(result['inventory'], 'blocked')
                self.assertEqual(result['blocker'], 'storage_unavailable' if kind == 'stat-failure' else 'unsafe_entry')
                self.assertEqual(self.files(), before)
                with sqlite3.connect(self.config.database) as db:
                    self.assertEqual('\n'.join(db.iterdump()), dump)
                self.assertEqual(self.jobs(), [])
                self.core.accept({**self.payload, 'idempotency_key': kind})
                staged = self.store.stage(b'ordinary', kind)
                self.assertEqual(self.store.read_staged(staged.reference), b'ordinary')
                if kind == 'nested':
                    path.rmdir()
                elif kind in ('stray-root', 'unknown-name'):
                    path.unlink()
                elif kind == 'nonempty-lock':
                    path.write_bytes(b'')

    def test_nonempty_control_lock_is_not_hidden_from_inventory(self):
        lock = self.store.root / '.artifact-maintenance.lock'
        lock.write_bytes(b'unrelated-lock-secret')
        before = self.files()
        status = self.core.prepare_erasure()
        self.assertEqual(status['inventory'], 'blocked')
        self.assertEqual(status['blocker'], 'unsafe_entry')
        self.assertEqual(status['phase'], 'unprepared')
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.files(), before)

    def test_nonempty_legacy_and_known_foreign_store_never_auto_claimed(self):
        legacy_root = self.root / 'legacy-store'
        legacy = LocalArtifactStore(legacy_root)
        legacy.stage(b'legacy-secret', 'legacy-key')
        legacy_core = Core(replace(self.config, database=self.root / 'legacy.sqlite3', artifact_store=legacy_root))
        self.assertEqual(legacy_core.prepare_erasure()['blocker'], 'ownership_unverified')
        self.assertFalse((legacy_root / '.core-owner.json').exists())
        foreign = Core(replace(self.config, database=self.root / 'foreign.sqlite3'))
        with self.assertRaises(retention.RetentionUnavailable):
            foreign.artifact_store.stage(b'foreign-secret', 'foreign-key')
        before = self.files()
        status = foreign.prepare_erasure()
        self.assertEqual(status['blocker'], 'foreign_owner')
        self.assertEqual(self.files(), before)
        self.assertEqual(self.core.erasure_preflight()['inventory'], 'verified')

    def test_missing_or_nonlocal_store_with_known_obligations_stays_blocked(self):
        for store in (None, object()):
            config = replace(self.config, database=self.root / ('missing.sqlite3' if store is None else 'remote.sqlite3'),
                             artifact_store=None)
            core = Core(config, artifact_store=store)
            with sqlite3.connect(config.database) as db:
                db.execute("INSERT INTO artifact_deletions VALUES ('artifact', ?, NULL)", ('e' * 32,))
            status = core.prepare_erasure()
            self.assertEqual(status['inventory'], 'blocked')
            self.assertEqual(status['phase'], 'unprepared')
            self.assertFalse(status['complete'])
            self.assertEqual(status['pending_files'], 0)
            self.assertEqual(core.prepare_erasure(), status)

    def test_first_inventory_failure_does_not_freeze_but_prepared_retry_keeps_worklist(self):
        self.store.stage(b'pending-secret', 'stage-key')
        from piecetogether import erasure
        with patch.object(erasure, 'inventory', side_effect=OSError('private-path-secret')):
            status = self.core.prepare_erasure()
        self.assertEqual(status['phase'], 'unprepared')
        self.assertEqual(status['blocker'], 'storage_unavailable')
        self.assertNotIn('private-path-secret', repr(status))
        self.core.accept(self.payload)
        recovered = self.core.prepare_erasure()
        self.assertEqual(recovered['inventory'], 'verified')
        jobs = self.jobs()
        with patch.object(erasure, 'inventory', side_effect=OSError('private-path-secret')):
            failed = self.core.prepare_erasure()
        self.assertEqual(failed['inventory'], 'blocked')
        self.assertEqual(failed['phase'], 'prepared')
        self.assertEqual(Core(self.config).erasure_status(), failed)
        with self.assertRaises(retention.RetentionUnavailable):
            self.core.accept(self.payload)
        self.assertEqual(self.jobs(), jobs)
        self.assertEqual(self.core.prepare_erasure()['pending_files'], len(jobs))

    def test_direct_acquisition_reads_and_workers_stop_before_transport_or_checkpoint_io(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig, EmailAcquisitionWorker, ImapConfig
        from piecetogether.telegram_channel import TelegramChannel, TelegramConfig, TelegramAcquisitionWorker
        class OfflineTransport:
            def fetch(self, *args):
                raise AssertionError('frozen worker must not contact transport')
            def send(self, *args):
                raise AssertionError('frozen channel must not contact transport')
        transport = OfflineTransport()
        email = EmailChannel(self.config.database, EmailConfig('core@example.invalid', 'localhost', security='plain'),
                             transport)
        telegram_config = TelegramConfig('123', 'synthetic-reference')
        telegram = TelegramChannel(self.config.database, telegram_config, transport, 'synthetic-token')
        imap = ImapConfig('localhost', 'synthetic-user', 'synthetic-reference')
        workers = (EmailAcquisitionWorker(self.core, email, transport, imap),
                   TelegramAcquisitionWorker(self.core, telegram, transport, telegram_config))
        self.core.prepare_erasure()
        actions = [lambda: email.acquired('mailbox', '1', '1'), email.acquisition_outcomes,
                   telegram.offset, telegram.acquisition_outcomes,
                   lambda: email.record_acquisition('mailbox', '1', '1', 'unsupported', None, 'unsupported'),
                   lambda: telegram.record_acquisition(1, None, 'unsupported'),
                   lambda: email.normalize(b'bad'), lambda: telegram.normalize({}),
                   lambda: email.deliver(None), lambda: telegram.deliver(None)]
        for action in (*actions, *(worker.poll_once for worker in workers)):
            with self.subTest(action=action), self.assertRaises(retention.RetentionUnavailable):
                action()

    def workers(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig, EmailAcquisitionWorker, ImapConfig
        from piecetogether.telegram_channel import TelegramChannel, TelegramConfig, TelegramAcquisitionWorker
        class Transport:
            def __init__(self, kind):
                self.kind, self.calls = kind, 0
                self.on_fetch = lambda: None
            def fetch(self, *args):
                self.calls += 1
                self.on_fetch()
                return ('1', ()) if self.kind == 'email' else []
            def send(self, *args):
                raise AssertionError('no outbound transport expected')
        for kind in ('email', 'telegram'):
            transport = Transport(kind)
            email = EmailConfig('core@example.invalid', 'localhost', security='plain') if kind == 'email' else None
            telegram = TelegramConfig('123', 'synthetic-reference', poll_seconds=2) if kind == 'telegram' else None
            config = replace(self.config, channel=kind, database=self.root / (kind + '.sqlite3'),
                             artifact_store=self.root / (kind + '-store'), email=email, telegram=telegram,
                             secret_references={'synthetic-reference': 'SYNTHETIC_NOT_READ'},
                             capabilities=('receive', 'reply', 'text', 'threading'),
                             identities={('email:sender@example.invalid' if kind == 'email' else 'telegram:123'): 'actor'})
            if kind == 'email':
                channel_config = email
                channel = EmailChannel(config.database, channel_config, transport)
                core = Core(config, channel=channel)
                worker = EmailAcquisitionWorker(core, channel, transport,
                    ImapConfig('localhost', 'synthetic-user', 'synthetic-reference', poll_seconds=2))
            else:
                channel_config = telegram
                channel = TelegramChannel(config.database, channel_config, transport, 'synthetic-token')
                core = Core(config, channel=channel)
                worker = TelegramAcquisitionWorker(core, channel, transport, channel_config)
            yield core, worker, transport

    def test_worker_run_exits_cleanly_without_transport_or_sleep_after_real_preparation(self):
        for core, worker, transport in self.workers():
            with self.subTest(channel=core.config.channel):
                core.prepare_erasure()
                worker.sleep = lambda delay: self.fail('frozen worker must exit, not sleep/spin')
                with self.assertLogs('piecetogether.' + core.config.channel + '_channel', level='WARNING') as logs:
                    worker.run()
                self.assertEqual([record.getMessage() for record in logs.records],
                                 ['Acquisition stopped: retention unavailable.'])
                self.assertEqual(transport.calls, 0)

    def test_worker_run_backs_off_real_store_contention_then_stops_on_freeze(self):
        import fcntl
        for core, worker, transport in self.workers():
            with self.subTest(channel=core.config.channel):
                with (core.artifact_store.root / '.artifact-maintenance.lock').open('r+') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    transport.on_fetch = lambda: core.artifact_store.stage(b'temporary', 'busy')
                    waits = []
                    def sleep(delay):
                        waits.append(delay)
                        if len(waits) == 10:
                            fcntl.flock(lock, fcntl.LOCK_UN)
                            core.prepare_erasure()
                        elif len(waits) > 10:
                            self.fail('worker spun after freeze')
                    worker.sleep = sleep
                    worker.run()
                self.assertEqual(waits, [2, 4, 8, 16, 32, 64, 128, 256, 300, 300])
                self.assertEqual(transport.calls, 10)
                self.assertEqual(list(core.artifact_store.staging.iterdir()), [])

    def test_worker_unavailable_stop_logs_one_sanitized_warning_without_second_transport_call(self):
        for core, worker, transport in self.workers():
            with self.subTest(channel=core.config.channel):
                waits = []
                def sleep(delay):
                    waits.append(delay)
                    with sqlite3.connect(core.config.database) as db:
                        db.execute("INSERT INTO retention_state VALUES ('communications', 'delete')")
                worker.sleep = sleep
                with self.assertLogs('piecetogether.' + core.config.channel + '_channel', level='WARNING') as logs:
                    worker.run()
                self.assertEqual(transport.calls, 1)
                self.assertEqual(waits, [2])
                self.assertEqual(len(logs.records), 1)
                record = logs.records[0]
                self.assertEqual(record.levelname, 'WARNING')
                self.assertEqual(record.getMessage(), 'Acquisition stopped: retention unavailable.')
                self.assertIsNone(record.exc_info)
                self.assertFalse(record.args)

    def test_worker_normal_poll_cadence_still_runs_before_freeze(self):
        for core, worker, transport in self.workers():
            waits = []
            def sleep(delay):
                waits.append(delay)
                core.prepare_erasure()
            worker.sleep = sleep
            worker.run()
            self.assertEqual(waits, [2])
            self.assertEqual(transport.calls, 1)

    def test_owner_marker_cannot_create_marker_chosen_directories_or_locks(self):
        import json
        from piecetogether import erasure
        marker = self.store.root / erasure.OWNER_FILE
        original = marker.read_bytes()
        chosen = self.root / 'never-create' / 'nested' / 'chosen.sqlite3'
        for store in (self.store, LocalArtifactStore(self.store.root, create=False)):
            with self.subTest(bound=store.database is not None):
                marker.write_text(json.dumps({'token': 'a' * 32, 'database': str(chosen)}))
                with self.assertRaises(retention.RetentionUnavailable) as raised:
                    store.stage(b'never-written', 'tampered')
                self.assertNotIn(str(chosen), str(raised.exception))
                self.assertFalse(chosen.parent.parent.exists())
                self.assertEqual(list(self.store.staging.iterdir()), [])
        marker.write_bytes(original)
        self.assertEqual(self.store.stage(b'normal', 'normal').size, 6)

    def test_marker_requires_canonical_existing_database_and_matching_root_token_evidence(self):
        import json
        from piecetogether import erasure
        marker = self.store.root / erasure.OWNER_FILE
        original = marker.read_bytes()
        owner = json.loads(original)
        arbitrary = self.root / 'arbitrary.sqlite3'
        with sqlite3.connect(arbitrary) as db:
            erasure.initialize(db)
            db.execute('INSERT INTO artifact_store_owner VALUES (1, ?, ?)',
                       ('b' * 32, str(self.root / 'wrong-root')))
        absent_evidence = self.root / 'absent-evidence.sqlite3'
        with sqlite3.connect(absent_evidence) as db:
            db.execute('CREATE TABLE unrelated (value TEXT)')
        alias = self.root / 'database-alias'
        alias.symlink_to(self.config.database)
        cases = [dict(owner, database=str(absent_evidence)),
                 dict(owner, database=str(arbitrary)), dict(owner, database=str(alias)),
                 dict(owner, database=__import__('os').path.relpath(arbitrary)), dict(owner, database='bad\u0000path'),
                 dict(owner, token='b' * 32)]
        for store in (self.store, LocalArtifactStore(self.store.root, create=False)):
            for value in cases:
                with self.subTest(bound=store.database is not None, value=value):
                    marker.write_text(json.dumps(value))
                    with self.assertRaises(retention.RetentionUnavailable):
                        store.stage(b'never-written', 'invalid-owner')
                    self.assertFalse(Path(str(arbitrary) + '.retention.lock').exists())
                    self.assertFalse(Path(str(absent_evidence) + '.retention.lock').exists())
        marker.write_bytes(original)
        unbound = LocalArtifactStore(self.store.root, create=False)
        unbound.stage(b'normal', 'normal')
        self.core.prepare_erasure()
        with self.assertRaises(retention.RetentionUnavailable):
            unbound.stage(b'frozen', 'frozen')

    def test_corrupt_symlinked_and_racing_marker_fail_as_sanitized_unavailable(self):
        import os
        from piecetogether import erasure
        marker = self.store.root / erasure.OWNER_FILE
        original = marker.read_bytes()
        outside = self.root / 'outside-owner-secret'
        outside.write_bytes(original)
        for store in (self.store, LocalArtifactStore(self.store.root, create=False)):
            for malformed in (b'not-json-private-secret', b'\xff', b'{}', b'x' * 4097):
                marker.write_bytes(malformed)
                with self.assertRaises(retention.RetentionUnavailable) as raised:
                    store.stage(b'never-written', 'corrupt')
                self.assertNotIn('private-secret', str(raised.exception))
                self.assertNotIn(str(self.root), str(raised.exception))
            marker.unlink()
            marker.symlink_to(outside)
            with self.assertRaises(retention.RetentionUnavailable):
                store.stage(b'never-written', 'symlink')
            marker.unlink()
            marker.write_bytes(original)
            original_open = os.open
            def racing_open(path, flags, *args, **kwargs):
                if Path(path) == marker:
                    marker.unlink(missing_ok=True)
                return original_open(path, flags, *args, **kwargs)
            with patch('piecetogether.erasure.os.open', side_effect=racing_open):
                with self.assertRaises(retention.RetentionUnavailable):
                    store.stage(b'never-written', 'race')
            marker.write_bytes(original)
        self.assertEqual(outside.read_bytes(), original)
        self.assertEqual(list(self.store.staging.iterdir()), [])

    def test_unprepared_marker_loss_preserves_bytes_and_bound_access_after_restart_without_attestation(self):
        staged = self.store.stage(b'preserved-secret', 'preserved-key')
        marker = self.store.root / '.core-owner.json'
        marker.unlink()
        before = self.files()
        with sqlite3.connect(self.config.database) as db:
            owner = db.execute('SELECT * FROM artifact_store_owner').fetchall()
        for core in (self.core, Core(self.config)):
            store = core.artifact_store
            self.assertEqual(self.files(), before)
            self.assertEqual(store.read_staged(staged.reference), b'preserved-secret')
            self.assertEqual(store.stage(b'preserved-secret', 'preserved-key'), staged)
            self.assertEqual(core.prepare_erasure()['phase'], 'unprepared')
            self.assertEqual(core.erasure_preflight()['blocker'], 'ownership_unverified')
            self.assertFalse(marker.exists())
            with sqlite3.connect(self.config.database) as db:
                self.assertEqual(db.execute('SELECT * FROM artifact_store_owner').fetchall(), owner)
            self.assertEqual(self.files(), before)
        extra = store.stage(b'ordinary', 'ordinary')
        store.finalize(extra)
        self.assertEqual(store.read(extra.reference), b'ordinary')
        store.remove(extra.reference)
        self.assertFalse((store.persistent / extra.reference).exists())
        self.core.accept(self.payload)
        with retention.operation(self.config.database, exclusive=True):
            with self.assertRaises(retention.RetentionBusy):
                store.read_staged(staged.reference)
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO retention_state VALUES ('communications', 'delete')")
        for core in (self.core, Core(self.config)):
            self.assertEqual(core.artifact_store.read_staged(staged.reference), b'preserved-secret')
            with self.assertRaisesRegex(retention.RetentionUnavailable, 'processing unavailable after retention'):
                core.artifact_bytes('unknown')
        self.assertEqual(store.staging.joinpath(staged.reference).read_bytes(), b'preserved-secret')
        self.assertFalse(marker.exists())

    def test_legacy_terminal_direct_bytes_remain_readable_with_present_or_missing_marker(self):
        staged = self.store.stage(b'legacy-owned', 'legacy-owned')
        self.store.finalize(staged)
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO retention_state VALUES ('communications', 'delete')")
        marker = self.store.root / '.core-owner.json'
        for present in (True, False):
            if not present:
                marker.unlink()
            for core in (self.core, Core(self.config)):
                self.assertEqual(core.artifact_store.read(staged.reference), b'legacy-owned')
                with self.assertRaises(retention.RetentionUnavailable):
                    core.artifact_bytes('unknown')
        self.assertFalse(marker.exists())

    def test_known_empty_store_marker_loss_is_not_repaired_or_rebound_on_restart(self):
        marker = self.store.root / '.core-owner.json'
        marker.unlink()
        with sqlite3.connect(self.config.database) as db:
            before = '\n'.join(db.iterdump())
        restarted = Core(self.config)
        self.assertFalse(marker.exists())
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual('\n'.join(db.iterdump()), before)
        self.assertEqual(restarted.prepare_erasure()['blocker'], 'ownership_unverified')
        self.assertEqual(restarted.erasure_status()['phase'], 'unprepared')
        self.assertEqual(restarted.artifact_store.stage(b'ordinary', 'ordinary').size, 8)
        self.assertFalse(marker.exists())

    def test_missing_marker_still_checks_known_root_before_creating_database_fence(self):
        marker = self.store.root / '.core-owner.json'
        marker.unlink()
        lock = Path(str(self.config.database) + '.retention.lock')
        lock.unlink()
        moved = self.root / 'moved-store'
        self.store.root.rename(moved)
        wrong = LocalArtifactStore(moved, create=False)
        wrong.database = self.config.database
        with self.assertRaisesRegex(retention.RetentionUnavailable, '^Artifact owner state unavailable$'):
            wrong.stage(b'never-written', 'wrong-root')
        self.assertFalse(lock.exists())
        self.assertEqual(list(wrong.staging.iterdir()), [])
        self.assertFalse((moved / '.core-owner.json').exists())

    def test_byte_access_preserves_freeze_error_and_sanitizes_bad_owner(self):
        self.core.prepare_erasure()
        with self.assertRaisesRegex(retention.RetentionUnavailable,
                                    '^Core local erasure prepared; deployment frozen$'):
            self.store.stage(b'frozen', 'frozen')
        (self.store.root / '.core-owner.json').write_bytes(b'private-secret')
        with self.assertRaisesRegex(retention.RetentionUnavailable, '^Artifact owner state unavailable$'):
            self.store.stage(b'never-written', 'bad-owner')

    def test_missing_marker_preserves_bound_freeze_but_not_false_legacy_erase_authority(self):
        staged = self.store.stage(b'preserved', 'preserved')
        marker = self.store.root / '.core-owner.json'
        self.core.prepare_erasure()
        marker.unlink()
        before = self.files()
        for store in (self.store, Core(self.config).artifact_store):
            for action in (lambda: store.stage(b'frozen', 'missing-marker'),
                           lambda: store.read_staged(staged.reference),
                           lambda: store.finalize(staged), lambda: store.remove(staged.reference)):
                with self.assertRaisesRegex(retention.RetentionUnavailable, 'deployment frozen'):
                    action()
        self.assertEqual(self.files(), before)
        self.assertFalse(marker.exists())
        legacy_root = self.root / 'standalone-legacy'
        legacy = LocalArtifactStore(legacy_root)
        self.assertEqual(legacy.stage(b'legacy', 'legacy').size, 6)
        legacy_core = Core(replace(self.config, database=self.root / 'legacy-missing.sqlite3',
                                   artifact_store=legacy_root))
        self.assertEqual(legacy_core.prepare_erasure()['inventory'], 'blocked')
        self.assertEqual(legacy_core.erasure_status()['phase'], 'unprepared')
        legacy_core.accept(self.payload)

    def test_database_and_store_lock_symlinks_are_not_followed_or_modified(self):
        outside = self.root / 'outside-lock-secret'
        outside.write_bytes(b'unchanged-secret')
        original_stat = outside.stat()
        for lock, acquire in (
                (Path(str(self.config.database) + '.retention.lock'),
                 lambda: retention.operation(self.config.database)),
                (self.store.root / '.artifact-maintenance.lock', self.store.maintenance_lock)):
            with self.subTest(lock=lock.name):
                lock.unlink(missing_ok=True)
                lock.symlink_to(outside)
                with self.assertRaises(OSError):
                    with acquire():
                        self.fail('must not acquire a lock through a symlink')
                self.assertEqual(outside.read_bytes(), b'unchanged-secret')
                self.assertEqual(outside.stat().st_ino, original_stat.st_ino)
                lock.unlink()

    def test_invalid_first_prepare_is_unprepared_readonly_and_source_remains_usable(self):
        from piecetogether import erasure
        alias = self.root / 'ancestor-alias'
        real = self.root / 'real-parent'
        real.mkdir()
        alias.symlink_to(real, target_is_directory=True)
        alias_core = Core(replace(self.config, database=self.root / 'ancestor.sqlite3',
                                  artifact_store=alias / 'store'))
        wrong_core = Core(replace(self.config, database=self.root / 'wrong-root.sqlite3',
                                  artifact_store=self.root / 'wrong-root'))
        missing_root = self.root / 'missing-root'
        cases = [(alias_core, lambda: alias_core.prepare_erasure()),
                 (self.core, lambda: erasure.prepare_local(self.config.database,
                                                          store_root=wrong_core.artifact_store.root)),
                 (self.core, lambda: erasure.prepare_local(self.config.database, store_root=missing_root)),
                 (self.core, lambda: erasure.prepare_local(self.config.database, store_root=None)),
                 (self.core, lambda: erasure.prepare_local(self.config.database, store_root=alias_core.artifact_store.root))]
        for core, prepare in cases:
            with self.subTest(database=core.config.database.name):
                with sqlite3.connect(core.config.database) as db:
                    before = '\n'.join(db.iterdump())
                result = prepare()
                self.assertEqual(result['phase'], 'unprepared')
                self.assertEqual(result['inventory'], 'blocked')
                with sqlite3.connect(core.config.database) as db:
                    self.assertEqual('\n'.join(db.iterdump()), before)
                    self.assertEqual(db.execute('SELECT count(*) FROM erasure_state').fetchone()[0], 0)
                core.accept(self.payload)
                core.artifact_store.stage(b'still-usable', 'usable')
        self.assertFalse(missing_root.exists())

    def test_first_prepare_revalidates_owner_after_durable_freeze_without_a_writer_transaction(self):
        from piecetogether import erasure
        marker = self.store.root / erasure.OWNER_FILE
        original = erasure.inspect_inventory
        phases = []
        def inspect(database, store):
            with sqlite3.connect(database, timeout=0) as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT phase FROM erasure_state').fetchone()
                phases.append(row[0] if row else 'unprepared')
                db.rollback()
            if phases[-1] == 'prepared':
                marker.write_text('{}')  # Ownership changed after readonly full inventory.
            return original(database, store)
        with patch.object(erasure, 'inspect_inventory', side_effect=inspect):
            result = self.core.prepare_erasure()
        self.assertEqual(result['phase'], 'prepared')
        self.assertEqual(result['blocker'], 'ownership_unverified')
        self.assertEqual(phases, ['unprepared', 'prepared'])
        self.assertEqual(self.jobs(), [])
        with self.assertRaises(retention.RetentionUnavailable):
            self.core.accept(self.payload)

    def test_byte_access_revalidates_marker_after_database_fence_without_following_new_owner(self):
        import json
        from contextlib import contextmanager
        from piecetogether import erasure
        marker = self.store.root / erasure.OWNER_FILE
        original = json.loads(marker.read_bytes())
        chosen = self.root / 'never-create-race' / 'db.sqlite3'
        operation = retention.operation
        @contextmanager
        def fenced(database, **kwargs):
            with operation(database, **kwargs):
                marker.write_text(json.dumps(dict(original, database=str(chosen))))
                yield
        for store in (self.store, LocalArtifactStore(self.store.root, create=False)):
            marker.write_text(json.dumps(original))
            with patch('piecetogether.retention.operation', side_effect=fenced):
                with self.assertRaises(retention.RetentionUnavailable):
                    store.stage(b'never-written', 'race-owner')
            self.assertFalse(chosen.parent.exists())
            self.assertEqual(list(self.store.staging.iterdir()), [])

    def test_prepared_retry_of_wrong_root_keeps_freeze_and_original_jobs(self):
        from piecetogether import erasure
        self.store.stage(b'preserved', 'preserved')
        self.core.prepare_erasure()
        before = self.jobs()
        result = erasure.prepare_local(self.config.database, store_root=None)
        self.assertEqual(result['phase'], 'prepared')
        self.assertEqual(result['blocker'], 'store_missing')
        self.assertEqual(self.jobs(), before)
        with self.assertRaises(retention.RetentionUnavailable):
            self.core.accept(self.payload)

    def test_job_batch_sql_failure_rolls_back_jobs_but_keeps_durable_freeze(self):
        self.store.stage(b'pending-secret', 'stage-key')
        with sqlite3.connect(self.config.database) as db:
            db.execute("CREATE TRIGGER reject_stage BEFORE INSERT ON erasure_jobs "
                       "WHEN NEW.kind='stage' BEGIN SELECT RAISE(ABORT, 'injected'); END")
        before = self.files()
        with self.assertRaises(sqlite3.IntegrityError):
            self.core.prepare_erasure()
        self.assertEqual(self.core.erasure_status()['phase'], 'prepared')
        self.assertEqual(self.core.erasure_status()['inventory'], 'pending')
        self.assertEqual(self.jobs(), [])
        self.assertEqual(self.files(), before)
        with sqlite3.connect(self.config.database) as db:
            db.execute('DROP TRIGGER reject_stage')
        self.assertEqual(Core(self.config).prepare_erasure()['inventory'], 'verified')
        self.assertEqual(len(self.jobs()), 3)

    def test_changed_inode_invalid_refs_and_symlink_roots_never_become_removal_authority(self):
        staged = self.store.stage(b'pending-secret', 'stage-key')
        self.core.prepare_erasure()
        jobs = self.jobs()
        target = self.store.staging / staged.reference
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'foreign-secret')
        replacement.replace(target)  # Simulate noncooperating filesystem writer, not supported Core I/O.
        self.assertEqual(self.core.prepare_erasure()['blocker'], 'inventory_changed')
        self.assertEqual(self.jobs(), jobs)
        self.assertEqual(target.read_bytes(), b'foreign-secret')
        config = replace(self.config, database=self.root / 'malformed.sqlite3', artifact_store=None)
        core = Core(config)
        with sqlite3.connect(config.database) as db:
            db.execute("INSERT INTO artifact_deletions VALUES ('artifact', '../unrelated', NULL)")
        self.assertEqual(core.prepare_erasure()['blocker'], 'invalid_reference')
        alias = self.root / 'store-alias'
        alias.symlink_to(self.store.root, target_is_directory=True)
        alias_core = Core(replace(self.config, database=self.root / 'alias.sqlite3', artifact_store=alias))
        self.assertEqual(alias_core.prepare_erasure()['blocker'], 'unsafe_boundary')
        traversal_core = Core(replace(self.config, database=self.root / 'traversal.sqlite3',
                                      artifact_store=self.store.root / '..' / 'artifacts'))
        self.assertEqual(traversal_core.prepare_erasure()['blocker'], 'unsafe_boundary')
        self.assertEqual(target.read_bytes(), b'foreign-secret')

    def test_pending_snapshot_replay_fails_before_any_model_store_or_channel_construction(self):
        import hashlib
        from piecetogether.core import connect
        case = evaluation.capture(self.core, lambda recorder: recorder.accept(self.payload))
        self.core.prepare_erasure()
        # Simulate an old capture tool preserving the frozen DB; replay must reject it.
        with sqlite3.connect(self.config.database) as source:
            snapshot = self.root / 'pending-snapshot.sqlite3'
            with sqlite3.connect(snapshot) as destination:
                source.backup(destination)
        frozen_state = snapshot.read_bytes()
        case = replace(case, initial_state=frozen_state, state_digest=hashlib.sha256(frozen_state).hexdigest())
        with patch('piecetogether.evaluation._CapturedModel', side_effect=AssertionError('no model construction')), \
             patch('piecetogether.core.LocalArtifactStore', side_effect=AssertionError('no local replay store')):
            with self.assertRaises(evaluation.ReplayUnavailable):
                evaluation.replay(case)
        with self.assertRaises(evaluation.ReplayUnavailable):
            evaluation._snapshot(self.config.database)
        with connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM turns').fetchone()[0], 0)

    def test_rollback_and_legacy_snapshot_schema_remain_default_until_genuine_freeze(self):
        from piecetogether.core import connect
        config = replace(self.config, database=self.root / 'legacy-default.sqlite3', artifact_store=None)
        core = Core(config)
        result = core.accept(self.payload)
        with connect(config.database) as db:
            for table in ('artifact_store_owner', 'erasure_state', 'erasure_jobs'):
                db.execute(f'DROP TABLE {table}')
            before = [tuple(row) for row in db.execute('SELECT * FROM turns')]
        # A pre-R2 database remains replayable; absence is not inferred erasure.
        case = evaluation.capture(core, lambda recorder: recorder.accept({**self.payload, 'idempotency_key': 'new'}))
        self.assertTrue(evaluation.replay(case).matched)
        migrated = Core(config)
        self.assertEqual(migrated.accept(self.payload), result)
        self.assertEqual(migrated.erasure_status()['phase'], 'unprepared')
        self.assertEqual(migrated.erasure_preflight()['pending_files'], 0)
        with connect(config.database) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM turns')], before)
            # Before preparation, additive empty schema can be removed without data loss.
            for table in ('artifact_store_owner', 'erasure_state', 'erasure_jobs'):
                db.execute(f'DROP TABLE {table}')
        self.assertEqual(Core(config).accept(self.payload), result)

    def test_owned_store_operation_holds_nonblocking_database_fence_through_io(self):
        import threading
        started = threading.Event()
        release = threading.Event()
        failures = []
        def digest(content):
            started.set()
            if not release.wait(5):
                raise AssertionError('test release timed out')
            import hashlib
            return hashlib.sha256(content).hexdigest()
        def stage():
            try:
                self.store.stage(b'pending-secret', 'stage-key')
            except BaseException as error:
                failures.append(error)
        with patch.object(self.store, '_digest', side_effect=digest):
            worker = threading.Thread(target=stage)
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(retention.RetentionBusy):
                    self.core.prepare_erasure()
                self.assertEqual(self.core.erasure_status()['phase'], 'unprepared')
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(self.core.prepare_erasure()['pending_files'], 3)

    def test_preexisting_standalone_store_handle_and_missing_root_remain_fail_closed(self):
        standalone = LocalArtifactStore(self.store.root)
        staged = standalone.stage(b'pending-secret', 'stage-key')
        self.core.prepare_erasure()
        with self.assertRaises(retention.RetentionUnavailable):
            standalone.read_staged(staged.reference)
        moved = self.root / 'moved-store'
        self.store.root.rename(moved)
        restarted = Core(self.config)
        self.assertFalse(self.store.root.exists())
        self.assertEqual(restarted.prepare_erasure()['blocker'], 'storage_unavailable')
        self.assertFalse(restarted.erasure_status()['complete'])
        self.assertEqual((moved / 'staging' / staged.reference).read_bytes(), b'pending-secret')

    def test_default_legacy_tables_maintenance_busy_and_terminal_state_are_not_inferred_or_cleared(self):
        config = replace(self.config, database=self.root / 'default.sqlite3', artifact_store=None)
        core = Core(config)
        with retention.operation(config.database):
            with self.assertRaises(retention.RetentionBusy):
                core.prepare_erasure()
        self.assertEqual(core.erasure_status()['phase'], 'unprepared')
        with sqlite3.connect(config.database) as db:
            db.execute("INSERT INTO retention_state VALUES ('communications', 'delete')")
        status = core.prepare_erasure()
        self.assertEqual(status['phase'], 'prepared')
        self.assertFalse(status['complete'])
        with sqlite3.connect(config.database) as db:
            self.assertEqual(db.execute('SELECT * FROM retention_state').fetchall(), [('communications', 'delete')])
        self.assertEqual(Core(config).erasure_status(), status)


if __name__ == '__main__':
    unittest.main()
