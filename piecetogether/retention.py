"""Deployment-wide local retention, availability and maintenance coordination.

Not a scheduler or selective-erasure API. ArtifactStore still owns bytes.
"""

import fcntl
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Concatenate, ParamSpec, TypeVar

if TYPE_CHECKING:
    from .core import Core, DevelopmentChannel
    from .email_channel import EmailChannel
    from .telegram_channel import TelegramChannel


CATEGORIES = ('communications', 'semantic_history', 'evaluation_traces', 'current_view')
_ACTIONS = ('retain', 'redact', 'delete')


class RetentionUnavailable(RuntimeError):
    """Required evidence was erased; processing cannot safely continue."""


class RetentionBusy(RuntimeError):
    """Maintenance conflicts with an active local Core operation; retry quiescently."""


@dataclass(frozen=True)
class RetentionPolicy:
    communications: str = 'retain'
    semantic_history: str = 'retain'
    evaluation_traces: str = 'retain'
    current_view: str = 'retain'

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or value not in _ACTIONS for value in asdict(self).values()):
            raise ValueError('retention actions must be retain, redact or delete')

    @classmethod
    def from_dict(cls, value: Any) -> 'RetentionPolicy':
        if not isinstance(value, dict) or set(value) - set(CATEGORIES):
            raise ValueError('retention must be a static category policy object')
        try:
            return cls(**value)
        except TypeError as error:
            raise ValueError('invalid retention policy') from error


def initialize(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS retention_state (
        category TEXT PRIMARY KEY CHECK(category IN
            ('communications', 'semantic_history', 'evaluation_traces', 'current_view')),
        availability TEXT NOT NULL CHECK(availability IN ('redact', 'delete'))
    )""")


def availability(db: sqlite3.Connection) -> dict[str, str]:
    """Absent state means legacy/default retain; rows contain no record identifiers."""
    result = dict.fromkeys(CATEGORIES, 'retain')
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='retention_state'").fetchone():
        result.update((row[0], row[1]) for row in db.execute(
            'SELECT category, availability FROM retention_state'))
    return result


def require(db: sqlite3.Connection, category: str) -> None:
    if availability(db)[category] != 'retain':
        raise RetentionUnavailable(f'{category} unavailable after retention')


def require_processing(db: sqlite3.Connection) -> None:
    if any(value != 'retain' for value in availability(db).values()):
        raise RetentionUnavailable('processing unavailable after retention; provision a fresh deployment')


@contextmanager
def operation(database: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Cross-process fence, held through I/O, never a long SQLite semantic lock.

    A sweep rejects rather than racing an in-flight model, channel or sink call.
    All Core instances for this deployment must use the same canonical DB path.
    """
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(database) + '.retention.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RetentionBusy('retention requires quiescent local Core operations') from error
        yield
    finally:
        os.close(descriptor)


P = ParamSpec('P')
R = TypeVar('R')


def coordinated(method: Callable[Concatenate['Core', P], R]) -> Callable[Concatenate['Core', P], R]:
    @wraps(method)
    def wrapped(self: 'Core', /, *args: P.args, **kwargs: P.kwargs) -> R:
        with operation(self.config.database):
            return method(self, *args, **kwargs)
    return wrapped


def processing(method: Callable[Concatenate['Core', P], R]) -> Callable[Concatenate['Core', P], R]:
    @wraps(method)
    def wrapped(self: 'Core', /, *args: P.args, **kwargs: P.kwargs) -> R:
        with operation(self.config.database):
            with closing(sqlite3.connect(self.config.database)) as db:
                require_processing(db)
            return method(self, *args, **kwargs)
    return wrapped


C = TypeVar('C', 'DevelopmentChannel', 'EmailChannel', 'TelegramChannel')


def channel_processing(method: Callable[Concatenate[C, P], R]) -> Callable[Concatenate[C, P], R]:
    """Fence checkpoint/handoff writes that acquisition workers make outside Core."""
    @wraps(method)
    def wrapped(self: C, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with operation(self.database):
            with closing(sqlite3.connect(self.database)) as db:
                require_processing(db)
            return method(self, *args, **kwargs)
    return wrapped


def apply(db: sqlite3.Connection, policy: RetentionPolicy) -> dict[str, str]:
    """Caller owns exclusive operation fence and BEGIN IMMEDIATE transaction.

    Redaction deliberately retains only the same non-identifying availability
    marker as deletion. No per-record tombstones, reasons or counts survive.
    Source erasure invalidates both source categories and every dependent copy.
    """
    previous = availability(db)
    effective = {category: max((previous[category], action), key=_ACTIONS.index)
                 for category, action in asdict(policy).items()}
    source_action = max((effective['communications'], effective['semantic_history']), key=_ACTIONS.index)
    if source_action != 'retain':
        if (db.execute('SELECT 1 FROM artifact_deletions LIMIT 1').fetchone()
                or db.execute("SELECT 1 FROM artifact_publications WHERE state='pending' LIMIT 1").fetchone()):
            raise RetentionBusy('complete pending Artifact publication/deletion before source retention')
        effective = {category: max((action, source_action), key=_ACTIONS.index)
                     for category, action in effective.items()}
        # All Core-owned evidence tables, including first-party acquisition/handoff
        # and local sink copies. Bytes are deliberately outside this transaction.
        tables = (
            'processing_attempts', 'deliveries', 'turns', 'semantic_commits',
            'trusted_records', 'reference_assertions', 'reference_commits',
            'trusted_relationships', 'trusted_groundings', 'trusted_grounding_items',
            'exposed_groundings', 'pending_items', 'candidate_target_bindings',
            'event_delivery_attempts', 'event_delivery', 'semantic_outbox',
            'emergent_concepts', 'artifact_ingress',
            'artifact_publication_failures', 'artifact_publications', 'artifact_stages',
            'artifact_lifecycle', 'artifact_deletions', 'trusted_artifacts', 'semantic_state',
            'projection_deliveries',
            'email_handoffs', 'email_acquisition', 'telegram_handoffs', 'telegram_acquisition',
        )
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table in existing:
                db.execute(f'DELETE FROM {table}')
    elif effective['evaluation_traces'] != 'retain':
        for table in ('processing_attempts', 'candidate_target_bindings', 'pending_items', 'exposed_groundings'):
            db.execute(f'DELETE FROM {table}')
        # Pending exposure Proposals can contain decision diagnostics as well.
        # Discard working copies, not committed Grounding outcomes. Communications
        # and the trusted ledger remain independent, but retry is no longer safe.
        db.execute('UPDATE turns SET trace=NULL, proposal=NULL')
    for category, action in effective.items():
        if action != 'retain':
            db.execute('INSERT INTO retention_state VALUES (?, ?) '
                       'ON CONFLICT(category) DO UPDATE SET availability=excluded.availability',
                       (category, action))
    return effective
