import io
import json
import sqlite3
import subprocess
import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piecetogether.core import Bootstrap, Core
from piecetogether.contracts import DomainContract
from piecetogether.proposals import EntityOperation, GroundingPlanOperation, GroundingResolutionOperation, SemanticProposal


TOKEN = '900:captured-secret-not-real'
CAPABILITIES = ('receive', 'reply', 'text', 'threading')


def update(number=1, sender=123, text='La scelta è blu.', reply=None):
    message = {'message_id': number, 'date': 1767268800, 'text': text,
               'from': {'id': sender, 'is_bot': False},
               'chat': {'id': sender, 'type': 'private'}}
    if reply is not None:
        message['reply_to_message'] = {'message_id': reply}
    return {'update_id': number, 'message': message}


class CapturedTelegram:
    def __init__(self, result='success', batches=()):
        self.result = result
        self.batches = iter(batches)
        self.messages = []
        self.offsets = []

    def send(self, chat_id, text, reply_to):
        self.messages.append((chat_id, text, reply_to))
        return self.result, 799 + len(self.messages) if self.result == 'success' else None

    def fetch(self, offset):
        self.offsets.append(offset)
        return next(self.batches, [])


class ContractProvider:
    def get(self, version):
        return DomainContract(version, entity_types={'Subject': {'creation': True, 'attributes': {}}},
                              grounding_policies={'p': {'acceptance': ['explicit']}})


class CapturedModel:
    def __init__(self, item=None):
        self.item, self.calls = item, 0

    def propose(self, inbound, version, context_pack):
        self.calls += 1
        operations = ((GroundingResolutionOperation(self.item, 'p', inbound.id, 'explicit', 'accepted'),)
                      if self.item else (EntityOperation('subject', 'Subject'),
                                        GroundingPlanOperation('p', (), 'explicit', ('subject',))))
        return SemanticProposal(1, version, inbound.id, (), 'Interpretazione', operations,
                                intent='semantic_commit' if self.item else 'candidate',
                                semantic_revision=0 if self.item else None)


class CapturedResponse:
    def __init__(self, body, status=200):
        self.body = json.dumps(body).encode()
        self.status = status

    def read(self, limit):
        return self.body[:limit]


class CapturedConnection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def connect(self):
        pass

    def request(self, method, path, body, headers):
        self.requests.append((method, path, json.loads(body), headers))

    def getresponse(self):
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        pass


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'deployment.json'
        self.path.write_text(json.dumps({
            'database': 'core.sqlite3', 'channel': 'telegram',
            'capabilities': list(CAPABILITIES),
            'identities': {'telegram:123': 'alice'},
            'telegram': {'bot_id': '900', 'token_secret_reference': 'telegram-token'},
            'secret_references': {'telegram-token': 'PT_TELEGRAM_BOT_TOKEN'},
        }))
        self.env = patch.dict(os.environ, {'PT_TELEGRAM_BOT_TOKEN': TOKEN}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def core(self, transport=None, **kwargs):
        from piecetogether.telegram_channel import TelegramChannel
        config = Bootstrap.from_file(self.path)
        channel = TelegramChannel(config.database, config.telegram, transport or CapturedTelegram(), TOKEN)
        return Core(config, channel=channel, **kwargs)

    def test_failure_retries_saved_reply_after_restart_and_success_alone_exposes_without_grounding(self):
        failed = CapturedTelegram('failure', [[update()]])
        model = CapturedModel()
        core = self.core(failed, model=model, contract_provider=ContractProvider())
        self.worker(core, failed).poll_once()
        outcome = core.inspect_telegram_acquisition()[0]
        record = core.inspect(outcome['communication_id'])
        self.assertEqual(record['grounding_items'], [])
        self.assertEqual(record['trace']['delivery_result'], 'failure')
        success = CapturedTelegram()
        restarted = self.core(success, model=model, contract_provider=ContractProvider())
        recovered, _ = restarted.recover_channel_pending()
        self.assertEqual(recovered[0]['status'], 'completed')
        self.assertEqual(model.calls, 1)
        self.assertEqual(success.messages, failed.messages)
        record = restarted.inspect(outcome['communication_id'])
        self.assertEqual(len(record['grounding_items']), 1)
        self.assertEqual(restarted.inspect_history()['revision'], 0)
        self.assertEqual(restarted.trusted_events(), [])
        self.assertEqual(restarted.accept_transport(update()), recovered[0])
        self.assertEqual(len(success.messages), 1)
        item = record['grounding_items'][0]['id']
        accepting = self.core(success, model=CapturedModel(item), contract_provider=ContractProvider())
        reply = accepting.accept_transport(update(2, text='Yes', reply=800))
        self.assertEqual(reply['status'], 'completed')
        self.assertEqual(accepting.inspect(reply['communication_id'])['inbound']['reply_to'], record['outbound']['id'])
        self.assertEqual(accepting.inspect_history()['revision'], 1)
        self.assertEqual(len(accepting.trusted_events()), 1)
        self.assertNotIn('Interpretazione', json.dumps(accepting.trusted_events()))

    def test_indeterminate_and_interrupted_send_are_never_resent_or_exposed(self):
        class Interrupted(CapturedTelegram):
            def send(self, *args):
                super().send(*args)
                raise KeyboardInterrupt()
        for name, transport in [('lost', CapturedTelegram('indeterminate')), ('crash', Interrupted())]:
            with self.subTest(name=name):
                data = json.loads(self.path.read_text())
                data['database'] = name + '.sqlite3'
                self.path.write_text(json.dumps(data))
                model = CapturedModel()
                core = self.core(transport, model=model, contract_provider=ContractProvider())
                if name == 'crash':
                    with self.assertRaises(KeyboardInterrupt):
                        core.accept_transport(update())
                else:
                    self.assertEqual(core.accept_transport(update())['status'], 'retryable')
                fresh = CapturedTelegram()
                restarted = self.core(fresh, model=model, contract_provider=ContractProvider())
                results, _ = restarted.recover_channel_pending()
                record = restarted.inspect(results[0]['communication_id'])
                self.assertEqual(record['trace']['delivery_result'], 'indeterminate')
                self.assertEqual(record['grounding_items'], [])
                self.assertEqual(fresh.messages, [])
                self.assertEqual(model.calls, 1)
                self.assertEqual(restarted.inspect_history()['revision'], 0)

    def test_crash_after_core_ingress_before_inference_and_checkpoint_recovers_from_database(self):
        class CrashedModel(CapturedModel):
            def propose(self, *args):
                raise KeyboardInterrupt()
        transport = CapturedTelegram(batches=[[update()]])
        core = self.core(transport, model=CrashedModel(), contract_provider=ContractProvider())
        with self.assertRaises(KeyboardInterrupt):
            self.worker(core, transport).poll_once()
        self.assertEqual(core.inspect_telegram_acquisition(), [])
        self.assertEqual(core.channel.offset(), 0)
        model = CapturedModel()
        restarted = self.core(transport, model=model, contract_provider=ContractProvider())
        results, _ = restarted.recover_channel_pending()
        self.assertEqual(results[0]['status'], 'completed')
        self.assertEqual(model.calls, 1)
        # Redelivered source after restart checkpoints the same durable ingress without reprocessing.
        replay = CapturedTelegram(batches=[[update()]])
        self.worker(restarted, replay).poll_once()
        self.assertEqual(restarted.inspect_telegram_acquisition()[0]['communication_id'], results[0]['communication_id'])
        self.assertEqual(replay.offsets, [0])
        self.assertEqual(restarted.channel.offset(), 2)
        self.assertEqual(model.calls, 1)

    def test_crash_after_accepted_handoff_before_core_exposure_recovers_without_resending(self):
        import piecetogether.core as core_module
        model = CapturedModel()
        transport = CapturedTelegram()
        core = self.core(transport, model=model, contract_provider=ContractProvider())
        with (patch.object(core_module.history, 'expose', side_effect=KeyboardInterrupt()),
              self.assertRaises(KeyboardInterrupt)):
            core.accept_transport(update())
        restarted_transport = CapturedTelegram()
        restarted = self.core(restarted_transport, model=model, contract_provider=ContractProvider())
        results, _ = restarted.recover_channel_pending()
        self.assertEqual(results[0]['status'], 'completed')
        self.assertEqual(restarted_transport.messages, [])
        self.assertEqual(len(restarted.inspect(results[0]['communication_id'])['grounding_items']), 1)
        self.assertEqual(model.calls, 1)

    def test_recovery_never_sends_a_pending_turn_through_a_different_configured_bot(self):
        class CrashedModel:
            def propose(self, *args):
                raise KeyboardInterrupt()
        core = self.core(model=CrashedModel())
        with self.assertRaises(KeyboardInterrupt):
            core.accept_transport(update())
        data = json.loads(self.path.read_text())
        data['telegram']['bot_id'] = '901'
        self.path.write_text(json.dumps(data))
        transport = CapturedTelegram()
        changed_bot = self.core(transport)
        self.assertEqual(changed_bot.recover_channel_pending(), ([], 0))
        self.assertEqual(transport.messages, [])
        self.assertEqual(changed_bot.channel.offset(), 0)

    def test_checkpoint_storage_failure_replays_same_completed_core_turn(self):
        transport = CapturedTelegram(batches=[[update()], [update()]])
        model = CapturedModel()
        core = self.core(transport, model=model, contract_provider=ContractProvider())
        worker = self.worker(core, transport)
        with (patch.object(core.channel, 'record_acquisition', side_effect=sqlite3.OperationalError('disk full')),
              self.assertRaises(sqlite3.OperationalError)):
            worker.poll_once()
        self.assertEqual(core.channel.offset(), 0)
        self.assertEqual(worker.poll_once(), 1)
        self.assertEqual(len(transport.messages), 1)
        self.assertEqual(model.calls, 1)
        self.assertEqual(core.channel.offset(), 2)

    def test_failed_inference_is_checkpointed_but_recovered_while_polling_is_empty(self):
        class UnavailableModel(CapturedModel):
            def propose(self, *args):
                raise OSError(TOKEN)
        transport = CapturedTelegram(batches=[[update()]])
        core = self.core(transport, model=UnavailableModel(), contract_provider=ContractProvider())
        self.worker(core, transport).poll_once()
        outcome = core.inspect_telegram_acquisition()[0]
        self.assertEqual(core.inspect(outcome['communication_id'])['status'], 'retryable')
        model = CapturedModel()
        restarted = self.core(transport, model=model, contract_provider=ContractProvider())
        with self.assertRaises(KeyboardInterrupt):
            self.worker(restarted, transport, sleep=lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt())).run()
        self.assertEqual(restarted.inspect(outcome['communication_id'])['status'], 'completed')
        self.assertEqual(model.calls, 1)
        self.assertNotIn(TOKEN, json.dumps(restarted.inspect(outcome['communication_id'])))

    def test_unsupported_messages_and_secret_text_never_reach_inference_or_delivery(self):
        cases = [update(sender=456), {'update_id': 9, 'edited_message': update()['message']},
                 update(text=TOKEN), update(text=' '), update(text='x' * 4097)]
        for modification in ({'chat': {'id': -10, 'type': 'group'}}, {'from': {'id': 123, 'is_bot': True}},
                             {'photo': [{'file_id': 'never-fetch'}]}, {'message_id': True}, {'date': True},
                             {'chat': {'id': 456, 'type': 'private'}}, {'reply_to_message': {'message_id': True}},
                             {'business_connection_id': 'unsupported'}):
            item = update()
            item['message'].update(modification)
            cases.append(item)
        model = CapturedModel()
        transport = CapturedTelegram()
        core = self.core(transport, model=model, contract_provider=ContractProvider())
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                core.accept_transport(item)
        self.assertEqual(model.calls, 0)
        self.assertEqual(transport.messages, [])
        self.assertEqual(core.trusted_events(), [])

    def test_transport_classifies_captured_failure_malformed_and_lost_responses_safely(self):
        from piecetogether.telegram_channel import BotApiTransport, TelegramConfig, TelegramUnavailable
        cases = [
            (CapturedResponse({'ok': False, 'description': TOKEN, 'error_code': 403}, 403), 'failure'),
            (CapturedResponse({'ok': False, 'description': TOKEN}, 500), 'indeterminate'),
            (CapturedResponse({'ok': True, 'result': {}}), 'indeterminate'),
            (CapturedResponse({'ok': False, 'description': TOKEN}, 302), 'indeterminate'),
            (OSError('https://api.telegram.org/bot' + TOKEN + '/sendMessage'), 'indeterminate'),
        ]
        for response, expected in cases:
            transport = BotApiTransport(TelegramConfig('900', 'token'), {'token': 'PT_TELEGRAM_BOT_TOKEN'})
            connection = CapturedConnection([response, response])
            with patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=connection):
                self.assertEqual(transport.send('123', 'reply', '1'), (expected, None))
                with self.assertRaises(TelegramUnavailable) as error:
                    transport.fetch(0)
                self.assertNotIn(TOKEN, str(error.exception))
        malformed = CapturedResponse({'ok': True})
        malformed.body = b'{bad json' + TOKEN.encode()
        connection = CapturedConnection([malformed])
        with patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=connection):
            self.assertEqual(transport.send('123', 'reply', '1'), ('indeterminate', None))

    def test_poll_failures_back_off_and_honor_captured_rate_limit_with_controlled_clock(self):
        from piecetogether.telegram_channel import BotApiTransport, TelegramConfig
        clock = [100.0]
        config = TelegramConfig('900', 'token', poll_seconds=2)
        connection = CapturedConnection([
            OSError(TOKEN), OSError(TOKEN),
            CapturedResponse({'ok': False, 'parameters': {'retry_after': 20}, 'description': TOKEN}, 429),
            CapturedResponse({'ok': True, 'result': []}),
        ])
        transport = BotApiTransport(config, {'token': 'PT_TELEGRAM_BOT_TOKEN'}, clock=lambda: clock[0])
        with patch.dict(os.environ, {'PT_TELEGRAM_POLL_SECONDS': '2'}):
            core = self.core(transport)
        delays = []
        def sleep(seconds):
            delays.append(seconds)
            clock[0] += seconds
            if len(delays) == 4:
                raise KeyboardInterrupt()
        with (patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=connection),
              self.assertRaises(KeyboardInterrupt)):
            self.worker(core, transport, sleep=sleep).run()
        self.assertEqual(delays, [2, 4, 20, 2])
        self.assertEqual(core.inspect_telegram_acquisition(), [])

    def test_malformed_batches_are_retryable_without_advancing_checkpoint(self):
        for batch in ([{'update_id': True}], [update(), update()], [update(2**63)],
                      [update(number) for number in range(33)], 'not-an-array'):
            core = self.core()
            transport = CapturedTelegram(batches=[batch])
            with self.subTest(batch=str(batch)[:80]), self.assertRaises(OSError):
                self.worker(core, transport).poll_once()
            self.assertEqual(core.channel.offset(), 0)

    def test_invalid_static_config_and_missing_or_wrong_token_fail_cli_without_secrets(self):
        for field, value in [('bot_id', 'username'), ('batch_size', True), ('batch_size', 101),
                             ('long_poll_seconds', 0), ('timeout_seconds', 30),
                             ('timeout_seconds', float('inf')), ('poll_seconds', float('nan')),
                             ('unknown', TOKEN)]:
            data = json.loads(self.path.read_text())
            data['telegram'][field] = value
            self.path.write_text(json.dumps(data))
            with self.subTest(field=field), self.assertRaises(ValueError):
                Bootstrap.from_file(self.path)
            data['telegram'].pop(field)
            if field == 'bot_id':
                data['telegram'][field] = '900'
            self.path.write_text(json.dumps(data))
        for token in ('', '901:' + TOKEN):
            environment = dict(os.environ, PT_TELEGRAM_BOT_TOKEN=token)
            process = subprocess.run([sys.executable, '-m', 'piecetogether', '--config', str(self.path),
                                      '--serve-telegram'], env=environment, text=True, capture_output=True)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(process.stdout, '')
            self.assertEqual(process.stderr.strip(), 'Invalid or unavailable deployment configuration/state.')
            self.assertNotIn(TOKEN, process.stderr)
        for variable, value in [('PT_TELEGRAM_IDENTITIES', '{bad'), ('PT_TELEGRAM_BATCH_SIZE', TOKEN),
                                ('PT_TELEGRAM_BOT_ID', TOKEN), ('PT_TELEGRAM_IDENTITIES', '{"telegram:001":"a"}')]:
            with patch.dict(os.environ, {variable: value}), self.subTest(variable=variable), self.assertRaises(ValueError):
                Bootstrap.from_file(self.path)

    def test_partial_long_reply_only_retries_failed_part_and_never_opens_exposure_early(self):
        class LongModel(CapturedModel):
            def propose(self, *args):
                from dataclasses import replace
                return replace(super().propose(*args), draft_response='x' * 5000)
        class PartialTransport(CapturedTelegram):
            def send(self, *args):
                self.result = 'success' if not self.messages else 'failure'
                return super().send(*args)
        model, partial = LongModel(), PartialTransport()
        core = self.core(partial, model=model, contract_provider=ContractProvider())
        result = core.accept_transport(update())
        self.assertEqual(result['status'], 'retryable')
        self.assertEqual(core.inspect(result['communication_id'])['grounding_items'], [])
        fresh = CapturedTelegram()
        restarted = self.core(fresh, model=model, contract_provider=ContractProvider())
        results, _ = restarted.recover_channel_pending()
        self.assertEqual(results[0]['status'], 'completed')
        self.assertEqual(fresh.messages, partial.messages[1:])
        self.assertEqual(model.calls, 1)
        self.assertEqual(len(restarted.inspect(result['communication_id'])['grounding_items']), 1)

    def test_model_and_network_work_hold_no_semantic_transaction(self):
        from piecetogether.core import connect
        database = self.path.parent / 'core.sqlite3'
        def assert_unlocked():
            with connect(database) as db:
                db.execute('BEGIN IMMEDIATE')
        class UnlockedModel(CapturedModel):
            def propose(self, *args):
                assert_unlocked()
                return super().propose(*args)
        class UnlockedTransport(CapturedTelegram):
            def send(self, *args):
                assert_unlocked()
                return super().send(*args)
        core = self.core(UnlockedTransport(), model=UnlockedModel(), contract_provider=ContractProvider())
        self.assertEqual(core.accept_transport(update())['status'], 'completed')

    def test_connection_failure_before_request_is_retryable_but_lost_acknowledgement_is_not(self):
        from piecetogether.telegram_channel import BotApiTransport, TelegramConfig
        transport = BotApiTransport(TelegramConfig('900', 'token'), {'token': 'PT_TELEGRAM_BOT_TOKEN'})
        connection = CapturedConnection([])
        with (patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=connection),
              patch.object(connection, 'connect', side_effect=ConnectionRefusedError(TOKEN))):
            self.assertEqual(transport.send('123', 'reply', '1'), ('failure', None))
            self.assertEqual(connection.requests, [])
        lost = CapturedConnection([OSError(TOKEN)])
        with patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=lost):
            self.assertEqual(transport.send('123', 'reply', '1'), ('indeterminate', None))
            self.assertEqual(len(lost.requests), 1)

    def test_send_rate_limit_defers_network_retry_and_does_not_leak_error_body(self):
        from piecetogether.telegram_channel import BotApiTransport, TelegramConfig
        clock = [0]
        transport = BotApiTransport(TelegramConfig('900', 'token'), {'token': 'PT_TELEGRAM_BOT_TOKEN'},
                                    clock=lambda: clock[0])
        response = CapturedResponse({'ok': False, 'description': TOKEN, 'parameters': {'retry_after': 10}}, 429)
        connection = CapturedConnection([response, response])
        with patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=connection):
            self.assertEqual(transport.send('123', 'reply', '1'), ('failure', None))
            self.assertEqual(transport.send('123', 'reply', '1'), ('failure', None))
            self.assertEqual(len(connection.requests), 1)
            clock[0] = 10
            self.assertEqual(transport.send('123', 'reply', '1'), ('failure', None))
            self.assertEqual(len(connection.requests), 2)

    def test_telegram_acquisition_and_candidates_are_isolated_from_authenticated_state_api(self):
        from dataclasses import replace
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        from piecetogether.application_api import ApplicationApiConfig, ApplicationApiServer, ApplicationCredential
        core = self.core(model=CapturedModel(), contract_provider=ContractProvider())
        transport = CapturedTelegram(batches=[[update()]])
        self.worker(core, transport).poll_once()
        config = replace(core.config, application_api=ApplicationApiConfig('127.0.0.1', 0,
            (ApplicationCredential('api', ('state:read', 'events:consume')),)),
            secret_references={**core.config.secret_references, 'api': 'PT_API_TOKEN'})
        configured = Core(config, channel=core.channel, contract_provider=ContractProvider())
        with patch.dict(os.environ, {'PT_API_TOKEN': 'private-api-credential'}):
            server = ApplicationApiServer(configured)
            server.start()
        self.addCleanup(server.close)
        for path in ('/v1/state?actor_id=alice', '/v1/events'):
            request = Request(server.base_url + path, headers={'Authorization': 'Bearer private-api-credential'})
            with urlopen(request, timeout=2) as response:
                body = json.loads(response.read())
            self.assertNotIn('telegram', json.dumps(body))
            self.assertNotIn('Interpretazione', json.dumps(body))
            self.assertNotIn('trace', json.dumps(body))
            self.assertNotIn(TOKEN, json.dumps(body))
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(server.base_url + '/v1/events', headers={'Authorization': 'Bearer ' + TOKEN}), timeout=2)
        self.assertEqual(error.exception.code, 401)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(server.base_url + '/inspect', headers={'Authorization': 'Bearer private-api-credential'}), timeout=2)
        self.assertEqual(error.exception.code, 404)

    def test_sender_retrieval_request_is_refused_without_disclosing_pending_history(self):
        class RetrievalModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), 'SECRET HISTORY', response_intent='retrieval')
        core = self.core(model=CapturedModel(), contract_provider=ContractProvider())
        core.accept_transport(update(text='Private old subject'))
        transport = CapturedTelegram()
        retrieving = self.core(transport, model=RetrievalModel(), contract_provider=ContractProvider())
        result = retrieving.accept_transport(update(2, text='Show all stored history'))
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('Private old subject', result['reply'])
        self.assertNotIn('SECRET HISTORY', result['reply'])
        self.assertEqual(retrieving.inspect_history()['revision'], 0)

    def test_cli_keeps_inspection_and_api_modes_separate_from_telegram_acquisition(self):
        from piecetogether.__main__ import main
        for extra in ([], ['--receive-email'], ['--serve-email']):
            stderr = io.StringIO()
            with (patch.object(sys, 'argv', ['piecetogether', '--config', str(self.path), *extra]),
                  patch.object(sys, 'stderr', stderr)):
                self.assertEqual(main(), 1)
            self.assertNotIn(TOKEN, stderr.getvalue())
        core = self.core()
        result = core.accept_transport(update())
        stdout = io.StringIO()
        with (patch.object(sys, 'argv', ['piecetogether', '--config', str(self.path), '--inspect', result['communication_id']]),
              patch.object(sys, 'stdout', stdout)):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(stdout.getvalue())['status'], 'completed')
        self.assertNotIn(TOKEN, stdout.getvalue())

    def test_long_unicode_reply_is_delivered_completely_without_exceeding_telegram_budget(self):
        transport = CapturedTelegram()
        core = self.core(transport)
        result = core.accept_transport(update(text='🌱' * 4096))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(''.join(message[1] for message in transport.messages), result['reply'])
        self.assertTrue(all(len(message[1].encode('utf-16-le')) // 2 <= 4096 for message in transport.messages))
        self.assertEqual(core.accept_transport(update(text='🌱' * 4096)), result)
        self.assertEqual(len(transport.messages), 3)

    def test_actual_serve_command_uses_environment_and_captured_bot_api_without_secret_output(self):
        from piecetogether.__main__ import main
        (self.path.parent / '.env').write_text(
            'PT_TELEGRAM_BOT_TOKEN=900:file-secret\nPT_TELEGRAM_BOT_ID=900\n'
            'PT_TELEGRAM_IDENTITIES=\'{"telegram:777":"wrong-actor"}\'\n'
            'PT_TELEGRAM_LONG_POLL_SECONDS=10\nPT_TELEGRAM_TIMEOUT_SECONDS=20\n'
            'PT_TELEGRAM_BATCH_SIZE=3\nPT_TELEGRAM_POLL_SECONDS=2\n')
        connection = CapturedConnection([
            CapturedResponse({'ok': True, 'result': [update()]}),
            CapturedResponse({'ok': True, 'result': {'message_id': 800, 'text': 'I understood: La scelta è blu.',
                'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 900, 'is_bot': True}}}),
            CapturedResponse({'ok': True, 'result': [update(2, text='Secondo messaggio')]}),
            CapturedResponse({'ok': True, 'result': {'message_id': 801, 'text': 'I understood: Secondo messaggio',
                'chat': {'id': 123, 'type': 'private'}, 'from': {'id': 900, 'is_bot': True}}}),
            CapturedResponse({'ok': True, 'result': []}),
        ])
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.dict(os.environ, {'PT_TELEGRAM_IDENTITIES': '{"telegram:123":"alice"}'}),
              patch.object(sys, 'argv', ['piecetogether', '--config', str(self.path), '--serve-telegram']),
              patch.object(sys, 'stdout', stdout), patch.object(sys, 'stderr', stderr),
              patch('piecetogether.telegram_channel.http.client.HTTPSConnection', return_value=connection) as https,
              patch('piecetogether.telegram_channel.time.sleep', side_effect=[None, None, KeyboardInterrupt()]) as sleep):
            self.assertEqual(main(), 0)
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')
        https.assert_called_with('api.telegram.org', timeout=20.0)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 2, 2])
        payloads = [item[2] for item in connection.requests]
        self.assertEqual(payloads[0], {'offset': 0, 'limit': 3, 'timeout': 10, 'allowed_updates': ['message']})
        self.assertEqual(payloads[1], {'chat_id': 123, 'text': 'I understood: La scelta è blu.',
                                     'reply_parameters': {'message_id': 1}})
        self.assertEqual([item['offset'] for item in payloads if 'offset' in item], [0, 2, 3])
        core = self.core()
        outcomes = core.inspect_telegram_acquisition()
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(core.inspect(outcomes[0]['communication_id'])['inbound']['actor_id'], 'alice')
        self.assertNotIn(TOKEN.encode(), core.config.database.read_bytes())
        self.assertIn('900:file-secret', (self.path.parent / '.env').read_text())

    def worker(self, core, transport, **kwargs):
        from piecetogether.telegram_channel import TelegramAcquisitionWorker
        return TelegramAcquisitionWorker(core, core.channel, transport, core.config.telegram, **kwargs)

    def test_polling_is_durable_deduplicated_and_skips_unsupported_and_unmapped_updates(self):
        transport = CapturedTelegram(batches=[
            [update(), {'update_id': 2, 'message': {'photo': []}}, update(3, sender=456), update(4)],
            [], [update(), update(4)],
        ])
        core = self.core(transport)
        worker = self.worker(core, transport)
        self.assertEqual(worker.poll_once(), 4)
        outcomes = core.inspect_telegram_acquisition()
        self.assertEqual([item['status'] for item in outcomes], ['acquired', 'unsupported', 'unsupported', 'acquired'])
        self.assertEqual(outcomes[2]['outcome'], 'unmapped_identity')
        for item in (outcomes[0], outcomes[3]):
            self.assertEqual(core.inspect(item['communication_id'])['status'], 'completed')
        self.assertEqual(worker.poll_once(), 0)
        self.assertEqual(len(transport.messages), 2)
        restarted = self.core(transport)
        self.assertEqual(self.worker(restarted, transport).poll_once(), 0)
        self.assertEqual(transport.offsets, [0, 5, 5])
        self.assertEqual(restarted.inspect_telegram_acquisition(), outcomes)
        self.assertNotIn(TOKEN, json.dumps(outcomes))

    def test_private_text_normalizes_actor_and_reply_routes_to_original_conversation(self):
        transport = CapturedTelegram()
        core = self.core(transport)
        result = core.accept_transport(update())
        self.assertEqual(result['status'], 'completed')
        record = core.inspect(result['communication_id'])
        self.assertEqual(record['inbound']['actor_id'], 'alice')
        self.assertEqual(record['inbound']['sender'], '123')
        self.assertEqual(record['inbound']['thread_id'], '123')
        self.assertEqual(record['inbound']['transport_message_id'], '1')
        self.assertEqual(record['outbound']['sender'], '900')
        self.assertEqual(record['outbound']['recipient'], '123')
        self.assertEqual(record['outbound']['transport_reply_to'], '1')
        self.assertEqual(transport.messages, [('123', result['reply'], '1')])
        self.assertEqual(core.channel.capabilities, CAPABILITIES)
        self.assertEqual(core.current_view()['assertion_sets'], [])
        self.assertEqual(set(result), {'communication_id', 'status', 'reply'})
        second = core.accept_transport(update(2, reply=800))
        self.assertEqual(core.inspect(second['communication_id'])['inbound']['reply_to'], record['outbound']['id'])
        self.assertNotIn(TOKEN, json.dumps(core.inspect(second['communication_id'])))


if __name__ == '__main__':
    unittest.main()
