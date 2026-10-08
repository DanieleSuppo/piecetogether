import base64
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from piecetogether.artifact_store import LocalArtifactStore
from piecetogether.contracts import DomainContract
from piecetogether.core import Bootstrap, Core
from piecetogether.proposals import (
    ArtifactOperation, ClaimOperation, EntityOperation, GroundingPlanOperation,
    GroundingResolutionOperation, SemanticProposal,
)


class Provider:
    def __init__(self, contract):
        self.contract = contract

    def get(self, version):
        return self.contract


class Model:
    def __init__(self, operations, revision):
        self.operations, self.revision = operations, revision

    def propose(self, inbound, version, context_pack):
        return SemanticProposal(1, version, inbound.id, (), 'Done.', self.operations,
                                intent='semantic_commit', semantic_revision=self.revision)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.config = Bootstrap(root / 'core.sqlite3', {'development:s1': 'a1', 'development:s2': 'a2'},
                                artifact_store=root / 'artifacts')
        self.rule = {'roles': ['persistent-domain-artifact'], 'persistence': 'allowed',
                     'retention': {'metadata': 'retain', 'bytes': 'retain', 'provenance': 'retain'},
                     'supersession': True}
        self.store = LocalArtifactStore(self.config.artifact_store)
        self.sequence = 0
        self.domain = {}

    def core(self, operations=(), revision=None, store=None):
        contract = DomainContract('development-v1', artifact_types={'Record': copy.deepcopy(self.rule)}, **self.domain)
        if revision is None:
            revision = Core(self.config, contract_provider=Provider(contract), artifact_store=store or self.store).inspect_history()['revision']
        return Core(self.config, model=Model(operations, revision), contract_provider=Provider(contract),
                    artifact_store=store or self.store)

    def send(self, core, attachment=None, sender='s1', key=None):
        self.sequence += 1
        payload = {'channel': 'development', 'sender': sender, 'idempotency_key': key or str(self.sequence),
                   'text': 'Document', 'sent_at': '2026-10-10T12:00:00Z'}
        if attachment:
            payload['attachments'] = [{'id': attachment, 'content_base64': base64.b64encode(attachment.encode()).decode()}]
        return core.accept(payload)

    def persist(self):
        core = self.core((ArtifactOperation('original', 'Record', ('persistent-domain-artifact',), action='persist'),))
        self.assertEqual(self.send(core, 'original')['status'], 'completed')
        return core.inspect_history()['artifacts'][-1]

    def test_supersession_preserves_predecessor_and_links_distinct_published_content(self):
        predecessor = self.persist()
        core = self.core((ArtifactOperation('replacement', 'Record', ('persistent-domain-artifact',),
                                            action='supersede', predecessor_id=predecessor['id']),))
        self.assertEqual(self.send(core, 'replacement', key='replace')['status'], 'completed')
        history = core.inspect_history()
        self.assertEqual(history['artifacts'][0], predecessor)
        successor = history['artifacts'][1]
        self.assertNotEqual(successor['id'], predecessor['id'])
        self.assertEqual(core.artifact_bytes(successor['id']), b'replacement')
        self.assertEqual(core.artifact_bytes(predecessor['id']), b'original')
        relation = history['relationships'][0]
        self.assertEqual((relation['relationship_type'], relation['source_id'], relation['target_id']),
                         ('supersedes', successor['id'], predecessor['id']))
        self.assertEqual([a['current'] for a in core.current_view()['artifacts']], [False, True])
        self.assertEqual(self.send(core, 'replacement', key='replace')['status'], 'completed')
        self.assertEqual(core.inspect_history(), history)

    def test_supersession_rejects_unknown_foreign_and_forbidden_predecessors(self):
        predecessor = self.persist()
        for target, sender, allowed in [('unknown', 's1', True), (predecessor['id'], 's2', True),
                                        (predecessor['id'], 's1', False)]:
            with self.subTest(target=target, sender=sender, allowed=allowed):
                self.rule['supersession'] = allowed
                core = self.core((ArtifactOperation('replacement', 'Record', ('persistent-domain-artifact',),
                                                    action='supersede', predecessor_id=target),))
                before = core.inspect_history()
                self.assertEqual(self.send(core, 'replacement', sender)['status'], 'rejected')
                self.assertEqual(core.inspect_history(), before)

    def deletion(self, artifact_id, reason='privacy request', revision=None, store=None):
        return self.core((ArtifactOperation(artifact_id, 'Record', ('persistent-domain-artifact',),
                                            action='delete', reason=reason),), revision, store)

    def test_deletion_applies_all_eight_independent_retention_combinations(self):
        from itertools import product
        for metadata, content, provenance in product(('retain', 'delete'), repeat=3):
            with self.subTest(metadata=metadata, content=content, provenance=provenance):
                self.rule['retention'] = dict.fromkeys(('metadata', 'bytes', 'provenance'), 'retain')
                files_before = set(self.store.persistent.iterdir())
                original = self.persist()
                content_path = (set(self.store.persistent.iterdir()) - files_before).pop()
                self.rule['retention'] = {'metadata': metadata, 'bytes': content, 'provenance': provenance}
                core = self.deletion(original['id'])
                self.assertEqual(self.send(core, key='delete-' + original['id'])['status'], 'completed')
                artifacts = [a for a in core.inspect_history()['artifacts'] if a['id'] == original['id']]
                if metadata == provenance == 'delete':
                    self.assertEqual(artifacts, [])
                else:
                    self.assertEqual(artifacts[0]['availability'], 'deleted')
                    self.assertEqual('artifact_type' in artifacts[0], metadata == 'retain')
                    self.assertEqual('provenance' in artifacts[0], provenance == 'retain')
                with self.assertRaises(KeyError):
                    core.artifact_bytes(original['id'])
                self.assertFalse(any(a['id'] == original['id'] and a['available'] for a in core.current_view()['artifacts']))
                self.assertEqual(content_path.exists(), content == 'retain')
                with sqlite3.connect(self.config.database) as db:
                    source = db.execute('SELECT communication_id, attachment_id FROM trusted_artifacts WHERE id=?',
                                        (original['id'],)).fetchone()
                    ingress = db.execute('SELECT count(*) FROM artifact_ingress WHERE communication_id=?',
                                         (original['provenance']['source_communication_id'],)).fetchone()[0]
                    stages = db.execute('SELECT count(*) FROM artifact_stages WHERE communication_id=?',
                                        (original['provenance']['source_communication_id'],)).fetchone()[0]
                if provenance == 'delete':
                    self.assertEqual(ingress, 0)
                    self.assertEqual(stages, 0)
                    if metadata == 'retain':
                        self.assertEqual(source, ('', original['id']))
                else:
                    self.assertEqual(source, (original['provenance']['source_communication_id'], 'original'))
                self.assertEqual(core.inspect_history()['revision'], core.current_view()['revision'])
                transitions = [r for r in core.inspect_history()['artifact_lifecycle'] if r['artifact_id'] == original['id']]
                self.assertEqual(bool(transitions), metadata == 'retain' or provenance == 'retain')
                if transitions:
                    self.assertEqual(transitions[0]['action'], 'delete')
                    self.assertEqual(transitions[0]['reason'], 'privacy request')
                    self.assertEqual(transitions[0]['retention'], self.rule['retention'])
                self.assertEqual(self.send(core, key='delete-' + original['id'])['status'], 'completed')
                self.assertEqual(self.core().inspect_history()['artifacts'], core.inspect_history()['artifacts'])

    def test_deletion_rejects_foreign_unknown_mismatched_policy_and_missing_reason(self):
        original = self.persist()
        for target, sender, reason in [(original['id'], 's2', 'privacy'), ('unknown', 's1', 'privacy'),
                                        (original['id'], 's1', ''), (original['id'], 's1', None)]:
            with self.subTest(target=target, sender=sender, reason=reason):
                core = self.deletion(target, reason)
                before = core.inspect_history()
                self.assertEqual(self.send(core, sender=sender)['status'], 'rejected')
                self.assertEqual(core.inspect_history(), before)
        core = self.core((ArtifactOperation(original['id'], 'Record', ('persistent-domain-artifact',),
                                            action='delete', reason='privacy', retention={'bytes': 'delete'}),))
        self.assertEqual(self.send(core)['status'], 'rejected')

    def test_byte_removal_failure_is_unavailable_and_recovers_without_model_after_restart(self):
        class FailingStore(LocalArtifactStore):
            def remove(self, reference):
                raise OSError('storage unavailable')
        original = self.persist()
        self.rule['retention']['bytes'] = 'delete'
        core = self.deletion(original['id'], store=FailingStore(self.config.artifact_store))
        result = self.send(core, key='remove-failure')
        self.assertEqual(result['status'], 'retryable')
        self.assertEqual(core.inspect_history()['artifacts'][0]['availability'], 'deleted')
        self.assertFalse(core.current_view()['artifacts'][0]['available'])
        with self.assertRaises(KeyError):
            core.artifact_bytes(original['id'])
        self.assertTrue(list(self.store.persistent.iterdir()))
        self.assertEqual(core.inspect_history()['artifact_deletions'][0]['failure']['error_type'], 'OSError')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['failure_stage'], 'artifact_delete')
        restarted = self.core()
        self.assertEqual(self.send(restarted, key='remove-failure')['status'], 'completed')
        self.assertEqual(list(self.store.persistent.iterdir()), [])
        self.assertEqual(list(self.store.staging.iterdir()), [])
        self.assertEqual(list(self.store.keys.iterdir()), [])
        self.assertEqual(len(restarted.inspect_history()['artifact_lifecycle']), 1)

    def test_delete_pending_publication_removes_stage_and_old_commit_retry_cannot_resurrect_it(self):
        class UnavailableStore(LocalArtifactStore):
            def finalize(self, staged):
                raise OSError('publication unavailable')
        store = UnavailableStore(self.config.artifact_store)
        core = self.core((ArtifactOperation('pending', 'Record', ('persistent-domain-artifact',), action='persist'),), store=store)
        self.assertEqual(self.send(core, 'pending', key='pending-source')['status'], 'retryable')
        original = core.inspect_history()['artifacts'][0]
        self.rule['retention'] = dict.fromkeys(('metadata', 'bytes', 'provenance'), 'delete')
        deletion = self.deletion(original['id'], store=store)
        self.assertEqual(self.send(deletion, key='delete-pending')['status'], 'completed')
        self.assertEqual(deletion.inspect_history()['artifacts'], [])
        self.assertEqual(list(store.staging.iterdir()), [])
        self.assertEqual(self.send(self.core(), 'pending', key='pending-source')['status'], 'completed')
        self.assertEqual(self.core().inspect_history()['artifacts'], [])
        self.assertEqual(list(store.staging.iterdir()), [])
        self.assertEqual(list(store.persistent.iterdir()), [])
        self.assertEqual(list(store.keys.iterdir()), [])

    def test_concurrent_finalization_cannot_restore_deleted_availability(self):
        import threading
        import time

        class PausedStore(LocalArtifactStore):
            block = False
            entered = threading.Event()
            release = threading.Event()

            def finalize(self, staged):
                if not self.block:
                    raise OSError('publication postponed')
                self.entered.set()
                if not self.release.wait(5):
                    raise OSError('test publication timeout')
                return super().finalize(staged)

        store = PausedStore(self.config.artifact_store)
        publication = self.core((ArtifactOperation('pending', 'Record', ('persistent-domain-artifact',), action='persist'),), store=store)
        self.assertEqual(self.send(publication, 'pending')['status'], 'retryable')
        artifact_id = publication.inspect_history()['artifacts'][0]['id']
        self.rule['retention']['bytes'] = 'delete'
        deletion = self.deletion(artifact_id, store=store)
        outcomes = []
        store.block = True
        finalizer = threading.Thread(target=lambda: publication.retry_artifact_finalization())
        deleter = threading.Thread(target=lambda: outcomes.append(self.send(deletion)))
        finalizer.start()
        try:
            self.assertTrue(store.entered.wait(2))
            deleter.start()
            deadline = time.monotonic() + 2
            while (deletion.inspect_history()['artifacts'][0]['availability'] != 'deleted'
                   and time.monotonic() < deadline):
                threading.Event().wait(.01)
            self.assertFalse(deletion.current_view()['artifacts'][0]['available'])
        finally:
            store.release.set()
            finalizer.join(5)
            if deleter.ident is not None:
                deleter.join(5)
        self.assertFalse(finalizer.is_alive())
        self.assertFalse(deleter.is_alive())
        self.assertEqual(outcomes[0]['status'], 'completed')
        self.assertEqual(deletion.inspect_history()['artifacts'][0]['availability'], 'deleted')
        self.assertEqual(list(store.persistent.iterdir()), [])
        self.assertEqual(deletion.retry_artifact_finalization(), {})

    def expose_artifact_claim(self, artifact_id):
        self.domain = {
            'entity_types': {'Subject': {'creation': True, 'attributes': {}}},
            'grounding_policies': {'p': {'acceptance': ['explicit']}},
            'claim_concepts': {'note': {'target_types': ['Subject'], 'value': {'type': 'string'}, 'grounding_policy': 'p'}},
        }

        class CandidateModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'I read the document.', (
                    EntityOperation('subject', 'Subject'),
                    ClaimOperation('note', 'subject', 'note', 'observed', 'p', artifact_id=artifact_id),
                    GroundingPlanOperation('p', ('note',), 'explicit', ('subject',)),
                ))

        core = self.core()
        core.model = CandidateModel()
        result = self.send(core)
        self.assertEqual(result['status'], 'completed')
        return core.inspect(result['communication_id'])

    def resolve(self, core, exposed, artifact_operation, claim_only=False):
        revision = core.inspect_history()['revision']

        class AcceptanceModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'Confirmed.', tuple(
                    GroundingResolutionOperation(item['id'], 'p', inbound.id, 'explicit', 'accepted')
                    for item in exposed['grounding_items'] if not claim_only or item['candidate_id'] == 'note'
                ) + (artifact_operation,), intent='semantic_commit', semantic_revision=revision)

        core.model = AcceptanceModel()
        payload = {'channel': 'development', 'sender': 's1', 'idempotency_key': 'resolve',
                   'text': 'Yes', 'sent_at': '2026-10-10T12:01:00Z', 'reply_to': exposed['outbound']['id']}
        if artifact_operation.action == 'supersede':
            payload['attachments'] = [{'id': artifact_operation.id, 'content_base64': 'cmVwbGFjZW1lbnQ='}]
        return core.accept(payload)

    def test_same_commit_cannot_ground_new_claims_from_an_artifact_it_deletes(self):
        original = self.persist()
        exposed = self.expose_artifact_claim(original['id'])
        core = self.core()
        before = core.inspect_history()
        operation = ArtifactOperation(original['id'], 'Record', ('persistent-domain-artifact',),
                                      action='delete', reason='privacy')
        result = self.resolve(core, exposed, operation)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['invalid_artifact_provenance'])
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.artifact_bytes(original['id']), b'original')

    def test_rejected_grounding_batch_keeps_supersession_stage_cleanable(self):
        original = self.persist()
        exposed = self.expose_artifact_claim(original['id'])
        core = self.core()
        before = core.inspect_history()
        operation = ArtifactOperation('replacement', 'Record', ('persistent-domain-artifact',),
                                      action='supersede', predecessor_id=original['id'])
        result = self.resolve(core, exposed, operation, claim_only=True)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(core.inspect(result['communication_id'])['trace']['validation_reasons'],
                         ['ungrounded_dependency'])
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(len(core.cleanup_artifact_staging(0)), 1)
        self.assertEqual(core.artifact_bytes(original['id']), b'original')

    def test_store_removal_is_idempotent_and_rejects_paths_outside_its_root(self):
        staged = self.store.stage(b'content', 'key')
        self.store.finalize(staged)
        self.store.remove(staged.reference)
        self.store.remove(staged.reference)
        self.assertEqual(list(self.store.persistent.iterdir()), [])
        with self.assertRaises(ValueError):
            self.store.remove('../outside')

    def test_supersession_cannot_reuse_a_deleted_predecessor(self):
        original = self.persist()
        self.assertEqual(self.send(self.deletion(original['id']))['status'], 'completed')
        core = self.core((ArtifactOperation('replacement', 'Record', ('persistent-domain-artifact',),
                                            action='supersede', predecessor_id=original['id']),))
        before = core.inspect_history()
        self.assertEqual(self.send(core, 'replacement')['status'], 'rejected')
        self.assertEqual(core.inspect_history(), before)

    def test_deletion_rolls_back_before_removing_bytes_and_stale_work_has_no_effect(self):
        original = self.persist()
        self.rule['retention']['bytes'] = 'delete'
        core = self.deletion(original['id'], revision=0)
        before = core.inspect_history()
        self.assertEqual(self.send(core)['status'], 'reprocess_required')
        self.assertEqual(core.inspect_history(), before)
        with sqlite3.connect(self.config.database) as db:
            db.executescript("CREATE TRIGGER fail_lifecycle BEFORE INSERT ON semantic_outbox BEGIN SELECT RAISE(ABORT, 'outbox failed'); END;")
        core = self.deletion(original['id'])
        self.assertEqual(self.send(core)['status'], 'retryable')
        self.assertEqual(core.inspect_history(), before)
        self.assertEqual(core.artifact_bytes(original['id']), b'original')


if __name__ == '__main__':
    unittest.main()
