"""Local immutable Artifact bytes; semantic records store only Core-owned metadata."""

import fcntl
import hashlib
import os
import re
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Callable, Concatenate, ContextManager, ParamSpec, Protocol, TypeVar
from functools import wraps
from uuid import uuid4


_locks: dict[str, threading.RLock] = {}
_lock_depth: dict[str, int] = {}
P = ParamSpec('P')
R = TypeVar('R')


def _access(method: Callable[Concatenate['LocalArtifactStore', P], R]) -> Callable[Concatenate['LocalArtifactStore', P], R]:
    @wraps(method)
    def wrapped(self: 'LocalArtifactStore', /, *args: P.args, **kwargs: P.kwargs) -> R:
        from . import erasure, retention
        database = erasure.store_database(self)
        with ExitStack() as stack:
            if database is not None:
                stack.enter_context(retention.operation(database))
            stack.enter_context(self.maintenance_lock())
            erasure.require_store_access(self, database)
            return method(self, *args, **kwargs)
    return wrapped


@dataclass(frozen=True)
class StagedContent:
    reference: str
    checksum: str
    size: int


@dataclass(frozen=True)
class FinalizedContent:
    reference: str
    checksum: str


class ArtifactStore(Protocol):
    """Core-owned byte lifecycle; semantic state retains only storage metadata."""

    def maintenance_lock(self) -> ContextManager[None]: ...
    def stage(self, content: bytes, idempotency_key: str) -> StagedContent: ...
    def finalize(self, staged: StagedContent) -> FinalizedContent: ...
    def read(self, reference: str) -> bytes: ...
    def read_staged(self, reference: str) -> bytes: ...
    def remove(self, reference: str) -> None: ...
    def remove_staged(self, reference: str, minimum_age_seconds: float) -> bool | None: ...
    def cleanup(self, preserved: set[str], minimum_age_seconds: float) -> list[str]: ...


class LocalArtifactStore:
    """Static local store with durable idempotency keys and atomic publication."""

    def __init__(self, root: Path, *, create: bool = True):
        self.root = root
        self.database: Path | None = None
        self.staging = root / 'staging'
        self.persistent = root / 'persistent'
        self.keys = root / 'keys'
        if create:
            from .erasure import require_no_sentinel
            from .retention import RetentionUnavailable
            try:
                require_no_sentinel(root)
            except RetentionUnavailable:
                return  # A new/stale unbound handle must not recreate erased directories.
            for directory in (self.staging, self.persistent, self.keys):
                directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def maintenance_lock(self) -> Iterator[None]:
        """Coordinate Core staging registration and orphan sweeping on this local store."""
        from .retention import RetentionBusy
        key = str(self.root.resolve())
        lock = _locks.setdefault(key, threading.RLock())
        with lock:
            if _lock_depth.get(key, 0):
                yield
                return
            descriptor = os.open(self.root / '.artifact-maintenance.lock',
                                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RetentionBusy('Artifact store maintenance is busy') from error
                _lock_depth[key] = 1
                yield
            finally:
                _lock_depth.pop(key, None)
                os.close(descriptor)

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @_access
    def stage(self, content: bytes, idempotency_key: str) -> StagedContent:
        checksum = self._digest(content)
        key_path = self.keys / self._digest(idempotency_key.encode())
        if key_path.exists():
            try:
                reference, previous, size = key_path.read_text().split(':')
                valid = (bool(re.fullmatch(r'[0-9a-f]{32}', reference))
                         and bool(re.fullmatch(r'[0-9a-f]{64}', previous)) and int(size) >= 0)
            except (ValueError, TypeError):
                valid = False
            if not valid:
                key_path.unlink(missing_ok=True)
                return self.stage(content, idempotency_key)
            if previous != checksum or int(size) != len(content):
                raise ValueError('conflicting artifact idempotency key')
            staged_path = self.staging / reference
            if not staged_path.exists() and not (self.persistent / reference).exists():
                temporary = self.staging / (reference + '.tmp')
                temporary.write_bytes(content)
                if self._digest(temporary.read_bytes()) != checksum:
                    temporary.unlink(missing_ok=True)
                    raise OSError('artifact staging checksum mismatch')
                os.replace(temporary, staged_path)
            return StagedContent(reference, checksum, len(content))
        # The key hash is opaque (the key includes a Core UUID) and lets a partial key
        # recover the same staged reference already recorded by Core metadata.
        reference = self._digest(idempotency_key.encode())[:32]
        temporary = self.staging / (reference + '.tmp')
        temporary.write_bytes(content)
        if self._digest(temporary.read_bytes()) != checksum:
            temporary.unlink(missing_ok=True)
            raise OSError('artifact staging checksum mismatch')
        os.replace(temporary, self.staging / reference)
        try:
            key_temporary = self.keys / (uuid4().hex + '.tmp')
            key_temporary.write_text(f'{reference}:{checksum}:{len(content)}')
            os.replace(key_temporary, key_path)
        except OSError:
            # A later retry can safely stage the same supplied content under the same key.
            (self.staging / reference).unlink(missing_ok=True)
            raise
        return StagedContent(reference, checksum, len(content))

    @_access
    def finalize(self, staged: StagedContent) -> FinalizedContent:
        source = self.staging / staged.reference
        target = self.persistent / staged.reference
        if target.exists():
            if self._digest(target.read_bytes()) != staged.checksum:
                raise OSError('artifact finalization checksum mismatch')
            return FinalizedContent(staged.reference, staged.checksum)
        if not source.exists() or self._digest(source.read_bytes()) != staged.checksum:
            raise OSError('artifact stage unavailable or corrupt')
        os.replace(source, target)
        return FinalizedContent(staged.reference, staged.checksum)

    @_access
    def read(self, reference: str) -> bytes:
        return (self.persistent / reference).read_bytes()

    @_access
    def read_staged(self, reference: str) -> bytes:
        if not re.fullmatch(r'[0-9a-f]{32}', reference):
            raise ValueError('invalid Artifact storage reference')
        return (self.staging / reference).read_bytes()

    @_access
    def remove(self, reference: str) -> None:
        """Idempotently remove publication, stage and its durable staging key."""
        if not re.fullmatch(r'[0-9a-f]{32}', reference):
            raise ValueError('invalid Artifact storage reference')
        for path in (self.persistent / reference, self.staging / reference,
                     self.staging / (reference + '.tmp')):
            path.unlink(missing_ok=True)
        for key in self.keys.glob(reference + '*'):
            key.unlink(missing_ok=True)

    @_access
    def remove_staged(self, reference: str, minimum_age_seconds: float) -> bool | None:
        """Remove one old staged file: True removed, False missing, None still young."""
        path = self.staging / reference
        if not path.exists():
            return False
        if __import__('time').time() - path.stat().st_mtime < minimum_age_seconds:
            return None
        path.unlink()
        return True

    @_access
    def cleanup(self, preserved: set[str], minimum_age_seconds: float) -> list[str]:
        """Remove only old, unlinked staged files; recent acquisition is never touched."""
        now = __import__('time').time()
        removed = []
        for path in self.staging.iterdir():
            if (path.name.endswith('.tmp') or path.name not in preserved) and (
                now - path.stat().st_mtime >= minimum_age_seconds
            ):
                path.unlink(missing_ok=True)
                removed.append(path.name)
        return removed
