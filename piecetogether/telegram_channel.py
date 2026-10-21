"""First-party Telegram private text channel; Bot API adapters are static."""

import base64
import http.client
import json
import os
import math
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from .email_channel import DeliveryResult, valid_media
from .retention import channel_processing

if TYPE_CHECKING:
    from .core import Communication, Core

CAPABILITIES = ('receive', 'reply', 'text', 'threading', 'attachments')
TEXT_CAPABILITIES = ('receive', 'reply', 'text', 'threading')
MAX_MEDIA_BYTES = 1_048_576


def numeric_id(value: Any) -> bool:
    return (isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,15}', value) is not None
            and int(value) < 2**52)


@dataclass(frozen=True)
class TelegramConfig:
    bot_id: str
    token_secret_reference: str
    timeout_seconds: float = 40
    long_poll_seconds: int = 30
    poll_seconds: float = 1
    batch_size: int = 32
    max_media_bytes: int = MAX_MEDIA_BYTES

    def __post_init__(self) -> None:
        if (not numeric_id(self.bot_id)
                or not isinstance(self.token_secret_reference, str)
                or not self.token_secret_reference.strip() or len(self.token_secret_reference) > 256
                or type(self.long_poll_seconds) is not int or not 1 <= self.long_poll_seconds <= 50
                or type(self.timeout_seconds) not in (int, float) or not math.isfinite(self.timeout_seconds)
                or not self.long_poll_seconds < self.timeout_seconds <= 300
                or type(self.poll_seconds) not in (int, float) or not math.isfinite(self.poll_seconds)
                or not 0 < self.poll_seconds <= 86400
                or type(self.batch_size) is not int or not 1 <= self.batch_size <= 100
                or type(self.max_media_bytes) is not int or not 1 <= self.max_media_bytes <= MAX_MEDIA_BYTES):
            raise ValueError('invalid static Telegram configuration')

    @classmethod
    def from_dict(cls, data: Any) -> 'TelegramConfig':
        if not isinstance(data, dict):
            raise ValueError('telegram must be a static transport object')
        try:
            return cls(**data)
        except TypeError:
            raise ValueError('invalid static Telegram configuration') from None


class TelegramTransport(Protocol):
    def send(self, chat_id: str, text: str, reply_to: str) -> tuple[DeliveryResult, int | None]: ...
    def fetch(self, offset: int) -> list[dict[str, Any]]: ...
    def download(self, file_id: str, maximum_size: int) -> bytes: ...


class UnsupportedTelegramMedia(ValueError):
    """A definite provider/media rejection that may be checkpointed and skipped."""


class TelegramUnavailable(OSError):
    """Sanitized acquisition failure with only a provider-requested retry delay."""

    def __init__(self, retry_after: float = 0, before_send: bool = False):
        super().__init__('Telegram acquisition unavailable')
        self.retry_after, self.before_send = retry_after, before_send


class BotApiTransport:
    """Bounded HTTPS POSTs to Telegram only; never log provider bodies or token URLs."""

    def __init__(self, config: TelegramConfig, secret_references: dict[str, str],
                 clock: Callable[[], float] = time.monotonic):
        token = os.environ.get(secret_references.get(config.token_secret_reference, ''))
        if (not token or not re.fullmatch(re.escape(config.bot_id) + r':[A-Za-z0-9_-]+', token)):
            raise ValueError('Telegram token reference is unresolved or does not match bot_id')
        self.config, self._token, self.clock = config, token, clock
        self.not_before = 0.0

    def _request(self, method: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.clock() < self.not_before:
            raise TelegramUnavailable(self.not_before - self.clock())
        connection = http.client.HTTPSConnection('api.telegram.org', timeout=self.config.timeout_seconds)
        connected = False
        try:
            connection.connect()
            connected = True
            connection.request('POST', f'/bot{self._token}/{method}',
                               json.dumps(payload).encode('utf-8'), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise TelegramUnavailable()
            body = json.loads(raw)
            if not isinstance(body, dict) or type(body.get('ok')) is not bool:
                raise TelegramUnavailable()
            parameters = body.get('parameters')
            delay = parameters.get('retry_after') if isinstance(parameters, dict) else None
            if body['ok'] is False and type(delay) is int and 0 < delay <= 86400:
                self.not_before = self.clock() + delay
            return response.status, body
        except (OSError, http.client.HTTPException, ValueError, TypeError, RecursionError):
            raise TelegramUnavailable(before_send=not connected) from None
        finally:
            try:
                connection.close()
            except OSError:
                pass  # Cleanup cannot invalidate a captured provider acknowledgement.

    def download(self, file_id: str, maximum_size: int) -> bytes:
        if (not isinstance(file_id, str) or not file_id or len(file_id) > 256
                or type(maximum_size) is not int or not 0 < maximum_size <= MAX_MEDIA_BYTES):
            raise UnsupportedTelegramMedia('invalid Telegram file reference')
        status, body = self._request('getFile', {'file_id': file_id})
        result = body.get('result') if isinstance(body, dict) else None
        if status in (404, 408, 429) or status >= 500 or self.clock() < self.not_before:
            raise TelegramUnavailable(max(0, self.not_before - self.clock()))
        if (status != 200 or body.get('ok') is not True or not isinstance(result, dict)
                or result.get('file_id') != file_id or not isinstance(result.get('file_unique_id'), str)
                or not result['file_unique_id'] or len(result['file_unique_id']) > 256
                or type(result.get('file_size')) is not int or not 0 < result['file_size'] <= maximum_size
                or not isinstance(result.get('file_path'), str)
                or not re.fullmatch(r'[A-Za-z0-9._/-]{1,512}', result['file_path'])
                or '..' in result['file_path'].split('/')):
            raise UnsupportedTelegramMedia('invalid Telegram file response')
        connection = http.client.HTTPSConnection('api.telegram.org', timeout=self.config.timeout_seconds)
        try:
            connection.request('GET', f'/file/bot{self._token}/{result["file_path"]}')
            response = connection.getresponse()
            content = response.read(maximum_size + 1)
            if response.status in (404, 408, 429) or response.status >= 500:
                raise TelegramUnavailable()
            if response.status != 200 or len(content) != result['file_size'] or not content or len(content) > maximum_size:
                raise UnsupportedTelegramMedia('invalid Telegram file content')
            return content
        except (OSError, http.client.HTTPException):
            raise TelegramUnavailable() from None
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def fetch(self, offset: int) -> list[dict[str, Any]]:
        status, body = self._request('getUpdates', {'offset': offset, 'limit': self.config.batch_size,
            'timeout': self.config.long_poll_seconds, 'allowed_updates': ['message']})
        if status != 200 or body['ok'] is not True or not isinstance(body.get('result'), list):
            raise TelegramUnavailable(max(0, self.not_before - self.clock()))
        return cast(list[dict[str, Any]], body['result'])

    def send(self, chat_id: str, text: str, reply_to: str) -> tuple[DeliveryResult, int | None]:
        if self.clock() < self.not_before:
            return 'failure', None
        try:
            status, body = self._request('sendMessage', {'chat_id': int(chat_id), 'text': text,
                'reply_parameters': {'message_id': int(reply_to)}})
        except TelegramUnavailable as error:
            # No Bot API deduplication key: losing the response cannot justify a resend.
            return ('failure' if error.before_send else 'indeterminate'), None
        except (OSError, ValueError, TypeError):
            return 'indeterminate', None
        if body['ok'] is False and (status == 200 or 400 <= status < 500):
            return 'failure', None
        message = body.get('result')
        if (status != 200 or body['ok'] is not True or not isinstance(message, dict)
                or type(message.get('message_id')) is not int or not numeric_id(str(message['message_id']))
                or not isinstance(message.get('chat'), dict) or type(message['chat'].get('id')) is not int
                or message['chat']['id'] != int(chat_id) or message['chat'].get('type') != 'private'
                or not isinstance(message.get('from'), dict) or type(message['from'].get('id')) is not int
                or message['from']['id'] != int(self.config.bot_id) or message.get('text') != text):
            return 'indeterminate', None
        return 'success', message['message_id']


class TelegramChannel:
    capabilities: tuple[str, ...] = CAPABILITIES

    def __init__(self, database: Path, config: TelegramConfig, transport: TelegramTransport,
                 token: str):
        from .core import connect
        self.database, self.config, self.transport = database, config, transport
        self._token = token
        database.parent.mkdir(parents=True, exist_ok=True)
        with connect(database) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS telegram_handoffs (
                bot_id TEXT NOT NULL, id TEXT NOT NULL, part INTEGER NOT NULL, recipient TEXT NOT NULL,
                text TEXT NOT NULL, reply_to TEXT NOT NULL, result TEXT NOT NULL,
                message_id INTEGER, PRIMARY KEY(bot_id, id, part)
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS telegram_acquisition (
                bot_id TEXT NOT NULL, update_id INTEGER NOT NULL, status TEXT NOT NULL,
                communication_id TEXT, outcome TEXT NOT NULL, PRIMARY KEY(bot_id, update_id)
            )''')

    def offset(self) -> int:
        from .core import connect
        with connect(self.database) as db:
            return int(db.execute('SELECT COALESCE(MAX(update_id) + 1, 0) FROM telegram_acquisition '
                                  'WHERE bot_id=?', (self.config.bot_id,)).fetchone()[0])

    @channel_processing
    def record_acquisition(self, update_id: int, communication_id: str | None, outcome: str) -> None:
        from .core import connect
        with connect(self.database) as db:
            db.execute('INSERT OR IGNORE INTO telegram_acquisition VALUES (?, ?, ?, ?, ?)',
                       (self.config.bot_id, update_id, 'acquired' if communication_id else 'unsupported',
                        communication_id, outcome))

    def acquisition_outcomes(self) -> list[dict[str, Any]]:
        from .core import connect
        with connect(self.database) as db:
            return [dict(row) for row in db.execute('SELECT * FROM telegram_acquisition WHERE bot_id=? '
                                                    'ORDER BY update_id', (self.config.bot_id,))]

    def sender_identity(self, payload: Any) -> str:
        if (not isinstance(payload, dict) or type(payload.get('update_id')) is not int
                or payload['update_id'] < 0 or not isinstance(payload.get('message'), dict)):
            raise ValueError('unsupported Telegram update')
        message = payload['message']
        sender, chat = message.get('from'), message.get('chat')
        if (not isinstance(sender, dict) or not isinstance(chat, dict)
                or type(sender.get('id')) is not int or not numeric_id(str(sender['id']))
                or sender.get('is_bot') is not False or chat.get('type') != 'private'
                or type(chat.get('id')) is not int or chat['id'] != sender['id']):
            raise ValueError('unsupported Telegram private message')
        return str(sender['id'])

    @channel_processing
    def normalize(self, payload: Any) -> dict[str, Any]:
        from .core import connect
        sender_id = self.sender_identity(payload)
        message = payload['message']
        unsupported = ('business_connection_id', 'paid_media', 'audio', 'voice', 'video', 'video_note',
                       'animation', 'sticker', 'contact', 'location', 'venue', 'poll', 'dice', 'game',
                       'invoice', 'story')
        if (any(field in message for field in unsupported)
                or type(message.get('message_id')) is not int or not numeric_id(str(message['message_id']))
                or type(message.get('date')) is not int or message['date'] < 0):
            raise ValueError('unsupported Telegram private message')
        text = message.get('text', message.get('caption', ''))
        if (not isinstance(text, str) or len(text) > 4096 or self._token in text
                or any(0xd800 <= ord(character) <= 0xdfff for character in text)):
            raise ValueError('unsupported Telegram text')
        attachments = []
        media_fields = [field for field in ('photo', 'document') if field in message]
        if len(media_fields) > 1:
            raise ValueError('unsupported Telegram media')
        if media_fields:
            if 'attachments' not in self.capabilities:
                raise ValueError('Telegram attachments are not enabled')
            if media_fields[0] == 'photo':
                photos = message['photo']
                if (not isinstance(photos, list) or not photos
                        or any(not isinstance(item, dict) or not isinstance(item.get('file_id'), str)
                               or type(item.get('file_size')) is not int for item in photos)):
                    raise ValueError('invalid Telegram photo')
                source = max(photos, key=lambda item: item['file_size'])
                media_type = 'image/jpeg'
            else:
                source = message['document']
                if not isinstance(source, dict) or source.get('mime_type') not in ('image/png', 'image/jpeg', 'application/pdf'):
                    raise ValueError('unsupported Telegram document')
                media_type = source['mime_type']
            file_id, size = source.get('file_id'), source.get('file_size')
            if (not isinstance(file_id, str) or not file_id or len(file_id) > 256
                    or type(size) is not int or not 0 < size <= self.config.max_media_bytes):
                raise ValueError('invalid Telegram media')
            content = self.transport.download(file_id, self.config.max_media_bytes)
            if (not isinstance(content, bytes) or not content or len(content) != size
                    or not valid_media(media_type, content)):
                raise ValueError('invalid Telegram media download')
            attachments.append({'id': 'attachment-1', 'content_base64': base64.b64encode(content).decode('ascii'),
                                'media_type': media_type})
        elif not text.strip():
            raise ValueError('Telegram requires text or supported media')
        try:
            sent_at = datetime.fromtimestamp(message['date'], timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            raise ValueError('invalid Telegram message timestamp') from None
        reply = message.get('reply_to_message')
        reply_to = None
        if reply is not None:
            if not isinstance(reply, dict) or type(reply.get('message_id')) is not int or not numeric_id(str(reply['message_id'])):
                raise ValueError('invalid Telegram reply reference')
            reply_to = str(reply['message_id'])
            with connect(self.database) as db:
                row = db.execute("SELECT id FROM telegram_handoffs WHERE bot_id=? AND recipient=? "
                                 "AND message_id=? AND result='success'",
                                 (self.config.bot_id, sender_id, reply['message_id'])).fetchone()
            if row:
                reply_to = row['id']
        return {'channel': 'telegram', 'sender': sender_id,
                'idempotency_key': f"{self.config.bot_id}:{sender_id}:{message['message_id']}",
                'text': text, 'sent_at': sent_at, 'thread_id': sender_id,
                'transport_message_id': str(message['message_id']),
                'transport_reply_to': str(reply['message_id']) if reply is not None else None,
                'reply_to': reply_to, 'attachments': attachments}

    def prepare(self, outbound: 'Communication', inbound: 'Communication') -> 'Communication':
        return replace(outbound, sender=self.config.bot_id, recipient=inbound.sender,
                       transport_reply_to=inbound.transport_message_id)

    @channel_processing
    def deliver(self, outbound: 'Communication') -> DeliveryResult:
        from .core import connect
        if (outbound.channel != 'telegram' or outbound.sender != self.config.bot_id
                or not numeric_id(outbound.recipient) or outbound.thread_id != outbound.recipient
                or not numeric_id(outbound.transport_reply_to) or self._token in outbound.text
                or not 0 < len(outbound.text) <= 65536):
            raise ValueError('invalid canonical outbound Telegram text')
        parts: list[str] = []
        start, units = 0, 0
        for index, character in enumerate(outbound.text):
            size = 2 if ord(character) > 0xffff else 1
            if units + size > 4096:
                parts.append(outbound.text[start:index])
                start, units = index, 0
            units += size
        parts.append(outbound.text[start:])
        for part, text in enumerate(parts):
            result = self._deliver_part(outbound, part, text)
            if result != 'success':
                return result
        return 'success'

    def _deliver_part(self, outbound: 'Communication', part: int, text: str) -> DeliveryResult:
        from .core import connect
        with connect(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM telegram_handoffs WHERE bot_id=? AND id=? AND part=?',
                             (self.config.bot_id, outbound.id, part)).fetchone()
            if row:
                if (row['recipient'], row['text'], row['reply_to']) != (
                        outbound.recipient, text, outbound.transport_reply_to):
                    raise ValueError('Telegram handoff identity belongs to a different message')
                if row['result'] != 'failure':
                    return 'success' if row['result'] == 'success' else 'indeterminate'
                db.execute("UPDATE telegram_handoffs SET result='started' WHERE bot_id=? AND id=? AND part=?",
                           (self.config.bot_id, outbound.id, part))
            else:
                db.execute('INSERT INTO telegram_handoffs VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                           (self.config.bot_id, outbound.id, part, outbound.recipient,
                            text, outbound.transport_reply_to, 'started'))
        try:
            result, message_id = self.transport.send(outbound.recipient or '', text,
                                                     outbound.transport_reply_to or '')
            if result not in ('success', 'failure', 'indeterminate'):
                result = 'indeterminate'
            if result == 'success' and (type(message_id) is not int or not numeric_id(str(message_id))):
                result = 'indeterminate'
        except Exception:
            result, message_id = 'indeterminate', None
        with connect(self.database) as db:
            db.execute('UPDATE telegram_handoffs SET result=?, message_id=? WHERE bot_id=? AND id=? AND part=?',
                       (result, message_id if result == 'success' else None, self.config.bot_id, outbound.id, part))
        return result


class TelegramAcquisitionWorker:
    """One private-chat polling worker per bot; Core owns processing/recovery."""

    def __init__(self, core: 'Core', channel: TelegramChannel, transport: TelegramTransport,
                 config: TelegramConfig, sleep: Callable[[float], None] | None = None):
        self.core, self.channel, self.transport, self.config = core, channel, transport, config
        self.sleep = sleep or time.sleep
        self.recovery_cursor = 0

    def poll_once(self) -> int:
        offset = self.channel.offset()
        updates = self.transport.fetch(offset)
        if (not isinstance(updates, list) or len(updates) > self.config.batch_size
                or any(not isinstance(item, dict) or type(item.get('update_id')) is not int
                       or not 0 <= item['update_id'] < 2**63 - 1 for item in updates)
                or len({item['update_id'] for item in updates}) != len(updates)):
            raise OSError('invalid Telegram update batch')
        acquired = 0
        for item in sorted(updates, key=lambda item: item['update_id']):
            if item['update_id'] < offset:
                continue
            try:
                sender = self.channel.sender_identity(item)
            except (ValueError, TypeError, RecursionError):
                self.channel.record_acquisition(item['update_id'], None, 'unsupported_message')
            else:
                if f"telegram:{sender}" not in self.core.config.identities:
                    self.channel.record_acquisition(item['update_id'], None, 'unmapped_identity')
                else:
                    try:
                        normalized = self.channel.normalize(item)
                        result = self.core.accept(normalized)
                    except UnsupportedTelegramMedia:
                        self.channel.record_acquisition(item['update_id'], None, 'unsupported_message')
                    except (ValueError, TypeError, RecursionError):
                        self.channel.record_acquisition(item['update_id'], None, 'invalid_communication')
                    else:
                        self.channel.record_acquisition(item['update_id'], result['communication_id'], result['status'])
            acquired += 1
        return acquired

    def run(self) -> None:
        backoff = self.config.poll_seconds
        maximum_backoff = max(backoff, 300)
        while True:
            try:
                _, self.recovery_cursor = self.core.recover_channel_pending(self.recovery_cursor)
                self.poll_once()
            except (OSError, sqlite3.Error) as error:
                self.sleep(max(backoff, error.retry_after if isinstance(error, TelegramUnavailable) else 0))
                backoff = min(backoff * 2, maximum_backoff)
            else:
                backoff = self.config.poll_seconds
                self.sleep(backoff)
