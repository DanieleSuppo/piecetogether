"""Operator-only controlled local erasure, never a global/physical erasure claim.

Lock order is database exclusive fence, then store lock. Filesystem work never
spans a SQLite transaction. Frozen jobs retain original authority until purge.
"""

import json
import os
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import retention
from .artifact_store import ArtifactStore, LocalArtifactStore

OWNER_FILE = '.core-owner.json'
LOCK_FILE = '.artifact-maintenance.lock'
ERASED_FILE = '.core-erased'
_ERASED_CONTENT = b'core-local-erased-v1\n'
# Explicit ownership, not an arbitrary delete-all-tables loop. Unknown SQL
# resources block completion. Child tables precede parents for foreign keys.
_RUNTIME_TABLES = {
    'record_expiry': 'communication_id trace_expired payload_expired settled_at',
    'view_access': 'singleton withheld',
    'processing_attempts': 'communication_id attempt proposal outbound trace status',
    'deliveries': 'id outbound',
    'turns': 'id channel sender idempotency_key inbound proposal outbound trace status',
    'trusted_records': 'id kind record grounding_id candidate_id',
    'reference_assertions': 'id record', 'reference_commits': 'id record',
    'trusted_relationships': 'id record', 'trusted_groundings': 'id record',
    'trusted_grounding_items': 'id record',
    'exposed_groundings': 'id communication_id actor_id contract_version exposed_at proposal',
    'pending_items': 'id grounding_id candidate_id policy',
    'candidate_target_bindings': 'grounding_id candidate_id target_grounding_id target_candidate_id',
    'event_delivery_attempts': 'event_id attempt outcome started_at finished_at error_type',
    'event_delivery': 'event_id state attempt attempts_since_recovery next_attempt_at lease_until',
    'projection_deliveries': 'event_id semantic_commit_id record', 'semantic_outbox': 'id record',
    'semantic_commits': 'id communication_id record',
    'emergent_concepts': 'name communication_id record',
    'artifact_ingress': 'communication_id attachment_id actor_id media_type checksum size received_at',
    'artifact_publication_failures': 'artifact_id record',
    'artifact_publications': 'artifact_id stage_ref checksum size state storage_ref',
    'artifact_stages': 'communication_id attachment_id actor_id stage_ref checksum size state',
    'artifact_lifecycle': 'id artifact_id record', 'artifact_deletions': 'artifact_id reference failure',
    'trusted_artifacts': 'id communication_id attachment_id record',
    'semantic_state': 'singleton revision contract_version',
    'email_handoffs': 'id message_id sender recipient message result',
    'email_acquisition': 'mailbox uidvalidity uid status communication_id outcome',
    'telegram_handoffs': 'bot_id id part recipient text reply_to result message_id',
    'telegram_acquisition': 'bot_id update_id status communication_id outcome',
    'retention_state': 'category availability', 'artifact_store_owner': 'singleton token root',
    'erasure_jobs': 'relative_path kind size device inode state',
}
_DIRECTORIES = ('persistent', 'staging', 'keys')
_REFERENCE = re.compile(r'[0-9a-f]{32}')
_BLOCKERS = frozenset((
    'unsafe_boundary', 'unsafe_entry', 'ownership_unverified', 'foreign_owner',
    'invalid_reference', 'store_missing', 'store_unsupported', 'inventory_changed',
    'storage_unavailable', 'sentinel_unverified', 'database_resources_unverified',
    'runtime_pending', 'resources_pending', 'database_unavailable', 'state_unavailable',
))


class InventoryBlocked(ValueError):
    """A fixed classification; an optional internal job key never enters reports."""

    def __init__(self, classification: str, *, relative: str | None = None):
        super().__init__(classification)
        self.relative = relative


@dataclass(frozen=True)
class RemovalFile:
    relative_path: str
    kind: str
    size: int
    device: int
    inode: int


def initialize(db: sqlite3.Connection) -> None:
    # Expand the private R2a CHECK constraints atomically, preserving every job.
    # An outer transaction can roll this back; never use executescript here.
    db.execute('SAVEPOINT erasure_expand')
    try:
        for table, column, values in (
            ('erasure_state', 'phase', "'prepared', 'removing', 'files_removed', 'complete'"),
            ('erasure_jobs', 'state', "'pending', 'absent', 'unavailable'"),
        ):
            row = db.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
            if row is None:
                continue
            sql = row[0]
            legacy = f"CHECK({column}='{ 'prepared' if column == 'phase' else 'pending' }')"
            if legacy not in sql.replace(' ', '').replace('\n', ''):
                continue
            if db.execute("SELECT 1 FROM sqlite_master WHERE tbl_name=? AND type IN ('trigger','index') "
                          "AND sql IS NOT NULL", (table,)).fetchone():
                raise ValueError('erasure schema expansion blocked by unknown resources')
            # R2a has exactly these columns. Unknown additions must not be lost.
            columns = [item[1] for item in db.execute(f'PRAGMA table_info({table})')]
            expected = (['singleton', 'phase', 'inventory', 'blocker'] if column == 'phase'
                        else ['relative_path', 'kind', 'size', 'device', 'inode', 'state'])
            if columns != expected:
                raise ValueError('erasure schema expansion blocked by unknown resources')
            expanded = re.sub(rf"CHECK\s*\(\s*{column}\s*=\s*'(?:prepared|pending)'\s*\)",
                              f'CHECK({column} IN ({values}))', sql)
            expanded = expanded.replace(f'CREATE TABLE {table}', f'CREATE TABLE {table}_expanded', 1)
            db.execute(expanded)
            db.execute(f'INSERT INTO {table}_expanded SELECT * FROM {table}')
            db.execute(f'DROP TABLE {table}')
            db.execute(f'ALTER TABLE {table}_expanded RENAME TO {table}')
        db.execute("""CREATE TABLE IF NOT EXISTS artifact_store_owner (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            token TEXT NOT NULL, root TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS erasure_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            phase TEXT NOT NULL CHECK(phase IN ('prepared', 'removing', 'files_removed', 'complete')),
            inventory TEXT NOT NULL CHECK(inventory IN ('pending', 'verified', 'blocked')),
            blocker TEXT
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS erasure_jobs (
            relative_path TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('publication', 'stage', 'key', 'temporary', 'owner')),
            size INTEGER NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending', 'absent', 'unavailable'))
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS erasure_control (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            store_required INTEGER NOT NULL CHECK(store_required IN (0,1)),
            failures INTEGER NOT NULL CHECK(failures>=0)
        )""")
        db.execute('RELEASE erasure_expand')
    except BaseException:
        db.execute('ROLLBACK TO erasure_expand')
        db.execute('RELEASE erasure_expand')
        raise


def frozen(db: sqlite3.Connection) -> bool:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erasure_state'").fetchone():
        return False
    return db.execute('SELECT 1 FROM erasure_state LIMIT 1').fetchone() is not None


def require_unfrozen(db: sqlite3.Connection) -> None:
    if frozen(db):
        raise retention.RetentionUnavailable('Core local erasure prepared; deployment frozen')


def _directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise InventoryBlocked('unsafe_boundary')


def _boundary(root: Path, database: Path) -> None:
    # Check every lexical ancestor before resolve can conceal a symlink.
    if '..' in root.parts:
        raise InventoryBlocked('unsafe_boundary')
    absolute = root.absolute()
    for parent in reversed((absolute, *absolute.parents)):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise InventoryBlocked('unsafe_boundary')
    _directory(root)
    if database.resolve().is_relative_to(root.resolve()):
        raise InventoryBlocked('unsafe_boundary')
    for name in _DIRECTORIES:
        _directory(root / name)


def _regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid()):
        raise InventoryBlocked('unsafe_entry')
    return info


def _read_owner(root: Path) -> dict[str, Any]:
    path = root / OWNER_FILE
    _regular(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise InventoryBlocked('unsafe_entry')
        raw = stream.read(4097)
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise InventoryBlocked('ownership_unverified') from None
    if (len(raw) > 4096 or not isinstance(value, dict)
            or set(value) != {'token', 'database'}
            or not isinstance(value['token'], str) or not _REFERENCE.fullmatch(value['token'])
            or not isinstance(value['database'], str)):
        raise InventoryBlocked('ownership_unverified')
    return value


def bind_empty_store(store: LocalArtifactStore, database: Path) -> None:
    """Establish evidence only for an empty store, never attest existing bytes.

    The Core caller holds its database operation fence. Existing foreign evidence
    is never replaced. Failure leaves normal legacy processing compatible, but
    preparation must report that store unverified. No legacy override is exposed.
    """
    store.database = database
    try:
        require_no_sentinel(store.root)
        _boundary(store.root, database)
        with store.maintenance_lock():
            if _regular(store.root / LOCK_FILE).st_size != 0:
                return
            with closing(sqlite3.connect(database)) as db:
                if frozen(db):
                    return
                row = db.execute('SELECT token, root FROM artifact_store_owner WHERE singleton=1').fetchone()
            if (store.root / OWNER_FILE).exists():
                return  # Inventory will compare evidence; never overwrite any owner.
            if (set(path.name for path in store.root.iterdir()) - {*_DIRECTORIES, LOCK_FILE}
                    or any(any((store.root / name).iterdir()) for name in _DIRECTORIES)):
                return
            root = str(store.root.resolve())
            if row is not None:
                return  # Never restore lost owner evidence or implicitly rebind a known store.
            token = uuid4().hex
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('INSERT OR IGNORE INTO artifact_store_owner VALUES (1, ?, ?)', (token, root))
            # Exclusive create makes crashes/partial evidence safe blocking, not re-attestation.
            descriptor = os.open(store.root / OWNER_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                json.dump({'token': token, 'database': str(database.resolve())}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = os.open(store.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except (OSError, InventoryBlocked):
        # Safe default for legacy/missing/unowned stores: no erase authorization.
        return


def store_database(store: LocalArtifactStore) -> Path | None:
    """Read and validate byte-access authority before creating any database fence.

    Unmarked legacy stores remain usable, never attested. Bound handles retain
    their database freeze even without a marker. Marker-derived paths must name
    an existing canonical database with matching token/root evidence, not merely
    a readable SQLite file. Call again under both fences before byte I/O.
    """
    try:
        require_no_sentinel(store.root)
        bound = store.database.resolve(strict=True) if store.database is not None else None
        try:
            (store.root / OWNER_FILE).lstat()
        except FileNotFoundError:
            if bound is not None:
                with closing(sqlite3.connect(bound.as_uri() + '?mode=ro', uri=True)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='artifact_store_owner'").fetchone():
                        row = db.execute('SELECT root FROM artifact_store_owner WHERE singleton=1').fetchone()
                        if row is not None:
                            _regular(bound)
                            _boundary(store.root, bound)
                            if row[0] != str(store.root.resolve()):
                                raise InventoryBlocked('foreign_owner')
                    require_unfrozen(db)
            return bound
        owner = _read_owner(store.root)
        database = Path(owner['database'])
        if (not database.is_absolute() or str(database.resolve(strict=True)) != owner['database']
                or (bound is not None and database != bound)):
            raise InventoryBlocked('foreign_owner')
        _regular(database)
        _boundary(store.root, database)
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT token, root FROM artifact_store_owner WHERE singleton=1').fetchone()
            if row is None or tuple(row) != (owner['token'], str(store.root.resolve())):
                raise InventoryBlocked('ownership_unverified')
            require_unfrozen(db)
        return database
    except retention.RetentionUnavailable:
        raise
    except (OSError, ValueError, sqlite3.Error, RuntimeError):
        raise retention.RetentionUnavailable('Artifact owner state unavailable') from None


def require_store_access(store: LocalArtifactStore, database: Path | None) -> None:
    """Revalidate under the previously selected database fence and store lock."""
    if store_database(store) != database:
        raise retention.RetentionUnavailable('Artifact owner state unavailable')


def _known_obligations(db: sqlite3.Connection) -> bool:
    return any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None for table in (
        'artifact_ingress', 'artifact_stages', 'artifact_publications', 'artifact_deletions', 'trusted_artifacts'))


def _validate_store(database: Path, store: ArtifactStore | None) -> None:
    """Read-only boundary/ownership prerequisites; no lock, schema or byte writes."""
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        obligations = _known_obligations(db)
        owner_row = db.execute('SELECT token, root FROM artifact_store_owner WHERE singleton=1').fetchone()
        references = [row[0] for query in (
            'SELECT stage_ref FROM artifact_stages', 'SELECT stage_ref FROM artifact_publications',
            'SELECT storage_ref FROM artifact_publications WHERE storage_ref IS NOT NULL',
            'SELECT reference FROM artifact_deletions') for row in db.execute(query)]
    if any(not isinstance(reference, str) or not _REFERENCE.fullmatch(reference) for reference in references):
        raise InventoryBlocked('invalid_reference')
    if store is None:
        if obligations or owner_row:
            raise InventoryBlocked('store_missing')
        return
    # Do not treat replay, remote adapters or arbitrary subclasses as a filesystem store.
    if type(store) is not LocalArtifactStore:
        raise InventoryBlocked('store_unsupported')
    if store.database is not None and store.database.resolve() != database.resolve():
        raise InventoryBlocked('foreign_owner')
    _boundary(store.root, database)
    try:
        owner = _read_owner(store.root)
    except FileNotFoundError:
        raise InventoryBlocked('ownership_unverified') from None
    if owner['database'] != str(database.resolve()):
        raise InventoryBlocked('foreign_owner')
    if owner_row is None or (owner['token'], str(store.root.resolve())) != tuple(owner_row):
        raise InventoryBlocked('ownership_unverified')


def inventory(database: Path, store: ArtifactStore | None) -> list[RemovalFile]:
    """Finite, non-destructive allowlisted inventory; caller owns both fences.

    Missing individual files are already absent, not successful finalization.
    Original publication/deletion tables remain obligations until final purge.
    """
    _validate_store(database, store)
    if store is None:
        return []
    assert type(store) is LocalArtifactStore  # Validated above; no adapters/subclasses.
    if set(path.name for path in store.root.iterdir()) != {*_DIRECTORIES, OWNER_FILE, LOCK_FILE}:
        raise InventoryBlocked('unsafe_entry')
    if _regular(store.root / LOCK_FILE).st_size != 0:
        raise InventoryBlocked('unsafe_entry')
    paths = [(store.root / OWNER_FILE, 'owner')]
    for directory in _DIRECTORIES:
        for path in (store.root / directory).iterdir():
            name = path.name
            if directory == 'keys' and re.fullmatch(r'[0-9a-f]{64}', name):
                kind = 'key'
            elif directory != 'keys' and _REFERENCE.fullmatch(name):
                kind = 'publication' if directory == 'persistent' else 'stage'
            elif directory in ('staging', 'keys') and re.fullmatch(r'[0-9a-f]{32}\.tmp', name):
                kind = 'temporary'
            else:
                raise InventoryBlocked('unsafe_entry')
            paths.append((path, kind))
    result = []
    for path, kind in paths:
        info = _regular(path)
        result.append(RemovalFile(str(path.relative_to(store.root)), kind, info.st_size, info.st_dev, info.st_ino))
    return sorted(result, key=lambda item: item.relative_path)


def status(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute('SELECT phase, inventory, blocker FROM erasure_state WHERE singleton=1').fetchone()
    result = report(row[0] if row else 'unprepared', row[1] if row else 'pending', row[2] if row else None,
                    db.execute("SELECT count(*) FROM erasure_jobs WHERE state!='absent'").fetchone()[0])
    if row and row[0] == 'complete':
        try:
            _runtime_empty(db)
        except InventoryBlocked as error:
            result.update(inventory='blocked', blocker=str(error))
        else:
            result['local_logical_complete'] = True
    # Offline read-only status also works before R2a CHECK expansion is applied.
    has_control = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='erasure_control'").fetchone()
    control = db.execute('SELECT failures FROM erasure_control WHERE singleton=1').fetchone() if has_control else None
    if control and control[0]:
        result['retry_after_seconds'] = min(300, 2 ** min(control[0], 9))
    return result


def report(phase: str, verified: str, blocker: str | None, count: int) -> dict[str, Any]:
    if blocker is not None and blocker not in _BLOCKERS:
        blocker = 'state_unavailable'
    return {'scope': 'core_local', 'phase': phase, 'inventory': verified, 'blocker': blocker,
            'pending_files': count, 'complete': False, 'local_logical_complete': False,
            'global_complete': False,
            'retryable': blocker in ('storage_unavailable', 'database_unavailable'),
            'retry_after_seconds': 0,
            'outside_core_local': [
                {'resource': resource, 'owner': 'operator', 'action': 'verify_separately', 'status': 'unverified'}
                for resource in ('providers_and_transports', 'backups_and_storage', 'exports_logs_and_caller_copies')]}


def inspect_inventory(database: Path, store: ArtifactStore | None) -> tuple[list[RemovalFile], str | None]:
    try:
        if type(store) is LocalArtifactStore:
            _boundary(store.root, database)
            with store.maintenance_lock():
                return inventory(database, store), None
        return inventory(database, store), None
    except InventoryBlocked as error:
        return [], str(error)
    except OSError:
        return [], 'storage_unavailable'


def preflight(database: Path, store: ArtifactStore | None) -> dict[str, Any]:
    """Inspect only; caller owns the exclusive database fence."""
    files, blocker = inspect_inventory(database, store)
    with closing(sqlite3.connect(database)) as db:
        current = status(db)
        if current['phase'] not in ('unprepared', 'prepared'):
            current['local_logical_complete'] = False  # Inspection is not a completion receipt.
            return current
        phase = current['phase']
    return report(phase, 'blocked' if blocker else 'verified', blocker, len(files))


@contextmanager
def _local_maintenance(database: Path, store_root: Path | None) -> Iterator[LocalArtifactStore | None]:
    with retention.operation(database, exclusive=True):
        # Existing initialized Core databases only; never fabricate an empty DB.
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True)) as db, db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'turns', 'semantic_state', 'artifact_ingress', 'artifact_stages',
                    'artifact_publications', 'artifact_deletions', 'trusted_artifacts'} <= tables:
                raise ValueError('erasure maintenance requires an initialized Core database')
            initialize(db)
        yield LocalArtifactStore(store_root, create=False) if store_root is not None else None


def preflight_local(database: Path, *, store_root: Path | None) -> dict[str, Any]:
    """Explicit local operator inspection without normal Core/provider startup.

    Adds missing empty erasure schema only. Never attests ownership or creates a store.
    """
    with _local_maintenance(database, store_root) as store:
        return preflight(database, store)


def prepare_local(database: Path, *, store_root: Path | None) -> dict[str, Any]:
    """Prepare an existing database without startup publication/deletion/recovery.

    Pass the explicit configured local root, or None for a no-store deployment.
    Unsupported/remote resources still require separate verification; no guessing.
    """
    with _local_maintenance(database, store_root) as store:
        return prepare(database, store)


def prepare(database: Path, store: ArtifactStore | None) -> dict[str, Any]:
    """Inspect full inventory, freeze, then revalidate/merge authoritative inventory.

    Caller holds the exclusive database fence. First-time inventory errors
    leave retained deployments unprepared. Already-prepared deployments never unfreeze
    or lose jobs on new errors. All inventory I/O stays outside SQLite transactions.
    """
    with closing(sqlite3.connect(database)) as db:
        prepared = frozen(db)
        if prepared and status(db)['phase'] != 'prepared':
            current = status(db)
            current['local_logical_complete'] = False
            return current
    if not prepared:
        _, blocker = inspect_inventory(database, store)
        if blocker is not None:
            return report('unprepared', 'blocked', blocker, 0)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT OR IGNORE INTO erasure_state VALUES (1, 'prepared', 'pending', NULL)")
    files, blocker = inspect_inventory(database, store)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if blocker is None:
            for item in files:
                previous = db.execute('SELECT kind, size, device, inode FROM erasure_jobs WHERE relative_path=?',
                                      (item.relative_path,)).fetchone()
                if previous is not None and tuple(previous) != (item.kind, item.size, item.device, item.inode):
                    blocker = 'inventory_changed'
                    break
            if blocker is None:
                db.executemany('INSERT OR IGNORE INTO erasure_jobs (relative_path, kind, size, device, inode) '
                               'VALUES (?, ?, ?, ?, ?)',
                               [(item.relative_path, item.kind, item.size, item.device, item.inode) for item in files])
        db.execute('UPDATE erasure_state SET inventory=?, blocker=? WHERE singleton=1',
                   ('blocked' if blocker else 'verified', blocker))
        return status(db)


def require_no_sentinel(root: Path) -> None:
    """Even malformed fences deny ordinary bytes; absence alone permits lookup."""
    try:
        (root / ERASED_FILE).lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise retention.RetentionUnavailable('Artifact erased-store state unavailable') from None
    raise retention.RetentionUnavailable('Artifact store erased; deployment frozen')


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sentinel(root: Path, *, create: bool = False) -> None:
    """Validate and durably sync the fixed non-identifying fence, before unlink."""
    path = root / ERASED_FILE
    if create:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(_ERASED_CONTENT)
                stream.flush()
                os.fsync(stream.fileno())
    _regular(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stream.read(len(_ERASED_CONTENT) + 1) != _ERASED_CONTENT):
            raise InventoryBlocked('sentinel_unverified')
        os.fsync(stream.fileno())
    _fsync_directory(root)


def _controlled_tables(db: sqlite3.Connection) -> set[str]:
    resources = db.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
    allowed = {**_RUNTIME_TABLES, 'erasure_state': 'singleton phase inventory blocker',
               'erasure_control': 'singleton store_required failures'}
    if any((kind == 'table' and name not in allowed) or kind in ('trigger', 'view')
           or (kind == 'index' and sql is not None) for kind, name, sql in resources):
        raise InventoryBlocked('database_resources_unverified')
    tables = {name for kind, name, _ in resources if kind == 'table'}
    for table in tables:
        # Retained flags must not hide an added payload column. Runtime schema
        # additions/defaults are also unknown resources, not deletion authority.
        columns = db.execute(f'PRAGMA table_xinfo({table})').fetchall()
        if ([column[1] for column in columns] != allowed[table].split()
                or any(column[6] != 0 or (column[4] is not None
                       and not (table == 'erasure_jobs' and column[1] == 'state' and column[4] == "'pending'"))
                       for column in columns)):
            raise InventoryBlocked('database_resources_unverified')
    return tables


def _runtime_empty(db: sqlite3.Connection) -> None:
    tables = _controlled_tables(db)
    if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
           for table in _RUNTIME_TABLES if table in tables):
        raise InventoryBlocked('runtime_pending')
    state = [tuple(row) for row in db.execute('SELECT singleton, phase, inventory, blocker FROM erasure_state')]
    control = [tuple(row) for row in db.execute('SELECT singleton, store_required, failures FROM erasure_control')]
    if (state != [(1, 'complete', 'verified', None)] or len(control) != 1
            or control[0][0] != 1 or control[0][1] not in (0, 1) or control[0][2] != 0):
        raise InventoryBlocked('runtime_pending')


def _jobs(database: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(database)) as db:
        return db.execute('SELECT relative_path, kind, size, device, inode, state FROM erasure_jobs '
                          "ORDER BY kind='owner', relative_path").fetchall()


def _job_name(relative: str, kind: str) -> None:
    if relative == OWNER_FILE and kind == 'owner':
        return
    parts = relative.split('/')
    if len(parts) != 2:
        raise InventoryBlocked('invalid_reference')
    directory, name = parts
    valid = ((directory == 'persistent' and kind == 'publication' and _REFERENCE.fullmatch(name))
             or (directory == 'staging' and kind == 'stage' and _REFERENCE.fullmatch(name))
             or (directory == 'keys' and kind == 'key' and re.fullmatch(r'[0-9a-f]{64}', name))
             or (directory in ('staging', 'keys') and kind == 'temporary'
                 and re.fullmatch(r'[0-9a-f]{32}\.tmp', name)))
    if not valid:
        raise InventoryBlocked('invalid_reference')


def _identity(info: os.stat_result, job: tuple[Any, ...]) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or (info.st_size, info.st_dev, info.st_ino) != job[2:5]):
        raise InventoryBlocked('inventory_changed', relative=job[0])


def _validate_work(database: Path, store: LocalArtifactStore, jobs: list[tuple[Any, ...]],
                   *, allow_owner_loss: bool) -> None:
    """Never adopt new files, replacement inodes, foreign roots or unknown work."""
    _boundary(store.root, database)
    with closing(sqlite3.connect(database)) as db:
        owner = db.execute('SELECT token, root FROM artifact_store_owner WHERE singleton=1').fetchone()
    if owner is None or owner[1] != str(store.root.resolve()):
        raise InventoryBlocked('ownership_unverified')
    by_name = {job[0]: job for job in jobs}
    if OWNER_FILE not in by_name or by_name[OWNER_FILE][1] != 'owner':
        raise InventoryBlocked('ownership_unverified')
    for job in jobs:
        _job_name(job[0], job[1])
    try:
        marker = _read_owner(store.root)
    except FileNotFoundError:
        if (not allow_owner_loss or any(job[5] != 'absent' for job in jobs if job[1] != 'owner')):
            raise InventoryBlocked('ownership_unverified') from None
        _sentinel(store.root)
    else:
        if marker != {'token': owner[0], 'database': str(database.resolve())}:
            raise InventoryBlocked('ownership_unverified')
    names = {path.name for path in store.root.iterdir()}
    if (names - {*_DIRECTORIES, OWNER_FILE, LOCK_FILE, ERASED_FILE}
            or not {*_DIRECTORIES, LOCK_FILE} <= names
            or _regular(store.root / LOCK_FILE).st_size != 0):
        raise InventoryBlocked('unsafe_entry')
    for directory in _DIRECTORIES:
        for path in (store.root / directory).iterdir():
            relative = directory + '/' + path.name
            if relative not in by_name:
                raise InventoryBlocked('inventory_changed')
            job = by_name[relative]
            if job[5] == 'absent':
                raise InventoryBlocked('inventory_changed', relative=job[0])
            _identity(path.lstat(), job)
    if OWNER_FILE in names:
        if by_name[OWNER_FILE][5] == 'absent':
            raise InventoryBlocked('inventory_changed', relative=OWNER_FILE)
        _identity((store.root / OWNER_FILE).lstat(), by_name[OWNER_FILE])
    if ERASED_FILE in names:
        _sentinel(store.root)


def _remove_job(root: Path, job: tuple[Any, ...]) -> None:
    """Erasure-only anchored unlink. Ordinary store removal stays unavailable.

    A retry after unlink-before-commit fsyncs the containing directory again.
    No-follow + stat identity protect cooperative operation, not hostile OS races.
    """
    _job_name(job[0], job[1])
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory_fd = root_fd
    try:
        parts = job[0].split('/')
        if len(parts) == 2:
            directory_fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            info = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.fsync(directory_fd)
            return
        _identity(info, job)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            _identity(os.fstat(descriptor), job)
            _identity(os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False), job)
            os.unlink(parts[-1], dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            os.close(descriptor)
    finally:
        if directory_fd != root_fd:
            os.close(directory_fd)
        os.close(root_fd)


def _empty_store(database: Path, store: LocalArtifactStore) -> None:
    _boundary(store.root, database)
    _sentinel(store.root)
    if (set(path.name for path in store.root.iterdir()) != {*_DIRECTORIES, LOCK_FILE, ERASED_FILE}
            or _regular(store.root / LOCK_FILE).st_size != 0
            or any(any((store.root / name).iterdir()) for name in _DIRECTORIES)):
        raise InventoryBlocked('resources_pending')
    for directory in (*_DIRECTORIES, '.'):
        _fsync_directory(store.root / directory)


def _finish(database: Path) -> dict[str, Any]:
    """Separate runtime gate: no normal retention path can call this purge."""
    with closing(sqlite3.connect(database)) as db, db:
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        tables = _controlled_tables(db)
        phase = db.execute('SELECT phase FROM erasure_state WHERE singleton=1').fetchone()
        if (phase != ('files_removed',)
                or db.execute("SELECT 1 FROM erasure_jobs WHERE state!='absent' LIMIT 1").fetchone()):
            raise InventoryBlocked('resources_pending')
        for table in _RUNTIME_TABLES:
            if table in tables:
                db.execute(f'DELETE FROM {table}')
        db.execute("UPDATE erasure_state SET phase='complete', inventory='verified', blocker=NULL WHERE singleton=1")
        db.execute('UPDATE erasure_control SET failures=0 WHERE singleton=1')
        # Includes schema/trigger inspection and emptiness AFTER the final writes.
        _runtime_empty(db)
        return status(db)


def _failure(database: Path, blocker: str, relative: str | None = None) -> dict[str, Any]:
    try:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM erasure_state WHERE phase='complete'").fetchone():
                # Invalid repeat inputs cannot rewrite an already completed receipt.
                return report('complete', 'blocked', blocker, 0)
            db.execute("UPDATE erasure_state SET inventory='blocked', blocker=? WHERE singleton=1", (blocker,))
            db.execute('UPDATE erasure_control SET failures=failures+1 WHERE singleton=1')
            if relative is not None:
                db.execute("UPDATE erasure_jobs SET state='unavailable' WHERE relative_path=?", (relative,))
            return status(db)
    except sqlite3.Error:
        # The existing durable freeze remains authoritative if storage cannot record
        # this attempt. Never leak SQL/trigger messages or claim durable retry state.
        raise retention.RetentionUnavailable('Erasure status unavailable; deployment remains frozen') from None


def erase(database: Path, store: ArtifactStore | None) -> dict[str, Any]:
    """Internal seam. Caller owns the exclusive database maintenance fence."""
    with closing(sqlite3.connect(database)) as db:
        current = status(db)
    if current['phase'] == 'unprepared':
        current = prepare(database, store)
    if current['phase'] == 'unprepared':
        return current
    if store is not None and type(store) is not LocalArtifactStore:
        return _failure(database, 'store_unsupported')
    relative = None
    try:
        with closing(sqlite3.connect(database)) as db:
            _controlled_tables(db)
            control = db.execute('SELECT store_required FROM erasure_control WHERE singleton=1').fetchone()
            owner = db.execute('SELECT 1 FROM artifact_store_owner LIMIT 1').fetchone()
        required = bool(control[0]) if control else bool(owner)
        if required != (store is not None):
            raise InventoryBlocked('store_missing' if required else 'ownership_unverified')
        if current['phase'] == 'complete':
            if store is not None:
                _boundary(store.root, database)
                with store.maintenance_lock():
                    _empty_store(database, store)
            return current
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('INSERT OR IGNORE INTO erasure_control VALUES (1, ?, 0)', (int(required),))
        if store is None:
            with closing(sqlite3.connect(database)) as db, db:
                if _known_obligations(db) or _jobs(database):
                    raise InventoryBlocked('store_missing')
                db.execute("UPDATE erasure_state SET phase='files_removed' WHERE singleton=1")
            return _finish(database)
        _boundary(store.root, database)
        with store.maintenance_lock():
            jobs = _jobs(database)
            _validate_work(database, store, jobs, allow_owner_loss=current['phase'] != 'prepared')
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("UPDATE erasure_state SET phase='removing' WHERE singleton=1")
            _sentinel(store.root, create=True)
            for job in jobs:
                relative = job[0]
                # Validate the root, owner and entire closed worklist before each unlink.
                fresh = _jobs(database)
                _validate_work(database, store, fresh, allow_owner_loss=True)
                if job[1] == 'owner':
                    if any(item[5] != 'absent' for item in fresh if item[1] != 'owner'):
                        raise InventoryBlocked('resources_pending')
                    if any(any((store.root / name).iterdir()) for name in _DIRECTORIES):
                        raise InventoryBlocked('resources_pending')
                _remove_job(store.root, job)
                with closing(sqlite3.connect(database)) as db, db:
                    db.execute("UPDATE erasure_jobs SET state='absent' WHERE relative_path=?", (relative,))
            relative = None
            _empty_store(database, store)
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("UPDATE erasure_state SET phase='files_removed', inventory='verified', blocker=NULL "
                           'WHERE singleton=1')
            return _finish(database)
    except retention.RetentionBusy:
        raise
    except InventoryBlocked as error:
        return _failure(database, str(error), error.relative or relative)
    except OSError:
        return _failure(database, 'storage_unavailable', relative)
    except sqlite3.Error:
        return _failure(database, 'database_unavailable', relative)


def erase_local(database: Path, *, store_root: Path | None) -> dict[str, Any]:
    """Explicit offline local operator authority. Never construct Core to start erasure."""
    with _local_maintenance(database, store_root) as store:
        return erase(database, store)


def status_local(database: Path) -> dict[str, Any]:
    """Read persisted status without Core startup, schema writes or store access."""
    with retention.operation(database):
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            return status(db)
