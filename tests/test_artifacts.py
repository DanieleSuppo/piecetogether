import base64
import tempfile
import unittest
from pathlib import Path

from piecetogether.contracts import DomainContract
from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import (
    ArtifactOperation, ClaimOperation, EntityOperation, GroundingPlanOperation,
    GroundingResolutionOperation, SemanticProposal,
)


class ContractProvider:
    def __init__(self, contract):
        self.contract = contract

    def get(self, version):
        return self.contract


class ArtifactModel:
    def propose(self, inbound, version, context_pack):
        return SemanticProposal(
            1, version, inbound.id, (), 'I received the quote.',
            (ArtifactOperation('quote', 'Record', ('persistent-domain-artifact',),
                               action='persist'),),
            intent='semantic_commit', semantic_revision=0,
        )


class ArtifactTests(unittest.TestCase):
    def test_poison_publication_does_not_prevent_restart_or_healthy_publication(self):
        from piecetogether.artifact_store import LocalArtifactStore

        class SelectiveStore:
            def __init__(self, root):
                self.store = LocalArtifactStore(root)
                self.poison = None
                self.fail_once = True

            def stage(self, content, key):
                staged = self.store.stage(content, key)
                if content == b'poison':
                    self.poison = staged.reference
                return staged

            def finalize(self, staged):
                if staged.reference == self.poison and self.fail_once:
                    self.fail_once = False
                    raise OSError('temporary storage outage')
                return self.store.finalize(staged)

            def read(self, reference):
                return self.store.read(reference)

        class PersistModel:
            def __init__(self, attachment_id, revision):
                self.attachment_id = attachment_id
                self.revision = revision

            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'Stored.', (
                    ArtifactOperation(self.attachment_id, 'Record', ('persistent-domain-artifact',), action='persist'),
                ), intent='semantic_commit', semantic_revision=self.revision)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', artifact_types={'Record': {
                'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                'supersession': False,
            }})
            store = SelectiveStore(config.artifact_store)
            poison = Core(config, model=PersistModel('poison', 0), artifact_store=store,
                          contract_provider=ContractProvider(contract)).accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'poison', 'text': 'P',
                'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'poison', 'content_base64': base64.b64encode(b'poison').decode()}],
            })
            self.assertEqual(poison['status'], 'retryable')
            (store.store.staging / store.poison).unlink()
            healthy = Core(config, model=PersistModel('healthy', 1), artifact_store=store,
                           contract_provider=ContractProvider(contract)).accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'healthy', 'text': 'H',
                'sent_at': '2026-10-10T12:01:00Z',
                'attachments': [{'id': 'healthy', 'content_base64': base64.b64encode(b'healthy').decode()}],
            })
            self.assertEqual(healthy['status'], 'completed')
            artifacts = Core(config, artifact_store=store, contract_provider=ContractProvider(contract)).inspect_history()['artifacts']
            self.assertEqual([artifact['availability'] for artifact in artifacts], ['pending', 'available'])
            self.assertEqual(artifacts[0]['publication_failure']['error_type'], 'OSError')
            self.assertGreaterEqual(artifacts[0]['publication_failure']['attempts'], 2)

    def test_attachment_candidate_claim_can_ground_without_persisting_a_domain_artifact(self):
        class CandidateModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'I read the attachment.', (
                    EntityOperation('subject', 'Subject'),
                    ClaimOperation('note', 'subject', 'note', 'observed', 'p', artifact_id='evidence'),
                    GroundingPlanOperation('p', ('note',), 'explicit', ('subject',)),
                ))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', entity_types={'Subject': {'creation': True, 'attributes': {}}},
                grounding_policies={'p': {'acceptance': ['explicit']}},
                claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'p'}})
            provider = ContractProvider(contract)
            candidate_core = Core(config, model=CandidateModel(), contract_provider=provider)
            candidate = candidate_core.accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'candidate', 'text': 'See attachment',
                'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'evidence', 'content_base64': base64.b64encode(b'evidence').decode()}],
            })
            exposed = candidate_core.inspect(candidate['communication_id'])

            class AcceptModel:
                def propose(self, inbound, version, context_pack):
                    return SemanticProposal(1, version, inbound.id, (), 'Confirmed.', tuple(
                        GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                        for item in exposed['grounding_items']), intent='semantic_commit', semantic_revision=0)

            completed = Core(config, model=AcceptModel(), contract_provider=provider).accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'accepted', 'text': 'Yes',
                'sent_at': '2026-10-10T12:01:00Z', 'reply_to': exposed['outbound']['id'],
            })
            self.assertEqual(completed['status'], 'completed')
            provenance = Core(config, contract_provider=provider).inspect_history()['claims'][0]['provenance']
            self.assertEqual(provenance['artifact_ingress']['attachment_id'], 'evidence')
            self.assertNotIn('artifact_id', provenance)

    def test_cross_actor_cannot_forge_an_artifact_claim_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1', 'development:s2': 'a2'},
                               artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', entity_types={'Subject': {'creation': True, 'attributes': {}}},
                grounding_policies={'p': {'acceptance': ['explicit']}},
                claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'p'}},
                artifact_types={'Record': {'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                    'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'}, 'supersession': False}})
            provider = ContractProvider(contract)
            owner = Core(config, model=ArtifactModel(), contract_provider=provider).accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'owner', 'text': 'Q',
                'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}],
            })
            artifact_id = Core(config, contract_provider=provider).inspect_history()['artifacts'][0]['id']

            class ForgingModel:
                def propose(self, inbound, version, context_pack):
                    return SemanticProposal(1, version, inbound.id, (), 'Unsafe.', (
                        EntityOperation('subject', 'Subject'),
                        ClaimOperation('note', 'subject', 'note', 'forged', 'p', artifact_id=artifact_id),
                    ))

            forged = Core(config, model=ForgingModel(), contract_provider=provider).accept({
                'channel': 'development', 'sender': 's2', 'idempotency_key': 'forge', 'text': 'Q',
                'sent_at': '2026-10-10T12:01:00Z',
            })
            self.assertEqual(owner['status'], 'completed')
            self.assertEqual(forged['status'], 'rejected')
            self.assertEqual(Core(config, contract_provider=provider).inspect(forged['communication_id'])
                             ['trace']['validation_reasons'], ['invalid_artifact_provenance'])

    def test_cleanup_releases_abandoned_cleaning_reservation_after_restart(self):
        class FailingModel:
            def propose(self, *args):
                raise OSError('model interrupted after staging')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            core = Core(config, model=FailingModel())
            payload = {'channel': 'development', 'sender': 's1', 'idempotency_key': 'orphan', 'text': 'Q',
                       'sent_at': '2026-10-10T12:00:00Z',
                       'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}]}
            self.assertEqual(core.accept(payload)['status'], 'retryable')
            with __import__('sqlite3').connect(config.database) as db:
                reference = db.execute('SELECT stage_ref FROM artifact_stages').fetchone()[0]
                db.execute("UPDATE artifact_stages SET state='cleaning'")
            restarted = Core(config)
            with __import__('sqlite3').connect(config.database) as db:
                self.assertEqual(db.execute('SELECT state FROM artifact_stages').fetchone()[0], 'staged')
            self.assertEqual(restarted.cleanup_artifact_staging(0), [reference])
            with __import__('sqlite3').connect(config.database) as db:
                self.assertEqual(db.execute('SELECT state FROM artifact_stages').fetchone()[0], 'deleted')

    def test_cleanup_unlink_error_releases_reservation_for_later_maintenance(self):
        from piecetogether.artifact_store import LocalArtifactStore

        class FailingRemoveStore:
            def __init__(self, root):
                self.store = LocalArtifactStore(root)
            def stage(self, content, key):
                return self.store.stage(content, key)
            def finalize(self, staged):
                return self.store.finalize(staged)
            def read(self, reference):
                return self.store.read(reference)
            def remove_staged(self, reference, age):
                raise OSError('unlink temporarily unavailable')

        class FailingModel:
            def propose(self, *args):
                raise OSError('stop after staging')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            store = FailingRemoveStore(config.artifact_store)
            core = Core(config, model=FailingModel(), artifact_store=store)
            payload = {'channel': 'development', 'sender': 's1', 'idempotency_key': 'unlink', 'text': 'Q',
                       'sent_at': '2026-10-10T12:00:00Z',
                       'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}]}
            self.assertEqual(core.accept(payload)['status'], 'retryable')
            for age in (float('nan'), float('inf'), -1, True):
                with self.subTest(age=age), self.assertRaises(ValueError):
                    core.cleanup_artifact_staging(age)
            self.assertEqual(core.cleanup_artifact_staging(0), [])
            with __import__('sqlite3').connect(config.database) as db:
                self.assertEqual(db.execute('SELECT state FROM artifact_stages').fetchone()[0], 'staged')

    def test_cleanup_removes_unknown_orphan_without_racing_staging_registration(self):
        import os
        import threading
        from piecetogether.artifact_store import LocalArtifactStore

        class FailingModel:
            def propose(self, *args):
                raise OSError('stop after staging')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            store = LocalArtifactStore(config.artifact_store)
            unknown = store.staging / ('f' * 32)
            unknown.write_bytes(b'orphan')
            os.utime(unknown, (0, 0))
            core = Core(config, model=FailingModel(), artifact_store=store)
            self.assertEqual(core.cleanup_artifact_staging(0), [unknown.name])
            self.assertFalse(unknown.exists())

            completed = threading.Event()
            payload = {'channel': 'development', 'sender': 's1', 'idempotency_key': 'overlap', 'text': 'Q',
                       'sent_at': '2026-10-10T12:00:00Z',
                       'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}]}
            with store.maintenance_lock():
                worker = threading.Thread(target=lambda: (core.accept(payload), completed.set()))
                worker.start()
                self.assertFalse(completed.wait(.05))
            worker.join(2)
            self.assertTrue(completed.is_set())
            with __import__('sqlite3').connect(config.database) as db:
                reference = db.execute('SELECT stage_ref FROM artifact_stages').fetchone()[0]
            self.assertEqual(core.cleanup_artifact_staging(0), [reference])

    def test_cleanup_reservation_prevents_a_delayed_commit_from_linking_deleted_bytes(self):
        class DelayedCommitModel:
            def __init__(self):
                self.core = None

            def propose(self, inbound, version, context_pack):
                assert self.core is not None
                self.asserted_cleanup = self.core.cleanup_artifact_staging(0)
                return SemanticProposal(1, version, inbound.id, (), 'Stored.', (
                    ArtifactOperation('quote', 'Record', ('persistent-domain-artifact',), action='persist'),
                ), intent='semantic_commit', semantic_revision=0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', artifact_types={'Record': {
                'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                'supersession': False,
            }})
            model = DelayedCommitModel()
            core = Core(config, model=model, contract_provider=ContractProvider(contract))
            model.core = core
            result = core.accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'quote', 'text': 'Q',
                'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'quote', 'content_base64': base64.b64encode(b'quote').decode()}],
            })
            self.assertEqual(result['status'], 'rejected')
            self.assertTrue(model.asserted_cleanup)
            self.assertEqual(core.inspect_history()['artifacts'], [])

    def test_attachment_and_store_failures_are_rejected_before_trusted_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', artifact_types={'Record': {
                'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                'supersession': False,
            }})
            core = Core(config, contract_provider=ContractProvider(contract))
            valid = {'channel': 'development', 'sender': 's1', 'idempotency_key': 'bad', 'text': 'Q',
                     'sent_at': '2026-10-10T12:00:00Z'}
            duplicate = [{'id': 'same', 'content_base64': 'YQ=='}, {'id': 'same', 'content_base64': 'Yg=='}]
            oversized = [{'id': str(index), 'content_base64': 'YQ=='} for index in range(33)]
            for attachments in (duplicate, oversized):
                with self.subTest(attachments=len(attachments)), self.assertRaises(ValueError):
                    core.accept({**valid, 'attachments': attachments})
            self.assertEqual(core.inspect_history()['artifacts'], [])

            from piecetogether.artifact_store import StagedContent
            class WrongEvidenceStore:
                def stage(self, content, key):
                    return StagedContent('not-a-core-reference', '0' * 64, len(content))
                def finalize(self, staged):
                    raise AssertionError('invalid stage evidence must not finalize')
                def read(self, reference):
                    raise AssertionError('invalid stage evidence must not read')

            failed = Core(config, model=ArtifactModel(), artifact_store=WrongEvidenceStore(),
                          contract_provider=ContractProvider(contract)).accept({
                **valid, 'idempotency_key': 'wrong-store',
                'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}],
            })
            self.assertEqual(failed['status'], 'retryable')
            self.assertEqual(core.inspect_history()['artifacts'], [])

            class FailingStageStore(WrongEvidenceStore):
                def stage(self, content, key):
                    raise OSError('storage unavailable')

            staged_failure = Core(config, model=ArtifactModel(), artifact_store=FailingStageStore(),
                                  contract_provider=ContractProvider(contract)).accept({
                **valid, 'idempotency_key': 'stage-failure',
                'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}],
            })
            self.assertEqual(staged_failure['status'], 'retryable')
            self.assertEqual(core.inspect_history()['artifacts'], [])

    def test_artifact_commit_rolls_back_on_outbox_failure_and_rejects_stale_revision(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'}, artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', artifact_types={'Record': {
                'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                'supersession': False,
            }})
            provider = ContractProvider(contract)
            Core(config, contract_provider=provider)
            with sqlite3.connect(config.database) as db:
                db.executescript("""
                    CREATE TRIGGER fail_artifact_outbox BEFORE INSERT ON semantic_outbox
                    BEGIN SELECT RAISE(ABORT, 'outbox unavailable'); END;
                """)
            payload = {'channel': 'development', 'sender': 's1', 'idempotency_key': 'rollback', 'text': 'Q',
                       'sent_at': '2026-10-10T12:00:00Z',
                       'attachments': [{'id': 'quote', 'content_base64': 'cXVvdGU='}]}
            failed = Core(config, model=ArtifactModel(), contract_provider=provider).accept(payload)
            self.assertEqual(failed['status'], 'retryable')
            self.assertEqual(Core(config, contract_provider=provider).inspect_history()['artifacts'], [])
            with sqlite3.connect(config.database) as db:
                db.execute('DROP TRIGGER fail_artifact_outbox')
            self.assertEqual(Core(config, model=ArtifactModel(), contract_provider=provider).accept(payload)['status'], 'completed')

            class StaleModel:
                def propose(self, inbound, version, context_pack):
                    return SemanticProposal(1, version, inbound.id, (), 'Stored.', (
                        ArtifactOperation('late', 'Record', ('persistent-domain-artifact',), action='persist'),
                    ), intent='semantic_commit', semantic_revision=0)

            stale = Core(config, model=StaleModel(), contract_provider=provider).accept({
                **payload, 'idempotency_key': 'stale',
                'attachments': [{'id': 'late', 'content_base64': 'bGF0ZQ=='}],
            })
            self.assertEqual(stale['status'], 'reprocess_required')
            self.assertEqual(len(Core(config, contract_provider=provider).inspect_history()['artifacts']), 1)

    def test_local_store_recovers_partial_key_and_rejects_conflicting_content(self):
        from piecetogether.artifact_store import LocalArtifactStore
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(Path(directory))
            key = 'communication:attachment'
            key_path = store.keys / hashlib.sha256(key.encode()).hexdigest()
            staged = store.stage(b'bytes', key)
            key_path.write_text('partial')
            self.assertEqual(store.stage(b'bytes', key), staged)
            self.assertTrue((store.staging / staged.reference).exists())
            with self.assertRaises(ValueError):
                store.stage(b'changed', key)

    def test_failed_finalization_keeps_content_unavailable_until_a_model_free_retry(self):
        class FlakyStore:
            def __init__(self, store):
                self.store = store
                self.fail = True

            def stage(self, content, key):
                return self.store.stage(content, key)

            def finalize(self, staged):
                if self.fail:
                    self.fail = False
                    raise OSError('temporary storage outage')
                return self.store.finalize(staged)

            def read(self, reference):
                return self.store.read(reference)

            def cleanup(self, preserved, age):
                return self.store.cleanup(preserved, age)

            def remove_staged(self, reference, age):
                return self.store.remove_staged(reference, age)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'},
                               artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', artifact_types={
                'Record': {'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                           'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                           'supersession': False},
            })
            from piecetogether.artifact_store import LocalArtifactStore
            store = FlakyStore(LocalArtifactStore(config.artifact_store))
            payload = {
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'quote-1',
                'text': 'Attached quote', 'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'quote', 'content_base64': base64.b64encode(b'quote bytes').decode()}],
            }
            core = Core(config, model=ArtifactModel(), artifact_store=store,
                        contract_provider=ContractProvider(contract))
            first = core.accept(payload)
            self.assertEqual(first['status'], 'retryable')
            self.assertEqual(core.inspect_history()['artifacts'][0]['availability'], 'pending')
            self.assertFalse(core.current_view()['artifacts'][0]['available'])
            self.assertEqual(core.cleanup_artifact_staging(0), [])

            class MustNotRun:
                def propose(self, *args):
                    raise AssertionError('a linked Artifact must not invoke the model again')

            recovered = Core(config, model=MustNotRun(), artifact_store=store,
                             contract_provider=ContractProvider(contract)).accept(payload)
            self.assertEqual(recovered['status'], 'completed')
            completed_core = Core(config, artifact_store=store, contract_provider=ContractProvider(contract))
            artifact = completed_core.inspect_history()['artifacts'][0]
            self.assertEqual(artifact['availability'], 'available')
            self.assertEqual(completed_core.cleanup_artifact_staging(0), [])
            self.assertEqual(completed_core.artifact_bytes(artifact['id']), b'quote bytes')

    def test_artifact_derived_claim_keeps_artifact_provenance_through_grounding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'},
                               artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1',
                entity_types={'Subject': {'creation': True, 'attributes': {}}},
                grounding_policies={'p': {'acceptance': ['explicit']}},
                claim_concepts={'note': {'target_types': ['Subject'], 'value': {'type': 'string'},
                                         'grounding_policy': 'p'}},
                artifact_types={'Record': {
                    'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                    'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                    'supersession': False,
                }})
            provider = ContractProvider(contract)
            first = Core(config, model=ArtifactModel(), contract_provider=provider).accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'quote-1',
                'text': 'Attached quote', 'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'quote', 'content_base64': base64.b64encode(b'quote bytes').decode()}],
            })
            artifact_id = Core(config, contract_provider=provider).inspect_history()['artifacts'][0]['id']

            class CandidateModel:
                def propose(self, inbound, version, context_pack):
                    return SemanticProposal(1, version, inbound.id, (), 'I read the quote.', (
                        EntityOperation('subject', 'Subject'),
                        ClaimOperation('note', 'subject', 'note', 'reviewed', 'p', artifact_id=artifact_id),
                        GroundingPlanOperation('p', ('note',), 'explicit', ('subject',)),
                    ))

            candidate_core = Core(config, model=CandidateModel(), contract_provider=provider)
            candidate = candidate_core.accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'note-1',
                'text': 'Review it', 'sent_at': '2026-10-10T12:01:00Z',
            })
            exposed = candidate_core.inspect(candidate['communication_id'])

            class AcceptModel:
                def propose(self, inbound, version, context_pack):
                    return SemanticProposal(1, version, inbound.id, (), 'Confirmed.', tuple(
                        GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                        for item in exposed['grounding_items']), intent='semantic_commit', semantic_revision=1)

            completed = Core(config, model=AcceptModel(), contract_provider=provider).accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'note-2',
                'text': 'Yes', 'sent_at': '2026-10-10T12:02:00Z',
                'reply_to': exposed['outbound']['id'],
            })
            self.assertEqual(first['status'], 'completed')
            self.assertEqual(completed['status'], 'completed')
            self.assertEqual(Core(config, contract_provider=provider).inspect_history()['claims'][0]
                             ['provenance']['artifact_id'], artifact_id)

    def test_persistent_attachment_is_staged_linked_and_finalized_without_a_channel_url(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1'},
                               artifact_store=root / 'artifacts')
            contract = DomainContract('development-v1', artifact_types={
                'Record': {
                    'roles': ['persistent-domain-artifact'], 'persistence': 'required',
                    'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                    'supersession': False,
                },
            })
            core = Core(config, model=ArtifactModel(), contract_provider=ContractProvider(contract))
            result = core.accept({
                'channel': 'development', 'sender': 's1', 'idempotency_key': 'quote-1',
                'text': 'Attached quote', 'sent_at': '2026-10-10T12:00:00Z',
                'attachments': [{'id': 'quote', 'content_base64': base64.b64encode(b'quote bytes').decode()}],
            })
            self.assertEqual(result['status'], 'completed')
            artifact = core.inspect_history()['artifacts'][0]
            self.assertEqual(artifact['availability'], 'available')
            self.assertEqual(artifact['provenance']['source_communication_id'], result['communication_id'])
            self.assertNotIn('url', str(artifact).casefold())
            self.assertEqual(core.artifact_bytes(artifact['id']), b'quote bytes')
            self.assertEqual(core.current_view()['artifacts'][0]['id'], artifact['id'])
            self.assertEqual(core.inspect_history()['events'][0]['artifact_ids'], [artifact['id']])


if __name__ == '__main__':
    unittest.main()
