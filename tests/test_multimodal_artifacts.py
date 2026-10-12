import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from piecetogether.core import Bootstrap, Core, MediaPolicy


EMAIL_CAPABILITIES = ('receive', 'reply', 'text', 'threading', 'attachments')
PNG = b'\x89PNG\r\n\x1a\nimage-bytes'
EMAIL = b'''From: Alice <alice@example.com>\r
To: core@example.com\r
Message-ID: <image@example.com>\r
Date: Thu, 1 Jan 2026 12:00:00 +0000\r
Subject: Visual reference\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary="x"\r
\r
--x\r
Content-Type: text/plain; charset=utf-8\r
\r
Use this image\r
--x\r
Content-Type: image/png\r
Content-Transfer-Encoding: base64\r
Content-Disposition: attachment; filename="untrusted.png"\r
\r
iVBORw0KGgppbWFnZS1ieXRlcw==\r
--x--\r
'''


class CapturedTransport:
    def send(self, sender, recipient, message):
        return 'success'


class ByteAwareModel:
    accepts_media = True

    def __init__(self):
        self.media = ()

    def propose(self, inbound, version, context_pack):
        self.media = context_pack.media
        from piecetogether.proposals import SemanticProposal
        return SemanticProposal(1, version, inbound.id, (), 'Received the visual reference.')


class ContractProvider:
    def get(self, version):
        from piecetogether.contracts import DomainContract
        return DomainContract(version, artifact_types={'VisualEvidence': {
            'roles': ['ephemeral-evidence'], 'persistence': 'forbidden',
            'retention': {'metadata': 'retain', 'bytes': 'delete', 'provenance': 'retain'},
            'supersession': False,
        }})


class PerceptionContractProvider:
    def get(self, version):
        from piecetogether.contracts import DomainContract
        return DomainContract(version,
            entity_types={'Subject': {'creation': True, 'attributes': {}}},
            grounding_policies={'confirm': {'acceptance': ['explicit']}},
            claim_concepts={'visual_note': {'target_types': ['Subject'], 'value': {'type': 'string'},
                                              'grounding_policy': 'confirm'}},
            artifact_types={'VisualEvidence': {'roles': ['ephemeral-evidence'], 'persistence': 'forbidden',
                'retention': {'metadata': 'retain', 'bytes': 'delete', 'provenance': 'retain'},
                'supersession': False}})


class MultimodalArtifactTests(unittest.TestCase):
    def test_email_image_is_default_denied_without_contract_media_permission(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'email:alice@example.com': 'alice'},
                               channel='email', capabilities=EMAIL_CAPABILITIES,
                               artifact_store=root / 'artifacts',
                               email=EmailConfig('core@example.com', 'localhost', security='plain'))
            model = ByteAwareModel()
            result = Core(config, model=model,
                          channel=EmailChannel(config.database, config.email, CapturedTransport())).accept_transport(EMAIL)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(model.media, ())

    def test_email_image_is_staged_before_model_and_only_core_metadata_is_durable(self):
        from piecetogether.email_channel import EmailChannel

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(
                root / 'core.sqlite3', {'email:alice@example.com': 'alice'},
                channel='email', capabilities=EMAIL_CAPABILITIES,
                artifact_store=root / 'artifacts',
                media_policy={'image/png': MediaPolicy('VisualEvidence', ('ephemeral-evidence',))},
                email=__import__('piecetogether.email_channel', fromlist=['EmailConfig']).EmailConfig(
                    'core@example.com', 'localhost', security='plain'),
            )
            model = ByteAwareModel()
            core = Core(config, model=model, contract_provider=ContractProvider(),
                        channel=EmailChannel(config.database, config.email, CapturedTransport()))
            result = core.accept_transport(EMAIL)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(len(model.media), 1)
            self.assertEqual(model.media[0].media_type, 'image/png')
            self.assertEqual(model.media[0].content, PNG)
            inbound = core.inspect(result['communication_id'])['inbound']
            self.assertEqual(inbound['attachments'][0]['size'], len(PNG))
            self.assertNotIn('untrusted.png', json.dumps(inbound))
            self.assertNotIn('content_base64', json.dumps(inbound))
            self.assertNotIn(PNG.decode('latin1'), core.config.database.read_text(errors='ignore'))

    def test_telegram_photo_downloads_only_after_mapped_identity_then_stages_bytes(self):
        from piecetogether.telegram_channel import TelegramChannel, TelegramConfig

        jpeg = b'\xff\xd8\xffimage-bytes'

        class TelegramTransport:
            def __init__(self):
                self.downloads = []
                self.messages = []
            def fetch(self, offset):
                return []
            def download(self, file_id, maximum_size):
                self.downloads.append((file_id, maximum_size))
                return jpeg
            def send(self, chat_id, text, reply_to):
                self.messages.append((chat_id, text, reply_to))
                return 'success', 2

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'telegram:123': 'alice'}, channel='telegram',
                               capabilities=EMAIL_CAPABILITIES, artifact_store=root / 'artifacts',
                               media_policy={'image/jpeg': MediaPolicy('VisualEvidence', ('ephemeral-evidence',))},
                               telegram=TelegramConfig('900', 'token'),
                               secret_references={'token': 'PT_TEST_TELEGRAM_TOKEN'})
            transport, model = TelegramTransport(), ByteAwareModel()
            channel = TelegramChannel(config.database, config.telegram, transport, '900:test-token')
            core = Core(config, model=model, channel=channel, contract_provider=ContractProvider())
            update = {'update_id': 1, 'message': {'message_id': 1, 'date': 1767268800,
                'photo': [{'file_id': 'small', 'file_size': 4}, {'file_id': 'largest', 'file_size': len(jpeg)}],
                'from': {'id': 123, 'is_bot': False}, 'chat': {'id': 123, 'type': 'private'}}}
            result = core.accept_transport(update)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(transport.downloads, [('largest', 1_048_576)])
            self.assertEqual(model.media[0].content, jpeg)
            unknown = {**update, 'update_id': 2,
                       'message': {**update['message'], 'from': {'id': 456, 'is_bot': False},
                                   'chat': {'id': 456, 'type': 'private'}}}
            from piecetogether.telegram_channel import TelegramAcquisitionWorker
            worker = TelegramAcquisitionWorker(core, channel, type('Batch', (), {'fetch': lambda self, offset: [unknown]})(), config.telegram)
            self.assertEqual(worker.poll_once(), 1)
            self.assertEqual(transport.downloads, [('largest', 1_048_576)])
            self.assertEqual(core.inspect_telegram_acquisition()[-1]['outcome'], 'unmapped_identity')

    def test_telegram_getfile_transient_responses_do_not_become_checkpointable_media_rejections(self):
        from piecetogether.telegram_channel import BotApiTransport, TelegramConfig, TelegramUnavailable

        with patch.dict(os.environ, {'PT_TEST_BOT': '900:fake'}, clear=True):
            transport = BotApiTransport(TelegramConfig('900', 'token'), {'token': 'PT_TEST_BOT'})
            for status, body in ((503, {'ok': False}), (429, {'ok': False, 'parameters': {'retry_after': 7}}),
                                 (404, {'ok': False})):
                with self.subTest(status=status), patch.object(transport, '_request', return_value=(status, body)):
                    with self.assertRaises(TelegramUnavailable):
                        transport.download('file-id', 100)

    def test_telegram_transient_getfile_and_download_wire_responses_raise_unavailable(self):
        from piecetogether.telegram_channel import BotApiTransport, TelegramConfig, TelegramUnavailable

        class Response:
            def __init__(self, status, body): self.status, self.body = status, body
            def read(self, limit): return self.body
        class Connection:
            def __init__(self, response): self.response = response
            def connect(self): pass
            def request(self, *args): pass
            def getresponse(self): return self.response
            def close(self): pass

        with patch.dict(os.environ, {'PT_TEST_BOT': '900:fake'}, clear=True):
            for response in (Response(503, b'{"ok":false}'), Response(429, b'{"ok":false,"parameters":{"retry_after":3}}')):
                with self.subTest(status=response.status), patch(
                    'piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=Connection(response)):
                    with self.assertRaises(TelegramUnavailable):
                        BotApiTransport(TelegramConfig('900', 'token'), {'token': 'PT_TEST_BOT'}).download('file-id', 100)
            get_file = Response(200, json.dumps({'ok': True, 'result': {'file_id': 'file-id',
                'file_unique_id': 'unique', 'file_size': 3, 'file_path': 'documents/a.pdf'}}).encode())
            with patch('piecetogether.telegram_channel.http.client.HTTPSConnection', side_effect=[Connection(get_file), Connection(Response(503, b'busy'))]):
                with self.assertRaises(TelegramUnavailable):
                    BotApiTransport(TelegramConfig('900', 'token'), {'token': 'PT_TEST_BOT'}).download('file-id', 100)

    def test_telegram_definite_media_rejection_is_checkpointed_before_later_text(self):
        from piecetogether.telegram_channel import (
            TelegramAcquisitionWorker, TelegramChannel, TelegramConfig, UnsupportedTelegramMedia,
        )

        class Transport:
            def download(self, file_id, maximum_size):
                raise UnsupportedTelegramMedia('captured invalid file')
            def send(self, chat_id, text, reply_to):
                return 'success', 9
            def fetch(self, offset):
                return [
                    {'update_id': 1, 'message': {'message_id': 1, 'date': 1767268800,
                     'document': {'file_id': 'bad', 'file_size': 10, 'mime_type': 'application/pdf'},
                     'from': {'id': 123, 'is_bot': False}, 'chat': {'id': 123, 'type': 'private'}}},
                    {'update_id': 2, 'message': {'message_id': 2, 'date': 1767268801, 'text': 'still works',
                     'from': {'id': 123, 'is_bot': False}, 'chat': {'id': 123, 'type': 'private'}}},
                ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'telegram:123': 'actor'}, channel='telegram',
                               capabilities=EMAIL_CAPABILITIES, artifact_store=root / 'artifacts',
                               telegram=TelegramConfig('900', 'token'),
                               secret_references={'token': 'PT_TEST_TOKEN'})
            transport = Transport()
            channel = TelegramChannel(config.database, config.telegram, transport, '900:fake')
            core = Core(config, channel=channel)
            self.assertEqual(TelegramAcquisitionWorker(core, channel, transport, config.telegram).poll_once(), 2)
            outcomes = core.inspect_telegram_acquisition()
            self.assertEqual([(item['status'], item['outcome']) for item in outcomes],
                             [('unsupported', 'unsupported_message'), ('acquired', 'completed')])

    def test_byte_aware_provider_processes_text_without_artifact_store(self):
        class TextProvider(ByteAwareModel):
            def __init__(self):
                super().__init__()
                self.calls = 0
            def propose(self, inbound, version, context_pack):
                self.calls += 1
                return super().propose(inbound, version, context_pack)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = TextProvider()
            config = Bootstrap(root / 'core.sqlite3', {'development:s': 'actor'})
            result = Core(config, model=provider).accept({
                'channel': 'development', 'sender': 's', 'idempotency_key': 'text', 'text': 'hello',
                'sent_at': '2026-01-01T00:00:00Z',
            })
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(provider.calls, 1)
            self.assertEqual(provider.media, ())

    def test_captured_openai_artifact_claim_is_exposed_then_grounded_with_ingress_provenance(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig
        from piecetogether.openai_responses import OpenAIResponsesConfig, OpenAIResponsesModel
        from piecetogether.proposals import GroundingResolutionOperation, SemanticProposal

        class Response:
            status = 200
            def read(self, limit):
                if hasattr(self, 'confirmation_items'):
                    proposal = {'schema_version': 1, 'contract_version': 'development-v1',
                        'communication_id': self.communication_id, 'candidate_claims': [],
                        'draft_response': 'Confirmed.', 'intent': 'semantic_commit', 'semantic_revision': 0,
                        'operations': [{'kind': 'grounding_resolution', 'item_id': item, 'policy': 'confirm',
                            'evidence_communication_id': self.communication_id, 'acceptance_mode': 'explicit',
                            'outcome': 'accepted', 'rationale': None, 'evidence_span': None, 'successor_ids': []}
                            for item in self.confirmation_items]}
                else:
                    proposal = {'schema_version': 1, 'contract_version': 'development-v1',
                        'communication_id': self.communication_id, 'candidate_claims': [],
                        'draft_response': 'The visual note appears blue.', 'intent': 'candidate', 'operations': [
                            {'kind': 'entity', 'id': 'subject', 'entity_type': 'Subject', 'attributes': {}, 'action': 'create'},
                            {'kind': 'claim', 'id': 'visual', 'target_id': 'subject', 'concept': 'visual_note',
                             'value': 'blue', 'grounding_policy': 'confirm', 'artifact_id': 'attachment-1'},
                            {'kind': 'grounding_plan', 'policy': 'confirm', 'claim_ids': ['visual'],
                             'resolution_ids': ['subject'], 'acceptance_mode': 'explicit'},
                        ]}
                return json.dumps({'output': [{'content': [{'type': 'output_text', 'text': json.dumps(proposal)}]}]}).encode()

        class Connection:
            def request(self, method, path, body, headers): pass
            def getresponse(self): return Response()
            def close(self): pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'email:alice@example.com': 'alice'}, channel='email',
                capabilities=EMAIL_CAPABILITIES, artifact_store=root / 'artifacts',
                media_policy={'image/png': MediaPolicy('VisualEvidence', ('ephemeral-evidence',))},
                email=EmailConfig('core@example.com', 'localhost', security='plain'))
            model = OpenAIResponsesModel(OpenAIResponsesConfig('gpt-4.1-2025-04-14', 'key'), 'fake-key')
            original = model.propose
            def propose(inbound, version, pack):
                Response.communication_id = inbound.id
                return original(inbound, version, pack)
            model.propose = propose
            with patch('piecetogether.openai_responses.http.client.HTTPSConnection', return_value=Connection()):
                first = Core(config, model=model, contract_provider=PerceptionContractProvider(),
                    channel=EmailChannel(config.database, config.email, CapturedTransport()))
                initial = first.accept_transport(EMAIL)
            items = tuple(item['id'] for item in first.inspect(initial['communication_id'])['grounding_items'])

            Response.confirmation_items = items
            outbound_id = first.inspect(initial['communication_id'])['outbound']['transport_message_id']
            reply = EMAIL.replace(b'<image@example.com>', b'<confirm@example.com>').replace(
                b'Use this image', b'Yes').replace(
                b'Subject:', b'In-Reply-To: ' + outbound_id.encode() + b'\r\nSubject:')
            with patch('piecetogether.openai_responses.http.client.HTTPSConnection', return_value=Connection()):
                completed = Core(config, model=model, contract_provider=PerceptionContractProvider(),
                    channel=EmailChannel(config.database, config.email, CapturedTransport())).accept_transport(reply)
            self.assertEqual((initial['status'], completed['status']), ('completed', 'completed'))
            claim = Core(config, contract_provider=PerceptionContractProvider()).inspect_history()['claims'][0]
            self.assertEqual(claim['provenance']['artifact_ingress']['attachment_id'], 'attachment-1')

    def test_configured_secrets_and_credential_urls_never_become_evidence_or_provider_output(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig
        from piecetogether.proposals import SemanticProposal

        class LeakingModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'fake-deployment-secret')

        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'PT_TEST_SECRET': 'fake-deployment-secret'}):
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'email:alice@example.com': 'alice'}, channel='email',
                               capabilities=('receive', 'reply', 'text', 'threading'),
                               secret_references={'provider': 'PT_TEST_SECRET'},
                               email=EmailConfig('core@example.com', 'localhost', security='plain'))
            core = Core(config, model=LeakingModel(),
                        channel=EmailChannel(config.database, config.email, CapturedTransport()))
            rejected_input = EMAIL.replace(b'Use this image', b'https://user:secret@example.test/x')
            with self.assertRaises(ValueError):
                core.accept_transport(rejected_input)
            safe = EMAIL.replace(b'Content-Type: multipart/mixed; boundary="x"', b'Content-Type: text/plain').split(b'--x\r\n')[0] + b'hello\r\n'
            result = core.accept_transport(safe)
            self.assertEqual(result['status'], 'retryable')
            self.assertNotIn('fake-deployment-secret', json.dumps(core.inspect(result['communication_id'])))

    def test_secret_model_output_is_never_captured_when_validation_is_stale_or_rejected(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig
        from piecetogether.proposals import EntityOperation, SemanticProposal

        class LeakingModel:
            def __init__(self, contract_version, operations=()):
                self.contract_version, self.operations = contract_version, operations
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, self.contract_version, inbound.id, (), 'FAKE_PROVIDER_SECRET',
                                        self.operations)

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {'PT_TEST_SECRET': 'FAKE_PROVIDER_SECRET'}, clear=True,
        ):
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'email:alice@example.com': 'alice'}, channel='email',
                capabilities=('receive', 'reply', 'text', 'threading'),
                secret_references={'provider': 'PT_TEST_SECRET'},
                email=EmailConfig('core@example.com', 'localhost', security='plain'))
            text_email = EMAIL.replace(b'Content-Type: multipart/mixed; boundary="x"', b'Content-Type: text/plain').split(b'--x\r\n')[0] + b'ordinary\r\n'
            for suffix, model in (
                ('stale', LeakingModel('wrong-version')),
                ('rejected', LeakingModel('development-v1', (EntityOperation('forbidden', 'Forbidden'),))),
            ):
                with self.subTest(suffix=suffix):
                    raw = text_email.replace(b'<image@example.com>', f'<{suffix}@example.com>'.encode())
                    core = Core(config, model=model,
                        channel=EmailChannel(config.database, config.email, CapturedTransport()))
                    outcome = core.accept_transport(raw)
                    self.assertEqual(outcome['status'], 'retryable')
                    self.assertNotIn('FAKE_PROVIDER_SECRET', json.dumps(core.inspect(outcome['communication_id'])))

    def test_static_openai_model_sends_inline_image_and_restores_only_versioned_proposal(self):
        from piecetogether.context import ContextPack, MediaAttachment
        from piecetogether.core import Communication
        from piecetogether.openai_responses import OpenAIResponsesConfig, OpenAIResponsesModel
        from piecetogether.proposals import SemanticProposal

        communication = Communication('turn', 'development', 's', 'a', 'inbound', 'k', 'look',
                                      '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        pack = ContextPack('a', 'development-v1', 0, media=(
            MediaAttachment('image', 'image/png', 'VisualEvidence', ('ephemeral-evidence',),
                            __import__('hashlib').sha256(PNG).hexdigest(), len(PNG), PNG),))
        proposal = {'schema_version': 1, 'contract_version': 'development-v1',
                    'communication_id': 'turn', 'candidate_claims': [],
                    'draft_response': 'I can inspect the image.', 'operations': []}

        class Response:
            status = 200
            def read(self, limit):
                return json.dumps({'output': [{'content': [{'type': 'output_text',
                                                             'text': json.dumps(proposal)}]}]}).encode()

        class Connection:
            def __init__(self): self.requested = None
            def request(self, method, path, body, headers): self.requested = (method, path, json.loads(body), headers)
            def getresponse(self): return Response()
            def close(self): pass

        connection = Connection()
        model = OpenAIResponsesModel(OpenAIResponsesConfig('gpt-4.1-2025-04-14', 'key'), 'fake-secret')
        with patch('piecetogether.openai_responses.http.client.HTTPSConnection', return_value=connection):
            result = model.propose(communication, 'development-v1', pack)
        self.assertEqual(result.communication_id, 'turn')
        self.assertEqual(connection.requested[0:2], ('POST', '/v1/responses'))
        content = connection.requested[2]['input'][0]['content']
        self.assertEqual(content[1]['type'], 'input_image')
        instruction = json.loads(content[0]['text'])
        self.assertEqual(set(instruction['proposal_schema']),
                         {'schema_version', 'contract_version', 'communication_id', 'candidate_claims',
                          'draft_response', 'intent', 'semantic_revision', 'operations'})
        self.assertNotIn('semantic_commit_example', instruction['proposal_schema'])
        self.assertEqual(SemanticProposal.from_dict(instruction['proposal_schema']).intent, 'candidate')
        self.assertEqual(SemanticProposal.from_dict(instruction['semantic_commit_example']).intent,
                         'semantic_commit')
        self.assertEqual(instruction['semantic_commit_example']['semantic_revision'], 0)
        self.assertIn('"attachment_id": "image"', content[0]['text'])
        self.assertIn('data:image/png;base64,iVBORw0KGgppbWFnZS1ieXRlcw==', content[1]['image_url'])
        self.assertFalse(connection.requested[2]['store'])

    def test_receive_email_cli_stages_permitted_image_and_calls_captured_openai(self):
        from piecetogether.__main__ import main

        class Response:
            status = 200
            def __init__(self, proposal): self.proposal = proposal
            def read(self, limit): return json.dumps({'output': [{'content': [
                {'type': 'output_text', 'text': json.dumps(self.proposal)}]}]}).encode()
        class OpenAIConnection:
            def request(self, method, path, body, headers):
                request = json.loads(body)
                self.content = request['input'][0]['content']
                communication_id = json.loads(self.content[0]['text'])['communication']['id']
                self.proposal = {'schema_version': 1, 'contract_version': 'development-v1',
                    'communication_id': communication_id, 'candidate_claims': [],
                    'draft_response': 'Captured image.', 'operations': []}
            def getresponse(self): return Response(self.proposal)
            def close(self): pass
        class Smtp:
            def ehlo_or_helo_if_needed(self): pass
            def mail(self, sender): return 250, b'ok'
            def rcpt(self, recipient): return 250, b'ok'
            def data(self, message): return 250, b'ok'
            def close(self): pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'contract.json').write_text(json.dumps({
                'schema_version': 1, 'version': 'development-v1', 'entity_types': {},
                'relationship_types': {}, 'claim_concepts': {}, 'grounding_policies': {},
                'emergent_concepts': {'allowed': False}, 'artifact_types': {'VisualEvidence': {
                    'roles': ['ephemeral-evidence'], 'persistence': 'forbidden',
                    'retention': {'metadata': 'retain', 'bytes': 'delete', 'provenance': 'retain'},
                    'supersession': False}}}))
            path = root / 'deployment.json'
            path.write_text(json.dumps({'database': 'core.sqlite3', 'channel': 'email',
                'model': 'openai-responses', 'contract': 'contract.json', 'contract_version': 'development-v1',
                'capabilities': list(EMAIL_CAPABILITIES), 'artifact_store': 'artifacts',
                'media_policy': {'image/png': {'artifact_type': 'VisualEvidence', 'roles': ['ephemeral-evidence']}},
                'identities': {'email:alice@example.com': 'alice'},
                'email': {'address': 'core@example.com', 'host': 'localhost', 'security': 'plain'},
                'openai': {'model_id': 'gpt-4.1-2025-04-14', 'secret_reference': 'openai'},
                'secret_references': {'openai': 'PT_TEST_OPENAI'}}))
            connection, stdout = OpenAIConnection(), io.StringIO()
            with (patch.dict(os.environ, {'PT_TEST_OPENAI': 'fake-openai-secret'}, clear=True),
                  patch.object(sys, 'argv', ['piecetogether', '--config', str(path), '--receive-email']),
                  patch.object(sys, 'stdin', type('Input', (), {'buffer': io.BytesIO(EMAIL)})()),
                  patch.object(sys, 'stdout', stdout),
                  patch('piecetogether.openai_responses.http.client.HTTPSConnection', return_value=connection),
                  patch('piecetogether.email_channel.smtplib.SMTP', return_value=Smtp())):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(stdout.getvalue())['status'], 'completed')
            self.assertEqual(connection.content[1]['type'], 'input_image')
            self.assertIn('data:image/png;base64,iVBORw0KGgppbWFnZS1ieXRlcw==', connection.content[1]['image_url'])

    def test_serve_email_command_acquires_mixed_media_and_sends_threaded_reply(self):
        from piecetogether.__main__ import main

        pdf = b'%PDF-1.4\nfixture'
        mixed = EMAIL.replace(b'--x--', b'--x\r\nContent-Type: application/pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0xLjQKZml4dHVyZQ==\r\n--x--')
        class Imap:
            def login(self, *args): pass
            def select(self, *args, **kwargs): return 'OK', [b'1']
            def response(self, name): return 'UIDVALIDITY', [b'7']
            def uid(self, command, *args):
                return ('OK', [b'1']) if command == 'search' else ('OK', [(b'1 (UID 1 BODY[] {1})', mixed)])
            def logout(self): pass
        class Smtp:
            def __init__(self): self.raw = None
            def ehlo_or_helo_if_needed(self): pass
            def mail(self, sender): return 250, b'ok'
            def rcpt(self, recipient): return 250, b'ok'
            def data(self, raw): self.raw = raw; return 250, b'ok'
            def close(self): pass
        class Response:
            status = 200
            def __init__(self, proposal): self.proposal = proposal
            def read(self, limit): return json.dumps({'output': [{'content': [{'type': 'output_text', 'text': json.dumps(self.proposal)}]}]}).encode()
        class OpenAI:
            def request(self, method, path, body, headers):
                self.content = json.loads(body)['input'][0]['content']
                cid = json.loads(self.content[0]['text'])['communication']['id']
                self.proposal = {'schema_version': 1, 'contract_version': 'development-v1', 'communication_id': cid,
                    'candidate_claims': [], 'draft_response': 'Captured mixed media.', 'operations': []}
            def getresponse(self): return Response(self.proposal)
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'contract.json').write_text(json.dumps({'schema_version': 1, 'version': 'development-v1',
                'entity_types': {}, 'relationship_types': {}, 'claim_concepts': {}, 'grounding_policies': {},
                'emergent_concepts': {'allowed': False}, 'artifact_types': {'VisualEvidence': {'roles': ['ephemeral-evidence'],
                'persistence': 'forbidden', 'retention': {'metadata': 'retain', 'bytes': 'delete', 'provenance': 'retain'}, 'supersession': False}}}))
            config = root / 'deployment.json'
            config.write_text(json.dumps({'database': 'core.sqlite3', 'channel': 'email', 'model': 'openai-responses',
                'contract': 'contract.json', 'contract_version': 'development-v1', 'capabilities': list(EMAIL_CAPABILITIES),
                'artifact_store': 'artifacts', 'media_policy': {mime: {'artifact_type': 'VisualEvidence', 'roles': ['ephemeral-evidence']} for mime in ('image/png', 'application/pdf')},
                'identities': {'email:alice@example.com': 'alice'}, 'email': {'address': 'core@example.com', 'host': 'localhost', 'security': 'plain'},
                'imap': {'host': 'imap.example.com', 'username': 'core', 'password_secret_reference': 'imap', 'security': 'tls', 'poll_seconds': 1},
                'openai': {'model_id': 'gpt-4.1-2025-04-14', 'secret_reference': 'openai'}, 'secret_references': {'openai': 'PT_OPENAI', 'imap': 'PT_IMAP'}}))
            smtp, openai = Smtp(), OpenAI()
            with (patch.dict(os.environ, {'PT_OPENAI': 'fake-openai', 'PT_IMAP': 'fake-imap'}, clear=True),
                  patch.object(sys, 'argv', ['piecetogether', '--config', str(config), '--serve-email']),
                  patch('piecetogether.email_channel.imaplib.IMAP4_SSL', return_value=Imap()),
                  patch('piecetogether.email_channel.smtplib.SMTP', return_value=smtp),
                  patch('piecetogether.openai_responses.http.client.HTTPSConnection', return_value=openai),
                  patch('piecetogether.email_channel.time.sleep', side_effect=KeyboardInterrupt())):
                self.assertEqual(main(), 0)
            self.assertEqual([part['type'] for part in openai.content[1:]], ['input_image', 'input_file'])
            self.assertIn(b'In-Reply-To: <image@example.com>', smtp.raw)
            with patch.dict(os.environ, {'PT_OPENAI': 'fake-openai', 'PT_IMAP': 'fake-imap'}, clear=True):
                core = Core(Bootstrap.from_file(config))
            acquired = core.inspect_email_acquisition()
            record = core.inspect(acquired[0]['communication_id'])
            self.assertEqual(record['inbound']['actor_id'], 'alice')
            self.assertEqual({item['size'] for item in record['inbound']['attachments']}, {len(PNG), len(pdf)})

    def test_static_openai_model_sends_pdf_as_input_file(self):
        from piecetogether.context import ContextPack, MediaAttachment
        from piecetogether.core import Communication
        from piecetogether.openai_responses import OpenAIResponsesConfig, OpenAIResponsesModel

        pdf = b'%PDF-1.4\nfixture'
        inbound = Communication('pdf-turn', 'development', 's', 'a', 'inbound', 'k', 'read',
                                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        pack = ContextPack('a', 'development-v1', 0, media=(
            MediaAttachment('document', 'application/pdf', 'VisualEvidence', ('ephemeral-evidence',),
                            __import__('hashlib').sha256(pdf).hexdigest(), len(pdf), pdf),))
        output = {'schema_version': 1, 'contract_version': 'development-v1', 'communication_id': 'pdf-turn',
                  'candidate_claims': [], 'draft_response': 'Read.', 'operations': []}
        class Response:
            status = 200
            def read(self, limit): return json.dumps({'output': [{'content': [
                {'type': 'output_text', 'text': json.dumps(output)}]}]}).encode()
        class Connection:
            def request(self, method, path, body, headers): self.body = json.loads(body)
            def getresponse(self): return Response()
            def close(self): pass
        connection = Connection()
        model = OpenAIResponsesModel(OpenAIResponsesConfig('gpt-4.1-2025-04-14', 'key'), 'fake-key')
        with patch('piecetogether.openai_responses.http.client.HTTPSConnection', return_value=connection):
            model.propose(inbound, 'development-v1', pack)
        file_part = connection.body['input'][0]['content'][1]
        self.assertEqual(file_part['type'], 'input_file')
        self.assertTrue(file_part['file_data'].startswith('data:application/pdf;base64,JVBERi0xLjQK'))

    def test_prestage_sweeper_cannot_observe_an_unregistered_stage_at_lock_exit(self):
        from piecetogether.artifact_store import LocalArtifactStore
        from piecetogether.email_channel import EmailChannel, EmailConfig

        class ExitSweeperStore(LocalArtifactStore):
            core = None
            staged = False
            swept = False
            removed = None

            def stage(self, *args):
                result = super().stage(*args)
                self.staged = True
                return result

            @contextmanager
            def maintenance_lock(self):
                with super().maintenance_lock():
                    yield
                if self.staged and not self.swept:
                    self.swept = True
                    self.removed = self.core.cleanup_artifact_staging(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'email:alice@example.com': 'alice'},
                channel='email', capabilities=EMAIL_CAPABILITIES, artifact_store=root / 'artifacts',
                media_policy={'image/png': MediaPolicy('VisualEvidence', ('ephemeral-evidence',))},
                email=EmailConfig('core@example.com', 'localhost', security='plain'))
            store, model = ExitSweeperStore(root / 'artifacts'), ByteAwareModel()
            core = Core(config, model=model, contract_provider=ContractProvider(), artifact_store=store,
                channel=EmailChannel(config.database, config.email, CapturedTransport()))
            store.core = core
            self.assertEqual(core.accept_transport(EMAIL)['status'], 'completed')
            self.assertEqual(store.removed, [])
            self.assertEqual(model.media[0].content, PNG)

    def test_email_media_recovery_uses_staged_bytes_without_the_original_mime(self):
        from piecetogether.email_channel import EmailChannel, EmailConfig

        class CrashModel(ByteAwareModel):
            def propose(self, *args):
                raise KeyboardInterrupt()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = Bootstrap(root / 'core.sqlite3', {'email:alice@example.com': 'alice'},
                               channel='email', capabilities=EMAIL_CAPABILITIES,
                               artifact_store=root / 'artifacts',
                               media_policy={'image/png': MediaPolicy('VisualEvidence', ('ephemeral-evidence',))},
                               email=EmailConfig('core@example.com', 'localhost', security='plain'))
            first = Core(config, model=CrashModel(), contract_provider=ContractProvider(),
                         channel=EmailChannel(config.database, config.email, CapturedTransport()))
            with self.assertRaises(KeyboardInterrupt):
                first.accept_transport(EMAIL)
            self.assertEqual(first.cleanup_artifact_staging(0), [])
            recovered_model = ByteAwareModel()
            recovered = Core(config, model=recovered_model, contract_provider=ContractProvider(),
                             channel=EmailChannel(config.database, config.email, CapturedTransport()))
            outcomes, _ = recovered.recover_email_pending()
            self.assertEqual(outcomes[0]['status'], 'completed')
            self.assertEqual(recovered_model.media[0].content, PNG)


if __name__ == '__main__':
    unittest.main()
