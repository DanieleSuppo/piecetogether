"""Post-commit event delivery at the Core boundary; no live services."""

import copy
import json
import sqlite3
import unittest
from pathlib import Path
from dataclasses import replace

from piecetogether import projections

import test_history as fixtures
from piecetogether.core import Bootstrap, Core
from piecetogether.references import ReferenceAssertion, ReferenceConfig


class RecordingSink:
    def __init__(self):
        self.received = []
        self.applied = {}

    def deliver(self, event):
        self.received.append(copy.deepcopy(event))
        key = (event['id'], event['semantic_commit_id'])
        self.applied.setdefault(key, copy.deepcopy(event))
        return True


class Clock:
    def __init__(self):
        self.value = 1_000.0

    def __call__(self):
        return self.value


class FailingSink(RecordingSink):
    def deliver(self, event):
        self.received.append(copy.deepcopy(event))
        raise OSError('temporary sink outage')


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.HistoryTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.exposed = self.flow.expose()
        self.core, self.payload, self.result = self.flow.accept_items(self.exposed)
        self.clock = Clock()

    def delivery_core(self, sink, **changes):
        config = replace(self.flow.config, projection=projections.ProjectionConfig(
            max_attempts=3, backoff_seconds=2, max_backoff_seconds=10, lease_seconds=30), **changes)
        return Core(config, projection_sink=sink, delivery_clock=self.clock,
                    model=fixtures.MustNotRunModel(),
                    contract_provider=fixtures.ContractProvider(self.flow.contract))

    def test_temporary_failure_retries_with_backoff_and_original_payload(self):
        before = self.core.inspect_history()
        view = self.core.current_view()
        sink = FailingSink()
        core = self.delivery_core(sink)
        core.dispatch_events()
        self.assertEqual(len(sink.received), 1)
        job = core.inspect_event_delivery()[0]
        self.assertEqual(job['state'], 'pending')
        self.assertEqual(job['next_attempt_at'], 1_002)
        self.assertEqual(job['attempts'][0]['error_type'], 'OSError')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.current_view(), view)
        self.assertEqual(core.accept(self.payload), self.result)
        self.clock.value += 2
        sink = RecordingSink()
        core.projection_sink = sink
        core.dispatch_events()
        self.assertEqual(sink.received, before['events'])
        job = core.inspect_event_delivery()[0]
        self.assertEqual(job['state'], 'delivered')
        self.assertEqual([a['outcome'] for a in job['attempts']], ['retryable', 'accepted'])
        self.assertEqual(core.inspect_history(), before)

    def test_startup_and_new_commit_publish_outside_semantic_transactions(self):
        before = self.core.inspect_history()
        observed = []
        database = self.flow.config.database
        self_config, self_contract = self.flow.config, self.flow.contract

        class UnlockedSink(RecordingSink):
            def deliver(self, event):
                # A second writer can acquire a lock during sink acceptance.
                import sqlite3
                with sqlite3.connect(database, timeout=0) as db:
                    db.execute('BEGIN IMMEDIATE')
                observed.append(Core(self_config, model=fixtures.MustNotRunModel(),
                                     contract_provider=fixtures.ContractProvider(self_contract)).inspect_history())
                return super().deliver(event)

        sink = RecordingSink()
        core = self.delivery_core(sink)
        self.assertEqual(sink.received, before['events'])
        exposed = self.flow.expose()
        self_core, _, result = self.flow.accept_items(exposed, revision=1, projection_sink=UnlockedSink())
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0], self_core.inspect_history())
        self.assertEqual(observed[0]['events'][-1]['semantic_revision'], 2)
        self.assertEqual(self_core.inspect_event_delivery()[-1]['state'], 'delivered')

    def test_persistent_failure_is_recoverable_after_restart(self):
        before = self.core.inspect_history()
        view = self.core.current_view()
        sink = FailingSink()
        core = self.delivery_core(sink)
        core.dispatch_events()
        self.clock.value += 2
        core.dispatch_events()
        self.assertEqual(core.inspect_event_delivery()[0]['next_attempt_at'], 1_006)
        self.clock.value += 4
        core.dispatch_events()
        job = core.inspect_event_delivery()[0]
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['attempts_since_recovery'], 3)
        self.clock.value += 100
        core.dispatch_events()
        self.assertEqual(len(sink.received), 3)
        self.assertEqual(sink.received, before['events'] * 3)
        sink = RecordingSink()
        restarted = self.delivery_core(sink)
        self.assertEqual(restarted.inspect_event_delivery(), core.inspect_event_delivery())
        self.assertTrue(restarted.recover_event_delivery(before['events'][0]['id']))
        self.assertFalse(restarted.recover_event_delivery(before['events'][0]['id']))
        restarted.dispatch_events()
        job = restarted.inspect_event_delivery()[0]
        self.assertEqual(job['state'], 'delivered')
        self.assertEqual([a['attempt'] for a in job['attempts']], [1, 2, 3, 4])
        self.assertEqual([a['outcome'] for a in job['attempts']], ['retryable'] * 3 + ['accepted'])
        self.assertEqual(sink.received, before['events'])
        self.assertEqual(restarted.inspect_history(), before)
        self.assertEqual(restarted.current_view(), view)
        self.assertEqual(restarted.accept(self.payload), self.result)

    def test_crash_after_acceptance_replays_same_event_idempotently_after_lease(self):
        before = self.core.inspect_history()
        view = self.core.current_view()

        class ProcessStopped(BaseException):
            pass

        class AcceptedThenStopped(RecordingSink):
            def deliver(self, event):
                super().deliver(event)
                raise ProcessStopped()

        sink = AcceptedThenStopped()
        with self.assertRaises(ProcessStopped):
            self.delivery_core(sink)
        pending = self.core.inspect_event_delivery()[0]
        self.assertEqual(pending['state'], 'delivering')
        self.assertEqual(pending['attempts'][0]['outcome'], 'started')
        self.assertEqual(self.core.inspect_history(), before)
        self.assertEqual(self.core.current_view(), view)
        restarted = self.delivery_core(RecordingSink())
        self.assertEqual(restarted.dispatch_events(), {})
        self.assertEqual(restarted.inspect_event_delivery(), [pending])
        self.clock.value += 30
        # Same durable idempotency store, but the process no longer crashes.
        recovered_sink = RecordingSink()
        recovered_sink.applied = sink.applied
        restarted.projection_sink = recovered_sink
        restarted.dispatch_events()
        self.assertEqual(recovered_sink.received, sink.received)
        self.assertEqual(len(recovered_sink.applied), 1)
        job = restarted.inspect_event_delivery()[0]
        self.assertEqual([a['outcome'] for a in job['attempts']], ['indeterminate', 'accepted'])
        self.assertEqual(job['state'], 'delivered')
        self.assertEqual(restarted.inspect_history(), before)
        self.assertEqual(restarted.current_view(), view)
        self.assertEqual(restarted.accept(self.payload), self.result)

    def test_storage_failure_recording_acceptance_preserves_commit_and_replays(self):
        before = self.core.inspect_history()
        view = self.core.current_view()
        with sqlite3.connect(self.flow.config.database) as db:
            db.executescript("CREATE TRIGGER fail_event_result BEFORE UPDATE ON event_delivery "
                             "WHEN NEW.state='delivered' BEGIN SELECT RAISE(ABORT, 'result unavailable'); END;")
        sink = RecordingSink()
        core = self.delivery_core(sink)
        self.assertEqual(sink.received, before['events'])
        self.assertEqual(core.inspect_event_delivery()[0]['state'], 'delivering')
        self.assertEqual(core.accept(self.payload), self.result)
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.current_view(), view)
        with sqlite3.connect(self.flow.config.database) as db:
            db.execute('DROP TRIGGER fail_event_result')
        self.clock.value += 30
        restarted = self.delivery_core(sink)
        self.assertEqual(sink.received, before['events'] * 2)
        self.assertEqual(len(sink.applied), 1)
        self.assertEqual(restarted.inspect_event_delivery()[0]['state'], 'delivered')
        self.assertEqual(restarted.inspect_history(), before)

    def test_recovery_keeps_original_payload_when_sink_mutates_its_copy(self):
        before = self.core.inspect_history()

        class MutatingSink(RecordingSink):
            def deliver(self, event):
                self.received.append(copy.deepcopy(event))
                event['id'] = 'changed'
                event['assertion_ids'].clear()
                return False

        sink = MutatingSink()
        core = self.delivery_core(sink)
        self.assertEqual(core.inspect_event_delivery()[0]['attempts'][0]['error_type'], 'SinkNotAccepted')
        self.assertEqual(core.inspect_history(), before)
        self.clock.value += 2
        sink = RecordingSink()
        restarted = self.delivery_core(sink)
        self.assertEqual(sink.received, before['events'])
        self.assertEqual(restarted.inspect_history(), before)

    def test_correction_event_names_existing_target_and_both_assertions(self):
        before = self.core.inspect_history()
        exposed = self.flow.next_claim(before, 4, 'corrects')
        sink = RecordingSink()
        core, _, result = self.flow.accept_items(exposed, revision=1, projection_sink=sink)
        self.assertEqual(result['status'], 'completed')
        history = core.inspect_history()
        event = sink.received[-1]
        self.assertEqual(event['entity_ids'], [before['entities'][0]['id']])
        self.assertEqual(set(event['assertion_ids']), {claim['id'] for claim in history['claims']})
        self.assertEqual(event['semantic_revision'], core.current_view()['revision'])

    def test_live_lease_prevents_overlap_and_expired_results_cannot_overwrite_winner(self):
        before = self.core.inspect_history()
        winner = RecordingSink()
        test = self

        class SlowSink(RecordingSink):
            def deliver(self, event):
                self.received.append(copy.deepcopy(event))
                peer = test.delivery_core(winner)
                test.assertEqual(winner.received, [])
                test.assertEqual(peer.dispatch_events(), {})
                test.clock.value += 30
                peer.dispatch_events()
                test.assertEqual(winner.received, before['events'])
                return False  # This late negative result belongs to the expired lease.

        core = self.delivery_core(SlowSink())
        job = core.inspect_event_delivery()[0]
        self.assertEqual(job['state'], 'delivered')
        self.assertEqual([a['outcome'] for a in job['attempts']], ['indeterminate', 'accepted'])
        self.assertEqual(core.inspect_history(), before)

    def test_sink_failure_after_new_commit_is_not_semantic_rejection_or_storage_rollback(self):
        exposed = self.flow.expose()
        sink = FailingSink()
        # Drain the old event first so this test observes only the new semantic effect.
        self.delivery_core(RecordingSink())
        core, payload, result = self.flow.accept_items(exposed, revision=1, projection_sink=sink)
        self.assertEqual(result['status'], 'completed')
        history = core.inspect_history()
        view = core.current_view()
        self.assertEqual(history['revision'], 2)
        self.assertEqual(len(history['commits']), 2)
        self.assertEqual(len(history['events']), 2)
        self.assertEqual({i['outcome'] for i in core.inspect(exposed['inbound']['id'])['grounding_items']}, {'accepted'})
        trace = core.inspect(result['communication_id'])['trace']
        self.assertEqual(trace['validation_result'], 'accepted')
        self.assertEqual(trace['commit_result'], 'committed')
        self.assertEqual(core.inspect_event_delivery()[-1]['state'], 'pending')
        core.model = fixtures.MustNotRunModel()
        self.assertEqual(core.accept(payload), result)
        self.assertEqual(core.inspect_history(), history)
        self.assertEqual(core.current_view(), view)
        self.assertEqual(len(sink.received), 1)

    def test_candidate_rejection_staleness_and_semantic_storage_error_emit_nothing(self):
        sink = RecordingSink()
        self.delivery_core(sink)
        sink.received.clear()
        for scenario, expected in (('candidate', 'completed'), ('rejected', 'rejected'),
                                   ('stale', 'reprocess_required'), ('storage', 'retryable')):
            with self.subTest(scenario=scenario):
                exposed = self.flow.expose()
                before = self.core.inspect_history()
                view = self.core.current_view()
                items = exposed['grounding_items']
                def operations(inbound):
                    return tuple(fixtures.GroundingResolutionOperation(item['id'], 'p', inbound.id,
                                                                        'explicit', 'accepted') for item in items)
                if scenario == 'storage':
                    with sqlite3.connect(self.flow.config.database) as db:
                        db.executescript("CREATE TRIGGER fail_semantic_event BEFORE INSERT ON semantic_outbox "
                                         "BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END;")
                core = Core(self.flow.config, projection_sink=sink,
                            model=fixtures.ProposalModel(() if scenario == 'candidate' else operations,
                                intent='candidate' if scenario == 'candidate' else 'semantic_commit',
                                semantic_revision=0 if scenario == 'stale' else 1),
                            contract_provider=fixtures.ContractProvider(self.flow.contract))
                payload = self.flow.message(text='No' if scenario == 'rejected' else 'Yes',
                                            reply_to=exposed['outbound']['id'])
                result = core.accept(payload)
                self.assertEqual(result['status'], expected)
                self.assertEqual(core.inspect_history(), before)
                self.assertEqual(core.current_view(), view)
                self.assertEqual(sink.received, [])
                self.assertEqual(len(core.inspect_event_delivery()), 1)
                self.assertEqual({i['outcome'] for i in core.inspect(exposed['inbound']['id'])['grounding_items']}, {'pending'})
                trace = core.inspect(result['communication_id'])['trace']
                if scenario == 'storage':
                    self.assertEqual(trace['failure_stage'], 'storage')
                    self.assertEqual(trace['commit_result'], 'retryable')
                    with sqlite3.connect(self.flow.config.database) as db:
                        db.execute('DROP TRIGGER fail_semantic_event')
                else:
                    self.assertEqual(trace['validation_result'], 'stale' if scenario == 'stale' else
                                     'rejected' if scenario == 'rejected' else 'accepted')

    def test_retry_after_contract_upgrade_keeps_original_event_contract(self):
        before = self.core.inspect_history()
        self.delivery_core(FailingSink())
        self.clock.value += 2
        sink = RecordingSink()
        upgraded = replace(self.flow.config, contract_version='v2',
                           projection=projections.ProjectionConfig(backoff_seconds=2))
        core = Core(upgraded, projection_sink=sink, delivery_clock=self.clock,
                    model=fixtures.MustNotRunModel())
        self.assertEqual(sink.received, before['events'])
        self.assertEqual(sink.received[0]['contract_version'], 'development-v1')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.accept(self.payload), self.result)

    def test_reference_outbox_is_dispatched_without_grounding_or_model_work(self):
        before = self.core.inspect_history()
        target_id = before['entities'][0]['id']
        config = replace(self.flow.config, reference_state=ReferenceConfig('application', Path('unused')))

        class Provider:
            provider_id = 'application'
            def get(self):
                return (ReferenceAssertion(target_id, 'count', 4, 'records/1', observed_version='v1'),)

        sink = RecordingSink()
        core = Core(config, projection_sink=sink, reference_provider=Provider(),
                    model=fixtures.MustNotRunModel(),
                    contract_provider=fixtures.ContractProvider(self.flow.contract))
        history = core.inspect_history()
        self.assertEqual(sink.received, history['events'])
        event = sink.received[-1]
        self.assertEqual(event['event_type'], 'reference_committed')
        self.assertEqual(event['entity_ids'], [target_id])
        self.assertEqual(event['assertion_ids'], [history['reference_assertions'][0]['id']])
        self.assertEqual(event['grounding_item_ids'], [])
        self.assertEqual(history['grounding_items'], before['grounding_items'])
        self.assertEqual(len(core.current_view()['assertion_sets'][0]['heads']), 2)
        core.refresh_references()
        self.assertEqual(len(sink.received), 2)

    def test_static_projection_bootstrap_validation_and_local_sink_idempotency(self):
        path = Path(self.flow.directory.name) / 'config.json'
        base = {'database': 'core.sqlite3', 'identities': self.flow.config.identities}
        for projection in (None, [], {'adapter': 'webhook'}, {'unexpected': 1},
                           {'max_attempts': True}, {'max_attempts': 0}, {'max_attempts': 101},
                           {'backoff_seconds': 0}, {'lease_seconds': float('inf')},
                           {'max_backoff_seconds': 0.5}):
            with self.subTest(projection=projection):
                path.write_text(json.dumps({**base, 'projection': projection}))
                with self.assertRaises(ValueError):
                    Bootstrap.from_file(path)
        path.write_text(json.dumps({**base, 'projection': {'max_attempts': 2, 'backoff_seconds': 2}}))
        config = Bootstrap.from_file(path)
        core = Core(config, model=fixtures.MustNotRunModel(),
                    contract_provider=fixtures.ContractProvider(self.flow.contract))
        event = self.core.inspect_history()['events'][0]
        sink = projections.DevelopmentProjectionSink(config.database)
        self.assertTrue(sink.deliver(event))
        self.assertTrue(sink.deliver(event))
        with self.assertRaises(ValueError):
            sink.deliver({**event, 'semantic_commit_id': 'different'})
        self.assertEqual(core.inspect_event_delivery()[0]['state'], 'delivered')
        self.assertEqual(len(core.inspect_event_delivery()[0]['attempts']), 1)

    def test_existing_outbox_delivers_original_trusted_event_once(self):
        before = self.core.inspect_history()
        trusted = self.core.current_view()
        sink = RecordingSink()
        core = Core(self.flow.config, projection_sink=sink,
                    model=fixtures.MustNotRunModel(),
                    contract_provider=fixtures.ContractProvider(self.flow.contract))
        core.dispatch_events()
        self.assertEqual(sink.received, before['events'])
        event = sink.received[0]
        self.assertEqual(event['semantic_commit_id'], before['commits'][0]['id'])
        self.assertEqual(event['contract_version'], self.flow.contract.version)
        self.assertEqual(event['semantic_revision'], trusted['revision'])
        self.assertEqual(event['entity_ids'], [before['entities'][0]['id']])
        self.assertEqual(event['context_ids'], [before['contexts'][0]['id']])
        self.assertEqual(event['assertion_ids'], [before['claims'][0]['id']])
        self.assertEqual(event['grounding_item_ids'], [item['id'] for item in before['grounding_items']])
        self.assertNotIn('proposal', event)
        self.assertNotIn('trace', event)
        self.assertNotIn('candidate_claims', event)
        self.assertEqual(core.accept(self.payload), self.result)
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.current_view(), trusted)
        self.assertEqual(len(sink.received), 1)
        delivery = core.inspect_event_delivery()
        self.assertEqual(delivery[0]['state'], 'delivered')
        self.assertEqual([a['outcome'] for a in delivery[0]['attempts']], ['accepted'])
