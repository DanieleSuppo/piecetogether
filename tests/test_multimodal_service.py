"""Captured operational commands: no live mailbox, Telegram, or model service."""

import base64
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch

from piecetogether.__main__ import main
from piecetogether.core import Bootstrap, Core
from piecetogether.telegram_channel import BotApiTransport
from test_multimodal_artifacts import EMAIL, PNG

JPEG = b'\xff\xd8\xffcaptured-photo'
PDF = b'%PDF-1.4\ncaptured-document'


class CapturedService:
    def __init__(self, root, channel):
        self.root, self.channel = root, channel
        self.path = root / 'deployment.json'
        self.environment = {'PT_CAPTURE_MODEL': 'fake-model-secret',
                            'PT_CAPTURE_BOT': '900:fake-bot-secret',
                            'PT_CAPTURE_IMAP': 'fake-imap-secret'}
        artifact = {'roles': ['ephemeral-evidence', 'source-evidence', 'persistent-domain-artifact'],
                    'persistence': 'allowed', 'retention': {'metadata': 'retain', 'bytes': 'retain',
                    'provenance': 'retain'}, 'supersession': False}
        contract = {'schema_version': 1, 'version': 'capture-v1',
                    'entity_types': {'Subject': {'creation': True, 'attributes': {}}},
                    'relationship_types': {}, 'claim_concepts': {'note': {'target_types': ['Subject'],
                    'value': {'type': 'string'}, 'grounding_policy': 'confirm'}},
                    'grounding_policies': {'confirm': {'acceptance': ['explicit']}},
                    'artifact_types': {'Evidence': artifact}, 'emergent_concepts': {'allowed': False}}
        (root / 'contract.json').write_text(json.dumps(contract))
        data = {'database': 'core.sqlite3', 'channel': channel, 'model': 'openai-responses',
                'contract_version': 'capture-v1', 'contract': 'contract.json',
                'capabilities': ['receive', 'reply', 'text', 'threading', 'attachments'],
                'artifact_store': 'artifacts', 'media_policy': {mime: {'artifact_type': 'Evidence',
                'roles': ['ephemeral-evidence']} for mime in ('image/png', 'image/jpeg', 'application/pdf')},
                'identities': {('email:alice@example.com' if channel == 'email' else 'telegram:123'): 'alice'},
                'openai': {'model_id': 'gpt-4.1-2025-04-14', 'secret_reference': 'model'},
                'secret_references': {'model': 'PT_CAPTURE_MODEL'}}
        if channel == 'email':
            data.update(email={'address': 'core@example.com', 'host': 'localhost', 'security': 'plain'},
                        imap={'host': 'imap.example.test', 'username': 'core',
                              'password_secret_reference': 'imap', 'poll_seconds': 1})
            data['secret_references']['imap'] = 'PT_CAPTURE_IMAP'
        else:
            data['telegram'] = {'bot_id': '900', 'token_secret_reference': 'bot', 'poll_seconds': 1}
            data['secret_references']['bot'] = 'PT_CAPTURE_BOT'
        self.path.write_text(json.dumps(data))
        self.batches, self.mail, self.requests, self.model_inputs, self.deliveries = [], {}, [], [], []
        self.getfile_count, self.download_count = 0, 0
        self.model_failures, self.crash_model, self.send_failure = 0, False, None
        self.getfile_failure, self.download_failure = None, None
        self.file_override, self.download_override = None, None
        self.operations = None
        self.persist = False
        self.raw_mail = EMAIL
        self.sleep_delays = []
        self.clock = 0

    @staticmethod
    def update(number, media='photo', sender=123):
        message = {'message_id': number, 'date': 1767268800 + number,
                   'from': {'id': sender, 'is_bot': False}, 'chat': {'id': sender, 'type': 'private'}}
        if media == 'photo':
            message['photo'] = [{'file_id': 'photo', 'file_size': len(JPEG)}]
        elif media == 'document':
            message['document'] = {'file_id': 'document', 'file_size': len(PDF), 'mime_type': 'application/pdf'}
        else:
            message['text'] = 'ordinary text'
        return {'update_id': number, 'message': message}

    def assert_unlocked(self):
        with sqlite3.connect(self.root / 'core.sqlite3', timeout=0.1) as db:
            db.execute('BEGIN IMMEDIATE')

    def response(self, status, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        return type('Response', (), {'status': status, 'read': lambda self, limit: raw[:limit]})()

    def http(self, host, **kwargs):
        if host not in ('api.telegram.org', 'api.openai.com'):
            raise AssertionError('unexpected external host')
        service = self

        class Connection:
            def connect(self):
                service.assert_unlocked()
            def request(self, method, path, body=None, headers=None):
                service.assert_unlocked()
                self.method, self.path = method, path
                self.body = json.loads(body) if body else {}
                service.requests.append((host, method, path, self.body))
            def getresponse(self):
                service.assert_unlocked()
                if host == 'api.openai.com':
                    parts = self.body['input'][0]['content']
                    instruction = json.loads(parts[0]['text'])
                    service.model_inputs.append((instruction, parts[1:]))
                    if service.crash_model:
                        service.crash_model = False
                        raise KeyboardInterrupt()
                    if service.model_failures:
                        service.model_failures -= 1
                        raise OSError('captured model outage')
                    cid = instruction['communication']['id']
                    if service.operations is not None:
                        operations = service.operations
                    elif service.persist:
                        operations = [{'kind': 'artifact', 'id': 'attachment-1', 'artifact_type': 'Evidence',
                                       'roles': ['source-evidence', 'persistent-domain-artifact'], 'action': 'persist'}]
                    elif parts[1:]:
                        operations = [{'kind': 'entity', 'id': 'subject', 'entity_type': 'Subject', 'attributes': {}},
                                      {'kind': 'claim', 'id': 'note', 'target_id': 'subject', 'concept': 'note',
                                       'value': 'blue', 'grounding_policy': 'confirm', 'artifact_id': 'attachment-1'},
                                      {'kind': 'grounding_plan', 'policy': 'confirm', 'claim_ids': ['note'],
                                       'resolution_ids': ['subject'], 'acceptance_mode': 'explicit'}]
                    else:
                        operations = []
                    proposal = {'schema_version': 1, 'contract_version': 'capture-v1', 'communication_id': cid,
                                'candidate_claims': [], 'draft_response': 'Captured observation.', 'operations': operations,
                                'intent': 'semantic_commit' if service.persist else 'candidate',
                                'semantic_revision': instruction['context']['semantic_revision'] if service.persist else None,
                                'decision_raw': {'confidence': 0.999}}
                    return service.response(200, {'output': [{'type': 'message', 'content': [
                        {'type': 'output_text', 'text': json.dumps(proposal)}]}]})
                if self.path.endswith('/getUpdates'):
                    return service.response(200, {'ok': True, 'result': service.batches.pop(0) if service.batches else []})
                if self.path.endswith('/getFile'):
                    service.getfile_count += 1
                    if service.getfile_failure:
                        failure = service.getfile_failure
                        service.getfile_failure = None
                        status, body = failure if isinstance(failure, tuple) else (
                            failure, {'ok': False, 'parameters': {'retry_after': 2}})
                        return service.response(status, body)
                    file_id = self.body['file_id']
                    content = JPEG if file_id == 'photo' else PDF
                    result = {'file_id': file_id, 'file_unique_id': 'unique-' + file_id,
                              'file_size': len(content), 'file_path': 'captured/' + file_id}
                    if service.file_override is not None:
                        result.update(service.file_override)
                        service.file_override = None
                    return service.response(200, {'ok': True, 'result': result})
                if self.method == 'GET':
                    service.download_count += 1
                    if service.download_failure:
                        status = service.download_failure
                        service.download_failure = None
                        return service.response(status, b'captured transient failure')
                    if service.download_override is not None:
                        content, service.download_override = service.download_override, None
                        return service.response(200, content)
                    return service.response(200, JPEG if self.path.endswith('/photo') else PDF)
                if self.path.endswith('/sendMessage'):
                    service.deliveries.append(self.body)
                    if service.send_failure == 'indeterminate':
                        raise OSError('captured lost acknowledgement')
                    if service.send_failure == 'failure':
                        return service.response(400, {'ok': False})
                    return service.response(200, {'ok': True, 'result': {'message_id': 800 + len(service.deliveries),
                        'text': self.body['text'], 'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 900}}})
                raise AssertionError('unexpected captured request')
            def close(self):
                pass
        return Connection()

    def imap(self, *args, **kwargs):
        service = self
        class Mailbox:
            def login(self, *args): service.assert_unlocked()
            def select(self, *args, **kwargs): return 'OK', [b'1']
            def response(self, name): return 'UIDVALIDITY', [b'7']
            def uid(self, command, *args):
                service.assert_unlocked()
                if command == 'search': return 'OK', [b' '.join(service.mail)]
                uid = args[0]
                return 'OK', [(b'1 (UID ' + uid + b' BODY[] {1})', service.mail[uid])]
            def logout(self): pass
        return Mailbox()

    def smtp(self, *args, **kwargs):
        service = self
        class Smtp:
            def ehlo_or_helo_if_needed(self): service.assert_unlocked()
            def mail(self, sender): return 250, b'ok'
            def rcpt(self, recipient): return 250, b'ok'
            def data(self, raw):
                service.assert_unlocked()
                service.deliveries.append(raw)
                return 250, b'ok'
            def close(self): pass
        return Smtp()

    def run(self, cycles=1, on_sleep=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        count = 0
        def sleep(delay):
            nonlocal count
            count += 1
            self.sleep_delays.append(delay)
            self.clock += delay
            if on_sleep:
                on_sleep(count)
            if count >= cycles:
                raise KeyboardInterrupt()
        with (patch.dict(os.environ, self.environment, clear=True),
              patch.object(sys, 'argv', ['piecetogether', '--config', str(self.path), '--serve-' + self.channel]),
              patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr),
              patch('http.client.HTTPSConnection', self.http),
              patch('piecetogether.core.BotApiTransport', partial(BotApiTransport, clock=lambda: self.clock)),
              patch('piecetogether.email_channel.imaplib.IMAP4_SSL', self.imap),
              patch('piecetogether.email_channel.smtplib.SMTP', self.smtp),
              patch('time.sleep', sleep)):
            result = main()
        if result != 0 or stdout.getvalue() or stderr.getvalue():
            raise AssertionError((result, stdout.getvalue(), stderr.getvalue()))

    def core(self):
        with patch.dict(os.environ, self.environment, clear=True):
            return Core(Bootstrap.from_file(self.path))


class MultimodalServiceTests(unittest.TestCase):
    def test_serve_telegram_acquires_photo_and_pdf_and_restart_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CapturedService(Path(directory), 'telegram')
            service.batches = [[service.update(1), service.update(2, 'document')]]
            service.run()
            core = service.core()
            acquired = core.inspect_telegram_acquisition()
            self.assertEqual([row['status'] for row in acquired], ['acquired', 'acquired'])
            for row in acquired:
                record = core.inspect(row['communication_id'])
                self.assertEqual((record['status'], record['inbound']['actor_id']), ('completed', 'alice'))
                self.assertEqual(len(record['grounding_items']), 2)
            self.assertEqual([base64.b64decode(parts[0].get('image_url', parts[0].get('file_data')).split(',', 1)[1])
                              for _, parts in service.model_inputs], [JPEG, PDF])
            self.assertEqual([item['reply_parameters']['message_id'] for item in service.deliveries], [1, 2])
            self.assertTrue(all(item['chat_id'] == 123 for item in service.deliveries))
            service.batches = [[service.update(1), service.update(2, 'document')]]
            service.run()
            self.assertEqual((len(service.deliveries), service.download_count), (2, 2))
            self.assertEqual(core.current_view()['assertion_sets'], [])
            self.assertEqual(core.trusted_events(), [])
            durable = json.dumps([core.inspect(row['communication_id']) for row in acquired])
            for forbidden in ('fake-bot-secret', 'fake-model-secret', 'captured/photo', 'content_base64', 'image_url'):
                self.assertNotIn(forbidden, durable)

    def test_both_commands_restart_pending_model_work_using_store_without_source(self):
        for channel in ('email', 'telegram'):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), channel)
                service.mail = {b'1': EMAIL}
                service.batches = [[service.update(1)]]
                service.model_failures = 1
                service.run()
                core = service.core()
                outcomes = core.inspect_email_acquisition() if channel == 'email' else core.inspect_telegram_acquisition()
                cid = outcomes[0]['communication_id']
                self.assertEqual(core.inspect(cid)['status'], 'retryable')
                self.assertEqual(core.cleanup_artifact_staging(0), [])
                service.mail, service.batches = {}, []
                downloads = service.download_count
                service.run()
                self.assertEqual(service.core().inspect(cid)['status'], 'completed')
                self.assertEqual(service.download_count, downloads)
                self.assertEqual(len(service.model_inputs), 2)
                self.assertEqual(service.model_inputs[0][1], service.model_inputs[1][1])
                self.assertEqual(len(service.deliveries), 1)

    def test_telegram_crash_after_durable_stage_recovers_with_no_provider_update(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CapturedService(Path(directory), 'telegram')
            service.batches = [[service.update(1, 'document')]]
            service.crash_model = True
            service.run()
            self.assertEqual(service.core().inspect_telegram_acquisition(), [])
            service.run()
            self.assertEqual((service.download_count, len(service.deliveries)), (1, 1))
            self.assertEqual(service.model_inputs[0][1], service.model_inputs[1][1])

    def test_both_commands_do_not_checkpoint_failed_staging_and_can_retry(self):
        from piecetogether.artifact_store import LocalArtifactStore
        for channel in ('email', 'telegram'):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), channel)
                service.mail = {b'1': EMAIL}
                update = service.update(1)
                service.batches = [[update]]
                with patch.object(LocalArtifactStore, 'stage', side_effect=OSError('captured storage outage')):
                    service.run()
                core = service.core()
                self.assertEqual(core.inspect_email_acquisition() if channel == 'email' else core.inspect_telegram_acquisition(), [])
                self.assertEqual(service.model_inputs, [])
                service.batches = [[update]]
                service.run()
                self.assertEqual(len(service.deliveries), 1)

    def test_telegram_remote_transient_failure_retries_without_checkpoint(self):
        for failure in ('getfile_failure', 'download_failure'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), 'telegram')
                update = service.update(1, 'document')
                service.batches = [[update], [update]]
                setattr(service, failure, 503)
                def checkpoint(count):
                    if count == 1:
                        self.assertEqual(service.core().inspect_telegram_acquisition(), [])
                service.run(cycles=2, on_sleep=checkpoint)
                self.assertEqual(service.core().inspect_telegram_acquisition()[0]['status'], 'acquired')
                offsets = [body['offset'] for _, _, path, body in service.requests if path.endswith('/getUpdates')]
                self.assertEqual(offsets, [0, 0])
                self.assertEqual(len(service.deliveries), 1)

    def test_remote_retry_delay_is_bounded_even_for_malformed_provider_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CapturedService(Path(directory), 'telegram')
            update = service.update(1, 'document')
            service.batches = [[update], [update]]
            service.getfile_failure = (429, {'ok': False, 'parameters': {'retry_after': 10**500}})
            service.run(cycles=2)
            self.assertEqual(service.sleep_delays, [1, 1])
            self.assertEqual(service.core().inspect_telegram_acquisition()[0]['status'], 'acquired')

    def test_definite_invalid_remote_files_are_skipped_without_leaking_provider_fields(self):
        for override, content in (({'file_id': 'wrong-file'}, None), ({'file_size': 1_048_577}, None),
                                  ({'file_path': 'https://user:password@example.test/file'}, None),
                                  ({}, b'%PDF-short'), ({}, PNG)):
            with self.subTest(override=override, content=content), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), 'telegram')
                service.file_override, service.download_override = override, content
                service.batches = [[service.update(1, 'document'), service.update(2)]]
                service.run()
                rows = service.core().inspect_telegram_acquisition()
                self.assertEqual([row['status'] for row in rows], ['unsupported', 'acquired'])
                self.assertEqual(len(service.deliveries), 1)
                self.assertNotIn('password', json.dumps(rows))
                self.assertNotIn('example.test/file', json.dumps(rows))

    def test_telegram_unsupported_oversize_unmapped_inputs_do_not_block_valid_media(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CapturedService(Path(directory), 'telegram')
            unsupported, oversized = service.update(1, 'document'), service.update(2, 'document')
            unsupported['message']['document']['mime_type'] = 'application/zip'
            oversized['message']['document']['file_size'] = 1_048_577
            service.batches = [[unsupported, oversized, service.update(3, sender=456), service.update(4, 'document')]]
            service.run()
            rows = service.core().inspect_telegram_acquisition()
            self.assertEqual([row['status'] for row in rows], ['unsupported', 'unsupported', 'unsupported', 'acquired'])
            self.assertEqual(rows[2]['outcome'], 'unmapped_identity')
            self.assertEqual((service.getfile_count, len(service.deliveries)), (1, 1))

    def test_telegram_media_handoff_failure_retries_but_indeterminate_never_resends(self):
        for result in ('failure', 'indeterminate'):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), 'telegram')
                service.batches = [[service.update(1)]]
                service.send_failure = result
                service.run()
                cid = service.core().inspect_telegram_acquisition()[0]['communication_id']
                self.assertEqual(service.core().inspect(cid)['grounding_items'], [])
                service.send_failure = None
                service.run()
                record = service.core().inspect(cid)
                self.assertEqual(len(service.model_inputs), 1)
                self.assertEqual(len(service.deliveries), 2 if result == 'failure' else 1)
                self.assertEqual(len(record['grounding_items']), 2 if result == 'failure' else 0)

    def test_email_unsupported_oversize_and_unmapped_inputs_do_not_block_valid_media(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CapturedService(Path(directory), 'email')
            oversized = EMAIL.replace(base64.b64encode(PNG), base64.b64encode(PNG + b'x' * 1_048_576))
            service.mail = {b'1': EMAIL.replace(b'image/png', b'application/zip'), b'2': oversized,
                            b'3': EMAIL.replace(b'alice@example.com', b'unknown@example.com'),
                            b'4': EMAIL.replace(b'<image@example.com>', b'<valid@example.com>')}
            service.run()
            rows = service.core().inspect_email_acquisition()
            self.assertEqual([row['status'] for row in rows], ['unsupported', 'unsupported', 'unsupported', 'acquired'])
            self.assertEqual((len(service.model_inputs), len(service.deliveries)), (1, 1))

    def test_wrong_confident_classification_and_missing_values_never_gain_trust(self):
        scenarios = [
            ('wrong-role', [{'kind': 'artifact', 'id': 'attachment-1', 'artifact_type': 'Evidence',
                             'roles': ['source-evidence'], 'action': 'classify'}], 'rejected'),
            ('missing-value', [{'kind': 'entity', 'id': 'subject', 'entity_type': 'Subject', 'attributes': {}},
                {'kind': 'claim', 'id': 'note', 'target_id': 'subject', 'concept': 'note', 'value': None,
                 'grounding_policy': 'confirm', 'artifact_id': 'attachment-1'}], 'rejected'),
            ('ambiguous', [{'kind': 'reference_resolution', 'reference_kind': 'entity', 'outcome': 'ambiguous'}], 'completed'),
            ('none', [{'kind': 'reference_resolution', 'reference_kind': 'entity', 'outcome': 'none'}], 'completed'),
        ]
        for channel in ('email', 'telegram'):
            for name, operations, expected in scenarios:
                with self.subTest(channel=channel, scenario=name), tempfile.TemporaryDirectory() as directory:
                    service = CapturedService(Path(directory), channel)
                    service.operations, service.mail, service.batches = operations, {b'1': EMAIL}, [[service.update(1)]]
                    service.run()
                    core = service.core()
                    rows = core.inspect_email_acquisition() if channel == 'email' else core.inspect_telegram_acquisition()
                    record = core.inspect(rows[0]['communication_id'])
                    self.assertEqual(record['status'], expected)
                    self.assertEqual(record['grounding_items'], [])
                    self.assertEqual(core.current_view()['assertion_sets'], [])
                    self.assertEqual(core.trusted_events(), [])

    def test_both_commands_default_deny_bytes_and_keep_candidate_data_off_state_api(self):
        from dataclasses import replace
        from urllib.request import Request, urlopen
        from piecetogether.application_api import ApplicationApiConfig, ApplicationApiServer, ApplicationCredential
        for channel in ('email', 'telegram'):
            for permit in (False, True):
                with self.subTest(channel=channel, permit=permit), tempfile.TemporaryDirectory() as directory:
                    service = CapturedService(Path(directory), channel)
                    if not permit:
                        data = json.loads(service.path.read_text())
                        data['media_policy'] = {}
                        service.path.write_text(json.dumps(data))
                    service.mail, service.batches = {b'1': EMAIL}, [[service.update(1, 'document')]]
                    service.run()
                    self.assertEqual(bool(service.model_inputs[0][1]), permit)
                    with patch.dict(os.environ, {**service.environment, 'PT_CAPTURE_API': 'fake-api-secret'}, clear=True):
                        config = replace(Bootstrap.from_file(service.path), application_api=ApplicationApiConfig(
                            '127.0.0.1', 0, (ApplicationCredential('api', ('state:read', 'events:consume')),)),
                            secret_references={**Bootstrap.from_file(service.path).secret_references, 'api': 'PT_CAPTURE_API'})
                        core = Core(config)
                        server = ApplicationApiServer(core)
                        server.start()
                    try:
                        for path in ('/v1/state?actor_id=alice', '/v1/events'):
                            with urlopen(Request(server.base_url + path, headers={
                                'Authorization': 'Bearer fake-api-secret'}), timeout=2) as response:
                                body = response.read().decode()
                            for forbidden in ('Captured observation', 'captured/document', 'attachment-1',
                                              'file_data', 'content_base64', 'fake-', 'decision_raw', 'trace'):
                                self.assertNotIn(forbidden, body)
                            self.assertNotIn(base64.b64encode(PDF).decode(), body)
                    finally:
                        server.close()

    def test_both_commands_recover_failed_finalization_without_reinvoking_model(self):
        from piecetogether.artifact_store import LocalArtifactStore
        for channel in ('email', 'telegram'):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), channel)
                data = json.loads(service.path.read_text())
                for permission in data['media_policy'].values():
                    permission['roles'] = ['source-evidence', 'persistent-domain-artifact']
                service.path.write_text(json.dumps(data))
                service.persist, service.mail, service.batches = True, {b'1': EMAIL}, [[service.update(1, 'document')]]
                with patch.object(LocalArtifactStore, 'finalize', side_effect=OSError('captured publication outage')):
                    service.run()
                    pending = service.core()
                    artifacts = pending.current_view()['artifacts']
                    self.assertEqual(len(artifacts), 1)
                    self.assertFalse(artifacts[0]['available'])
                core = service.core()
                self.assertTrue(core.current_view()['artifacts'][0]['available'])
                self.assertEqual(core.artifact_bytes(artifacts[0]['id']), PNG if channel == 'email' else PDF)
                service.mail, service.batches = {}, []
                service.run()
                self.assertEqual(len(service.model_inputs), 1)
                self.assertEqual(len(core.trusted_events()), 1)
                self.assertNotIn('content_base64', json.dumps(core.trusted_events()))

    def test_both_commands_recover_crash_after_accepted_handoff_without_resending(self):
        for channel in ('email', 'telegram'):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as directory:
                service = CapturedService(Path(directory), channel)
                service.mail, service.batches = {b'1': EMAIL}, [[service.update(1)]]
                with patch('piecetogether.history.expose', side_effect=KeyboardInterrupt()):
                    service.run()
                self.assertEqual(len(service.deliveries), 1)
                service.mail, service.batches = {}, []
                service.run()
                self.assertEqual((len(service.model_inputs), len(service.deliveries)), (1, 1))


if __name__ == '__main__':
    unittest.main()
