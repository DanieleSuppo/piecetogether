"""Operational sender-initiated continuity with captured transport/provider responses."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from unittest.mock import patch

from test_multimodal_service import CapturedService


class DialogueService(CapturedService):
    """Keep the configured OpenAI adapter on every turn; only its HTTPS peer is captured."""

    def __init__(self, root, channel):
        super().__init__(root, channel)
        self.path = self.path.rename(root / (channel + '.json'))
        data = json.loads(self.path.read_text())
        data['identities'].update({'email:bob@example.com' if channel == 'email' else 'telegram:456': 'bob'})
        self.path.write_text(json.dumps(data))
        contract_path = root / 'contract.json'
        contract = json.loads(contract_path.read_text())
        contract['grounding_policies']['confirm'] = {
            'acceptance': ['explicit', 'implicit'], 'confirmation_forms': ['Confermo']}
        contract['claim_concepts']['size'] = {**contract['claim_concepts']['note']}
        contract_path.write_text(json.dumps(contract))
        self.proposal_override = None

    def http(self, host, **kwargs):
        if host != 'api.openai.com':
            return super().http(host, **kwargs)
        service = self

        class Connection:
            def request(self, method, path, body=None, headers=None):
                service.assert_unlocked()
                self.body = json.loads(body)
                service.requests.append((host, method, path, self.body))

            def getresponse(self):
                service.assert_unlocked()
                parts = self.body['input'][0]['content']
                instruction = json.loads(parts[0]['text'])
                service.model_inputs.append((instruction, parts[1:]))
                if service.crash_model:
                    service.crash_model = False
                    raise KeyboardInterrupt()
                cid = instruction['communication']['id']
                text = instruction['communication']['text']
                operations = []
                intent = 'candidate'
                if text.startswith('Nuovo'):
                    operations = [
                        {'kind': 'entity', 'id': 'subject', 'entity_type': 'Subject', 'attributes': {}},
                        {'kind': 'claim', 'id': 'note', 'target_id': 'subject', 'concept': 'note',
                         'value': 'blue', 'grounding_policy': 'confirm'},
                        {'kind': 'claim', 'id': 'size', 'target_id': 'subject', 'concept': 'size',
                         'value': 'large', 'grounding_policy': 'confirm'},
                        {'kind': 'grounding_plan', 'policy': 'confirm', 'claim_ids': ['note', 'size'],
                         'resolution_ids': ['subject'], 'acceptance_mode': 'explicit'},
                    ]
                    if parts[1:]:
                        operations[1]['artifact_id'] = 'attachment-1'
                else:
                    for record in instruction['context']['records']:
                        if record['kind'] != 'pending_grounding' or not record['payload_json']:
                            continue
                        candidate = json.loads(record['payload_json'])['candidate']
                        term = candidate.get('value', candidate['id'])
                        for clause in text.split(';'):
                            clause = clause.strip()
                            if clause in (term + ' Confermo', term + ' sì', term + ' no'):
                                operations.append({'kind': 'grounding_resolution', 'item_id': record['id'],
                                    'policy': 'confirm', 'evidence_communication_id': cid,
                                    'acceptance_mode': 'implicit' if clause.endswith(' sì') else 'explicit',
                                    'outcome': 'rejected' if clause.endswith(' no') else 'accepted',
                                    'evidence_span': clause, 'rationale': clause if clause.endswith(' sì') else None})
                    if operations:
                        intent = 'semantic_commit'
                proposal = {'schema_version': 1, 'contract_version': 'capture-v1', 'communication_id': cid,
                    'candidate_claims': [], 'draft_response': 'Captured interpretation.',
                    'operations': operations, 'intent': intent,
                    'semantic_revision': instruction['context']['semantic_revision'] if intent == 'semantic_commit' else None}
                if service.proposal_override:
                    proposal.update(service.proposal_override(instruction))
                return service.response(200, {'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': json.dumps(proposal)}]}]})

            def close(self):
                pass

        return Connection()

    def smtp(self, *args, **kwargs):
        smtp = super().smtp(*args, **kwargs)
        send = smtp.data
        def data(raw):
            if self.send_failure == 'failure':
                self.assert_unlocked()
                return 451, b'captured temporary refusal'
            return send(raw)
        smtp.data = data
        return smtp


class Dialogue:
    def __init__(self, root):
        self.services = {channel: DialogueService(root, channel) for channel in ('email', 'telegram')}
        self.sequence = 0

    def send(self, channel, text, actor='alice', attachment=False):
        self.sequence += 1
        service = self.services[channel]
        if channel == 'email':
            message = EmailMessage()
            message['From'] = actor + '@example.com'
            message['To'] = 'core@example.com'
            message['Message-ID'] = '<turn-' + str(self.sequence) + '@example.com>'
            message['Date'] = 'Wed, 07 Oct 2026 12:00:00 +0000'
            message['Subject'] = 'Acquisition'
            message.set_content(text)
            if attachment:
                from test_multimodal_artifacts import PNG
                message.add_attachment(PNG, maintype='image', subtype='png', filename='untrusted.png')
            service.mail = {str(self.sequence).encode(): message.as_bytes()}
        else:
            update = service.update(self.sequence, 'photo' if attachment else 'text',
                                    sender={'alice': 123, 'bob': 456, 'mallory': 789}[actor])
            update['message']['caption' if attachment else 'text'] = text
            service.batches = [[update]]
        self.run(channel)
        core = service.core()
        rows = core.inspect_email_acquisition() if channel == 'email' else core.inspect_telegram_acquisition()
        if not rows:
            return core, core.inspect(service.model_inputs[-1][0]['communication']['id'])
        acquired = rows[-1]
        return core, core.inspect(acquired['communication_id']) if acquired['communication_id'] else acquired

    def run(self, channel):
        service = self.services[channel]
        service.clock = self.sequence * 60
        origin = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
        with patch('piecetogether.core.now', lambda: (origin + timedelta(seconds=service.clock)).isoformat()):
            service.run()

    def replies(self, channel):
        deliveries = self.services[channel].deliveries
        if channel == 'email':
            return [BytesParser(policy=default).parsebytes(raw).get_content().strip() for raw in deliveries]
        return [item['text'] for item in deliveries]


class CrossChannelTests(unittest.TestCase):
    def test_both_commands_resolve_independently_targeted_items_on_the_new_channel(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                core, exposed = dialogue.send(first, 'Nuovo subject blue large', attachment=True)
                self.assertEqual(exposed['status'], 'completed')
                self.assertEqual(len(exposed['grounding_items']), 3)
                self.assertEqual(core.current_view()['assertion_sets'], [])
                self.assertEqual(core.trusted_events(), [])
                core, resolved = dialogue.send(second, 'subject Confermo; blue Confermo; large Confermo')
                history = core.inspect_history()
                self.assertEqual({item['outcome'] for item in history['grounding_items']}, {'accepted'})
                self.assertEqual({claim['value'] for claim in history['claims']}, {'blue', 'large'})
                self.assertEqual(resolved['status'], 'completed')
                self.assertIsNone(resolved['inbound']['reply_to'])
                for claim in history['claims']:
                    self.assertEqual(claim['provenance']['source_communication_id'], exposed['inbound']['id'])
                    self.assertEqual(claim['provenance']['exposure_communication_id'], exposed['outbound']['id'])
                    self.assertEqual(claim['provenance']['evidence_communication_id'], resolved['inbound']['id'])
                    self.assertEqual(claim['provenance']['actor_id'], 'alice')
                note = next(claim for claim in history['claims'] if claim['concept'] == 'note')
                self.assertEqual(note['provenance']['artifact_ingress']['checksum'], exposed['inbound']['attachments'][0]['checksum'])
                self.assertEqual((len(dialogue.replies(first)), len(dialogue.replies(second))), (1, 1))
                self.assertEqual(resolved['outbound']['channel'], second)
                self.assertEqual(resolved['outbound']['recipient'], resolved['inbound']['sender'])
                for service in dialogue.services.values():
                    self.assertEqual(json.loads(service.path.read_text())['model'], 'openai-responses')
                    self.assertTrue(all(request[3]['model'] == 'gpt-4.1-2025-04-14'
                                        for request in service.requests if request[0] == 'api.openai.com'))

    def test_restart_and_redelivery_do_not_duplicate_exposure_commit_or_reply(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                core, exposed = dialogue.send(first, 'Nuovo subject blue large')
                dialogue.run(first)
                self.assertEqual(core.inspect(exposed['inbound']['id'])['grounding_items'], exposed['grounding_items'])
                self.assertEqual(len(dialogue.services[first].model_inputs), 1)
                core, resolved = dialogue.send(second, 'subject Confermo; blue Confermo; large Confermo')
                before = core.inspect_history()
                # Every run constructs a new Core through the actual operational command.
                dialogue.run(first)
                dialogue.run(second)
                self.assertEqual(core.inspect_history(), before)
                self.assertEqual((len(dialogue.replies(first)), len(dialogue.replies(second))), (1, 1))
                self.assertEqual(len(dialogue.services[second].model_inputs), 1)
                self.assertEqual(len(core.trusted_events()), 1)
                self.assertEqual(len(core.current_view()['assertion_sets']), 2)
                self.assertEqual(resolved['trace']['model_provider'], 'openai-responses')

    def test_partial_explicit_and_implicit_resolutions_preserve_unaddressed_items(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                _, exposed = dialogue.send(first, 'Nuovo subject blue large')
                core, entity = dialogue.send(second, 'subject Confermo')
                pending = core.inspect(exposed['inbound']['id'])['grounding_items']
                self.assertEqual({item['candidate_id']: item['outcome'] for item in pending},
                                 {'subject': 'accepted', 'note': 'pending', 'size': 'pending'})
                self.assertEqual(core.inspect_history()['claims'], [])
                model_pack = dialogue.services[second].model_inputs[-1][0]['context']
                payloads = [json.loads(record['payload_json'])['candidate']['id']
                            for record in model_pack['records']
                            if record['kind'] == 'pending_grounding' and record['payload_json']]
                self.assertEqual(payloads, ['subject'])
                core, note = dialogue.send(second, 'blue sì; large no')
                history = core.inspect_history()
                self.assertEqual({item['candidate_id']: item['outcome'] for item in history['grounding_items']},
                                 {'subject': 'accepted', 'note': 'accepted', 'size': 'rejected'})
                self.assertEqual([claim['value'] for claim in history['claims']], ['blue'])
                self.assertEqual(history['claims'][0]['provenance']['evidence_communication_id'], note['inbound']['id'])
                self.assertEqual(history['entities'][0]['provenance']['evidence_communication_id'], entity['inbound']['id'])
                self.assertEqual((len(dialogue.replies(first)), len(dialogue.replies(second))), (1, 2))

    def test_other_actor_never_receives_or_resolves_the_pending_grounding(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                _, exposed = dialogue.send(first, 'Nuovo subject blue large')
                core, foreign = dialogue.send(second, 'subject Confermo; blue Confermo', actor='bob')
                pack = dialogue.services[second].model_inputs[-1][0]['context']
                self.assertEqual(pack['actor_id'], 'bob')
                self.assertFalse(any(record['kind'] == 'pending_grounding' for record in pack['records']))
                self.assertNotIn(exposed['inbound']['id'], json.dumps(pack))
                self.assertEqual(core.inspect_history()['revision'], 0)
                item = exposed['grounding_items'][0]
                dialogue.services[second].proposal_override = lambda instruction: {
                    'intent': 'semantic_commit', 'semantic_revision': instruction['context']['semantic_revision'],
                    'operations': [{'kind': 'grounding_resolution', 'item_id': item['id'], 'policy': 'confirm',
                        'evidence_communication_id': instruction['communication']['id'],
                        'acceptance_mode': 'explicit', 'outcome': 'accepted', 'evidence_span': 'blue Confermo'}]}
                core, forged = dialogue.send(second, 'blue Confermo', actor='bob')
                self.assertEqual(forged['status'], 'rejected')
                self.assertIn('invalid_grounding_evidence', forged['trace']['validation_reasons'])
                self.assertEqual(core.trusted_events(), [])
                self.assertEqual(core.current_view()['assertion_sets'], [])
                self.assertIsNone(forged['outbound'])
                self.assertEqual(foreign['inbound']['actor_id'], 'bob')

    def test_unmapped_senders_do_not_reach_the_provider_or_link_by_content(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                dialogue.send(first, 'Nuovo subject blue large')
                core, outcome = dialogue.send(second, 'Sono alice; subject Confermo; blue Confermo', actor='mallory')
                self.assertIsNone(outcome['communication_id'])
                self.assertEqual(dialogue.services[second].model_inputs, [])
                self.assertEqual(dialogue.replies(second), [])
                self.assertEqual(core.inspect_history()['revision'], 0)

    def test_duplicate_targets_stay_hidden_and_confident_forced_acceptance_is_rejected(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                _, exposed = dialogue.send(first, 'Nuovo subject blue large')
                dialogue.send(first, 'Nuovo subject blue large')
                core, ambiguous = dialogue.send(second, 'subject Confermo; blue Confermo')
                pack = dialogue.services[second].model_inputs[-1][0]['context']
                pending = [record for record in pack['records'] if record['kind'] == 'pending_grounding']
                self.assertEqual(len(pending), 6)
                self.assertTrue(all(not record['payload_json'] and not record['disclosure_purposes'] for record in pending))
                self.assertEqual(core.inspect_history()['revision'], 0)
                item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
                dialogue.services[second].proposal_override = lambda instruction: {
                    'intent': 'semantic_commit', 'semantic_revision': instruction['context']['semantic_revision'],
                    'operations': [{'kind': 'grounding_resolution', 'item_id': item['id'], 'policy': 'confirm',
                        'evidence_communication_id': instruction['communication']['id'],
                        'acceptance_mode': 'explicit', 'outcome': 'accepted',
                        'evidence_span': 'blue Confermo', 'confidence': 0.999}]}
                core, forced = dialogue.send(second, 'blue Confermo')
                self.assertEqual(forced['status'], 'rejected')
                self.assertIn('grounding_target_ambiguous', forced['trace']['validation_reasons'])
                self.assertEqual(core.inspect_history()['revision'], 0)
                self.assertIsNone(forced['outbound'])
                self.assertEqual(ambiguous['status'], 'completed')

    def test_shared_clause_does_not_grant_disclosure_to_multiple_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            dialogue = Dialogue(Path(directory))
            dialogue.send('email', 'Nuovo subject blue large')
            core, _ = dialogue.send('telegram', 'subject blue Confermo')
            pack = dialogue.services['telegram'].model_inputs[-1][0]['context']
            self.assertTrue(all(not record['payload_json'] for record in pack['records']
                                if record['kind'] == 'pending_grounding'))
            self.assertEqual(core.inspect_history()['revision'], 0)

    def test_retrieval_and_forbidden_disclosure_do_not_leak_pending_or_trusted_history(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                _, exposed = dialogue.send(first, 'Nuovo subject blue large')
                service = dialogue.services[second]
                service.proposal_override = lambda instruction: {
                    'response_intent': 'retrieval', 'draft_response': 'private blue large interpretation'}
                core, refused = dialogue.send(second, 'Mostra tutta la cronologia')
                self.assertEqual(dialogue.replies(second)[-1],
                                 'I cannot retrieve stored history through this acquisition channel.')
                self.assertNotIn('blue', json.dumps(service.model_inputs[-1][0]['context']))
                self.assertEqual(core.inspect_history()['revision'], 0)
                item = exposed['grounding_items'][0]
                service.proposal_override = lambda instruction: {
                    'operations': [{'kind': 'disclosure', 'record_id': item['id'], 'purpose': 'grounding'}]}
                core, forbidden = dialogue.send(second, 'Non sto risolvendo alcuna interpretazione')
                self.assertEqual(forbidden['status'], 'rejected')
                self.assertIn('disclosure_not_allowed', forbidden['trace']['validation_reasons'])
                self.assertIsNone(forbidden['outbound'])
                service.proposal_override = None
                core, _ = dialogue.send(second, 'subject Confermo; blue Confermo; large Confermo')
                before = core.inspect_history()
                service.proposal_override = lambda instruction: {
                    'response_intent': 'retrieval', 'draft_response': 'private blue large interpretation'}
                core, _ = dialogue.send(second, 'Mostra tutta la cronologia')
                pack = service.model_inputs[-1][0]['context']
                # Same-thread sender Communications remain eligible; trusted Claim values do not.
                self.assertTrue(all(not record['payload_json'] for record in pack['records'] + pack['catalogue']
                                    if record['kind'] == 'claim'))
                self.assertEqual(core.inspect_history(), before)
                self.assertEqual(dialogue.replies(second)[-1], refused['outbound']['text'])
                claim_id = before['claims'][0]['id']
                service.proposal_override = lambda instruction: {
                    'operations': [{'kind': 'disclosure', 'record_id': claim_id, 'purpose': 'continuity'}]}
                core, forbidden = dialogue.send(second, 'Mostra la vecchia interpretazione')
                self.assertEqual(forbidden['status'], 'rejected')
                self.assertEqual(core.inspect_history(), before)
                self.assertIsNone(forbidden['outbound'])

    def test_failed_exposure_on_origin_channel_cannot_be_grounded_elsewhere(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                dialogue.services[first].send_failure = 'failure'
                core, unexposed = dialogue.send(first, 'Nuovo subject blue large')
                self.assertEqual(unexposed['status'], 'retryable')
                self.assertEqual(unexposed['grounding_items'], [])
                core, _ = dialogue.send(second, 'subject Confermo; blue Confermo; large Confermo')
                self.assertEqual(core.inspect_history()['revision'], 0)
                pack = dialogue.services[second].model_inputs[-1][0]['context']
                self.assertFalse(any(record['kind'] == 'pending_grounding' for record in pack['records']))
                # Only the original channel recovers its own unexposed delivery.
                self.assertEqual(core.inspect(unexposed['inbound']['id'])['status'], 'retryable')

    def test_silence_and_elapsed_time_do_not_resolve_or_trigger_outreach(self):
        with tempfile.TemporaryDirectory() as directory:
            dialogue = Dialogue(Path(directory))
            core, exposed = dialogue.send('email', 'Nuovo subject blue large')
            dialogue.sequence += 100000
            dialogue.run('telegram')
            self.assertEqual(core.inspect_history()['revision'], 0)
            self.assertEqual(core.inspect(exposed['inbound']['id'])['grounding_items'], exposed['grounding_items'])
            self.assertEqual(dialogue.services['telegram'].model_inputs, [])
            self.assertEqual(dialogue.replies('telegram'), [])
            self.assertEqual(len(dialogue.replies('email')), 1)

    def test_questions_wrong_forms_and_conflicting_evidence_cannot_resolve_across_channels(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            for text, span, reason in (
                ('subject Confermo?', 'subject Confermo', 'explicit_confirmation_required'),
                ('subject yes', 'subject yes', 'explicit_confirmation_required'),
                ('subject Confermo; subject no', 'subject Confermo', 'explicit_confirmation_required'),
            ):
                with self.subTest(first=first, text=text), tempfile.TemporaryDirectory() as directory:
                    dialogue = Dialogue(Path(directory))
                    _, exposed = dialogue.send(first, 'Nuovo subject blue large')
                    item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'subject')
                    dialogue.services[second].proposal_override = lambda instruction: {
                        'intent': 'semantic_commit', 'semantic_revision': instruction['context']['semantic_revision'],
                        'operations': [{'kind': 'grounding_resolution', 'item_id': item['id'], 'policy': 'confirm',
                            'evidence_communication_id': instruction['communication']['id'],
                            'acceptance_mode': 'explicit', 'outcome': 'accepted', 'evidence_span': span}]}
                    core, turn = dialogue.send(second, text)
                    self.assertEqual(turn['status'], 'rejected')
                    self.assertIn(reason, turn['trace']['validation_reasons'])
                    self.assertEqual(core.inspect_history()['revision'], 0)
                    self.assertEqual(core.trusted_events(), [])
                    self.assertIsNone(turn['outbound'])

    def test_necessary_item_disclosure_is_bounded_to_the_addressed_pending_item(self):
        with tempfile.TemporaryDirectory() as directory:
            dialogue = Dialogue(Path(directory))
            _, exposed = dialogue.send('email', 'Nuovo subject blue large')
            item = next(item for item in exposed['grounding_items'] if item['candidate_id'] == 'note')
            dialogue.services['telegram'].proposal_override = lambda instruction: {
                'intent': 'candidate', 'semantic_revision': None,
                'operations': [{'kind': 'disclosure', 'record_id': item['id'], 'purpose': 'grounding'}]}
            core, turn = dialogue.send('telegram', 'blue?')
            self.assertEqual(turn['status'], 'completed')
            self.assertIn('For grounding', turn['outbound']['text'])
            self.assertIn('blue', turn['outbound']['text'])
            self.assertNotIn('large', turn['outbound']['text'])
            self.assertEqual(core.inspect_history()['revision'], 0)

    def test_interrupted_new_channel_processing_recovers_durably_after_restart(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                dialogue.send(first, 'Nuovo subject blue large')
                service = dialogue.services[second]
                service.crash_model = True
                core, interrupted = dialogue.send(second, 'subject Confermo; blue Confermo; large Confermo')
                self.assertEqual(interrupted['status'], 'pending')
                self.assertEqual(core.inspect_history()['revision'], 0)
                # Acquisition no longer depends on either remote mailbox/queue retaining the source.
                service.mail.clear()
                service.batches.clear()
                dialogue.sequence += 1
                dialogue.run(second)
                self.assertEqual(core.inspect(interrupted['inbound']['id'])['status'], 'completed')
                self.assertEqual({claim['value'] for claim in core.inspect_history()['claims']}, {'blue', 'large'})
                self.assertEqual(len(core.trusted_events()), 1)
                self.assertEqual(len(service.model_inputs), 2)
                self.assertEqual((len(dialogue.replies(first)), len(dialogue.replies(second))), (1, 1))

    def test_commit_reply_failure_recovers_on_new_channel_without_another_model_call(self):
        for first, second in (('email', 'telegram'), ('telegram', 'email')):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as directory:
                dialogue = Dialogue(Path(directory))
                dialogue.send(first, 'Nuovo subject blue large')
                service = dialogue.services[second]
                service.send_failure = 'failure'
                core, committed = dialogue.send(second, 'subject Confermo; blue Confermo; large Confermo')
                self.assertEqual(committed['status'], 'retryable')
                before = core.inspect_history()
                self.assertEqual({claim['value'] for claim in before['claims']}, {'blue', 'large'})
                service.send_failure = None
                dialogue.run(second)
                self.assertEqual(core.inspect(committed['inbound']['id'])['status'], 'completed')
                self.assertEqual(core.inspect_history(), before)
                self.assertEqual(len(service.model_inputs), 1)
                self.assertEqual(len(dialogue.replies(first)), 1)
                self.assertEqual(len(core.trusted_events()), 1)


if __name__ == '__main__':
    unittest.main()
