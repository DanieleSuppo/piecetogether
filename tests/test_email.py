import io
import json
import smtplib
import os
import subprocess
import sys
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from piecetogether.core import Bootstrap, Core
from piecetogether.contracts import DomainContract
from piecetogether.proposals import EntityOperation, GroundingPlanOperation, GroundingResolutionOperation, SemanticProposal


CAPABILITIES = ('receive', 'reply', 'text', 'threading')
RAW = b'''From: Alice <alice@example.com>\r
To: core@example.com\r
Message-ID: <first@example.com>\r
Date: Thu, 1 Jan 2026 12:00:00 +0000\r
Subject: =?utf-8?q?Scelta_blu?=\r
MIME-Version: 1.0\r
Content-Type: text/plain; charset=utf-8\r
Content-Transfer-Encoding: quoted-printable\r
\r
La scelta =C3=A8 blu.\r
'''


class CapturedTransport:
    def __init__(self, result='success'):
        self.result = result
        self.messages = []

    def send(self, sender, recipient, message):
        self.messages.append((sender, recipient, message))
        return self.result


class CapturedSmtp:
    def __init__(self, result=250, close_error=False):
        self.result, self.close_error = result, close_error
        self.messages = []
        self.tls = None
        self.auth = None
    def starttls(self, context):
        self.tls = context
    def ehlo_or_helo_if_needed(self):
        pass
    def login(self, username, password):
        self.auth = (username, password)
    def mail(self, sender):
        return 250, b'ok'
    def rcpt(self, recipient):
        return 250, b'ok'
    def data(self, raw):
        self.messages.append(raw)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result, b'captured response'
    def close(self):
        if self.close_error:
            raise OSError('not-for-output')


class ContractProvider:
    def get(self, version):
        return DomainContract(version, entity_types={'Subject': {'creation': True, 'attributes': {}}},
                              grounding_policies={'p': {'acceptance': ['explicit']}})


class CapturedModel:
    def __init__(self, item=None):
        self.item = item
        self.calls = 0

    def propose(self, inbound, version, context_pack):
        self.calls += 1
        operations = ((GroundingResolutionOperation(self.item, 'p', inbound.id, 'explicit', 'accepted'),)
                      if self.item else (EntityOperation('subject', 'Subject'),
                                        GroundingPlanOperation('p', (), 'explicit', ('subject',))))
        return SemanticProposal(1, version, inbound.id, (), 'Interpretazione', operations,
                                intent='semantic_commit' if self.item else 'candidate',
                                semantic_revision=0 if self.item else None)


class EmailTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'deployment.json'
        self.path.write_text(json.dumps({
            'database': 'core.sqlite3', 'channel': 'email',
            'capabilities': list(CAPABILITIES),
            'identities': {'email:alice@example.com': 'alice'},
            'email': {'address': 'core@example.com', 'host': 'localhost',
                      'port': 2525, 'security': 'plain'},
        }))

    def core(self, transport=None, **kwargs):
        from piecetogether.email_channel import EmailChannel
        config = Bootstrap.from_file(self.path)
        channel = EmailChannel(config.database, config.email, transport or CapturedTransport())
        return Core(config, channel=channel, **kwargs)

    def test_email_normalizes_identity_text_and_thread_and_records_canonical_reply(self):
        transport = CapturedTransport()
        core = self.core(transport)
        result = core.accept_transport(RAW)
        self.assertEqual(result['status'], 'completed')
        record = core.inspect(result['communication_id'])
        self.assertEqual(record['inbound']['actor_id'], 'alice')
        self.assertEqual(record['inbound']['sender'], 'alice@example.com')
        self.assertEqual(record['inbound']['text'], 'La scelta è blu.')
        self.assertEqual(record['inbound']['thread_id'], '<first@example.com>')
        self.assertEqual(record['outbound']['reply_to'], result['communication_id'])
        self.assertEqual(record['outbound']['sender'], 'core@example.com')
        self.assertEqual(record['outbound']['recipient'], 'alice@example.com')
        self.assertEqual(core.channel.capabilities, CAPABILITIES)
        sender, recipient, raw = transport.messages[0]
        reply = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual((sender, recipient), ('core@example.com', 'alice@example.com'))
        self.assertEqual(str(reply['In-Reply-To']), '<first@example.com>')
        self.assertEqual(str(reply['References']), '<first@example.com>')
        self.assertEqual(str(reply['Subject']), 'Re: Scelta blu')
        self.assertEqual(str(reply['Message-ID']), record['outbound']['transport_message_id'])
        self.assertEqual(reply.get_content().strip(), result['reply'])
        self.assertEqual(core.current_view()['assertion_sets'], [])
        self.assertEqual(set(result), {'communication_id', 'status', 'reply'})

    def test_failed_handoff_is_unexposed_and_retry_after_restart_reuses_one_message(self):
        failed_transport = CapturedTransport('failure')
        model = CapturedModel()
        first_core = self.core(failed_transport, model=model, contract_provider=ContractProvider())
        failed = first_core.accept_transport(RAW)
        self.assertEqual((failed['status'], failed['reply']), ('retryable', None))
        record = first_core.inspect(failed['communication_id'])
        self.assertEqual(record['grounding_items'], [])
        self.assertEqual(record['trace']['delivery_result'], 'failure')
        retry_transport = CapturedTransport()
        retry_core = self.core(retry_transport, model=model, contract_provider=ContractProvider())
        retried = retry_core.accept_transport(RAW)
        self.assertEqual(retried['status'], 'completed')
        self.assertEqual(model.calls, 1)
        self.assertEqual(retry_transport.messages, failed_transport.messages)
        self.assertEqual(len(retry_core.inspect(retried['communication_id'])['grounding_items']), 1)
        again = self.core(CapturedTransport(), model=model, contract_provider=ContractProvider())
        self.assertEqual(again.accept_transport(RAW), retried)
        self.assertEqual(again.channel.transport.messages, [])

    def test_indeterminate_and_interrupted_handoffs_survive_restart_without_exposure_or_resend(self):
        class Interrupted(CapturedTransport):
            def send(self, sender, recipient, message):
                super().send(sender, recipient, message)
                raise KeyboardInterrupt()
        for transport in (CapturedTransport('indeterminate'), Interrupted()):
            with self.subTest(transport=type(transport).__name__):
                data = json.loads(self.path.read_text())
                data['database'] = type(transport).__name__ + '.sqlite3'
                self.path.write_text(json.dumps(data))
                core = self.core(transport, model=CapturedModel(), contract_provider=ContractProvider())
                if isinstance(transport, Interrupted):
                    with self.assertRaises(KeyboardInterrupt):
                        core.accept_transport(RAW)
                else:
                    result = core.accept_transport(RAW)
                    self.assertEqual(result['reply'], None)
                fresh = CapturedTransport()
                model = CapturedModel()
                restarted = self.core(fresh, model=model, contract_provider=ContractProvider())
                result = restarted.accept_transport(RAW)
                self.assertEqual((result['status'], result['reply']), ('retryable', None))
                record = restarted.inspect(result['communication_id'])
                self.assertEqual(record['grounding_items'], [])
                self.assertEqual(record['trace']['delivery_result'], 'indeterminate')
                self.assertEqual(fresh.messages, [])
                self.assertEqual(model.calls, 0)
                self.assertEqual(restarted.inspect_history()['revision'], 0)

    def test_email_reply_to_accepted_message_can_ground_with_configured_actor(self):
        core = self.core(model=CapturedModel(), contract_provider=ContractProvider())
        initial = core.accept_transport(RAW)
        record = core.inspect(initial['communication_id'])
        item = record['grounding_items'][0]['id']
        message_id = record['outbound']['transport_message_id']
        reply = RAW.replace(b'<first@example.com>', b'<second@example.com>').replace(
            b'La scelta =C3=A8 blu.', b'Yes').replace(
            b'Subject:', ('In-Reply-To: ' + message_id + '\r\nReferences: <first@example.com> ' + message_id + '\r\nSubject:').encode())
        accepting = self.core(model=CapturedModel(item), contract_provider=ContractProvider())
        result = accepting.accept_transport(reply)
        self.assertEqual(result['status'], 'completed')
        inbound = accepting.inspect(result['communication_id'])['inbound']
        self.assertEqual(inbound['reply_to'], record['outbound']['id'])
        self.assertEqual(inbound['thread_id'], '<first@example.com>')
        self.assertEqual(accepting.inspect_history()['revision'], 1)
        self.assertEqual(accepting.current_view()['entities'][0]['provenance']['actor_id'], 'alice')

    def test_documented_email_process_path_records_failure_without_exposing_operator_data(self):
        from piecetogether.__main__ import main
        command = [sys.executable, '-m', 'piecetogether', '--config', str(self.path), '--receive-email']
        output = io.StringIO()
        with (patch.object(sys, 'argv', ['piecetogether', *command[3:]]),
              patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(RAW))),
              patch.object(sys, 'stdout', output),
              patch('piecetogether.email_channel.smtplib.SMTP', side_effect=OSError('not-for-output'))):
            self.assertEqual(main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(set(result), {'communication_id', 'reply', 'status'})
        self.assertEqual((result['status'], result['reply']), ('retryable', None))
        inspect = subprocess.run(command[:-1] + ['--inspect', result['communication_id']], capture_output=True)
        record = json.loads(inspect.stdout)
        self.assertEqual(record['inbound']['actor_id'], 'alice')
        self.assertEqual(record['trace']['delivery_result'], 'failure')
        self.assertEqual(record['grounding_items'], [])
        invalid = subprocess.run(command, input=b'not an email', capture_output=True)
        self.assertEqual(invalid.returncode, 0)
        self.assertEqual(json.loads(invalid.stdout), {'error': 'invalid_communication'})

    def test_accepted_handoff_recovers_after_core_interruption_without_another_send(self):
        from piecetogether.email_channel import EmailChannel
        class InterruptedAfterAcceptance(EmailChannel):
            def deliver(self, outbound):
                assert super().deliver(outbound) == 'success'
                raise KeyboardInterrupt()
        config = Bootstrap.from_file(self.path)
        first = CapturedTransport()
        core = Core(config, model=CapturedModel(), contract_provider=ContractProvider(),
                    channel=InterruptedAfterAcceptance(config.database, config.email, first))
        with self.assertRaises(KeyboardInterrupt):
            core.accept_transport(RAW)
        second = CapturedTransport()
        model = CapturedModel()
        restarted = self.core(second, model=model, contract_provider=ContractProvider())
        result = restarted.accept_transport(RAW)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(first.messages), 1)
        self.assertEqual(second.messages, [])
        self.assertEqual(model.calls, 0)
        self.assertEqual(len(restarted.inspect(result['communication_id'])['grounding_items']), 1)

    def test_email_validation_rejects_unknown_identity_attachments_and_malformed_encoding(self):
        model = CapturedModel()
        core = self.core(model=model, contract_provider=ContractProvider())
        for raw in (RAW.replace(b'alice@example.com', b'unknown@example.com'),
                    RAW.replace(b'Content-Type: text/plain;', b'Content-Disposition: attachment; filename=test.txt\r\nContent-Type: text/plain;'),
                    RAW.replace(b'=C3=A8', b'=FF'),
                    RAW.replace(b'Message-ID:', b'Message-ID: <duplicate@example.com>\r\nMessage-ID:'),
                    RAW.replace(b'<first@example.com>', b'not-an-id')):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    core.accept_transport(raw)
        self.assertEqual(model.calls, 0)
        self.assertEqual(core.current_view()['revision'], 0)

    def test_default_deny_email_does_not_forward_model_historical_retrieval_draft(self):
        class LeakingModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'private-history-and-trace',
                                        response_intent='retrieval')
        transport = CapturedTransport()
        core = self.core(transport, model=LeakingModel())
        result = core.accept_transport(RAW)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('private-history-and-trace', json.dumps(result))
        self.assertNotIn(b'private-history-and-trace', b''.join(item[2] for item in transport.messages))

    def test_smtp_security_modes_and_delivery_outcomes_use_captured_provider_responses(self):
        from piecetogether.email_channel import EmailConfig, SmtpTransport
        for security in ('plain', 'starttls', 'tls'):
            for response, expected in ((250, 'success'), (550, 'failure'),
                                       (smtplib.SMTPDataError(554, b'refused'), 'failure'),
                                       (TimeoutError('unknown acceptance'), 'indeterminate')):
                with self.subTest(security=security, response=response):
                    smtp = CapturedSmtp(response)
                    config = EmailConfig('core@example.com', 'localhost', security=security)
                    with (patch('piecetogether.email_channel.smtplib.SMTP', return_value=smtp),
                          patch('piecetogether.email_channel.smtplib.SMTP_SSL', return_value=smtp) as tls):
                        result = SmtpTransport(config, {}).send('core@example.com', 'alice@example.com', RAW)
                    self.assertEqual(result, expected)
                    self.assertEqual(smtp.messages, [RAW])
                    if security == 'starttls':
                        self.assertTrue(smtp.tls.check_hostname)
                    if security == 'tls':
                        self.assertTrue(tls.call_args.kwargs['context'].check_hostname)
        smtp = CapturedSmtp(close_error=True)
        with patch('piecetogether.email_channel.smtplib.SMTP', return_value=smtp):
            self.assertEqual(SmtpTransport(EmailConfig('core@example.com', 'localhost', security='plain'), {}).send(
                'core@example.com', 'alice@example.com', RAW), 'success')

    def test_configured_cli_supports_all_declared_smtp_modes_and_secret_references(self):
        from piecetogether.__main__ import main
        for mode in ('plain', 'starttls', 'tls'):
            with self.subTest(mode=mode):
                data = json.loads(self.path.read_text())
                data['database'] = mode + '.sqlite3'
                data['email'] = {'address': 'core@example.com', 'host': 'localhost', 'security': mode}
                data['secret_references'] = {'smtp-password': 'PT_SMTP_PASSWORD'}
                if mode != 'plain':
                    data['email'].update(username='operator', password_secret_reference='smtp-password')
                self.path.write_text(json.dumps(data))
                output, smtp = io.StringIO(), CapturedSmtp()
                with (patch.object(sys, 'argv', ['piecetogether', '--config', str(self.path), '--receive-email']),
                      patch.object(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(RAW))),
                      patch.object(sys, 'stdout', output),
                      patch.dict('os.environ', {'PT_SMTP_PASSWORD': 'secret-not-for-output'}, clear=True),
                      patch('piecetogether.email_channel.smtplib.SMTP', return_value=smtp),
                      patch('piecetogether.email_channel.smtplib.SMTP_SSL', return_value=smtp)):
                    self.assertEqual(main(), 0)
                self.assertEqual(json.loads(output.getvalue())['status'], 'completed')
                self.assertEqual(len(smtp.messages), 1)
                if mode != 'plain':
                    self.assertEqual(smtp.auth, ('operator', 'secret-not-for-output'))
                self.assertNotIn('secret-not-for-output', output.getvalue())
                self.assertNotIn(b'secret-not-for-output', smtp.messages[0])

    def test_reply_remains_deliverable_at_reference_budget_without_losing_thread_root(self):
        refs = ' '.join('<ancestor%d@example.com>' % index for index in range(32))
        raw = RAW.replace(b'Subject:', ('References: ' + refs + '\r\nSubject:').encode())
        transport = CapturedTransport()
        core = self.core(transport)
        result = core.accept_transport(raw)
        self.assertEqual(result['status'], 'completed')
        reply = BytesParser(policy=policy.default).parsebytes(transport.messages[0][2])
        self.assertEqual(str(reply['References']).split()[0], '<ancestor0@example.com>')
        self.assertEqual(str(reply['References']).split()[-1], '<first@example.com>')
        self.assertEqual(len(str(reply['References']).split()), 32)

    def test_failed_and_indeterminate_delivery_cannot_be_grounded_by_a_later_email(self):
        for outcome in ('failure', 'indeterminate'):
            with self.subTest(outcome=outcome):
                data = json.loads(self.path.read_text())
                data['database'] = outcome + '.sqlite3'
                self.path.write_text(json.dumps(data))
                core = self.core(CapturedTransport(outcome), model=CapturedModel(), contract_provider=ContractProvider())
                initial = core.accept_transport(RAW)
                record = core.inspect(initial['communication_id'])
                message_id = record['outbound']['transport_message_id']
                reply = RAW.replace(b'<first@example.com>', b'<confirmation@example.com>').replace(
                    b'La scelta =C3=A8 blu.', b'Yes').replace(b'Subject:', ('In-Reply-To: ' + message_id + '\r\nSubject:').encode())
                accepting = self.core(model=CapturedModel('not-exposed'), contract_provider=ContractProvider())
                result = accepting.accept_transport(reply)
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(accepting.inspect_history()['revision'], 0)
                self.assertEqual(accepting.inspect_history()['events'], [])

    def test_reply_from_another_configured_actor_cannot_resolve_the_original_grounding(self):
        data = json.loads(self.path.read_text())
        data['identities']['email:bob@example.com'] = 'bob'
        self.path.write_text(json.dumps(data))
        core = self.core(model=CapturedModel(), contract_provider=ContractProvider())
        initial = core.accept_transport(RAW)
        record = core.inspect(initial['communication_id'])
        reply = RAW.replace(b'alice@example.com', b'bob@example.com').replace(
            b'<first@example.com>', b'<bob@example.com>').replace(b'La scelta =C3=A8 blu.', b'Yes').replace(
            b'Subject:', ('In-Reply-To: ' + record['outbound']['transport_message_id'] + '\r\nSubject:').encode())
        accepting = self.core(model=CapturedModel(record['grounding_items'][0]['id']), contract_provider=ContractProvider())
        result = accepting.accept_transport(reply)
        self.assertEqual(result['status'], 'rejected')
        inbound = accepting.inspect(result['communication_id'])['inbound']
        self.assertEqual(inbound['actor_id'], 'bob')
        self.assertEqual(inbound['reply_to'], record['outbound']['transport_message_id'])
        self.assertEqual(accepting.inspect_history()['revision'], 0)

    def test_email_idempotency_rejects_changed_content_without_a_second_send(self):
        transport = CapturedTransport()
        core = self.core(transport)
        first = core.accept_transport(RAW)
        with self.assertRaisesRegex(ValueError, 'idempotency'):
            core.accept_transport(RAW.replace(b'blu.', b'rossa.'))
        self.assertEqual(core.accept_transport(RAW), first)
        self.assertEqual(len(transport.messages), 1)

    def test_email_candidates_and_traces_stay_outside_authenticated_application_boundary(self):
        from piecetogether.application_api import ApplicationApiServer
        from test_application_api import ApplicationApiTests
        data = json.loads(self.path.read_text())
        data['secret_references'] = {'reader': 'PT_EMAIL_API_SECRET'}
        data['application_api'] = {'host': '127.0.0.1', 'port': 0, 'credentials': [
            {'secret_reference': 'reader', 'scopes': ['state:read', 'events:consume']},
        ]}
        self.path.write_text(json.dumps(data))
        with patch.dict(os.environ, {'PT_EMAIL_API_SECRET': 'private-deployment-credential'}):
            core = self.core(model=CapturedModel(), contract_provider=ContractProvider())
            result = core.accept_transport(RAW)
            self.assertTrue(core.inspect(result['communication_id'])['grounding_items'])
            server = ApplicationApiServer(core)
            server.start()
            try:
                status, state = ApplicationApiTests.request(server, '/v1/state?actor_id=alice', 'private-deployment-credential')
                self.assertEqual(status, 200)
                self.assertEqual(state['entities'], [])
                status, events = ApplicationApiTests.request(server, '/v1/events', 'private-deployment-credential')
                self.assertEqual(status, 200)
                self.assertEqual(events['events'], [])
                for body in (state, events):
                    serialized = json.dumps(body)
                    for forbidden in ('trace', 'proposal', 'candidate', 'private-deployment-credential', 'La scelta'):
                        self.assertNotIn(forbidden, serialized)
            finally:
                server.close()

    def test_email_bootstrap_rejects_unsupported_capabilities_transports_and_missing_secrets(self):
        original = json.loads(self.path.read_text())
        for change in ({'capabilities': ['receive', 'reply', 'attachments']},
                       {'email': {'address': 'core@example.com', 'host': 'smtp.example.com', 'security': 'plain'}},
                       {'email': {'address': 'core@example.com', 'host': 'localhost', 'adapter': 'dynamic'}},
                       {'email': {'address': 'core@example.com', 'host': 'localhost', 'security': 'tls',
                                  'username': 'operator', 'password_secret_reference': 'missing'}}):
            with self.subTest(change=change):
                self.path.write_text(json.dumps({**original, **change}))
                with self.assertRaises(ValueError):
                    Bootstrap.from_file(self.path)


if __name__ == '__main__':
    unittest.main()
