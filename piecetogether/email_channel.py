"""One semantic Email channel, with a statically configured SMTP transport."""

import imaplib
import os
import re
import smtplib
import sqlite3
import ssl
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol

if TYPE_CHECKING:
    from .core import Communication, Core

DeliveryResult = Literal['success', 'failure', 'indeterminate']
CAPABILITIES = ('receive', 'reply', 'text', 'threading')
MAX_EMAIL_BYTES = 131072


def address(value: Any) -> bool:
    return (isinstance(value, str) and len(value) <= 254
            and re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+', value) is not None)


def message_ids(value: str) -> tuple[str, ...]:
    ids = tuple(re.findall(r'<[^<>\s@]+@[^<>\s@]+>', value))
    if (len(ids) > 32 or any(len(item) > 256 or any(not 33 <= ord(char) <= 126 for char in item) for item in ids)
            or ' '.join(value.split()) != ' '.join(ids)):
        raise ValueError('invalid Email message references')
    return ids


@dataclass(frozen=True)
class EmailConfig:
    address: str
    host: str
    port: int = 587
    security: str = 'starttls'
    username: str | None = None
    password_secret_reference: str | None = None
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if (not address(self.address) or not isinstance(self.host, str)
                or not self.host.strip() or len(self.host) > 253
                or any(char.isspace() for char in self.host)
                or type(self.port) is not int or not 1 <= self.port <= 65535
                or self.security not in ('plain', 'starttls', 'tls')
                or type(self.timeout_seconds) not in (float, int)
                or not 0 < self.timeout_seconds <= 300):
            raise ValueError('invalid static Email transport configuration')
        if self.security == 'plain' and (self.host not in ('localhost', '127.0.0.1', '::1')
                                         or self.username is not None):
            raise ValueError('plain SMTP is restricted to unauthenticated loopback relays')
        if (self.username is None) != (self.password_secret_reference is None) or any(
            value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256)
            for value in (self.username, self.password_secret_reference)
        ):
            raise ValueError('SMTP authentication requires a username and secret reference')

    @classmethod
    def from_dict(cls, data: Any) -> 'EmailConfig':
        if not isinstance(data, dict):
            raise ValueError('email must be a static transport object')
        try:
            return cls(**data)
        except TypeError as error:
            raise ValueError('invalid static Email transport configuration') from error


@dataclass(frozen=True)
class ImapConfig:
    host: str
    username: str
    password_secret_reference: str
    port: int = 993
    security: str = 'tls'
    mailbox: str = 'INBOX'
    timeout_seconds: float = 30
    poll_seconds: float = 30
    batch_size: int = 32

    def __post_init__(self) -> None:
        if (not isinstance(self.host, str) or not self.host.strip() or len(self.host) > 253
                or any(char.isspace() for char in self.host)
                or not isinstance(self.username, str) or not self.username.strip() or len(self.username) > 256
                or not isinstance(self.password_secret_reference, str) or not self.password_secret_reference.strip()
                or len(self.password_secret_reference) > 256
                or type(self.port) is not int or not 1 <= self.port <= 65535
                or self.security not in ('starttls', 'tls')
                or not isinstance(self.mailbox, str) or not self.mailbox.strip() or len(self.mailbox) > 256
                or '\\r' in self.mailbox or '\\n' in self.mailbox
                or type(self.timeout_seconds) not in (float, int) or not 0 < self.timeout_seconds <= 300
                or type(self.poll_seconds) not in (float, int) or not 0 < self.poll_seconds <= 86400
                or type(self.batch_size) is not int or not 1 <= self.batch_size <= 100):
            raise ValueError('invalid static IMAP acquisition configuration')

    @classmethod
    def from_dict(cls, data: Any) -> 'ImapConfig':
        if not isinstance(data, dict):
            raise ValueError('imap must be a static acquisition object')
        try:
            return cls(**data)
        except TypeError as error:
            raise ValueError('invalid static IMAP acquisition configuration') from error


class EmailTransport(Protocol):
    def send(self, sender: str, recipient: str, message: bytes) -> DeliveryResult: ...


class SmtpTransport:
    def __init__(self, config: EmailConfig, secret_references: dict[str, str]):
        self.config = config
        self.password = None
        if config.password_secret_reference is not None:
            reference = secret_references.get(config.password_secret_reference)
            self.password = os.environ.get(reference or '')
            if not reference or not self.password:
                raise ValueError('SMTP secret reference is unresolved')

    def send(self, sender: str, recipient: str, message: bytes) -> DeliveryResult:
        config = self.config
        smtp: smtplib.SMTP | None = None
        sending_data = False
        try:
            if config.security == 'tls':
                smtp = smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout_seconds,
                                       context=ssl.create_default_context())
            else:
                smtp = smtplib.SMTP(config.host, config.port, timeout=config.timeout_seconds)
                if config.security == 'starttls':
                    smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo_or_helo_if_needed()
            if config.username is not None:
                assert self.password is not None
                smtp.login(config.username, self.password)
            if smtp.mail(sender)[0] != 250 or smtp.rcpt(recipient)[0] not in (250, 251):
                return 'failure'
            sending_data = True
            code, _ = smtp.data(message)
            return 'success' if code == 250 else 'failure'
        except smtplib.SMTPResponseException:
            return 'failure'
        except (OSError, smtplib.SMTPException):
            return 'indeterminate' if sending_data else 'failure'
        finally:
            if smtp is not None:
                try:
                    smtp.close()
                except OSError:
                    pass  # Cleanup cannot change the provider's DATA acceptance.


class EmailChannel:
    capabilities: tuple[str, ...] = CAPABILITIES

    def __init__(self, database: Path, config: EmailConfig,
                 transport: EmailTransport):
        from .core import connect
        self.database, self.config, self.transport = database, config, transport
        database.parent.mkdir(parents=True, exist_ok=True)
        with connect(database) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS email_handoffs (
                id TEXT PRIMARY KEY, message_id TEXT UNIQUE NOT NULL,
                sender TEXT NOT NULL, recipient TEXT NOT NULL,
                message BLOB NOT NULL, result TEXT NOT NULL
            )''')
            db.execute('''CREATE TABLE IF NOT EXISTS email_acquisition (
                mailbox TEXT NOT NULL, uidvalidity TEXT NOT NULL, uid TEXT NOT NULL,
                status TEXT NOT NULL, communication_id TEXT, outcome TEXT NOT NULL,
                PRIMARY KEY(mailbox, uidvalidity, uid)
            )''')

    def normalize(self, payload: Any) -> dict[str, Any]:
        from .core import connect
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_EMAIL_BYTES:
            raise ValueError('expected bounded Email bytes')
        mail = BytesParser(policy=policy.default).parsebytes(payload)
        for name in ('From', 'Message-ID', 'Date', 'Subject', 'In-Reply-To', 'References'):
            if len(mail.get_all(name, [])) > 1:
                raise ValueError('duplicate Email header')
        if any(part.defects for part in mail.walk()):
            raise ValueError('malformed Email')
        sender_header: Any = mail['From']
        addresses: Any = getattr(sender_header, 'addresses', ())
        if (len(addresses) != 1 or not address(addresses[0].addr_spec)
                or bool(getattr(sender_header, 'defects', ()))):
            raise ValueError('Email requires one technical sender')
        sender = addresses[0].addr_spec
        ids = message_ids(str(mail.get('Message-ID', '')))
        replies = message_ids(str(mail.get('In-Reply-To', '')))
        refs = message_ids(str(mail.get('References', '')))
        if len(ids) != 1 or len(replies) > 1:
            raise ValueError('Email requires one Message-ID and at most one reply reference')
        try:
            timestamp = parsedate_to_datetime(str(mail.get('Date', '')))
            if timestamp.tzinfo is None:
                raise ValueError('Email Date requires a timezone')
            sent_at = timestamp.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('invalid Email Date') from error
        # shortcut: only text/plain and multipart/alternative, add attachment acquisition in #27.
        if mail.get_content_type() not in ('text/plain', 'multipart/alternative'):
            raise ValueError('Email text capability requires plain text')
        parts = list(mail.walk())
        if any(part.get_content_disposition() == 'attachment' or part.get_filename()
               or part.get_content_type() not in ('text/plain', 'text/html', 'multipart/alternative')
               for part in parts):
            raise ValueError('Email attachments are not enabled')
        plain = [part for part in parts if part.get_content_type() == 'text/plain']
        if len(plain) != 1:
            raise ValueError('Email requires one unambiguous plain text body')
        try:
            decoded = plain[0].get_payload(decode=True)
            if not isinstance(decoded, bytes) or plain[0].defects:
                raise ValueError('malformed Email text body')
            text = decoded.decode(plain[0].get_content_charset() or 'ascii').replace('\r\n', '\n').rstrip('\n')
        except (LookupError, UnicodeError) as error:
            raise ValueError('invalid Email text encoding') from error
        subject = str(mail.get('Subject', ''))
        if len(subject) > 256 or '\r' in subject or '\n' in subject:
            raise ValueError('invalid Email subject')
        reply_to = replies[0] if replies else None
        if reply_to:
            with connect(self.database) as db:
                row = db.execute('SELECT id FROM email_handoffs WHERE message_id=? AND recipient=?',
                                 (reply_to, sender)).fetchone()
            if row:
                reply_to = row['id']
        return {'channel': 'email', 'sender': sender, 'idempotency_key': ids[0],
                'text': text, 'sent_at': sent_at,
                'thread_id': refs[0] if refs else replies[0] if replies else ids[0],
                'reply_to': reply_to, 'transport_message_id': ids[0],
                'transport_reply_to': replies[0] if replies else None,
                'references': refs, 'subject': subject}

    def acquired(self, mailbox: str, uidvalidity: str, uid: str) -> bool:
        from .core import connect
        with connect(self.database) as db:
            return db.execute('SELECT 1 FROM email_acquisition WHERE mailbox=? AND uidvalidity=? AND uid=?',
                              (mailbox, uidvalidity, uid)).fetchone() is not None

    def record_acquisition(self, mailbox: str, uidvalidity: str, uid: str, status: str,
                           communication_id: str | None, outcome: str) -> None:
        from .core import connect
        if status not in ('acquired', 'unsupported') or (status == 'acquired') != bool(communication_id):
            raise ValueError('invalid Email acquisition outcome')
        with connect(self.database) as db:
            db.execute('INSERT OR IGNORE INTO email_acquisition VALUES (?, ?, ?, ?, ?, ?)',
                       (mailbox, uidvalidity, uid, status, communication_id, outcome))

    def acquisition_outcomes(self) -> list[dict[str, str | None]]:
        from .core import connect
        with connect(self.database) as db:
            return [dict(row) for row in db.execute('SELECT mailbox, uidvalidity, uid, status, communication_id, '
                                                    'outcome FROM email_acquisition ORDER BY rowid')]

    def prepare(self, outbound: 'Communication', inbound: 'Communication') -> 'Communication':
        refs = tuple(dict.fromkeys((*inbound.references, inbound.transport_message_id)))
        if any(not isinstance(ref, str) for ref in refs):
            raise ValueError('Email reply requires transport message references')
        # Preserve the root and most recent ancestors within the canonical budget.
        if len(refs) > 32:
            refs = refs[:1] + refs[-31:]
        return replace(outbound, sender=self.config.address, recipient=inbound.sender,
                       subject=inbound.subject if (inbound.subject or '').lower().startswith('re:')
                       else 'Re: ' + (inbound.subject or 'PieceTogether'),
                       transport_message_id=f'<{outbound.id}@{self.config.address.split("@")[1]}>',
                       transport_reply_to=inbound.transport_message_id,
                       references=tuple(ref for ref in refs if ref is not None))

    def deliver(self, outbound: 'Communication') -> DeliveryResult:
        from .core import connect
        if (outbound.channel != 'email' or outbound.sender != self.config.address
                or not address(outbound.recipient) or not outbound.transport_message_id):
            raise ValueError('invalid canonical outbound Email')
        message = EmailMessage(policy=policy.SMTP)
        message['From'], message['To'] = outbound.sender, outbound.recipient
        message['Message-ID'] = outbound.transport_message_id
        message['Date'] = format_datetime(datetime.fromisoformat(outbound.sent_at))
        message['Subject'] = outbound.subject or 'PieceTogether'
        if outbound.transport_reply_to:
            message['In-Reply-To'] = outbound.transport_reply_to
        if outbound.references:
            message['References'] = ' '.join(outbound.references)
        message.set_content(outbound.text)
        raw = message.as_bytes()
        with connect(self.database) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM email_handoffs WHERE id=?', (outbound.id,)).fetchone()
            if row:
                if (row['message'] != raw or row['sender'] != outbound.sender
                        or row['recipient'] != outbound.recipient):
                    raise ValueError('Email handoff identity already belongs to a different message')
                if row['result'] != 'failure':
                    return 'success' if row['result'] == 'success' else 'indeterminate'
                db.execute("UPDATE email_handoffs SET result='started' WHERE id=?", (outbound.id,))
            else:
                db.execute('INSERT INTO email_handoffs VALUES (?, ?, ?, ?, ?, ?)',
                           (outbound.id, outbound.transport_message_id, outbound.sender,
                            outbound.recipient, raw, 'started'))
        try:
            result = self.transport.send(outbound.sender, outbound.recipient or '', raw)
            if result not in ('success', 'failure', 'indeterminate'):
                result = 'indeterminate'
        except Exception:
            result = 'indeterminate'
        with connect(self.database) as db:
            db.execute('UPDATE email_handoffs SET result=? WHERE id=?', (result, outbound.id))
        return result


class InboundEmailTransport(Protocol):
    def fetch(self) -> tuple[str, tuple[tuple[str, bytes], ...]]: ...


class ImapTransport:
    """Static IMAP wire adapter; mailbox reads never mutate message flags."""

    def __init__(self, config: ImapConfig, secret_references: dict[str, str],
                 acquired: Callable[[str, str, str], bool] | None = None):
        reference = secret_references.get(config.password_secret_reference)
        password = os.environ.get(reference or '')
        if not reference or not password:
            raise ValueError('IMAP secret reference is unresolved')
        self.config, self.password, self.acquired = config, password, acquired

    def fetch(self) -> tuple[str, tuple[tuple[str, bytes], ...]]:
        config = self.config
        client: Any = None
        try:
            if config.security == 'tls':
                client = imaplib.IMAP4_SSL(config.host, config.port, timeout=config.timeout_seconds,
                                            ssl_context=ssl.create_default_context())
            else:
                client = imaplib.IMAP4(config.host, config.port, timeout=config.timeout_seconds)
                client.starttls(ssl_context=ssl.create_default_context())
            client.login(config.username, self.password)
            status, _ = client.select(config.mailbox, readonly=True)
            if status != 'OK':
                raise OSError('IMAP mailbox is unavailable')
            validity = client.response('UIDVALIDITY')[1]
            if not validity or not isinstance(validity[0], bytes) or not validity[0].isdigit():
                raise OSError('IMAP UIDVALIDITY is unavailable')
            uidvalidity = validity[0].decode('ascii')
            status, found = client.uid('search', None, 'ALL')
            if status != 'OK' or not found or not isinstance(found[0], bytes):
                raise OSError('IMAP UID search failed')
            uids = found[0].split()
            messages: list[tuple[str, bytes]] = []
            for raw_uid in uids:
                if not raw_uid.isdigit():
                    raise OSError('IMAP returned an invalid UID')
                uid = raw_uid.decode('ascii')
                if self.acquired is not None and self.acquired(config.mailbox, uidvalidity, uid):
                    continue
                if len(messages) >= config.batch_size:
                    break
                status, data = client.uid('fetch', raw_uid, '(BODY.PEEK[])')
                if status != 'OK' or not data:
                    raise OSError('IMAP message fetch failed')
                payload = next((item[1] for item in data if isinstance(item, tuple)
                                and len(item) == 2 and isinstance(item[1], bytes)), None)
                if payload is None:
                    raise OSError('IMAP message fetch was empty')
                messages.append((uid, payload))
            return uidvalidity, tuple(messages)
        except (imaplib.IMAP4.error, OSError, ssl.SSLError) as error:
            raise OSError('IMAP acquisition unavailable') from error
        finally:
            if client is not None:
                try:
                    client.logout()
                except (OSError, imaplib.IMAP4.error):
                    pass


class EmailAcquisitionWorker:
    """Poll one configured mailbox; Core persistence is the acquisition boundary."""

    def __init__(self, core: 'Core', channel: EmailChannel, transport: InboundEmailTransport,
                 config: ImapConfig, sleep: Callable[[float], None] = time.sleep):
        self.core, self.channel, self.transport, self.config, self.sleep = (
            core, channel, transport, config, sleep)

    def poll_once(self) -> int:
        uidvalidity, messages = self.transport.fetch()
        if not uidvalidity.isdigit():
            raise OSError('IMAP UIDVALIDITY is unavailable')
        acquired = 0
        for uid, raw in messages:
            if not uid.isdigit():
                raise OSError('IMAP returned an invalid UID')
            if self.channel.acquired(self.config.mailbox, uidvalidity, uid):
                continue
            try:
                result = self.core.accept_transport(raw)
            except ValueError:
                # Unsupported normalized mail is a durable operational outcome, never a queue blocker.
                self.channel.record_acquisition(self.config.mailbox, uidvalidity, uid,
                                                'unsupported', None, 'unsupported_message')
            else:
                self.channel.record_acquisition(self.config.mailbox, uidvalidity, uid, 'acquired',
                                                result['communication_id'], result['status'])
            acquired += 1
        return acquired

    def run(self) -> None:
        backoff = self.config.poll_seconds
        self.core.recover_pending(channel='email')
        while True:
            try:
                self.poll_once()
            except (OSError, sqlite3.Error):
                self.sleep(backoff)
                backoff = min(backoff * 2, 300)
            else:
                backoff = self.config.poll_seconds
                self.sleep(backoff)
