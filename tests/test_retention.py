"""Deployment-scoped retention: real Core state, temporary databases only."""

import json
import os
import subprocess
import sys
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_history as fixtures

from piecetogether import history, view
from piecetogether.core import Bootstrap, Core


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = Bootstrap(Path(self.temporary.name) / 'core.sqlite3',
                                {'development:private-sender': 'private-actor'})
        self.payload = {'channel': 'development', 'sender': 'private-sender',
                        'idempotency_key': 'private-key', 'text': 'private-content',
                        'sent_at': '2026-10-21T12:00:00Z'}

    def policy(self, **settings):
        from piecetogether.retention import RetentionPolicy
        return RetentionPolicy(**settings)

    def populated(self, **settings):
        core = Core(replace(self.config, retention=self.policy(**settings)))
        result = core.accept(self.payload)
        self.assertEqual(result['status'], 'completed')
        return core, result['communication_id']

    def test_default_sweep_retains_existing_state_and_processing(self):
        core = Core(self.config)
        result = core.accept(self.payload)
        before = core.inspect(result['communication_id'])
        self.assertTrue(hasattr(core, 'apply_retention'), 'Core needs configured retention operation')
        self.assertEqual(core.apply_retention(), dict.fromkeys(
            ('communications', 'semantic_history', 'evaluation_traces', 'current_view'), 'retain'))
        self.assertEqual(core.inspect(result['communication_id']), before)
        self.assertEqual(core.accept(self.payload), result)

    def test_policy_is_static_validated_and_parsed_from_bootstrap(self):
        from piecetogether.retention import RetentionPolicy
        for settings in ({'communications': 'expire'}, {'evaluation_traces': None},
                         {'current_view': []}, {'semantic_history': True}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                RetentionPolicy(**settings)
        with self.assertRaises(ValueError):
            replace(self.config, retention={})
        config = Path(self.temporary.name) / 'bootstrap.json'
        base = {'database': 'core.sqlite3', 'identities': self.config.identities}
        for value in (None, [], {'unknown': 'delete'}, {'communications': 'erase'}):
            config.write_text(json.dumps({**base, 'retention': value}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                Bootstrap.from_file(config)
        config.write_text(json.dumps({**base, 'retention': {'current_view': 'redact'}}))
        parsed = Bootstrap.from_file(config)
        self.assertEqual(parsed.retention.current_view, 'redact')
        self.assertEqual(parsed.retention.communications, 'retain')

    def test_communication_erasure_removes_hidden_copies_and_fails_closed_after_restart(self):
        from piecetogether.retention import RetentionUnavailable
        for action in ('redact', 'delete'):
            with self.subTest(action=action):
                # Each case uses its own database, never a deployment database.
                self.config = replace(self.config, database=self.config.database.with_name(action + '.sqlite3'))
                core, communication_id = self.populated(communications=action)
                available = core.apply_retention()
                self.assertEqual(available, dict.fromkeys(
                    ('communications', 'semantic_history', 'evaluation_traces', 'current_view'), action))
                with sqlite3.connect(self.config.database) as db:
                    for table in ('turns', 'processing_attempts', 'deliveries', 'pending_items',
                                  'exposed_groundings', 'semantic_outbox', 'trusted_records'):
                        self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)
                    # Logical live state has no values, identifiers, provenance or tombstones.
                    dump = '\n'.join(db.iterdump())
                    for secret in ('private-content', 'private-sender', 'private-actor', communication_id):
                        self.assertNotIn(secret, dump)
                for instance in (core, Core(self.config)):
                    for read in (lambda: instance.inspect(communication_id), instance.inspect_history,
                                 instance.current_view, instance.trusted_events):
                        with self.assertRaises(RetentionUnavailable):
                            read()
                    with self.assertRaises(RetentionUnavailable):
                        instance.accept(self.payload)
                # Changing configuration cannot undo durable unavailability.
                restored = Core(replace(self.config, retention=self.policy()))
                self.assertEqual(restored.apply_retention(), available)
                with self.assertRaises(RetentionUnavailable):
                    restored.accept(self.payload)

    def test_history_erasure_invalidates_evidence_and_view_without_new_grounding(self):
        from piecetogether.retention import RetentionUnavailable
        core, _ = self.populated(semantic_history='delete')
        # Seed the storage shapes to catch dependent copies beyond the normal turn.
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO trusted_records VALUES ('private-record', 'entity', ?, 'g', 'c')",
                       (json.dumps({'id': 'private-record', 'kind': 'entity', 'attributes': {'secret': 'erased-history'}}),))
            db.execute("INSERT INTO semantic_outbox VALUES ('private-event', ?)",
                       (json.dumps({'value': 'erased-history'}),))
            db.execute("INSERT INTO projection_deliveries VALUES ('private-event', 'private-commit', ?)",
                       (json.dumps({'value': 'erased-history'}),))
        core.apply_retention()
        with sqlite3.connect(self.config.database) as db:
            self.assertNotIn('erased-history', '\n'.join(db.iterdump()))
            self.assertEqual(db.execute('SELECT count(*) FROM trusted_grounding_items').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM semantic_commits').fetchone()[0], 0)
        with self.assertRaises(RetentionUnavailable):
            core.inspect_history()
        with self.assertRaises(RetentionUnavailable):
            core.current_view()

    def test_trace_only_erasure_preserves_trusted_reads_and_communications(self):
        from piecetogether.retention import RetentionUnavailable
        for action in ('redact', 'delete'):
            with self.subTest(action=action):
                self.config = replace(self.config, database=self.config.database.with_name('trace-' + action + '.sqlite3'))
                core, communication_id = self.populated(evaluation_traces=action)
                before_history, before_view = core.inspect_history(), core.current_view()
                state = core.apply_retention()
                self.assertEqual(state, {'communications': 'retain', 'semantic_history': 'retain',
                                         'evaluation_traces': action, 'current_view': 'retain'})
                inspected = core.inspect(communication_id)
                self.assertEqual(inspected['inbound']['text'], 'private-content')
                self.assertEqual(inspected['trace'], {'availability': action})
                self.assertEqual(inspected['attempts'], [])
                self.assertEqual(core.inspect_history(), before_history)
                self.assertEqual(core.current_view(), before_view)
                with self.assertRaises(RetentionUnavailable):
                    core.accept(self.payload)
                with sqlite3.connect(self.config.database) as db:
                    self.assertIsNone(db.execute('SELECT trace FROM turns').fetchone()[0])
                    self.assertEqual(db.execute('SELECT count(*) FROM processing_attempts').fetchone()[0], 0)

    def test_view_only_erasure_cannot_be_reconstructed_but_keeps_source_history(self):
        from piecetogether.retention import RetentionUnavailable
        for action in ('redact', 'delete'):
            with self.subTest(action=action):
                self.config = replace(self.config, database=self.config.database.with_name('view-' + action + '.sqlite3'))
                core, communication_id = self.populated(current_view=action)
                before = core.inspect_history()
                state = core.apply_retention()
                self.assertEqual(state['semantic_history'], 'retain')
                self.assertEqual(state['current_view'], action)
                self.assertEqual(core.inspect_history(), before)
                self.assertEqual(core.inspect(communication_id)['inbound']['text'], 'private-content')
                for instance in (core, Core(self.config)):
                    with self.assertRaises(RetentionUnavailable):
                        instance.current_view()
                with sqlite3.connect(self.config.database) as db:
                    db.row_factory = sqlite3.Row
                    with self.assertRaises(RetentionUnavailable):
                        view.current_view(db)
                with self.assertRaises(RetentionUnavailable):
                    core.accept(self.payload)

    def test_history_redaction_and_deletion_remove_real_grounded_and_pending_copies(self):
        from piecetogether.retention import RetentionUnavailable
        for action in ('redact', 'delete'):
            with self.subTest(action=action):
                flow = fixtures.HistoryTests()
                flow.setUp()
                self.addCleanup(flow.doCleanups)
                exposed = flow.expose()
                core, _, result = flow.accept_items(exposed)
                self.assertEqual(result['status'], 'completed')
                before = core.inspect_history()
                self.assertEqual(len(before['commits']), 1)
                self.assertEqual({item['outcome'] for item in before['grounding_items']}, {'accepted'})
                pending = flow.expose()
                self.assertTrue(pending['grounding_items'])
                sweeper = Core(replace(flow.config, retention=self.policy(semantic_history=action)),
                               contract_provider=fixtures.ContractProvider(flow.contract))
                sweeper.apply_retention()
                with sqlite3.connect(flow.config.database) as db:
                    for table in ('semantic_commits', 'trusted_records', 'trusted_groundings',
                                  'trusted_grounding_items', 'pending_items', 'exposed_groundings',
                                  'candidate_target_bindings', 'semantic_outbox', 'processing_attempts',
                                  'turns', 'semantic_state'):
                        self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)
                with self.assertRaises(RetentionUnavailable):
                    core.accept(flow.message())

    def test_trace_erasure_removes_diagnostics_from_pending_proposal_copies(self):
        flow = fixtures.HistoryTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        core = Core(replace(flow.config, retention=self.policy(evaluation_traces='delete')),
                    model=fixtures.ProposalModel((
                        fixtures.EntityOperation('subject', 'Subject'),
                        fixtures.GroundingPlanOperation('p', (), 'explicit', ('subject',)),
                    ), decision_raw={'diagnostic': 'private-trace-diagnostic'}),
                    contract_provider=fixtures.ContractProvider(flow.contract))
        result = core.accept(flow.message(text='new subject'))
        self.assertTrue(core.inspect(result['communication_id'])['grounding_items'])
        before = core.inspect_history()
        core.apply_retention()
        self.assertEqual(core.inspect_history(), before)
        with sqlite3.connect(flow.config.database) as db:
            self.assertNotIn('private-trace-diagnostic', '\n'.join(db.iterdump()))
            self.assertEqual(db.execute('SELECT count(*) FROM exposed_groundings').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM pending_items').fetchone()[0], 0)

    def test_channel_checkpoints_cannot_recreate_identifiers_after_sweep(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig
        from piecetogether.telegram_channel import TelegramChannel, TelegramConfig
        from piecetogether.retention import RetentionUnavailable
        core, _ = self.populated(communications='delete')
        # No transport is called; only real local checkpoint persistence is exercised.
        email = EmailChannel(self.config.database, EmailConfig('core@example.org', 'localhost'), None)
        telegram = TelegramChannel(self.config.database, TelegramConfig('123', 'token'), None, '')
        email.record_acquisition('mailbox', '1', '1', 'acquired', 'private-id', 'completed')
        telegram.record_acquisition(1, 'private-id', 'completed')
        core.apply_retention()
        with self.assertRaises(RetentionUnavailable):
            email.record_acquisition('mailbox', '1', '2', 'acquired', 'private-id', 'completed')
        with self.assertRaises(RetentionUnavailable):
            telegram.record_acquisition(2, 'private-id', 'completed')
        with sqlite3.connect(self.config.database) as db:
            for table in ('email_acquisition', 'email_handoffs', 'telegram_acquisition', 'telegram_handoffs'):
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)

    def test_development_mailbox_cannot_recreate_erased_outbound(self):
        from piecetogether.core import Communication
        from piecetogether.retention import RetentionUnavailable
        core, communication_id = self.populated(communications='delete')
        outbound = Communication(**core.inspect(communication_id)['outbound'])
        core.apply_retention()
        with self.assertRaises(RetentionUnavailable):
            core.channel.deliver(outbound)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)

    def test_nondefault_startup_leaves_pending_events_and_references_untouched(self):
        from piecetogether.projections import ProjectionConfig
        from piecetogether.references import ReferenceConfig
        from test_projection import RecordingSink

        for category in ('communications', 'semantic_history', 'evaluation_traces', 'current_view'):
            with self.subTest(category=category):
                flow = fixtures.HistoryTests()
                flow.setUp()
                self.addCleanup(flow.doCleanups)
                source, _, _ = flow.accept_items(flow.expose())
                before = source.inspect_history()
                self.assertEqual(len(before['events']), 1)
                reference_file = flow.config.database.with_name('reference.json')
                reference_file.write_text(json.dumps([{
                    'target_id': before['entities'][0]['id'], 'concept': 'count', 'value': 4,
                    'source_reference': 'private-record', 'observed_version': 'v1',
                }]))
                sink = RecordingSink()
                config = replace(flow.config, retention=self.policy(**{category: 'delete'}),
                                 projection=ProjectionConfig(),
                                 reference_state=ReferenceConfig('application', reference_file))
                maintenance = Core(config, projection_sink=sink,
                                   contract_provider=fixtures.ContractProvider(flow.contract))
                self.assertEqual(sink.received, [], 'maintenance startup dispatched pending events')
                self.assertEqual(maintenance.inspect_history(), before,
                                 'maintenance startup refreshed references')
                self.assertEqual(maintenance.inspect_event_delivery(), [])
                with sqlite3.connect(config.database) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM retention_state').fetchone()[0], 0)
                # A default startup still refreshes the reference and dispatches both events.
                normal = Core(replace(config, retention=self.policy()), projection_sink=sink,
                              contract_provider=fixtures.ContractProvider(flow.contract))
                self.assertEqual(len(normal.inspect_history()['reference_assertions']), 1)
                self.assertEqual(sink.received, normal.inspect_history()['events'])
                self.assertEqual(len(sink.received), 2)

    def test_nondefault_startup_does_not_restore_legacy_attempt_checkpoints(self):
        core, _ = self.populated()
        with sqlite3.connect(self.config.database) as db:
            self.assertIsNotNone(db.execute('SELECT trace FROM turns').fetchone()[0])
            db.execute('DELETE FROM processing_attempts')
        Core(replace(self.config, retention=self.policy(communications='delete')))
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processing_attempts').fetchone()[0], 0)
        Core(self.config)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processing_attempts').fetchone()[0], 1)

    def test_nondefault_startup_preserves_pending_artifact_jobs_and_restart_fence(self):
        import test_artifact_lifecycle as artifact_fixtures
        from piecetogether.artifact_store import LocalArtifactStore
        from piecetogether.proposals import ArtifactOperation
        from piecetogether.retention import RetentionBusy

        class UnavailableStore(LocalArtifactStore):
            def finalize(self, staged):
                raise OSError('publication postponed')

            def remove(self, reference):
                raise OSError('removal postponed')

        for job in ('publication', 'deletion'):
            with self.subTest(job=job):
                flow = artifact_fixtures.LifecycleTests()
                flow.setUp()
                self.addCleanup(flow.doCleanups)
                unavailable = UnavailableStore(flow.config.artifact_store)
                if job == 'publication':
                    source = flow.core((ArtifactOperation('pending', 'Record',
                        ('persistent-domain-artifact',), action='persist'),), store=unavailable)
                    result = flow.send(source, 'pending')
                else:
                    original = flow.persist()
                    flow.rule['retention']['bytes'] = 'delete'
                    source = flow.deletion(original['id'], store=unavailable)
                    result = flow.send(source)
                self.assertEqual(result['status'], 'retryable')
                before = source.inspect_history()
                files = {path: path.read_bytes() for directory in (flow.store.staging, flow.store.persistent,
                                                                  flow.store.keys)
                         for path in directory.iterdir()}
                self.assertTrue(files)
                config = replace(flow.config, retention=self.policy(communications='delete'))
                contract = source.contract
                maintenance = Core(config, artifact_store=flow.store,
                                   contract_provider=artifact_fixtures.Provider(contract))
                self.assertEqual(maintenance.inspect_history(), before,
                                 'maintenance startup completed pending Artifact work')
                self.assertEqual({path: path.read_bytes() for path in files}, files)
                with self.assertRaises(RetentionBusy):
                    maintenance.apply_retention()
                # View-only terminal maintenance leaves jobs in place. Even a retain
                # restart must not recover them after the durable availability fence.
                maintenance = Core(replace(config, retention=self.policy(current_view='redact')),
                                   artifact_store=flow.store,
                                   contract_provider=artifact_fixtures.Provider(contract))
                maintenance.apply_retention()
                restarted = Core(flow.config, artifact_store=flow.store,
                                 contract_provider=artifact_fixtures.Provider(contract))
                self.assertEqual(restarted.inspect_history(), before)
                self.assertEqual({path: path.read_bytes() for path in files}, files)

    def test_restart_after_view_sweep_leaves_pending_events_and_references_untouched(self):
        from piecetogether.projections import ProjectionConfig
        from piecetogether.references import ReferenceConfig
        from test_projection import RecordingSink

        flow = fixtures.HistoryTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        source, _, _ = flow.accept_items(flow.expose())
        before = source.inspect_history()
        reference_file = flow.config.database.with_name('reference.json')
        reference_file.write_text(json.dumps([{
            'target_id': before['entities'][0]['id'], 'concept': 'count', 'value': 4,
            'source_reference': 'private-record', 'observed_version': 'v1',
        }]))
        sweeper = Core(replace(flow.config, retention=self.policy(current_view='delete')),
                       contract_provider=fixtures.ContractProvider(flow.contract))
        sweeper.apply_retention()
        sink = RecordingSink()
        restarted = Core(replace(flow.config, projection=ProjectionConfig(),
                                 reference_state=ReferenceConfig('application', reference_file)),
                         projection_sink=sink,
                         contract_provider=fixtures.ContractProvider(flow.contract))
        self.assertEqual(sink.received, [])
        self.assertEqual(restarted.inspect_history(), before)
        self.assertEqual(restarted.inspect_event_delivery(), [])

    def test_projection_mailbox_cannot_recreate_erased_event_directly(self):
        from piecetogether.projections import DevelopmentProjectionSink
        from piecetogether.retention import RetentionUnavailable
        core, _ = self.populated(communications='delete')
        sink = DevelopmentProjectionSink(self.config.database)
        event = {'id': 'private-event', 'semantic_commit_id': 'private-commit',
                 'content': 'private-projection-content'}
        self.assertTrue(sink.deliver(event))
        self.assertTrue(sink.deliver(event))
        with self.assertRaises(ValueError):
            sink.deliver({**event, 'content': 'conflicting-content'})
        core.apply_retention()
        with self.assertRaises(RetentionUnavailable):
            sink.deliver(event)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM projection_deliveries').fetchone()[0], 0)
            self.assertNotIn('private-projection-content', '\n'.join(db.iterdump()))

    def test_sweep_rejects_inflight_direct_projection_delivery(self):
        from piecetogether.projections import DevelopmentProjectionSink
        from piecetogether.retention import RetentionBusy, RetentionUnavailable
        core, _ = self.populated(communications='delete')
        sink = DevelopmentProjectionSink(self.config.database)
        event = {'id': 'private-event', 'semantic_commit_id': 'private-commit'}
        started, release = threading.Event(), threading.Event()
        outcomes, errors = [], []
        dumps = json.dumps

        def paused_dump(value, *args, **kwargs):
            started.set()
            if not release.wait(5):
                raise AssertionError('test did not release projection delivery')
            return dumps(value, *args, **kwargs)

        def deliver():
            try:
                outcomes.append(sink.deliver(event))
            except BaseException as error:
                errors.append(error)

        # Pause inside the real sink before INSERT, not inside a wrapper that could
        # acquire a different fence from the shipped direct method.
        with patch('piecetogether.projections.json.dumps', side_effect=paused_dump):
            thread = threading.Thread(target=deliver)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(RetentionBusy):
                    core.apply_retention()
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outcomes, [True])
        core.apply_retention()
        with self.assertRaises(RetentionUnavailable):
            sink.deliver(event)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM projection_deliveries').fetchone()[0], 0)

    def test_source_erasure_rejects_unfinished_artifact_byte_obligations(self):
        from piecetogether.retention import RetentionBusy
        for table, insert in (
            ('artifact_deletions', "INSERT INTO artifact_deletions VALUES ('private-artifact', 'private-ref', NULL)"),
            ('artifact_publications', "INSERT INTO artifact_publications VALUES ('private-artifact', 'private-stage', 'checksum', 3, 'pending', NULL)"),
        ):
            with self.subTest(table=table):
                self.config = replace(self.config, database=self.config.database.with_name(table + '.sqlite3'))
                core, communication_id = self.populated(communications='delete')
                before = core.inspect(communication_id)
                with sqlite3.connect(self.config.database) as db:
                    db.execute(insert)
                with self.assertRaises(RetentionBusy):
                    core.apply_retention()
                self.assertEqual(core.inspect(communication_id), before)
                with sqlite3.connect(self.config.database) as db:
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1)
                    self.assertEqual(db.execute('SELECT count(*) FROM retention_state').fetchone()[0], 0)

    def test_failed_sweep_rolls_back_erasure_and_availability_together(self):
        core, communication_id = self.populated(communications='delete')
        before = core.inspect(communication_id)
        with sqlite3.connect(self.config.database) as db:
            db.execute("CREATE TRIGGER reject_retention BEFORE INSERT ON retention_state "
                       "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            core.apply_retention()
        self.assertEqual(core.inspect(communication_id), before)
        self.assertEqual(core.accept(self.payload)['status'], 'completed')

    def test_cross_process_sweep_fence_blocks_ingress_without_persisting_content(self):
        from piecetogether.retention import RetentionBusy
        core = Core(self.config)
        process = subprocess.Popen([
            sys.executable, '-c',
            'import sys; from pathlib import Path; from piecetogether.retention import operation; '
            'guard=operation(Path(sys.argv[1]), exclusive=True); guard.__enter__(); '
            'print("locked", flush=True); sys.stdin.readline(); guard.__exit__(None, None, None)',
            str(self.config.database),
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'locked')
            with self.assertRaises(RetentionBusy):
                core.accept(self.payload)
        finally:
            process.communicate('\n', timeout=5)
        self.assertEqual(process.returncode, 0)
        with sqlite3.connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM turns').fetchone()[0], 0)

    def test_application_api_reports_unavailable_not_empty_state(self):
        from piecetogether.application_api import ApplicationApiConfig, ApplicationApiServer, ApplicationCredential
        core, _ = self.populated(communications='delete')
        config = replace(core.config, application_api=ApplicationApiConfig(
            '127.0.0.1', 0, (ApplicationCredential('reader', ('state:read', 'events:consume')),)),
            secret_references={'reader': 'PT_RETENTION_TEST_TOKEN'})
        with patch.dict(os.environ, {'PT_RETENTION_TEST_TOKEN': 'test-token'}):
            serving_core = Core(config)
            server = ApplicationApiServer(serving_core)
            server.start()
        self.addCleanup(server.close)
        core.apply_retention()
        for path in ('/v1/state?actor_id=private-actor', '/v1/events'):
            with self.subTest(path=path):
                request = Request(server.base_url + path, headers={'Authorization': 'Bearer test-token'})
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=2)
                self.assertEqual(raised.exception.code, 503)
                self.assertEqual(json.loads(raised.exception.read()), {'error': 'retention_unavailable'})

    def test_cli_rejects_erased_evidence_without_traceback(self):
        core, communication_id = self.populated(communications='delete')
        core.apply_retention()
        config = Path(self.temporary.name) / 'cli.json'
        config.write_text(json.dumps({'database': self.config.database.name, 'identities': self.config.identities}))
        command = [sys.executable, '-m', 'piecetogether', '--config', str(config)]
        processing = subprocess.run(command, input=json.dumps(self.payload) + '\n', text=True, capture_output=True)
        self.assertEqual(processing.returncode, 0, processing.stderr)
        self.assertEqual(json.loads(processing.stdout), {'error': 'retention_unavailable'})
        inspected = subprocess.run(command + ['--inspect', communication_id], text=True, capture_output=True)
        self.assertEqual(inspected.returncode, 1)
        self.assertEqual(inspected.stderr.strip(), 'Communication unavailable.')

    def test_artifact_bytes_remain_owned_by_artifact_store(self):
        import test_artifact_lifecycle as artifact_fixtures
        from piecetogether.retention import RetentionUnavailable
        flow = artifact_fixtures.LifecycleTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        artifact = flow.persist()
        flow.config = replace(flow.config, retention=self.policy(communications='delete'))
        core = flow.core()
        self.assertEqual(core.artifact_bytes(artifact['id']), b'original')
        with sqlite3.connect(flow.config.database) as db:
            storage_ref = db.execute('SELECT storage_ref FROM artifact_publications WHERE artifact_id=?',
                                     (artifact['id'],)).fetchone()[0]
        core.apply_retention()
        self.assertEqual(flow.store.read(storage_ref), b'original')
        with self.assertRaises(RetentionUnavailable):
            core.artifact_bytes(artifact['id'])

    def test_sweep_rejects_inflight_model_then_persisted_fence_blocks_old_core(self):
        from piecetogether.retention import RetentionBusy, RetentionUnavailable
        started, release = threading.Event(), threading.Event()
        from piecetogether.core import DeterministicModel

        class SlowModel(DeterministicModel):
            def propose(self, *args):
                started.set()
                if not release.wait(5):
                    raise AssertionError('test did not release model')
                return super().propose(*args)

        old_core = Core(self.config, model=SlowModel())
        sweeper = Core(replace(self.config, retention=self.policy(communications='delete')))
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(old_core.accept(self.payload)))
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaises(RetentionBusy):
                sweeper.apply_retention()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes[0]['status'], 'completed')
        sweeper.apply_retention()
        with self.assertRaises(RetentionUnavailable):
            old_core.accept(self.payload)
        with sqlite3.connect(self.config.database) as db:
            db.row_factory = sqlite3.Row
            with self.assertRaises(RetentionUnavailable):
                history.inspect_history(db)


if __name__ == '__main__':
    unittest.main()
