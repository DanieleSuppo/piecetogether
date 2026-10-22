"""Controlled local erasure: only disposable temporary deployments are removed."""
import os
import sqlite3
import tempfile
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import patch

from piecetogether import erasure, retention
from piecetogether.core import Bootstrap, Core
from piecetogether.artifact_store import LocalArtifactStore


class ErasureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = Bootstrap(self.root / 'core.sqlite3', {'development:sender': 'actor'},
                                artifact_store=self.root / 'artifacts')
        self.core = Core(self.config)
        self.store = self.core.artifact_store
        self.stale = LocalArtifactStore(self.store.root)

    def erase(self):
        return erasure.erase_local(self.config.database, store_root=self.config.artifact_store)

    def test_sentinel_blocks_every_bound_unbound_missing_owner_and_legacy_byte_path(self):
        staged = self.store.stage(b'secret', 'key')
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO retention_state VALUES ('communications','delete')")
        # Legacy terminal retention alone intentionally leaves direct store bytes readable.
        self.assertEqual(self.store.read_staged(staged.reference), b'secret')
        (self.store.root / '.core-erased').write_bytes(b'core-local-erased-v1\n')
        for missing in (False, True):
            if missing:
                (self.store.root / erasure.OWNER_FILE).unlink()
            for store in (self.store, self.stale, LocalArtifactStore(self.store.root)):
                for action in (lambda: store.stage(b'new', 'new'), lambda: store.read_staged(staged.reference),
                               lambda: store.finalize(staged), lambda: store.read(staged.reference),
                               lambda: store.remove(staged.reference),
                               lambda: store.cleanup(set(), 0), lambda: store.remove_staged(staged.reference, 0)):
                    with self.subTest(missing=missing, action=action), self.assertRaises(retention.RetentionUnavailable):
                        action()
        self.assertEqual((self.store.staging / staged.reference).read_bytes(), b'secret')

    def test_changed_identity_is_durable_unavailable_and_never_removes_replacement(self):
        staged = self.store.stage(b'original', 'key')
        self.core.prepare_erasure()
        path = self.store.staging / staged.reference
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'replacement-secret')
        replacement.replace(path)
        info = path.stat()
        receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'inventory_changed')
        self.assertFalse(receipt['local_logical_complete'])
        self.assertEqual(path.read_bytes(), b'replacement-secret')
        self.assertEqual(path.stat().st_ino, info.st_ino)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT state FROM erasure_jobs WHERE relative_path=?',
                                        ('staging/' + staged.reference,)).fetchone()[0], 'unavailable')
        self.assertEqual(Core(self.config).erasure_status()['blocker'], 'inventory_changed')
        self.assertEqual(self.erase()['blocker'], 'inventory_changed')
        self.assertEqual(path.read_bytes(), b'replacement-secret')

    def test_final_purge_failure_after_owner_loss_retains_frozen_work_and_sentinel(self):
        self.store.stage(b'private', 'key')
        original_finish = erasure._finish
        with patch.object(erasure, '_finish', side_effect=sqlite3.OperationalError('raw-private-path')):
            receipt = self.erase()
        self.assertEqual(receipt['phase'], 'files_removed')
        self.assertEqual(receipt['blocker'], 'database_unavailable')
        self.assertTrue(receipt['retryable'])
        self.assertEqual(receipt['retry_after_seconds'], 2)
        self.assertNotIn('raw-private-path', repr(receipt))
        self.assertFalse((self.store.root / erasure.OWNER_FILE).exists())
        for store in (self.store, self.stale, LocalArtifactStore(self.store.root)):
            with self.assertRaises(retention.RetentionUnavailable):
                store.stage(b'never-recreated', 'key')
        for core in (self.core, Core(self.config)):
            with self.assertRaises(retention.RetentionUnavailable):
                core.current_view()
        self.assertIs(erasure._finish, original_finish)
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_unlink_failure_is_durable_sanitized_retry_and_owner_last(self):
        self.store.stage(b'stage-secret', 'key')
        self.core.prepare_erasure()
        before = list(self.store.staging.iterdir())
        with patch.object(erasure.os, 'unlink', side_effect=PermissionError('raw-private-path')):
            receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'storage_unavailable')
        self.assertEqual(receipt['retry_after_seconds'], 2)
        self.assertEqual(list(self.store.staging.iterdir()), before)
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertTrue((self.store.root / '.core-erased').exists())
        self.assertEqual(Core(self.config).erasure_status(), receipt)
        self.assertNotIn('raw-private-path', repr(receipt))
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_new_files_after_prepare_are_never_adopted_on_retries(self):
        self.core.prepare_erasure()
        with sqlite3.connect(self.config.database) as db:
            before = db.execute('SELECT * FROM erasure_jobs').fetchall()
        path = self.store.staging / ('e' * 32)
        path.write_bytes(b'unclaimed-secret')
        for _ in range(2):
            receipt = self.erase()
            self.assertEqual(receipt['blocker'], 'inventory_changed')
            self.assertFalse(receipt['local_logical_complete'])
            self.assertEqual(path.read_bytes(), b'unclaimed-secret')
            with sqlite3.connect(self.config.database) as db:
                self.assertEqual(db.execute('SELECT * FROM erasure_jobs').fetchall(), before)
        path.unlink()  # Test-only removal of an unclaimed fixture, not an eraser override.
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_crash_after_unlink_before_job_commit_is_idempotent_including_owner_loss(self):
        class Crash(BaseException):
            pass
        for owner_crash in (False, True):
            with self.subTest(owner_crash=owner_crash):
                database = self.root / ('crash-owner.sqlite3' if owner_crash else 'crash-data.sqlite3')
                root = self.root / ('crash-owner' if owner_crash else 'crash-data')
                config = replace(self.config, database=database, artifact_store=root)
                core = Core(config)
                core.artifact_store.stage(b'private-crash', 'key')
                remove = erasure._remove_job
                def crash_after_unlink(store_root, job):
                    remove(store_root, job)
                    if (job[1] == 'owner') == owner_crash:
                        raise Crash()
                with patch.object(erasure, '_remove_job', side_effect=crash_after_unlink), self.assertRaises(Crash):
                    erasure.erase_local(database, store_root=root)
                with sqlite3.connect(database) as db:
                    self.assertTrue(erasure.frozen(db))
                    self.assertEqual(db.execute('SELECT phase FROM erasure_state').fetchone()[0], 'removing')
                    self.assertTrue(db.execute("SELECT 1 FROM erasure_jobs WHERE state='pending'").fetchone())
                restarted = Core(config)
                with self.assertRaises(retention.RetentionUnavailable):
                    restarted.current_view()
                with self.assertRaises(retention.RetentionUnavailable):
                    LocalArtifactStore(root).stage(b'forbidden', 'key')
                self.assertTrue(erasure.erase_local(database, store_root=root)['local_logical_complete'])

    def test_sentinel_fsync_failure_precedes_every_unlink_and_retries(self):
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        with patch.object(erasure, '_fsync_directory', side_effect=OSError('private-fsync')), \
             patch.object(erasure.os, 'unlink', side_effect=AssertionError('no unlink before fence durability')):
            receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'storage_unavailable')
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertTrue(list(self.store.staging.iterdir()))
        self.assertNotIn('private-fsync', repr(receipt))
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_every_unlink_has_durable_sentinel_and_owner_is_last(self):
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        synced = []
        unlinked = []
        fsync = os.fsync
        unlink = os.unlink
        def sync(descriptor):
            synced.append(os.readlink('/proc/self/fd/' + str(descriptor)))
            return fsync(descriptor)
        def remove(name, *args, **kwargs):
            for handle in (self.store, self.stale, LocalArtifactStore(self.store.root)):
                with self.assertRaises(retention.RetentionUnavailable):
                    handle.stage(b'forbidden-before-unlink', 'key')
            with sqlite3.connect(self.config.database, timeout=0) as db:
                db.execute('BEGIN IMMEDIATE')  # Filesystem I/O holds no semantic writer transaction.
                db.rollback()
            self.assertEqual((self.store.root / '.core-erased').read_bytes(), b'core-local-erased-v1\n')
            self.assertIn(str(self.store.root / '.core-erased'), synced)
            self.assertIn(str(self.store.root), synced)
            if name == erasure.OWNER_FILE:
                for directory in ('persistent', 'staging', 'keys'):
                    self.assertEqual(list((self.store.root / directory).iterdir()), [])
                with sqlite3.connect(self.config.database) as db:
                    self.assertFalse(db.execute("SELECT 1 FROM erasure_jobs WHERE kind!='owner' AND state!='absent'").fetchone())
            unlinked.append(name)
            result = unlink(name, *args, **kwargs)
            for handle in (self.store, self.stale, LocalArtifactStore(self.store.root)):
                with self.assertRaises(retention.RetentionUnavailable):
                    handle.stage(b'forbidden-after-unlink', 'key')
            return result
        with patch.object(erasure.os, 'fsync', side_effect=sync), patch.object(erasure.os, 'unlink', side_effect=remove):
            self.assertTrue(self.erase()['local_logical_complete'])
        self.assertEqual(unlinked[-1], erasure.OWNER_FILE)

    def test_missing_recorded_files_are_absent_not_reconstructed(self):
        staged = self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        (self.store.staging / staged.reference).unlink()
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_owner_loss_without_durable_valid_sentinel_never_authorizes_adoption(self):
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        (self.store.root / erasure.OWNER_FILE).unlink()
        self.assertEqual(self.erase()['blocker'], 'ownership_unverified')
        self.assertTrue(list(self.store.staging.iterdir()))
        self.assertFalse((self.store.root / '.core-erased').exists())

    def test_unknown_database_resources_block_without_deleting_or_false_receipt(self):
        self.store.stage(b'private', 'key')
        for sql, cleanup in (
                ("CREATE TABLE foreign_data (payload TEXT); INSERT INTO foreign_data VALUES ('foreign-secret')", 'DROP TABLE foreign_data'),
                ('CREATE VIEW foreign_view AS SELECT * FROM turns', 'DROP VIEW foreign_view'),
                ("CREATE TRIGGER copy_payload AFTER UPDATE ON erasure_state BEGIN INSERT INTO deliveries VALUES ('copy','secret'); END", 'DROP TRIGGER copy_payload')):
            with self.subTest(sql=sql):
                with sqlite3.connect(self.config.database) as db:
                    db.executescript(sql)
                receipt = self.erase()
                self.assertEqual(receipt['blocker'], 'database_resources_unverified')
                self.assertFalse(receipt['local_logical_complete'])
                self.assertTrue(list(self.store.staging.iterdir()))
                with sqlite3.connect(self.config.database) as db:
                    db.execute(cleanup)
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_no_store_completion_purges_source_and_legacy_terminal_residue(self):
        config = replace(self.config, database=self.root / 'no-store.sqlite3', artifact_store=None)
        core = Core(config)
        core.accept({'channel': 'development', 'sender': 'sender', 'idempotency_key': 'key',
                     'text': 'private-no-store', 'sent_at': '2026-10-21T12:00:00Z'})
        with sqlite3.connect(config.database) as db:
            db.execute("INSERT INTO retention_state VALUES ('communications','delete')")
        receipt = erasure.erase_local(config.database, store_root=None)
        self.assertTrue(receipt['local_logical_complete'])
        self.assertFalse(receipt['global_complete'])
        self.assertEqual(Core(config).erasure_status(), receipt)
        with sqlite3.connect(config.database) as db:
            self.assertNotIn('private-no-store', '\n'.join(db.iterdump()))
            self.assertEqual(db.execute('SELECT * FROM retention_state').fetchall(), [])
        self.assertEqual(erasure.erase_local(config.database, store_root=None), receipt)

    def test_all_private_runtime_tables_are_populated_then_purged_with_real_artifacts(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig
        from piecetogether.telegram_channel import TelegramChannel, TelegramConfig
        class Offline:
            def send(self, *args):
                raise AssertionError('no transport call')
        EmailChannel(self.config.database, EmailConfig('core@example.invalid', 'localhost', security='plain'), Offline())
        TelegramChannel(self.config.database, TelegramConfig('123', 'synthetic-reference'), Offline(), 'synthetic-token')
        stage = self.store.stage(b'private-stage-all', 'private-key-all')
        self.store.finalize(self.store.stage(b'private-publication-all', 'private-published-key-all'))
        with sqlite3.connect(self.config.database) as db:
            token = db.execute('SELECT token FROM artifact_store_owner').fetchone()[0]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            runtime = tables - {'artifact_store_owner', 'erasure_jobs', 'erasure_state', 'erasure_control'}
            self.assertEqual(len(runtime), 33)
            for table in runtime:
                columns = db.execute(f'PRAGMA table_info({table})').fetchall()
                values = []
                for _, name, data_type, *_ in columns:
                    if name in ('stage_ref', 'storage_ref', 'reference'):
                        values.append(stage.reference)
                    elif name == 'category':
                        values.append('communications')
                    elif name == 'availability':
                        values.append('delete')
                    elif data_type.upper() in ('INTEGER', 'REAL'):
                        values.append(1)
                    elif data_type.upper() == 'BLOB':
                        values.append(b'private-blob-' + table.encode())
                    else:
                        values.append('private-' + table + '-' + name)
                db.execute(f'INSERT OR REPLACE INTO {table} VALUES ({",".join("?" for _ in values)})', values)
            for table in runtime:
                self.assertTrue(db.execute(f'SELECT 1 FROM {table}').fetchone(), table)
            before = '\n'.join(db.iterdump())
            self.assertIn('private-', before)
        receipt = self.erase()
        self.assertTrue(receipt['local_logical_complete'])
        with sqlite3.connect(self.config.database) as db:
            for table in runtime | {'artifact_store_owner', 'erasure_jobs'}:
                self.assertEqual(db.execute(f'SELECT * FROM {table}').fetchall(), [], table)
            dump = '\n'.join(db.iterdump())
            for forbidden in ('private-', token, str(self.root), stage.reference, stage.checksum):
                self.assertNotIn(forbidden, dump)
            self.assertEqual(db.execute('SELECT * FROM erasure_state').fetchall(), [(1, 'complete', 'verified', None)])
            self.assertEqual(db.execute('SELECT * FROM erasure_control').fetchall(), [(1, 1, 0)])
        self.assertEqual({path.read_bytes() for path in self.store.root.rglob('*') if path.is_file()},
                         {b'', b'core-local-erased-v1\n'})

    def test_job_commit_failure_rolls_back_state_but_retries_unlinked_absence(self):
        staged = self.store.stage(b'private', 'key')
        connect = sqlite3.connect
        failures = []
        class FailJobCommit(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "UPDATE erasure_jobs SET state='absent'" in sql and not failures:
                    failures.append(sql)
                    raise sqlite3.OperationalError('private-job-commit')
                return super().execute(sql, parameters)
        with patch.object(erasure.sqlite3, 'connect', side_effect=lambda *args, **kwargs: connect(*args, factory=FailJobCommit, **kwargs)):
            receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'database_unavailable')
        self.assertEqual(len(failures), 1)
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertNotIn('private-job-commit', repr(receipt))
        self.assertTrue(self.erase()['local_logical_complete'])
        self.assertFalse((self.store.staging / staged.reference).exists())

    def test_partial_runtime_purge_failure_is_atomic_and_resumable(self):
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO deliveries VALUES ('private-id','private-outbound')")
            db.execute("INSERT INTO semantic_outbox VALUES ('private-event','private-event-content')")
        self.store.stage(b'private', 'key')
        connect = sqlite3.connect
        class FailPurge(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql == 'DELETE FROM semantic_outbox':
                    raise sqlite3.OperationalError('private-purge')
                return super().execute(sql, parameters)
        with patch.object(erasure.sqlite3, 'connect', side_effect=lambda *args, **kwargs: connect(*args, factory=FailPurge, **kwargs)):
            receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'database_unavailable')
        self.assertEqual(receipt['phase'], 'files_removed')
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT * FROM deliveries').fetchall(), [('private-id', 'private-outbound')])
            self.assertEqual(db.execute('SELECT * FROM semantic_outbox').fetchall(), [('private-event', 'private-event-content')])
            self.assertTrue(db.execute('SELECT * FROM artifact_store_owner').fetchone())
            self.assertTrue(db.execute('SELECT * FROM erasure_jobs').fetchone())
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_post_final_state_write_residue_and_trigger_cannot_claim_completion(self):
        self.store.stage(b'private', 'key')
        connect = sqlite3.connect
        for trigger in (False, True):
            inserted = []
            class ResidueAfterFinalWrite(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if "UPDATE erasure_state SET phase='complete'" in sql and not inserted:
                        inserted.append(True)
                        if trigger:
                            super().execute("CREATE TRIGGER unexpected AFTER UPDATE ON erasure_state BEGIN "
                                            "INSERT INTO deliveries VALUES ('private-copy','private-residue'); END")
                            return super().execute(sql, parameters)
                        result = super().execute(sql, parameters)
                        super().execute("INSERT INTO deliveries VALUES ('private-copy','private-residue')")
                        return result
                    return super().execute(sql, parameters)
            with patch.object(erasure.sqlite3, 'connect', side_effect=lambda *args, **kwargs: connect(*args, factory=ResidueAfterFinalWrite, **kwargs)):
                receipt = self.erase()
            self.assertFalse(receipt['local_logical_complete'])
            self.assertEqual(receipt['phase'], 'files_removed')
            self.assertEqual(receipt['blocker'], 'database_resources_unverified' if trigger else 'runtime_pending')
            with sqlite3.connect(self.config.database) as db:
                self.assertEqual(db.execute('SELECT * FROM deliveries').fetchall(), [])
                self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall(), [])
                self.assertTrue(db.execute('SELECT * FROM artifact_store_owner').fetchone())
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_complete_remains_frozen_for_workers_snapshots_and_replay(self):
        from piecetogether import evaluation
        from test_erasure_prepare import ErasurePrepareTests
        fixtures = ErasurePrepareTests()
        fixtures.root, fixtures.config = self.root, self.config
        for core, worker, transport in fixtures.workers():
            receipt = erasure.erase_local(core.config.database, store_root=core.config.artifact_store)
            self.assertTrue(receipt['local_logical_complete'])
            worker.sleep = lambda _: self.fail('no sleep after completed erase')
            with self.assertLogs('piecetogether.' + core.config.channel + '_channel', level='WARNING'):
                worker.run()
            self.assertEqual(transport.calls, 0)
            with self.assertRaises(evaluation.ReplayUnavailable):
                evaluation.capture(core, lambda recorder: self.fail('never capture erased state'))
            restarted = Core(core.config, channel=worker.channel)
            with self.assertRaises(retention.RetentionUnavailable):
                restarted.recover_channel_pending()

    def test_expands_real_prepared_r2a_jobs_then_erases_without_startup_publication(self):
        self.store.stage(b'private-old-schema', 'key')
        self.core.prepare_erasure()
        with sqlite3.connect(self.config.database) as db:
            jobs = db.execute('SELECT * FROM erasure_jobs').fetchall()
            db.executescript("""
                ALTER TABLE erasure_state RENAME TO expanded_state;
                CREATE TABLE erasure_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    phase TEXT NOT NULL CHECK(phase='prepared'),
                    inventory TEXT NOT NULL CHECK(inventory IN ('pending','verified','blocked')), blocker TEXT);
                INSERT INTO erasure_state SELECT * FROM expanded_state;
                DROP TABLE expanded_state;
                ALTER TABLE erasure_jobs RENAME TO expanded_jobs;
                CREATE TABLE erasure_jobs (
                    relative_path TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('publication','stage','key','temporary','owner')),
                    size INTEGER NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending' CHECK(state='pending'));
                INSERT INTO erasure_jobs SELECT * FROM expanded_jobs;
                DROP TABLE expanded_jobs;
                DROP TABLE erasure_control;
            """)
        with patch.object(LocalArtifactStore, 'finalize', side_effect=AssertionError('never publish')):
            restarted = Core(self.config)
        self.assertEqual(restarted.erasure_status()['phase'], 'prepared')
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT * FROM erasure_jobs').fetchall(), jobs)
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_completed_snapshot_and_replay_remain_unavailable(self):
        import hashlib
        from piecetogether import evaluation
        case = evaluation.capture(self.core, lambda recorder: None)
        self.assertTrue(self.erase()['local_logical_complete'])
        with self.assertRaises(evaluation.ReplayUnavailable):
            evaluation._snapshot(self.config.database)
        snapshot = self.root / 'complete-snapshot.sqlite3'
        with sqlite3.connect(self.config.database) as source, sqlite3.connect(snapshot) as destination:
            source.backup(destination)
        frozen = snapshot.read_bytes()
        case = replace(case, initial_state=frozen, state_digest=hashlib.sha256(frozen).hexdigest())
        with patch('piecetogether.evaluation._CapturedModel', side_effect=AssertionError('no model construction')):
            with self.assertRaises(evaluation.ReplayUnavailable):
                evaluation.replay(case)

    def test_erasure_busy_is_retryable_and_does_not_unfreeze_or_remove(self):
        import fcntl
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        with (self.store.root / erasure.LOCK_FILE).open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(retention.RetentionBusy):
                self.erase()
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertEqual(self.core.erasure_status()['phase'], 'prepared')
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_new_unbound_handle_does_not_recreate_missing_erased_directories(self):
        self.assertTrue(self.erase()['local_logical_complete'])
        self.store.keys.rmdir()  # Simulate post-erasure directory loss in this fixture only.
        handle = LocalArtifactStore(self.store.root)
        self.assertFalse(handle.keys.exists())
        with self.assertRaises(retention.RetentionUnavailable):
            handle.stage(b'forbidden', 'key')
        self.assertFalse(handle.keys.exists())

    def test_unknown_file_after_owner_loss_blocks_without_new_adoption(self):
        self.store.stage(b'private', 'key')
        with patch.object(erasure, '_finish', side_effect=sqlite3.OperationalError('private-finalization')):
            self.assertFalse(self.erase()['local_logical_complete'])
        unknown = self.store.staging / ('f' * 32)
        unknown.write_bytes(b'unknown-after-owner-loss')
        with sqlite3.connect(self.config.database) as db:
            jobs = db.execute('SELECT * FROM erasure_jobs').fetchall()
        for _ in range(2):
            receipt = self.erase()
            self.assertFalse(receipt['local_logical_complete'])
            self.assertEqual(receipt['blocker'], 'inventory_changed')
            self.assertEqual(unknown.read_bytes(), b'unknown-after-owner-loss')
            with sqlite3.connect(self.config.database) as db:
                self.assertEqual(db.execute('SELECT * FROM erasure_jobs').fetchall(), jobs)
            with self.assertRaises(retention.RetentionUnavailable):
                LocalArtifactStore(self.store.root).read_staged(unknown.name)

    def test_failure_persistence_error_is_sanitized_without_false_durability_claim(self):
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        connect = sqlite3.connect
        class UnwritableState(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if sql.startswith('UPDATE erasure_state'):
                    raise sqlite3.OperationalError('raw-private-location')
                return super().execute(sql, parameters)
        with patch.object(erasure.sqlite3, 'connect', side_effect=lambda *args, **kwargs: connect(*args, factory=UnwritableState, **kwargs)):
            with self.assertRaisesRegex(retention.RetentionUnavailable, '^Erasure status unavailable; deployment remains frozen$'):
                self.erase()
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertTrue(list(self.store.staging.iterdir()))
        self.assertEqual(self.core.erasure_status()['phase'], 'prepared')
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_completed_revalidation_still_requires_store_lock(self):
        import fcntl
        self.assertTrue(self.erase()['local_logical_complete'])
        with (self.store.root / erasure.LOCK_FILE).open('r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(retention.RetentionBusy):
                self.erase()
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_unknown_retained_flag_column_blocks_purge_and_false_completion(self):
        self.core.prepare_erasure()
        with sqlite3.connect(self.config.database) as db:
            db.execute('ALTER TABLE erasure_state ADD COLUMN unexpected_payload TEXT')
            db.execute("UPDATE erasure_state SET unexpected_payload='private-copy'")
        receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'database_resources_unverified')
        self.assertFalse(receipt['local_logical_complete'])
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT unexpected_payload FROM erasure_state').fetchone()[0], 'private-copy')

    def test_public_status_never_returns_unknown_stored_error_text(self):
        self.core.prepare_erasure()
        with sqlite3.connect(self.config.database) as db:
            db.execute("UPDATE erasure_state SET inventory='blocked', blocker='private-path-or-identifier'")
        status = erasure.status_local(self.config.database)
        self.assertEqual(status['blocker'], 'state_unavailable')
        self.assertNotIn('private-path-or-identifier', repr(status))

    def test_preflight_and_prepare_never_return_completion_receipts(self):
        self.assertTrue(self.erase()['local_logical_complete'])
        for report in (self.core.erasure_preflight(), self.core.prepare_erasure(),
                       erasure.preflight_local(self.config.database, store_root=self.config.artifact_store),
                       erasure.prepare_local(self.config.database, store_root=self.config.artifact_store)):
            self.assertFalse(report['complete'])
            self.assertFalse(report['local_logical_complete'])
        self.assertTrue(erasure.status_local(self.config.database)['local_logical_complete'])

    def test_corrupt_sentinel_never_permits_bytes_or_removal(self):
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        (self.store.root / '.core-erased').write_bytes(b'corrupt-private-sentinel')
        for store in (self.store, self.stale, LocalArtifactStore(self.store.root)):
            with self.assertRaises(retention.RetentionUnavailable):
                store.stage(b'new', 'key')
        receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'sentinel_unverified')
        self.assertFalse(receipt['local_logical_complete'])
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertTrue(list(self.store.staging.iterdir()))
        self.assertNotIn('corrupt-private-sentinel', repr(receipt))

    def test_unsafe_replacement_is_not_followed_or_removed(self):
        staged = self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        path = self.store.staging / staged.reference
        outside = self.root / 'outside'
        outside.write_bytes(b'outside-secret')
        path.unlink()
        path.symlink_to(outside)
        self.assertEqual(self.erase()['blocker'], 'inventory_changed')
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), b'outside-secret')
        path.unlink()
        os.link(outside, path)
        self.assertEqual(self.erase()['blocker'], 'inventory_changed')
        self.assertEqual(path.stat().st_nlink, 2)
        self.assertEqual(outside.read_bytes(), b'outside-secret')

    def test_directory_fsync_failure_after_unlink_retries_already_absent(self):
        self.store.stage(b'private', 'key')
        self.core.prepare_erasure()
        fsync = os.fsync
        failed = []
        def sync(descriptor):
            if os.readlink('/proc/self/fd/' + str(descriptor)) == str(self.store.keys) and not failed:
                failed.append(True)
                raise OSError('private-directory-fsync')
            return fsync(descriptor)
        with patch.object(erasure.os, 'fsync', side_effect=sync):
            receipt = self.erase()
        self.assertEqual(receipt['blocker'], 'storage_unavailable')
        self.assertEqual(list(self.store.keys.iterdir()), [])
        self.assertTrue((self.store.root / erasure.OWNER_FILE).exists())
        self.assertTrue(self.erase()['local_logical_complete'])

    def test_completed_database_unknown_table_is_not_claimed_erased(self):
        self.assertTrue(self.erase()['local_logical_complete'])
        with sqlite3.connect(self.config.database) as db:
            db.execute('CREATE TABLE foreign_payload (payload TEXT)')
            db.execute("INSERT INTO foreign_payload VALUES ('unclaimed-secret')")
        self.assertFalse(self.core.erasure_status()['local_logical_complete'])
        receipt = self.erase()
        self.assertFalse(receipt['local_logical_complete'])
        self.assertEqual(receipt['blocker'], 'database_resources_unverified')
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT * FROM foreign_payload').fetchall(), [('unclaimed-secret',)])

    def test_full_erasure_and_receipt_stay_frozen_on_restart_and_repeat(self):
        self.core.accept({'channel': 'development', 'sender': 'sender', 'idempotency_key': 'key',
                          'text': 'private-payload', 'sent_at': '2026-10-21T12:00:00Z'})
        staged = self.store.stage(b'private-stage', 'stage-key')
        self.store.finalize(self.store.stage(b'private-published', 'published-key'))
        (self.store.staging / ('a' * 32 + '.tmp')).write_bytes(b'private-partial')
        (self.store.keys / ('b' * 32 + '.tmp')).write_bytes(b'private-key-partial')
        (self.store.persistent / ('c' * 32)).write_bytes(b'private-orphan')
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO artifact_publications VALUES ('private-artifact', ?, ?, ?, 'pending', NULL)",
                       (staged.reference, staged.checksum, staged.size))
            db.execute("INSERT INTO artifact_deletions VALUES ('private-old', ?, NULL)", ('d' * 32,))
        with patch.object(Core, '__init__', side_effect=AssertionError('no normal Core startup')), \
             patch.object(LocalArtifactStore, 'finalize', side_effect=AssertionError('never publish')), \
             patch.object(LocalArtifactStore, 'remove', side_effect=AssertionError('no ordinary removal')):
            receipt = self.erase()
        self.assertTrue(receipt['local_logical_complete'])
        self.assertFalse(receipt['complete'])
        self.assertFalse(receipt['global_complete'])
        self.assertEqual(receipt['phase'], 'complete')
        self.assertTrue(all(item['status'] == 'unverified' for item in receipt['outside_core_local']))
        for forbidden in ('private', str(self.root), staged.reference, staged.checksum):
            self.assertNotIn(forbidden, repr(receipt))
        self.assertEqual({path.name for path in self.store.root.iterdir()},
                         {'persistent', 'staging', 'keys', '.core-erased', erasure.LOCK_FILE})
        for directory in ('persistent', 'staging', 'keys'):
            self.assertEqual(list((self.store.root / directory).iterdir()), [])
        with sqlite3.connect(self.config.database) as db:
            dump = '\n'.join(db.iterdump())
            for forbidden in ('private', str(self.root), staged.reference, staged.checksum):
                self.assertNotIn(forbidden, dump)
        for core in (self.core, Core(self.config)):
            self.assertEqual(core.erasure_status(), receipt)
            self.assertEqual(core.erase_all(), receipt)
            with self.assertRaises(retention.RetentionUnavailable):
                core.current_view()
        for store in (self.store, self.stale, LocalArtifactStore(self.store.root)):
            with self.assertRaises(retention.RetentionUnavailable):
                store.stage(b'new', 'key')
        self.assertEqual(self.erase(), receipt)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / 'core.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE erasure_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    phase TEXT NOT NULL CHECK(phase='prepared'),
                    inventory TEXT NOT NULL CHECK(inventory IN ('pending','verified','blocked')),
                    blocker TEXT);
                CREATE TABLE erasure_jobs (
                    relative_path TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK(kind IN ('publication','stage','key','temporary','owner')),
                    size INTEGER NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending' CHECK(state='pending'));
                INSERT INTO erasure_state VALUES (1,'prepared','verified',NULL);
                INSERT INTO erasure_jobs VALUES ('staging/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','stage',7,9,11,'pending');
            """)

    def test_expand_preserves_freeze_jobs_restart_idempotency_and_transaction_rollback(self):
        with sqlite3.connect(self.database) as db:
            before = list(db.iterdump())
            db.execute('BEGIN IMMEDIATE')
            erasure.initialize(db)
            db.execute("UPDATE erasure_state SET phase='removing'")
            db.execute("UPDATE erasure_jobs SET state='absent'")
            self.assertTrue(erasure.frozen(db))
            db.rollback()
            self.assertEqual(list(db.iterdump()), before)
        with sqlite3.connect(self.database) as db:
            erasure.initialize(db)
        with sqlite3.connect(self.database) as db:
            snapshot = list(db.iterdump())
            erasure.initialize(db)
            self.assertEqual(list(db.iterdump()), snapshot)
            self.assertEqual(db.execute('SELECT * FROM erasure_jobs').fetchall(),
                             [('staging/' + 'a' * 32, 'stage', 7, 9, 11, 'pending')])
            self.assertEqual(db.execute('SELECT phase,inventory FROM erasure_state').fetchone(),
                             ('prepared', 'verified'))
            for phase in ('removing', 'files_removed', 'complete'):
                db.execute('UPDATE erasure_state SET phase=?', (phase,))
                self.assertTrue(erasure.frozen(db))
                with self.assertRaises(retention.RetentionUnavailable):
                    erasure.require_unfrozen(db)

    def test_offline_status_reads_old_r2a_without_writing_or_requiring_expansion(self):
        with sqlite3.connect(self.database) as db:
            before = list(db.iterdump())
        status = erasure.status_local(self.database)
        self.assertEqual(status['phase'], 'prepared')
        self.assertEqual(status['pending_files'], 1)
        self.assertFalse(status['local_logical_complete'])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(list(db.iterdump()), before)

    def test_partial_expand_failure_rolls_back_both_tables(self):
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TRIGGER deny_job_migration BEFORE INSERT ON erasure_jobs BEGIN SELECT RAISE(ABORT,'stop'); END")
            before = list(db.iterdump())
            with self.assertRaises((sqlite3.Error, ValueError)):
                erasure.initialize(db)
            db.rollback()
            self.assertEqual(list(db.iterdump()), before)
            self.assertTrue(erasure.frozen(db))
