"""Ordinary expiry keeps retained-source processing alive; temporary state only."""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_history as fixtures
from piecetogether import evaluation, retention, view
from piecetogether.core import Bootstrap, Core, IdentitySource, connect
from piecetogether.proposals import SemanticProposal


REFERENCE = datetime(2021, 6, 1, tzinfo=timezone.utc)


class OldClock(IdentitySource):
    def now(self):
        return '2020-01-01T12:00:00+00:00'


class NoInference:
    def propose(self, *args):
        raise AssertionError('removed evidence must not cause inference')


class DiagnosticModel:
    def propose(self, inbound, version, pack):
        return SemanticProposal(1, version, inbound.id, (), 'Reply ' + inbound.text,
                                decision_provider='optional-provider',
                                decision_raw={'private': 'optional-diagnostic'})


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = Bootstrap(self.root / 'core.sqlite3', {'development:s': 'a'})
        self.payload = dict(channel='development', sender='s', idempotency_key='old',
                            text='old-secret', sent_at='1900-01-01T00:00:00Z')

    def old_turn(self, **kwargs):
        core = Core(self.config, identity_source=OldClock(), **kwargs)
        result = core.accept(self.payload)
        self.assertEqual(result['status'], 'completed')
        return core, result

    def expire(self, core, **kwargs):
        self.assertTrue(callable(getattr(core, 'expire_records', None)),
                        'ordinary retention needs a separate non-terminal API')
        return core.expire_records(reference_time=REFERENCE, **kwargs)

    def test_old_payload_and_controlled_duplicates_expire_without_semantic_change(self):
        core, result = self.old_turn(model=DiagnosticModel())
        before = (core.inspect_history(), core.current_view(), core.trusted_events())
        report = self.expire(core, dry_run=True)
        self.assertEqual(report['communications_erased'], 1)
        self.assertEqual(core.inspect(result['communication_id'])['inbound']['text'], 'old-secret')
        self.assertEqual(self.expire(core), report)
        self.assertEqual((core.inspect_history(), core.current_view(), core.trusted_events()), before)
        with connect(self.config.database) as db:
            dump = '\n'.join(db.iterdump())
            for content in ('old-secret', 'optional-diagnostic', 'optional-provider'):
                self.assertNotIn(content, dump)
            self.assertEqual(db.execute('SELECT count(*) FROM turns').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
            self.assertEqual(retention.availability(db), dict.fromkeys(retention.CATEGORIES, 'retain'))
        with self.assertRaises(retention.RetentionUnavailable):
            core.inspect(result['communication_id'])
        self.assertEqual(self.expire(core)['communications_erased'], 0)
        restarted = Core(self.config, model=NoInference())
        # Lookup must precede payload/attachment parsing and cannot reconstruct removed bytes.
        for payload in (self.payload, {**self.payload, 'attachments': 'malformed'},
                        {key: self.payload[key] for key in ('channel', 'sender', 'idempotency_key')}):
            with self.subTest(payload=payload), self.assertRaises(retention.RetentionUnavailable):
                restarted.accept(payload)
        fresh = Core(self.config).accept({**self.payload, 'idempotency_key': 'new', 'text': 'new note'})
        self.assertEqual(fresh['status'], 'completed')

    def test_expired_retry_authorization_precedes_existence_and_payload_parsing(self):
        import os
        from piecetogether.email_channel import TEXT_CAPABILITIES, EmailConfig
        class NoDelivery:
            def deliver(self, outbound):
                raise AssertionError('unauthorized retry must not reach delivery')
        core, result = self.old_turn()
        self.expire(core)
        disabled = replace(self.config, channel='email', identities={'email:s@example.com': 'a'},
                           capabilities=TEXT_CAPABILITIES,
                           email=EmailConfig('core@example.com', 'localhost', security='plain'))
        cases = (
            (replace(self.config, identities={'development:other': 'a'}), {}, 'unmapped channel identity'),
            (disabled, {}, 'channel is disabled'),
            (self.config, {'text': 'https://user:password@example.invalid/'},
             'credential-bearing evidence is not accepted'),
            (self.config, {'text': 'https://user:password@example.invalid/', 'attachments': b'invalid'},
             'credential-bearing evidence is not accepted'),
            (replace(self.config, secret_references={'fixture': 'PT_RETENTION_TEST_SECRET'}),
             {'text': 'configured-fixture-credential', 'attachments': b'invalid'},
             'credential-bearing evidence is not accepted'),
        )
        with connect(self.config.database) as db:
            self.assertTrue(retention.record_status(db, result['communication_id'])[1])
            before = '\n'.join(db.iterdump())
        for config, extra, message in cases:
            with patch.dict(os.environ, {'PT_RETENTION_TEST_SECRET': 'configured-fixture-credential'}):
                guarded = Core(config, model=NoInference(), channel=NoDelivery())
            for key in ('old', 'missing'):
                for full in (False, True):
                    payload = (dict(self.payload, attachments='malformed') if full else
                               {name: self.payload[name] for name in ('channel', 'sender', 'idempotency_key')})
                    payload.update(extra, idempotency_key=key)
                    with self.subTest(message=message, key=key, full=full):
                        with self.assertRaisesRegex(ValueError, message):
                            guarded.accept(payload)
        guarded = Core(self.config, model=NoInference())
        for name in ('channel', 'sender', 'idempotency_key'):
            for value in (None, 7, '', ' ', 'x' * 257):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    guarded.accept({**self.payload, name: value, 'attachments': 'malformed'})
        with connect(self.config.database) as db:
            self.assertEqual('\n'.join(db.iterdump()), before)
        for payload in ({name: self.payload[name] for name in ('channel', 'sender', 'idempotency_key')},
                        {**self.payload, 'attachments': 'malformed'}):
            with self.assertRaises(retention.RetentionUnavailable):
                guarded.accept(payload)

    def test_expired_outbound_cannot_be_republished_from_a_stale_channel_instance(self):
        from piecetogether.core import Communication
        core, result = self.old_turn()
        outbound = Communication(**core.inspect(result['communication_id'])['outbound'])
        self.expire(core)
        with self.assertRaises(retention.RetentionUnavailable):
            core.channel.deliver(outbound)
        with connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)
            self.assertNotIn('old-secret', '\n'.join(db.iterdump()))

    def test_trace_expiry_strips_diagnostics_keeps_payload_retry_and_future_work(self):
        core, result = self.old_turn(model=DiagnosticModel())
        policy = self.policy(communication_days=10000)
        report = self.expire(core, policy=policy)
        self.assertEqual(report['traces_erased'], 1)
        self.assertEqual(report['communications_erased'], 0)
        inspected = core.inspect(result['communication_id'])
        self.assertEqual(inspected['trace'], {'availability': 'expired'})
        self.assertEqual(inspected['attempts'], [])
        self.assertEqual(inspected['proposal']['draft_response'], 'Reply old-secret')
        self.assertIsNone(inspected['proposal'].get('decision_raw'))
        with connect(self.config.database) as db:
            self.assertNotIn('optional-diagnostic', '\n'.join(db.iterdump()))
        restarted = Core(self.config, model=NoInference())
        self.assertEqual(restarted.accept(self.payload), result)
        self.assertEqual(self.expire(restarted, policy=policy)['traces_erased'], 0)
        with self.assertRaises(evaluation.ReplayUnavailable):
            evaluation.capture(restarted, lambda recorder: recorder.accept(self.payload))

    def test_payload_expiry_counts_attempt_only_diagnostics_once(self):
        core, result = self.old_turn(model=DiagnosticModel())
        with connect(self.config.database) as db:
            db.execute('UPDATE turns SET trace=NULL')
            attempts = db.execute('SELECT trace FROM processing_attempts').fetchall()
            self.assertTrue(attempts)
            self.assertTrue(any(row['trace'] is not None for row in attempts))
            self.assertIn('optional-diagnostic', '\n'.join(db.iterdump()))
        policy = self.policy(trace_days=10000)
        planned = self.expire(core, policy=policy, dry_run=True)
        self.assertEqual((planned['traces_erased'], planned['communications_erased']), (1, 1))
        self.assertEqual(self.expire(core, policy=policy), planned)
        with connect(self.config.database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processing_attempts').fetchone()[0], 0)
            self.assertNotIn('optional-diagnostic', '\n'.join(db.iterdump()))
            self.assertEqual(retention.record_status(db, result['communication_id']), (True, True))
        self.assertEqual(self.expire(core, policy=policy)['traces_erased'], 0)

    def test_expiry_without_diagnostic_copies_does_not_count_trace_erasure(self):
        for trace_days, communication_days, payloads in ((30, 10000, 0), (10000, 90, 1)):
            with self.subTest(trace_days=trace_days):
                config = replace(self.config, database=self.root / f'no-trace-{trace_days}.sqlite3')
                core = Core(config, identity_source=OldClock())
                result = core.accept(self.payload)
                self.assertEqual(result['status'], 'completed')
                with connect(config.database) as db:
                    db.execute('UPDATE turns SET trace=NULL, proposal=NULL')
                    db.execute('DELETE FROM processing_attempts')
                policy = self.policy(trace_days=trace_days, communication_days=communication_days)
                planned = self.expire(core, policy=policy, dry_run=True)
                self.assertEqual((planned['traces_erased'], planned['communications_erased']), (0, payloads))
                self.assertEqual(self.expire(core, policy=policy), planned)
                with connect(config.database) as db:
                    self.assertEqual(retention.record_status(db, result['communication_id']), (True, bool(payloads)))
                self.assertEqual(self.expire(core, policy=policy)['traces_erased'], 0)

    def policy(self, **kwargs):
        self.assertTrue(hasattr(retention, 'ExpiryPolicy'), 'expiry requires validated static horizons')
        return retention.ExpiryPolicy(**kwargs)

    def test_policy_bootstrap_validation_and_time_rejection_are_nonmutating(self):
        policy = self.policy()
        self.assertEqual(asdict(policy), {'trace_days': 30, 'communication_days': 90})
        for value in (0, -1, True, 1.5, '30', None, 10**30):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.policy(trace_days=value)
        for raw in (None, [], {'unknown': 1}, {'trace_days': 0}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                retention.ExpiryPolicy.from_dict(raw)
        with self.assertRaises(ValueError):
            replace(self.config, expiry={})
        path = self.root / 'bootstrap.json'
        path.write_text(json.dumps({'database': 'core.sqlite3', 'identities': self.config.identities,
                                    'expiry': {'trace_days': 45}}))
        self.assertEqual(Bootstrap.from_file(path).expiry, self.policy(trace_days=45))
        core, result = self.old_turn()
        before = core.inspect(result['communication_id'])
        for reference in ('2021-01-01', datetime(2021, 1, 1),
                          datetime.now(timezone.utc) + timedelta(days=1)):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                core.expire_records(reference_time=reference)
        for kwargs in ({'policy': {}}, {'dry_run': 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.expire(core, **kwargs)
        self.assertEqual(core.inspect(result['communication_id']), before)

    def test_default_horizons_apply_at_exact_core_timestamp_boundaries(self):
        for age, traces, payloads in ((29, 0, 0), (30, 1, 0), (89, 1, 0), (90, 1, 1)):
            with self.subTest(age=age):
                received = REFERENCE - timedelta(days=age)
                class AtTime(IdentitySource):
                    def now(self):
                        return received.isoformat()
                config = replace(self.config, database=self.root / f'age-{age}.sqlite3')
                core = Core(config, identity_source=AtTime())
                result = core.accept(self.payload)
                self.assertEqual(result['status'], 'completed')
                report = self.expire(core)
                self.assertEqual((report['traces_erased'], report['communications_erased']), (traces, payloads))

    def test_real_pending_artifact_publication_and_files_remain_unchanged(self):
        from test_artifact_lifecycle import LifecycleTests
        from piecetogether.artifact_store import LocalArtifactStore
        from piecetogether.proposals import ArtifactOperation
        class UnavailableStore(LocalArtifactStore):
            def finalize(self, staged):
                raise OSError('publication postponed')
        flow = LifecycleTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        store = UnavailableStore(flow.config.artifact_store)
        core = flow.core((ArtifactOperation('pending', 'Record', ('persistent-domain-artifact',),
                                            action='persist'),), store=store)
        with patch('piecetogether.core.now', OldClock().now):
            result = flow.send(core, 'pending')
        self.assertEqual(result['status'], 'retryable')
        before = core.inspect(result['communication_id'])
        trusted = core.inspect_history()
        with connect(flow.config.database) as db:
            jobs = [tuple(row) for row in db.execute('SELECT * FROM artifact_publications')]
        files = {path: path.read_bytes() for directory in (store.staging, store.persistent, store.keys)
                 for path in directory.iterdir()}
        self.assertTrue(files)
        report = self.expire(core)
        self.assertEqual(report, {'traces_erased': 0, 'traces_protected': 1,
                                  'communications_erased': 0, 'communications_protected': 1,
                                  'obligations_pending': 1})
        self.assertEqual(core.inspect(result['communication_id']), before)
        self.assertEqual(core.inspect_history(), trusted)
        with connect(flow.config.database) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM artifact_publications')], jobs)
        self.assertEqual({path: path.read_bytes() for directory in (store.staging, store.persistent, store.keys)
                          for path in directory.iterdir()}, files)

    def test_sender_timestamp_cannot_age_new_ingress_and_recent_retry_is_protected(self):
        core = Core(self.config)
        result = core.accept(self.payload)
        report = self.expire(core)
        self.assertEqual(report['traces_erased'], 0)
        self.assertEqual(report['communications_erased'], 0)
        self.assertEqual(core.inspect(result['communication_id'])['inbound']['text'], 'old-secret')
        # A durable old ingress completed recently must not immediately lose its trace.
        with connect(self.config.database) as db:
            inbound = json.loads(db.execute('SELECT inbound FROM turns').fetchone()[0])
            inbound['received_at'] = OldClock().now()
            db.execute('UPDATE turns SET inbound=?', (json.dumps(inbound),))
        report = self.expire(core)
        self.assertEqual(report['traces_erased'], 0)
        self.assertEqual(report['communications_erased'], 0)
        self.assertEqual(report['communications_protected'], 1)

    def test_retry_checkpoint_and_transport_and_artifact_evidence_protected(self):
        class Refuse:
            def deliver(self, outbound):
                return False
        core = Core(self.config, channel=Refuse(), identity_source=OldClock())
        result = core.accept(self.payload)
        before = core.inspect(result['communication_id'])
        report = self.expire(core)
        self.assertEqual(report['traces_protected'], 1)
        self.assertEqual(report['communications_protected'], 1)
        self.assertEqual(core.inspect(result['communication_id']), before)
        resumed = Core(self.config, model=NoInference())
        self.assertEqual(resumed.accept(self.payload)['status'], 'completed')
        # Artifact ownership remains protected even when no job can be inferred complete.
        with connect(self.config.database) as db:
            db.execute('INSERT INTO artifact_ingress VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (result['communication_id'], 'attachment', 'a', 'image/png', 'checksum', 1, OldClock().now()))
        report = self.expire(resumed)
        self.assertEqual(report['communications_protected'], 1)
        self.assertEqual(report['communications_erased'], 0)
        # Conservative transport protection is explicit, not a guessed handoff closure.
        with connect(self.config.database) as db:
            db.execute('DELETE FROM artifact_ingress')
            db.execute("UPDATE turns SET channel='email'")
        self.assertEqual(self.expire(resumed)['communications_protected'], 1)

    def test_retained_diagnostic_copy_in_younger_turn_protects_old_payload(self):
        core, result = self.old_turn()
        from piecetogether.context import ContextPack
        class YoungerClock(IdentitySource):
            def now(self):
                return (REFERENCE - timedelta(days=10)).isoformat()
        core = Core(self.config, identity_source=YoungerClock())
        younger = core.accept({**self.payload, 'idempotency_key': 'younger', 'text': 'new'})
        self.assertEqual(younger['status'], 'completed')
        trace = core.inspect(younger['communication_id'])['trace']
        captured = ContextPack.from_dict(trace['context_pack']).captured()
        for copies in (captured['catalogue'], trace['selection'][0]['request']['candidates']):
            source = [copy for copy in copies if copy['kind'] == 'communication']
            self.assertEqual(len(source), 1)
            self.assertEqual((source[0]['id'], source[0]['summary']), (result['communication_id'], 'old-secret'))
            self.assertEqual(source[0]['payload_json'], '')
        with connect(self.config.database) as db:
            attempts = db.execute('SELECT trace FROM processing_attempts WHERE communication_id=?',
                                  (younger['communication_id'],)).fetchall()
            self.assertTrue(attempts)
            self.assertTrue(all('old-secret' in row['trace'] and result['communication_id'] in row['trace']
                                for row in attempts))
        report = self.expire(core)
        self.assertEqual(report['traces_erased'], 1)
        self.assertEqual(report['communications_protected'], 1)
        self.assertEqual(report['obligations_pending'], 0)
        self.assertEqual(core.inspect(result['communication_id'])['inbound']['text'], 'old-secret')
        self.assertIn('old-secret', json.dumps(core.inspect(younger['communication_id'])['trace']))
        # A later run removes the real younger copies, then releases both payloads.
        report = core.expire_records(reference_time=REFERENCE + timedelta(days=120))
        self.assertEqual((report['traces_erased'], report['communications_erased']), (1, 2))
        with connect(self.config.database) as db:
            self.assertNotIn('old-secret', '\n'.join(db.iterdump()))
            self.assertEqual(db.execute('SELECT count(*) FROM deliveries').fetchone()[0], 0)

    def test_pending_then_grounded_then_correction_stays_operational(self):
        flow = fixtures.HistoryTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        with patch('piecetogether.core.now', OldClock().now):
            exposed = flow.expose()
        core = flow.core()
        before = core.inspect_history()
        report = self.expire(core)
        self.assertEqual(report['communications_protected'], 1)
        self.assertEqual(core.inspect_history(), before)
        core, payload, result = flow.accept_items(exposed)
        self.assertEqual(result['status'], 'completed')
        trusted = core.inspect_history()
        self.assertEqual(trusted['revision'], 1)
        self.expire(core)
        self.assertEqual(core.inspect_history(), trusted)
        corrected = flow.next_claim(trusted, 4, 'corrects')
        core, _, correction = flow.accept_items(corrected, revision=1)
        self.assertEqual(correction['status'], 'completed')
        self.assertEqual(core.current_view()['assertion_sets'][0]['heads'][0]['value'], 4)
        no_model = Core(flow.config, model=NoInference(), contract_provider=fixtures.ContractProvider(flow.contract))
        self.assertEqual(no_model.accept(payload), result)

    def test_corrected_pending_target_bindings_survive_expiry_and_later_acceptance(self):
        from test_grounding import GroundingTests
        from piecetogether.proposals import ClaimOperation, GroundingPlanOperation, GroundingResolutionOperation
        flow = GroundingTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        with patch('piecetogether.core.now', OldClock().now):
            exposed = flow.expose()
        subject = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
        note = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
        core = flow.core(lambda inbound: (
            GroundingResolutionOperation(note['id'], 'p', inbound.id, 'explicit', 'corrected',
                                         evidence_span='blue no green', successor_ids=('green_note',)),
            ClaimOperation('green_note', 'subject', 'note', 'green', 'p'),
            GroundingPlanOperation('p', ('green_note',), 'explicit'),
        ), intent='semantic_commit', semantic_revision=0)
        with patch('piecetogether.core.now', lambda: '2020-01-02T12:00:00+00:00'):
            corrected = core.accept(flow.message(text='blue no green', reply_to=exposed['outbound']['id']))
        self.assertEqual(corrected['status'], 'completed')
        successor_turn = core.inspect(corrected['communication_id'])
        successor = successor_turn['grounding_items'][0]
        with connect(flow.config.database) as db:
            bindings = [tuple(row) for row in db.execute('SELECT * FROM candidate_target_bindings')]
        self.assertTrue(bindings)
        report = self.expire(core)
        self.assertEqual(report['traces_erased'], 2)
        self.assertEqual(report['communications_protected'], 2)
        with connect(flow.config.database) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM candidate_target_bindings')], bindings)
        for item, reply_to, revision in ((subject, exposed['outbound']['id'], 1),
                                         (successor, successor_turn['outbound']['id'], 2)):
            accepting = flow.core(lambda inbound: (GroundingResolutionOperation(
                item['id'], 'p', inbound.id, 'explicit', 'accepted'),),
                intent='semantic_commit', semantic_revision=revision)
            self.assertEqual(accepting.accept(flow.message(reply_to=reply_to))['status'], 'completed')
        trusted = flow.core().inspect_history()
        self.assertEqual(trusted['claims'][0]['value'], 'green')
        self.assertEqual(trusted['claims'][0]['target_id'], trusted['entities'][0]['id'])

    def test_optional_exposed_decision_diagnostics_removed_but_proposal_binding_retained(self):
        flow = fixtures.HistoryTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        with patch('piecetogether.core.now', OldClock().now):
            exposed = flow.expose()
        with connect(flow.config.database) as db:
            for table in ('turns', 'exposed_groundings'):
                proposal = json.loads(db.execute(f'SELECT proposal FROM {table}').fetchone()[0])
                proposal['decision_raw'] = {'secret': 'redundant-decision'}
                db.execute(f'UPDATE {table} SET proposal=?', (json.dumps(proposal),))
        core = flow.core()
        self.expire(core)
        with connect(flow.config.database) as db:
            self.assertNotIn('redundant-decision', '\n'.join(db.iterdump()))
            self.assertGreater(db.execute('SELECT count(*) FROM pending_items').fetchone()[0], 0)
        self.assertEqual(flow.accept_items(exposed)[2]['status'], 'completed')

    def test_view_withholding_reversible_without_processing_stop_or_legacy_resurrection(self):
        core, _ = self.old_turn()
        before = core.current_view()
        self.assertTrue(callable(getattr(core, 'set_view_withheld', None)), 'separate view-read policy needed')
        core.set_view_withheld(True)
        for instance in (core, Core(self.config)):
            with self.assertRaises(retention.RetentionUnavailable):
                instance.current_view()
            with connect(self.config.database) as db, self.assertRaises(retention.RetentionUnavailable):
                view.current_view(db)
            self.assertEqual(instance.accept({**self.payload, 'idempotency_key': str(id(instance))})['status'], 'completed')
        with self.assertRaises(ValueError):
            core.set_view_withheld(1)
        core.set_view_withheld(False)
        self.assertEqual(core.current_view(), before)
        terminal = Core(replace(self.config, retention=retention.RetentionPolicy(current_view='delete')))
        terminal.apply_retention()
        with self.assertRaises(retention.RetentionUnavailable):
            terminal.set_view_withheld(False)
        with self.assertRaises(retention.RetentionUnavailable):
            self.expire(terminal)
        with self.assertRaises(retention.RetentionUnavailable):
            Core(self.config).accept(self.payload)

    def test_snapshot_and_capture_match_source_directly_after_expiry_and_replay_new_work(self):
        self.config = replace(self.config, expiry=self.policy(trace_days=45, communication_days=100))
        core, result = self.old_turn()
        self.expire(core)
        source_view = core.current_view()
        core.set_view_withheld(True)
        source_history, source_events = core.inspect_history(), core.trusted_events()
        def state(database):
            with connect(database) as db:
                return {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                        for table in evaluation._STATE_TABLES}
        source_state = state(self.config.database)
        with connect(self.config.database) as db:
            expected_expiry = [tuple(row) for row in db.execute('SELECT * FROM record_expiry ORDER BY communication_id')]
            expected_access = [tuple(row) for row in db.execute('SELECT * FROM view_access')]
        snapshot_path = self.root / 'restored.sqlite3'
        evaluation._restore(evaluation._snapshot(self.config.database), snapshot_path)
        restored = Core(replace(self.config, database=snapshot_path), model=NoInference())
        self.assertEqual(state(snapshot_path), source_state)
        self.assertEqual(restored.inspect_history(), source_history)
        self.assertEqual(restored.trusted_events(), source_events)
        with self.assertRaises(retention.RetentionUnavailable):
            restored.inspect(result['communication_id'])
        with self.assertRaises(retention.RetentionUnavailable):
            restored.current_view()
        with connect(snapshot_path) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM record_expiry ORDER BY communication_id')], expected_expiry)
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM view_access')], expected_access)
        restored.set_view_withheld(False)
        self.assertEqual(restored.current_view(), source_view)
        def scenario(recorder):
            # Initial isolated capture must agree with source, not just its own replay.
            self.assertEqual(state(recorder.core.config.database), source_state)
            self.assertEqual(recorder.core.inspect_history(), source_history)
            self.assertEqual(recorder.core.trusted_events(), source_events)
            with self.assertRaises(retention.RetentionUnavailable):
                recorder.core.inspect(result['communication_id'])
            with connect(recorder.core.config.database) as db:
                self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM record_expiry ORDER BY communication_id')], expected_expiry)
            recorder.accept({**self.payload, 'idempotency_key': 'capture-new', 'text': 'new'})
            recorder.restart()
            recorder.accept({**self.payload, 'idempotency_key': 'capture-new', 'text': 'new'})
        case = evaluation.capture(core, scenario)
        self.assertEqual(case.configuration['expiry'], asdict(core.config.expiry))
        with patch('piecetogether.core.DeterministicModel.propose', side_effect=AssertionError('no live inference')):
            self.assertEqual(evaluation.replay(case).mismatches, ())
        with self.assertRaises(evaluation.ReplayUnavailable):
            evaluation.capture(core, lambda recorder: recorder.accept(self.payload))

    def test_withheld_state_api_explicitly_unavailable_events_and_processing_continue(self):
        import os
        from test_application_api import ApplicationApiTests
        flow = ApplicationApiTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        with patch.dict(os.environ, {'PT_API_READER': 'reader-secret', 'PT_API_CONSUMER': 'consumer-secret',
                                     'PT_API_BOTH': 'both-secret'}, clear=True):
            server = flow.server()
            flow.core.set_view_withheld(True)
            status, body = flow.request(server, f'/v1/state?entity_id={flow.entity_id}', 'reader-secret')
            self.assertEqual((status, body), (503, {'error': 'retention_unavailable'}))
            self.assertEqual(flow.request(server, '/v1/events', 'consumer-secret')[0], 200)
            flow.core.model = fixtures.ProposalModel(())
            result = flow.core.accept(flow.flow.message(text='New ordinary input'))
            self.assertEqual(result['status'], 'completed')
            flow.core.set_view_withheld(False)
            self.assertEqual(flow.request(server, f'/v1/state?entity_id={flow.entity_id}', 'reader-secret')[0], 200)
            self.assertEqual(flow.request(server, '/v1/admin/retention', 'both-secret')[0], 404)

    def test_old_replay_requiring_removed_payload_rejects_without_inference(self):
        import hashlib
        core, _ = self.old_turn()
        case = evaluation.capture(core, lambda recorder: recorder.accept(self.payload))
        self.expire(core)
        erased = evaluation._snapshot(self.config.database)
        case = replace(case, initial_state=erased, state_digest=hashlib.sha256(erased).hexdigest())
        with patch('piecetogether.core.DeterministicModel.propose', side_effect=AssertionError('no inference')):
            with self.assertRaises(evaluation.ReplayUnavailable):
                evaluation.replay(case)

    def test_failed_event_delivery_protected_until_owner_resumes_without_inference(self):
        from piecetogether import projections
        from test_projection import FailingSink, RecordingSink
        flow = fixtures.HistoryTests()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        with patch('piecetogether.core.now', OldClock().now):
            exposed = flow.expose()
        with patch('piecetogether.core.now', lambda: '2020-01-02T12:00:00+00:00'):
            core, _, result = flow.accept_items(exposed)
        self.assertEqual(result['status'], 'completed')
        before = core.inspect_history()
        self.assertEqual(before['revision'], 1)
        # Durable outbox content protects provenance, not redundant settled traces.
        report = self.expire(core, dry_run=True)
        self.assertEqual(report['traces_erased'], 2)
        self.assertEqual(report['obligations_pending'], 0)
        config = replace(flow.config, projection=projections.ProjectionConfig(max_attempts=1))
        core = Core(config, projection_sink=FailingSink(), model=NoInference(),
                    contract_provider=fixtures.ContractProvider(flow.contract))
        self.assertEqual(core.inspect_event_delivery()[0]['state'], 'failed')
        jobs = core.inspect_event_delivery()
        report = self.expire(core)
        self.assertEqual(report['traces_erased'], 0)
        self.assertEqual(report['traces_protected'], 2)
        self.assertEqual(report['communications_protected'], 2)
        self.assertEqual(report['obligations_pending'], 1)
        self.assertEqual(core.inspect_event_delivery(), jobs)
        self.assertEqual(core.inspect_history(), before)
        sink = RecordingSink()
        core.projection_sink = sink
        self.assertTrue(core.recover_event_delivery(jobs[0]['event_id']))
        core.dispatch_events()
        self.assertEqual(sink.received, before['events'])
        report = self.expire(core)
        self.assertEqual(report['traces_erased'], 2)
        self.assertEqual(report['obligations_pending'], 0)
        self.assertEqual(core.inspect_history(), before)

    def test_additive_migration_and_malformed_stored_times_fail_atomically(self):
        core, result = self.old_turn()
        with connect(self.config.database) as db:
            db.execute('DROP TABLE record_expiry')
            db.execute('DROP TABLE view_access')
            db.execute('DROP TABLE retention_state')
        migrated = Core(self.config, model=NoInference())
        self.assertEqual(migrated.accept(self.payload), result)
        self.assertEqual(self.expire(migrated, dry_run=True)['communications_erased'], 1)
        self.assertEqual(migrated.inspect(result['communication_id'])['inbound']['text'], 'old-secret')
        # The second old turn is malformed after the first turn has been planned.
        other = Core(self.config, identity_source=OldClock()).accept({**self.payload, 'idempotency_key': 'bad'})
        with connect(self.config.database) as db:
            inbound = json.loads(db.execute('SELECT inbound FROM turns WHERE id=?', (other['communication_id'],)).fetchone()[0])
            inbound['received_at'] = '2020-01-01T00:00:00'
            db.execute('UPDATE turns SET inbound=? WHERE id=?', (json.dumps(inbound), other['communication_id']))
            before = '\n'.join(db.iterdump())
        with self.assertRaises(ValueError):
            self.expire(migrated)
        with connect(self.config.database) as db:
            self.assertEqual('\n'.join(db.iterdump()), before)
        # Very early injected times must not underflow; future stored time cannot age a turn.
        with connect(self.config.database) as db:
            inbound['received_at'] = '9999-12-31T00:00:00+00:00'
            db.execute('UPDATE turns SET inbound=? WHERE id=?', (json.dumps(inbound), other['communication_id']))
        self.assertEqual(core.expire_records(reference_time=datetime(1, 1, 1, tzinfo=timezone.utc))['communications_erased'], 0)

    def test_expiry_rejects_busy_maintenance_and_does_not_migrate_terminal_state(self):
        core, _ = self.old_turn()
        with retention.operation(self.config.database):
            with self.assertRaises(retention.RetentionBusy):
                self.expire(core)
        terminal = Core(replace(self.config, retention=retention.RetentionPolicy(evaluation_traces='delete')))
        terminal.apply_retention()
        with self.assertRaises(retention.RetentionUnavailable):
            self.expire(Core(self.config))
        with connect(self.config.database) as db:
            self.assertEqual(retention.availability(db)['evaluation_traces'], 'delete')
