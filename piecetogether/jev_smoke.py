"""Irreversible USD 5 smoke hold and fixed supplemental diagnostic attempt log.

No filesystem, environment-secret or network access occurs at import. Only an
explicit selector call touches the ledger. This local guard does not resist owner deletion,
filesystem rollback or execution on another host. Never delete an unresolved hold.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
from dataclasses import asdict
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import unquote

from .context import (BudgetExhausted, SelectionConfig, SelectionFailure,
                      SelectionRequest, SelectionResult, encoded, validate_selection)
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


def _check_directory(fd: int, private: bool = False, *, trust_primary_group: bool = False) -> None:
    node = os.fstat(fd)
    mode = stat.S_IMODE(node.st_mode)
    # Shared sticky root-owned ancestry (e.g. /tmp in tests) is safe to traverse;
    # experiment directories themselves must be owned and private.
    sticky_root = node.st_uid == 0 and bool(node.st_mode & stat.S_ISVTX)
    # Opt-in trusts only this account's primary group on its nonprivate ancestry.
    trusted_group = (trust_primary_group is True and not private
                     and node.st_uid == os.getuid()
                     and node.st_gid == pwd.getpwuid(os.getuid()).pw_gid)
    unsafe_write = bool(mode & 0o002 or mode & 0o020 and not trusted_group)
    if (not stat.S_ISDIR(node.st_mode) or node.st_uid not in (0, os.getuid())
            or unsafe_write and not sticky_root
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


def _open_chain(path: Path, *, create: bool, trust_primary_group: bool = False) -> list[tuple[int, str]]:
    """Open and validate each component without following any symlink."""
    if not path.is_absolute() or '..' in path.parts:
        raise ReservationRefused()
    chain = [(os.open('/', _DIRECTORY), '')]
    try:
        _check_directory(chain[0][0], trust_primary_group=trust_primary_group)
        for name in path.parts[1:]:
            parent = chain[-1][0]
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            fd = os.open(name, _DIRECTORY, dir_fd=parent)
            chain.append((fd, name))
            _check_directory(fd, private=name in ('piecetogether', 'jev-usd5-smoke'),
                             trust_primary_group=trust_primary_group)
            _check_binding(parent, name, fd)
        return chain
    except BaseException:
        for fd, _ in reversed(chain):
            os.close(fd)
        raise


def _validate_chain(chain: list[tuple[int, str]], *, trust_primary_group: bool = False) -> None:
    for index, (fd, name) in enumerate(chain):
        _check_directory(fd, private=name in ('piecetogether', 'jev-usd5-smoke'),
                         trust_primary_group=trust_primary_group)
        if index:
            _check_binding(chain[index - 1][0], name, fd)


def _write_all(fd: int, body: bytes) -> None:
    while body:
        count = os.write(fd, body)
        if count <= 0:
            raise ReservationRefused()
        body = body[count:]


def reserve_once(*, trust_primary_group: bool = False) -> None:
    """Durably hold all 500 cents before dispatch. Any existing file refuses.

    Interrupted writes and fsync errors intentionally leave their partial record.
    Only literal True opts into primary-group trust on owned nonprivate ancestry.
    There is no release, reconciliation, reset, force option or second attempt.
    """
    chain: list[tuple[int, str]] = []
    fd: int | None = None
    reason = 'unsafe_ancestry'
    try:
        chain = _open_chain(_ledger_root(), create=True, trust_primary_group=trust_primary_group)
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
        _validate_chain(chain, trust_primary_group=trust_primary_group)
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

    def __init__(self, config: SelectionConfig, api_key: str, *, trust_primary_group: bool = False):
        if not api_key:
            raise ValueError('smoke secret unavailable')
        self._delegate = JevSelector(config, api_key)
        self._key = api_key
        self._trust_primary_group = trust_primary_group is True
        self.attempted_dispatches = 0
        self.reserved_cents: int | None = 0
        self.failure: str | None = None
        self.reservation_refusal_reason: str | None = None

    def select(self, request: SelectionRequest) -> SelectionResult:
        try:
            reserve_once(trust_primary_group=self._trust_primary_group)
        except ReservationRefused as error:
            # Existing/partial/unsafe evidence cannot establish a zero hold.
            self.reserved_cents = None
            self.failure = 'reservation_refused'
            self.reservation_refusal_reason = _refusal_reason(error.reason)
            raise ReservationRefused(self.reservation_refusal_reason) from None
        self.reserved_cents = 500
        self.attempted_dispatches = 1  # Delegation, NOT a claim of actual HTTP work.
        try:
            return _safe_select(self._delegate, self._key, request)
        except BudgetExhausted:
            self.failure = 'budget_exhausted'
            raise BudgetExhausted({'classification': 'budget_exhausted'}) from None
        except Exception:
            # Raw provider JSON, headers, exception messages and chained causes are
            # deliberately unavailable. They are never passed to Core or receipts.
            self.failure = 'selector_unavailable'
            raise SelectionFailure({'classification': 'selector_unavailable'}) from None


def _safe_select(delegate: JevSelector, key: str, request: SelectionRequest) -> SelectionResult:
    """Shared pre-Core echo/schema/usage boundary. Preserve safe values exactly."""
    result = delegate.select(request)
    if _unsafe(asdict(result), key):
        raise SelectionFailure(None)
    validate_selection(result, request)
    if (result.provider != 'typesafe' or result.model != 'jev-1.13.0'
            or result.input_tokens is None or result.output_tokens is None):
        raise SelectionFailure(None)
    return result


def _request_digest(request: SelectionRequest) -> str:
    return hashlib.sha256(encoded(asdict(request)).encode()).hexdigest()


def _record_bytes(record: Any) -> bytes:
    return (json.dumps(record, sort_keys=True) + '\n').encode()


def _read_record(directory: int, name: str, expected: bytes) -> None:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
    try:
        _check_file(fd)
        body = b''
        while len(body) <= len(expected):
            chunk = os.read(fd, len(expected) + 1 - len(body))
            if not chunk:
                break
            body += chunk
        if body != expected:
            raise ReservationRefused()
        _check_file(fd)
        _check_binding(directory, name, fd)
    finally:
        os.close(fd)


def _exclusive_record(directory: int, name: str, expected: bytes) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=directory)
    try:
        _check_file(fd)
        _write_all(fd, expected)
        os.fsync(fd)
        os.fsync(directory)
        _check_file(fd)
        _check_binding(directory, name, fd)
    finally:
        os.close(fd)


class FollowupJevSelector:
    """One nonresumable five-slot attempt log under the original unreleased hold.

    The owner-confirmed provider wallet, NOT this log, bounds aggregate cash.
    Construction is inert. Any error makes this instance terminal; no retry/reset.
    """
    provider_id = 'typesafe'
    model_id = 'jev-1.13.0'

    def __init__(self, config: SelectionConfig, api_key: str,
                 planned_requests: tuple[SelectionRequest, ...], *, plan_hash: str,
                 trust_primary_group: bool = False, ack_no_off_credit_charges: bool = False):
        from .jev import _payload
        if (ack_no_off_credit_charges is not True or not api_key
                or not isinstance(plan_hash, str) or re.fullmatch(r'[0-9a-f]{64}', plan_hash) is None
                or type(planned_requests) is not tuple or len(planned_requests) != 5
                or config.minimum_relevance != .7 or config.rubric_version != 'context-relevance-v1'
                or any(r.minimum_relevance != .7 or r.rubric_version != config.rubric_version
                       or not r.candidates or not 1 <= r.timeout_ms <= 1000
                       or r.remaining_calls < 1 or not 1 <= r.remaining_tokens <= 65536
                       for r in planned_requests)):
            raise ValueError('invalid frozen diagnostic plan or financial acknowledgement')
        self._digests = tuple(_request_digest(r) for r in planned_requests)
        if (len(set(self._digests)) != 5
                or len({encoded(_payload(r, self.model_id)) for r in planned_requests}) != 5):
            raise ValueError('diagnostic inputs must be distinct')
        self._delegate = JevSelector(config, api_key)
        self._key = api_key
        self._trust = trust_primary_group is True
        self._claim = _record_bytes({'experiment': 'jev-catalogue-followup-v1', 'plan_hash': plan_hash,
            'max_new_calls': 5, 'prior_recorded_attempts': 1,
            'original_record_sha256': hashlib.sha256(_record_bytes(_RECORD)).hexdigest(),
            'request_digests': self._digests})
        self._markers = tuple(_record_bytes({'plan_hash': plan_hash, 'slot': i + 1, 'request_digest': digest})
                              for i, digest in enumerate(self._digests))
        self._lock = Lock()
        self._terminal = False
        self._claimed = False
        self._started: float | None = None
        self.recorded_slots = 0  # Invocation-local durable markers; NOT HTTP count.
        self.attempted_dispatches = 0  # Invocation-local selector delegations only.
        self.reserved_cents: int | None = None
        self.failure: str | None = None
        self.prior_batch_attempts_known = False

    def _mark(self) -> None:
        chain: list[tuple[int, str]] = []
        try:
            # Never create missing ancestry or resurrect the old reservation.
            chain = _open_chain(_ledger_root(), create=False, trust_primary_group=self._trust)
            directory = chain[-1][0]
            _check_directory(directory, private=True)
            self.reserved_cents = None
            _read_record(directory, 'reservation.json', _record_bytes(_RECORD))
            self.reserved_cents = 500
            if not self._claimed:
                _exclusive_record(directory, 'catalogue-followup.json', self._claim)
                self._claimed = True
                self.prior_batch_attempts_known = True
            _read_record(directory, 'catalogue-followup.json', self._claim)
            for index in range(self.recorded_slots):
                _read_record(directory, f'catalogue-attempt-{index + 1}.json', self._markers[index])
            index = self.recorded_slots
            _exclusive_record(directory, f'catalogue-attempt-{index + 1}.json', self._markers[index])
            # A durable marker burns its slot, even if subsequent validation fails.
            self.recorded_slots += 1
            _validate_chain(chain, trust_primary_group=self._trust)
            _read_record(directory, 'reservation.json', _record_bytes(_RECORD))
            _read_record(directory, 'catalogue-followup.json', self._claim)
            _read_record(directory, f'catalogue-attempt-{index + 1}.json', self._markers[index])
        except Exception:
            # A partial/fsync-uncertain marker is burned but cannot support an
            # exact global count. Do not turn local zero into historical zero.
            self.prior_batch_attempts_known = False
            raise ReservationRefused() from None
        finally:
            for directory, _ in reversed(chain):
                os.close(directory)

    def select(self, request: SelectionRequest) -> SelectionResult:
        with self._lock:
            try:
                if (self._terminal or self.recorded_slots >= 5
                        or _request_digest(request) != self._digests[self.recorded_slots]):
                    raise SelectionFailure(None)
                if self._started is None:
                    self._started = monotonic()
                if monotonic() + request.timeout_ms / 1000 > self._started + 15:
                    raise BudgetExhausted()
                self._mark()
                if monotonic() + request.timeout_ms / 1000 > self._started + 15:
                    raise BudgetExhausted()
                self.attempted_dispatches += 1
                started = monotonic()
                result = _safe_select(self._delegate, self._key, request)
                if (monotonic() >= self._started + 15
                        or (monotonic() - started) * 1000 >= request.timeout_ms
                        or result.latency_ms >= request.timeout_ms
                        or result.input_tokens is not None and result.input_tokens > request.remaining_tokens):
                    raise BudgetExhausted()
                if result.outcome not in ('completed', 'abstained'):
                    raise SelectionFailure(None)
                return result
            except ReservationRefused:
                self._terminal = True
                self.failure = 'reservation_refused'
                raise ReservationRefused() from None
            except BudgetExhausted:
                self._terminal = True
                self.failure = 'budget_exhausted'
                raise BudgetExhausted({'classification': 'budget_exhausted'}) from None
            except Exception:
                self._terminal = True
                self.failure = 'selector_unavailable'
                raise SelectionFailure({'classification': 'selector_unavailable'}) from None
            except BaseException:
                self._terminal = True
                raise


def _new_output(path: Path, *, trust_primary_group: bool = False) -> int:
    """Create a new private output leaf before reservation; return its directory FD."""
    chain: list[tuple[int, str]] = []
    fd: int | None = None
    try:
        absolute = path.absolute()
        chain = _open_chain(absolute.parent, create=False, trust_primary_group=trust_primary_group)
        os.mkdir(absolute.name, mode=0o700, dir_fd=chain[-1][0])
        fd = os.open(absolute.name, _DIRECTORY, dir_fd=chain[-1][0])
        _check_directory(fd, private=True)
        _check_binding(chain[-1][0], absolute.name, fd)
        _validate_chain(chain, trust_primary_group=trust_primary_group)
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
