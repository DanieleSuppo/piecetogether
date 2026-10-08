"""Concurrent processing scenarios at the Core boundary, without live providers."""

import base64
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event

import test_history as fixtures
from piecetogether.artifact_store import LocalArtifactStore
from piecetogether.core import Core
from piecetogether.proposals import (
    ArtifactOperation, ContextOperation, EntityOperation, GroundingPlanOperation,
    GroundingResolutionOperation, SemanticProposal,
)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.HistoryTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def core(self, model, **kwargs):
        return Core(self.flow.config, model=model,
                    contract_provider=fixtures.ContractProvider(self.flow.contract), **kwargs)

    def test_parallel_interpretations_commit_once_and_stale_work_reprocesses_without_clock_ordering(self):
        exposures = [self.flow.expose(), self.flow.expose()]
        barrier = Barrier(2)
        packs = []

        class CommitModel:
            def __init__(model, items, synchronize):
                model.items = items
                model.synchronize = synchronize

            def propose(model, inbound, version, context_pack):
                packs.append(context_pack)
                if model.synchronize:
                    barrier.wait(timeout=5)
                return SemanticProposal(1, version, inbound.id, (), 'Accepted', tuple(
                    GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                    for item in model.items
                ), intent='semantic_commit', semantic_revision=context_pack.semantic_revision)

        cores = [self.core(CommitModel(exposure['grounding_items'], True)) for exposure in exposures]
        payloads = [self.flow.message(reply_to=exposure['outbound']['id'], sent_at=sent_at)
                    for exposure, sent_at in zip(exposures, ('2099-01-01T00:00:00Z', '1999-01-01T00:00:00Z'))]
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(core.accept, payload) for core, payload in zip(cores, payloads)]
            results = [future.result(timeout=10) for future in futures]
        self.assertCountEqual([result['status'] for result in results], ['completed', 'reprocess_required'])
        self.assertEqual([pack.semantic_revision for pack in packs], [0, 0])
        history = cores[0].inspect_history()
        self.assertEqual(history['revision'], 1)
        for name in ('entities', 'contexts', 'claims', 'commits', 'events'):
            self.assertEqual(len(history[name]), 1)
        self.assertEqual(len(history['grounding_items']), 3)
        self.assertEqual(len(cores[0].current_view()['assertion_sets']), 1)
        loser = next(index for index, result in enumerate(results) if result['status'] == 'reprocess_required')
        stale = cores[loser].inspect(results[loser]['communication_id'])
        self.assertIsNone(stale['outbound'])
        self.assertEqual(stale['trace']['validation_result'], 'stale')
        self.assertEqual({item['outcome'] for item in cores[loser].inspect(
            exposures[loser]['inbound']['id'])['grounding_items']}, {'pending'})
        fresh = self.core(CommitModel(exposures[loser]['grounding_items'], False))
        self.assertEqual(fresh.accept(payloads[loser])['status'], 'completed')
        self.assertEqual(packs[-1].semantic_revision, 1)
        history = fresh.inspect_history()
        self.assertEqual(history['revision'], 2)
        self.assertEqual(len(history['commits']), 2)
        self.assertEqual(len(history['events']), 2)
        self.assertEqual(len(history['claims']), 2)
        for index, payload in enumerate(payloads):
            self.assertEqual(fresh.inspect(results[index]['communication_id'])['inbound']['sent_at'], payload['sent_at'])

    def enable_artifacts(self):
        self.flow.contract = replace(self.flow.contract, artifact_types={'Record': {
            'roles': ['persistent-domain-artifact'], 'persistence': 'required',
            'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
            'supersession': False,
        }})
        self.flow.config = replace(self.flow.config, artifact_store=Path(self.flow.directory.name) / 'artifacts')

    def test_rejected_mixed_commit_leaves_grounding_and_artifact_stage_unlinked(self):
        self.enable_artifacts()
        exposure = self.flow.expose()
        claim = next(item for item in exposure['grounding_items'] if item['candidate_id'] == 'count')

        class MixedModel:
            def propose(model, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'Invalid dependency', (
                    ArtifactOperation('file', 'Record', ('persistent-domain-artifact',), action='persist'),
                    GroundingResolutionOperation(claim['id'], 'p', inbound.id, 'explicit', 'accepted'),
                ), intent='semantic_commit', semantic_revision=context_pack.semantic_revision)

        core = self.core(MixedModel())
        before = core.inspect_history()
        view = core.current_view()
        payload = self.flow.message(reply_to=exposure['outbound']['id'], attachments=[{
            'id': 'file', 'content_base64': base64.b64encode(b'evidence').decode(),
        }])
        result = core.accept(payload)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'], ['ungrounded_dependency'])
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.current_view(), view)
        self.assertEqual({item['outcome'] for item in core.inspect(exposure['inbound']['id'])['grounding_items']}, {'pending'})
        self.assertEqual(len(core.cleanup_artifact_staging(0)), 1)
        self.assertEqual(self.core(fixtures.MustNotRunModel()).accept(payload), result)

    def test_exception_rolls_back_mixed_commit_and_retry_commits_all_effects_once(self):
        self.enable_artifacts()
        exposure = self.flow.expose()

        class MixedModel:
            def propose(model, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'Accepted', (
                    ArtifactOperation('file', 'Record', ('persistent-domain-artifact',), action='persist'),
                    *(GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                      for item in exposure['grounding_items']),
                ), intent='semantic_commit', semantic_revision=context_pack.semantic_revision)

        core = self.core(MixedModel())
        before = core.inspect_history()
        view = core.current_view()
        payload = self.flow.message(reply_to=exposure['outbound']['id'], attachments=[{
            'id': 'file', 'content_base64': base64.b64encode(b'evidence').decode(),
        }])
        with sqlite3.connect(self.flow.config.database) as db:
            db.executescript("CREATE TRIGGER fail_outbox BEFORE INSERT ON semantic_outbox "
                             "BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END;")
        result = core.accept(payload)
        self.assertEqual(result['status'], 'retryable')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.current_view(), view)
        checkpoint = core.inspect(result['communication_id'])
        self.assertEqual(checkpoint['trace']['validation_result'], 'accepted')
        self.assertEqual(checkpoint['trace']['commit_result'], 'retryable')
        self.assertEqual(checkpoint['trace']['error_type'], 'IntegrityError')
        self.assertEqual({item['outcome'] for item in core.inspect(exposure['inbound']['id'])['grounding_items']}, {'pending'})
        with sqlite3.connect(self.flow.config.database) as db:
            db.execute('DROP TRIGGER fail_outbox')
        completed = core.accept(payload)
        self.assertEqual(completed['status'], 'completed')
        history = core.inspect_history()
        self.assertEqual(history['revision'], 1)
        self.assertEqual(len(history['commits']), 1)
        self.assertEqual(len(history['events']), 1)
        self.assertEqual(len(history['entities']), 1)
        self.assertEqual(len(history['claims']), 1)
        self.assertEqual(len(history['grounding_items']), 3)
        self.assertEqual(core.artifact_bytes(history['artifacts'][0]['id']), b'evidence')
        self.assertEqual(self.core(fixtures.MustNotRunModel()).accept(payload), completed)
        self.assertEqual(core.inspect_history(), history)
        self.assertEqual(core.cleanup_artifact_staging(0), [])

    def test_remote_artifact_io_does_not_hold_semantic_locks(self):
        self.enable_artifacts()
        for boundary in ('stage', 'finalize'):
            with self.subTest(boundary=boundary):
                exposure = self.flow.expose()
                entered, release = Event(), Event()

                class BlockingStore(LocalArtifactStore):
                    def stage(store, content, key):
                        if boundary == 'stage':
                            entered.set()
                            if not release.wait(5):
                                raise OSError('test staging timed out')
                        return super().stage(content, key)

                    def finalize(store, staged):
                        if boundary == 'finalize':
                            entered.set()
                            if not release.wait(5):
                                raise OSError('test finalization timed out')
                        return super().finalize(staged)

                class ArtifactModel:
                    def propose(model, inbound, version, context_pack):
                        return SemanticProposal(1, version, inbound.id, (), 'Stored', (
                            ArtifactOperation('file', 'Record', ('persistent-domain-artifact',), action='persist'),
                        ), intent='semantic_commit', semantic_revision=context_pack.semantic_revision)

                class AcceptModel:
                    def propose(model, inbound, version, context_pack):
                        return SemanticProposal(1, version, inbound.id, (), 'Accepted', tuple(
                            GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                            for item in exposure['grounding_items']
                        ), intent='semantic_commit', semantic_revision=context_pack.semantic_revision)

                artifact_core = self.core(ArtifactModel(), artifact_store=BlockingStore(self.flow.config.artifact_store))
                other = self.core(AcceptModel())
                payload = self.flow.message(attachments=[{
                    'id': 'file', 'content_base64': base64.b64encode(boundary.encode()).decode(),
                }])
                before = other.inspect_history()['revision']
                with ThreadPoolExecutor(max_workers=2) as workers:
                    future = workers.submit(artifact_core.accept, payload)
                    try:
                        self.assertTrue(entered.wait(5))
                        trusted = workers.submit(other.accept, self.flow.message(reply_to=exposure['outbound']['id']))
                        self.assertEqual(trusted.result(timeout=3)['status'], 'completed')
                    finally:
                        release.set()
                    self.assertEqual(future.result(timeout=10)['status'], 'completed')
                history = artifact_core.inspect_history()
                self.assertEqual(history['revision'], before + 2)
                self.assertEqual(len(history['events']), before + 2)
                artifact = history['artifacts'][-1]
                self.assertEqual(artifact['availability'], 'available')
                self.assertEqual(artifact_core.artifact_bytes(artifact['id']), boundary.encode())

    def test_changed_context_is_stale_not_terminal_rejection_and_retry_uses_fresh_pack(self):
        initial, _, _ = self.flow.accept_items(self.flow.expose())
        context_id = initial.current_view()['contexts'][0]['id']
        operations = (
            ContextOperation(context_id, action='suspend'),
            GroundingPlanOperation('p', (), 'explicit', resolution_ids=(context_id,)),
        )
        concurrent_history = []
        packs = []

        class RacingModel:
            def propose(model, inbound, version, context_pack):
                packs.append(context_pack)
                other = self.core(fixtures.ProposalModel(operations))
                exposure = other.accept(self.flow.message(text=context_id))
                committed, _, result = self.flow.accept_items(other.inspect(exposure['communication_id']), revision=1)
                self.assertEqual(result['status'], 'completed')
                concurrent_history.append(committed.inspect_history())
                return SemanticProposal(1, version, inbound.id, (), 'Suspend', operations)

        core = self.core(RacingModel())
        payload = self.flow.message(text=context_id)
        stale = core.accept(payload)
        self.assertEqual(stale['status'], 'reprocess_required')
        self.assertIsNone(stale['reply'])
        self.assertEqual(core.inspect_history(), concurrent_history[0])
        self.assertEqual(core.inspect(stale['communication_id'])['grounding_items'], [])

        class FreshModel:
            def propose(model, inbound, version, context_pack):
                packs.append(context_pack)
                return SemanticProposal(1, version, inbound.id, (), 'Resume', (
                    ContextOperation(context_id, action='resume'),
                    GroundingPlanOperation('p', (), 'explicit', resolution_ids=(context_id,)),
                ))

        fresh = self.core(FreshModel())
        self.assertEqual(fresh.accept(payload)['status'], 'completed')
        self.assertEqual([pack.semantic_revision for pack in packs], [1, 2])
        checkpoint = fresh.inspect(stale['communication_id'])
        self.assertEqual([attempt['trace']['validation_result'] for attempt in checkpoint['attempts']],
                         ['stale', 'accepted'])
        self.assertEqual(fresh.inspect_history(), concurrent_history[0])
        self.assertEqual(len(checkpoint['grounding_items']), 1)

    def test_failed_or_rejected_loser_preserves_winning_candidate_and_attempt_evidence(self):
        for failure in ('exception', 'rejection'):
            with self.subTest(failure=failure):
                payload = self.flow.message(text='A new subject')
                winner = self.core(fixtures.ProposalModel((
                    EntityOperation('winner', 'Subject'),
                    GroundingPlanOperation('p', (), 'explicit', resolution_ids=('winner',)),
                )))
                completed = []

                class RacingModel:
                    def propose(model, inbound, version, context_pack):
                        completed.append(winner.accept(payload))
                        if failure == 'exception':
                            raise ValueError('losing provider failed')
                        return SemanticProposal(1, version, inbound.id, (), 'Rejected draft', (
                            EntityOperation('forbidden', 'Forbidden'),
                        ))

                core = self.core(RacingModel())
                result = core.accept(payload)
                self.assertEqual(result, completed[0])
                checkpoint = core.inspect(result['communication_id'])
                self.assertEqual(checkpoint['status'], 'completed')
                self.assertEqual([item['candidate_id'] for item in checkpoint['grounding_items']], ['winner'])
                losing, winning = checkpoint['attempts']
                self.assertEqual(losing['status'], 'superseded')
                self.assertEqual(winning['status'], 'completed')
                if failure == 'exception':
                    self.assertEqual(losing['trace']['failure_stage'], 'model')
                    self.assertEqual(losing['trace']['error_type'], 'ValueError')
                else:
                    self.assertEqual(losing['trace']['validation_result'], 'rejected')
                self.assertEqual(core.current_view()['entities'], [])
                self.assertEqual(core.inspect_history()['events'], [])

    def test_losing_candidate_cannot_replace_completed_checkpoint_or_expose_another_draft(self):
        payload = self.flow.message(text='A new subject')
        completed = []
        winner = self.core(fixtures.ProposalModel((
            EntityOperation('winner', 'Subject'),
            GroundingPlanOperation('p', (), 'explicit', resolution_ids=('winner',)),
        )))

        class RacingModel:
            def propose(model, inbound, version, context_pack):
                completed.append(winner.accept(payload))
                return SemanticProposal(1, version, inbound.id, (), 'Losing draft', (
                    EntityOperation('loser', 'Subject'),
                    GroundingPlanOperation('p', (), 'explicit', resolution_ids=('loser',)),
                ))

        core = self.core(RacingModel())
        result = core.accept(payload)
        self.assertEqual(result, completed[0])
        checkpoint = core.inspect(result['communication_id'])
        self.assertEqual([item['candidate_id'] for item in checkpoint['grounding_items']], ['winner'])
        self.assertEqual(checkpoint['proposal']['operations'][0]['id'], 'winner')
        self.assertEqual(checkpoint['outbound']['text'], completed[0]['reply'])
        self.assertEqual([attempt['attempt'] for attempt in checkpoint['attempts']], [1, 2])
        self.assertEqual(checkpoint['status'], 'completed')
        restarted = self.core(fixtures.MustNotRunModel())
        self.assertEqual(restarted.accept(payload), completed[0])
        self.assertEqual(restarted.inspect_history()['revision'], 0)


if __name__ == '__main__':
    unittest.main()
