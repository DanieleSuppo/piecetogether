"""One irreversible USD 5 synthetic-smoke reservation, not a billing controller.

No filesystem, environment-secret or network access occurs at import. Only an
explicit selector call reserves. This local guard does not resist owner deletion,
filesystem rollback or execution on another host. Never delete an unresolved hold.
"""
import json
import os
from pathlib import Path
import pwd
import re
import stat
from dataclasses import asdict
from typing import Any
from urllib.parse import unquote

from .context import (BudgetExhausted, SelectionConfig, SelectionFailure,
                      SelectionRequest, SelectionResult, validate_selection)
from .jev import JevSelector


def _refusal_reason(value: object) -> str:
    # Do not stringify external exception data or accept arbitrary diagnostics.
    return value if type(value) is str and value in (
        'already_reserved', 'unsafe_ancestry', 'durability_failed',
    ) else 'reservation_refused'


class ReservationRefused(OSError):
    def __init__(self, reason: object = None) -> None:
        super().__init__('reservation refused')
        self.reason = _refusal_reason(reason)


def _ledger_root() -> Path:
    # OS account identity, deliberately not HOME, XDG, cwd or an output/run ID.
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/piecetogether/jev-usd5-smoke'


_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_RECORD = {'experiment': 'jev-usd5-smoke-v1', 'currency': 'USD', 'limit_cents': 500,
           'reserved_cents': 500, 'reconciled_cents': 0, 'attempted_dispatches': 1}


def _check_directory(fd: int, private: bool = False) -> None:
    node = os.fstat(fd)
    mode = stat.S_IMODE(node.st_mode)
    # Shared sticky root-owned ancestry (e.g. /tmp in tests) is safe to traverse;
    # experiment directories themselves must be owned and private.
    sticky_root = node.st_uid == 0 and bool(node.st_mode & stat.S_ISVTX)
    if (not stat.S_ISDIR(node.st_mode) or node.st_uid not in (0, os.getuid())
            or mode & 0o022 and not sticky_root
            or private and (node.st_uid != os.getuid() or mode != 0o700)):
        raise ReservationRefused()


def _check_file(fd: int) -> None:
    node = os.fstat(fd)
    if (not stat.S_ISREG(node.st_mode) or node.st_uid != os.getuid()
            or stat.S_IMODE(node.st_mode) != 0o600 or node.st_nlink != 1):
        raise ReservationRefused()


def _check_binding(parent: int, name: str, fd: int) -> None:
    linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(fd)
    if (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
        raise ReservationRefused()


def _open_chain(path: Path, *, create: bool) -> list[tuple[int, str]]:
    """Open and validate each component without following any symlink."""
    if not path.is_absolute() or '..' in path.parts:
        raise ReservationRefused()
    chain = [(os.open('/', _DIRECTORY), '')]
    try:
        _check_directory(chain[0][0])
        for name in path.parts[1:]:
            parent = chain[-1][0]
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            fd = os.open(name, _DIRECTORY, dir_fd=parent)
            chain.append((fd, name))
            _check_directory(fd, private=name in ('piecetogether', 'jev-usd5-smoke'))
            _check_binding(parent, name, fd)
        return chain
    except BaseException:
        for fd, _ in reversed(chain):
            os.close(fd)
        raise


def _validate_chain(chain: list[tuple[int, str]]) -> None:
    for index, (fd, name) in enumerate(chain):
        _check_directory(fd, private=name in ('piecetogether', 'jev-usd5-smoke'))
        if index:
            _check_binding(chain[index - 1][0], name, fd)


def _write_all(fd: int, body: bytes) -> None:
    while body:
        count = os.write(fd, body)
        if count <= 0:
            raise ReservationRefused()
        body = body[count:]


def reserve_once() -> None:
    """Durably hold all 500 cents before dispatch. Any existing file refuses.

    Interrupted writes and fsync errors intentionally leave their partial record.
    There is no release, reconciliation, reset, force option or second attempt.
    """
    chain: list[tuple[int, str]] = []
    fd: int | None = None
    reason = 'unsafe_ancestry'
    try:
        chain = _open_chain(_ledger_root(), create=True)
        _check_directory(chain[-1][0], private=True)
        # Creation, writes, fsync and final validation can leave uncertain evidence.
        reason = 'durability_failed'
        try:
            fd = os.open('reservation.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=chain[-1][0])
        except FileExistsError:
            reason = 'already_reserved'
            raise
        _check_file(fd)
        _write_all(fd, (json.dumps(_RECORD, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        # Persist the leaf entry and every possibly newly created ancestor.
        for directory, _ in reversed(chain):
            os.fsync(directory)
        _validate_chain(chain)
        _check_binding(chain[-1][0], 'reservation.json', fd)
        _check_file(fd)
    except Exception:
        raise ReservationRefused(reason) from None
    finally:
        if fd is not None:
            os.close(fd)
        for directory, _ in reversed(chain):
            os.close(directory)


def _echo_text(value: str) -> str:
    # Check only: two fixed passes cover common mixed percent/JSON-unicode echoes.
    # Never rewrite the returned result or broaden Jev's pinned response schema.
    for _ in range(2):
        value = unquote(value)
        value = re.sub(r'\\u([0-9a-fA-F]{4})', lambda match: chr(int(match[1], 16)), value)
    return value.casefold()


def _unsafe(value: Any, key: str) -> bool:
    if isinstance(value, str):
        text = _echo_text(value)
        return (_echo_text(key) in text or 'authorization' in text
                or re.search(r'bearer\s+', text) is not None)
    if isinstance(value, dict):
        return any(_unsafe(k, key) or _unsafe(v, key) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_unsafe(item, key) for item in value)
    return False


class GuardedJevSelector:
    """Reserve once, delegate once, sanitize before Core can capture metadata."""
    provider_id = 'typesafe'
    model_id = 'jev-1.13.0'

    def __init__(self, config: SelectionConfig, api_key: str):
        if not api_key:
            raise ValueError('smoke secret unavailable')
        self._delegate = JevSelector(config, api_key)
        self._key = api_key
        self.attempted_dispatches = 0
        self.reserved_cents: int | None = 0
        self.failure: str | None = None
        self.reservation_refusal_reason: str | None = None

    def select(self, request: SelectionRequest) -> SelectionResult:
        try:
            reserve_once()
        except ReservationRefused as error:
            # Existing/partial/unsafe evidence cannot establish a zero hold.
            self.reserved_cents = None
            self.failure = 'reservation_refused'
            self.reservation_refusal_reason = _refusal_reason(error.reason)
            raise ReservationRefused(self.reservation_refusal_reason) from None
        self.reserved_cents = 500
        self.attempted_dispatches = 1  # Delegation, NOT a claim of actual HTTP work.
        try:
            result = self._delegate.select(request)
            if _unsafe(asdict(result), self._key):
                raise SelectionFailure(None)
            validate_selection(result, request)
            if (result.provider != self.provider_id or result.model != self.model_id
                    or result.input_tokens is None or result.output_tokens is None):
                raise SelectionFailure(None)
            return result
        except BudgetExhausted:
            self.failure = 'budget_exhausted'
            raise BudgetExhausted({'classification': 'budget_exhausted'}) from None
        except Exception:
            # Raw provider JSON, headers, exception messages and chained causes are
            # deliberately unavailable. They are never passed to Core or receipts.
            self.failure = 'selector_unavailable'
            raise SelectionFailure({'classification': 'selector_unavailable'}) from None


def _new_output(path: Path) -> int:
    """Create a new private output leaf before reservation; return its directory FD."""
    chain: list[tuple[int, str]] = []
    fd: int | None = None
    try:
        absolute = path.absolute()
        chain = _open_chain(absolute.parent, create=False)
        os.mkdir(absolute.name, mode=0o700, dir_fd=chain[-1][0])
        fd = os.open(absolute.name, _DIRECTORY, dir_fd=chain[-1][0])
        _check_directory(fd, private=True)
        _check_binding(chain[-1][0], absolute.name, fd)
        _validate_chain(chain)
        os.fsync(chain[-1][0])
        return fd
    except Exception:
        if fd is not None:
            os.close(fd)
        raise ValueError('unsafe or existing smoke output') from None
    finally:
        for directory, _ in reversed(chain):
            os.close(directory)


def _write_private(directory: int, name: str, value: Any) -> None:
    # Only fixed internal filenames are used by the smoke command.
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    try:
        _check_file(fd)
        _write_all(fd, (json.dumps(value, sort_keys=True, ensure_ascii=False) + '\n').encode())
        os.fsync(fd)
        os.fsync(directory)
    finally:
        os.close(fd)
