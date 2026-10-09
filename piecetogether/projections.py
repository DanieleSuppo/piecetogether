"""Delivery of committed outbox records, separate from semantic transactions."""

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ProjectionConfig:
    adapter: str = 'development'
    max_attempts: int = 3
    backoff_seconds: float = 1
    max_backoff_seconds: float = 60
    lease_seconds: float = 60

    def __post_init__(self) -> None:
        if self.adapter != 'development':
            raise ValueError('only the static development ProjectionSink is available')
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 100:
            raise ValueError('max_attempts must be an integer from 1 to 100')
        if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 86400
               for value in (self.backoff_seconds, self.max_backoff_seconds, self.lease_seconds)):
            raise ValueError('delivery timings must be finite positive seconds, at most one day')
        if self.max_backoff_seconds < self.backoff_seconds:
            raise ValueError('max_backoff_seconds must be at least backoff_seconds')


class ProjectionSink(Protocol):
    """True means accepted; consumers deduplicate by event and semantic commit ID."""

    def deliver(self, event: dict[str, Any]) -> bool: ...


class DevelopmentProjectionSink:
    """Durable local mailbox with idempotent acceptance, not an external connector."""

    def __init__(self, database: Path):
        self.database = database

    def deliver(self, event: dict[str, Any]) -> bool:
        with sqlite3.connect(self.database) as db:
            record = json.dumps(event)
            db.execute('INSERT OR IGNORE INTO projection_deliveries VALUES (?, ?, ?)',
                       (event['id'], event['semantic_commit_id'], record))
            stored = db.execute('SELECT record FROM projection_deliveries WHERE event_id=?',
                                (event['id'],)).fetchone()
            if stored is None or json.loads(stored[0]) != event:
                raise ValueError('event identity already belongs to a different payload')
        return True


def initialize(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS event_delivery (
            event_id TEXT PRIMARY KEY REFERENCES semantic_outbox(id),
            state TEXT NOT NULL, attempt INTEGER NOT NULL,
            attempts_since_recovery INTEGER NOT NULL,
            next_attempt_at REAL NOT NULL, lease_until REAL
        );
        CREATE TABLE IF NOT EXISTS event_delivery_attempts (
            event_id TEXT NOT NULL REFERENCES semantic_outbox(id),
            attempt INTEGER NOT NULL, outcome TEXT NOT NULL,
            started_at REAL NOT NULL, finished_at REAL, error_type TEXT,
            PRIMARY KEY(event_id, attempt)
        );
        CREATE TABLE IF NOT EXISTS projection_deliveries (
            event_id TEXT PRIMARY KEY, semantic_commit_id TEXT NOT NULL, record TEXT NOT NULL
        );
    """)


def claim(db: sqlite3.Connection, event_id: str, timestamp: float,
          config: ProjectionConfig) -> int | None:
    """Caller holds a short writer transaction; an expired lease fences late results."""
    db.execute("INSERT OR IGNORE INTO event_delivery VALUES (?, 'pending', 0, 0, 0, NULL)", (event_id,))
    row = db.execute('SELECT * FROM event_delivery WHERE event_id=?', (event_id,)).fetchone()
    assert row is not None
    if row['state'] in ('delivered', 'failed'):
        return None
    if row['state'] == 'delivering':
        if row['lease_until'] > timestamp:
            return None
        db.execute("UPDATE event_delivery_attempts SET outcome='indeterminate', finished_at=?, "
                   "error_type='LeaseExpired' WHERE event_id=? AND attempt=? AND outcome='started'",
                   (timestamp, event_id, row['attempt']))
        if row['attempts_since_recovery'] >= config.max_attempts:
            db.execute("UPDATE event_delivery SET state='failed', lease_until=NULL WHERE event_id=?", (event_id,))
            return None
    elif row['next_attempt_at'] > timestamp:
        return None
    attempt = row['attempt'] + 1
    db.execute("UPDATE event_delivery SET state='delivering', attempt=?, "
               "attempts_since_recovery=attempts_since_recovery+1, lease_until=? WHERE event_id=?",
               (attempt, timestamp + config.lease_seconds, event_id))
    db.execute("INSERT INTO event_delivery_attempts VALUES (?, ?, 'started', ?, NULL, NULL)",
               (event_id, attempt, timestamp))
    return int(attempt)


def finish(db: sqlite3.Connection, event_id: str, attempt: int, timestamp: float,
           config: ProjectionConfig, accepted: bool, error_type: str | None) -> str:
    row = db.execute('SELECT * FROM event_delivery WHERE event_id=?', (event_id,)).fetchone()
    assert row is not None
    if row['state'] != 'delivering' or row['attempt'] != attempt:
        return str(row['state'])
    state = ('delivered' if accepted else
             'failed' if row['attempts_since_recovery'] >= config.max_attempts else 'pending')
    delay = min(config.max_backoff_seconds,
                config.backoff_seconds * 2 ** (row['attempts_since_recovery'] - 1))
    db.execute('UPDATE event_delivery SET state=?, next_attempt_at=?, lease_until=NULL WHERE event_id=?',
               (state, timestamp + delay if not accepted else timestamp, event_id))
    db.execute('UPDATE event_delivery_attempts SET outcome=?, finished_at=?, error_type=? '
               'WHERE event_id=? AND attempt=?',
               ('accepted' if accepted else 'retryable', timestamp, error_type, event_id, attempt))
    return state


def inspect(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [{**dict(row), 'attempts': [dict(attempt) for attempt in db.execute(
        'SELECT * FROM event_delivery_attempts WHERE event_id=? ORDER BY attempt',
        (row['event_id'],))]} for row in db.execute('SELECT * FROM event_delivery ORDER BY rowid')]
