"""Versioned, authenticated transport for trusted application state."""

import base64
import json
import os
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .core import Core


_ALLOWED_SCOPES = frozenset({"state:read", "events:consume"})
_MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class ApplicationCredential:
    """A deployment-secret reference and its fixed application scopes."""

    secret_reference: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.secret_reference, str)
            or not self.secret_reference.strip()
            or len(self.secret_reference) > 256
            or not isinstance(self.scopes, tuple)
            or not self.scopes
            or any(scope not in _ALLOWED_SCOPES for scope in self.scopes)
            or len(set(self.scopes)) != len(self.scopes)
        ):
            raise ValueError("invalid application API credential")


@dataclass(frozen=True)
class ApplicationApiConfig:
    """Static loopback transport configuration; credentials remain environment secrets."""

    host: str
    port: int
    credentials: tuple[ApplicationCredential, ...]

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "::1"}:
            raise ValueError("application API must bind a loopback host")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("application API port must be an integer from 0 to 65535")
        if (
            not isinstance(self.credentials, tuple)
            or not self.credentials
            or any(not isinstance(credential, ApplicationCredential) for credential in self.credentials)
            or len({credential.secret_reference for credential in self.credentials}) != len(self.credentials)
        ):
            raise ValueError("application API requires unique static credentials")

    @classmethod
    def from_dict(cls, value: Any) -> "ApplicationApiConfig":
        if not isinstance(value, dict) or set(value) != {"host", "port", "credentials"}:
            raise ValueError("invalid application API configuration")
        credentials = value["credentials"]
        if not isinstance(credentials, list):
            raise ValueError("application API credentials must be an array")
        if any(
            not isinstance(credential, dict)
            or set(credential) != {"secret_reference", "scopes"}
            or not isinstance(credential.get("scopes"), list)
            for credential in credentials
        ):
            raise ValueError("invalid application API configuration")
        try:
            return cls(
                value["host"],
                value["port"],
                tuple(ApplicationCredential(
                    credential["secret_reference"], tuple(credential["scopes"])
                ) for credential in credentials),
            )
        except (KeyError, TypeError) as error:
            raise ValueError("invalid application API configuration") from error

    def resolve_credentials(self, secret_references: dict[str, str]) -> dict[str, frozenset[str]]:
        """Read opaque credentials once at transport startup; never serialize them."""
        resolved: dict[str, frozenset[str]] = {}
        for credential in self.credentials:
            environment_name = secret_references.get(credential.secret_reference)
            token = os.environ.get(environment_name or "")
            if (
                environment_name is None
                or not isinstance(token, str)
                or not token
                or token.strip() != token
                or len(token) > 1024
            ):
                raise ValueError("an application API credential secret is unresolved")
            if token in resolved:
                raise ValueError("application API credential secrets must be distinct")
            resolved[token] = frozenset(credential.scopes)
        return resolved


class _ApiError(Exception):
    def __init__(self, status: HTTPStatus, code: str):
        self.status = status
        self.code = code


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_cursor") from error
    if not isinstance(payload, dict):
        raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_cursor")
    return payload


def _single_query(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_request")
    return values[0]


def _page_size(query: dict[str, list[str]]) -> int:
    value = _single_query(query, "limit")
    if value is None:
        return 50
    try:
        result = int(value)
    except ValueError as error:
        raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_request") from error
    if not 1 <= result <= _MAX_PAGE_SIZE:
        raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_request")
    return result


def _actor_id(record: dict[str, Any]) -> str | None:
    provenance = record.get("provenance")
    return provenance.get("actor_id") if isinstance(provenance, dict) else None


def _state_page(core: "Core", selector: dict[str, str], limit: int, cursor: str | None) -> dict[str, Any]:
    current = core.current_view()
    revision = current["revision"]
    offset = 0
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        cursor_offset = decoded.get("offset")
        if (
            decoded.get("kind") != "state"
            or decoded.get("version") != 1
            or decoded.get("query") != selector
            or type(cursor_offset) is not int
            or cursor_offset < 0
            or decoded.get("revision") != revision
        ):
            raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_cursor")
        offset = cursor_offset

    entities = {record["id"]: record for record in current["entities"]}
    contexts = {record["id"]: record for record in current["contexts"]}
    key, value = next(iter(selector.items()))
    if key == "entity_id":
        if value not in entities:
            raise _ApiError(HTTPStatus.NOT_FOUND, "state_not_found")
        target_ids = [value]
    elif key == "context_id":
        context = contexts.get(value)
        if context is None:
            raise _ApiError(HTTPStatus.NOT_FOUND, "state_not_found")
        target_ids = [value, *sorted(context["entity_ids"])]
    else:
        target_ids = sorted(
            record_id
            for record_id, record in {**entities, **contexts}.items()
            if _actor_id(record) == value
        )
    page_targets = target_ids[offset:offset + limit]
    page_target_set = set(page_targets)
    assertion_sets = [
        item for item in current["assertion_sets"] if item["target_id"] in page_target_set
    ]
    assertion_sets.sort(key=lambda item: (item["target_id"], item["concept"]))
    next_cursor = (
        _encode_cursor({
            "kind": "state", "version": 1, "query": selector,
            "revision": revision, "offset": offset + len(page_targets),
        })
        if offset + len(page_targets) < len(target_ids)
        else None
    )
    return {
        "api_version": "v1",
        "revision": revision,
        "query": selector,
        "entities": [entities[item] for item in page_targets if item in entities],
        "contexts": [contexts[item] for item in page_targets if item in contexts],
        "assertion_sets": assertion_sets,
        "next_cursor": next_cursor,
    }


def _events_page(core: "Core", limit: int, cursor: str | None) -> dict[str, Any]:
    events = core.trusted_events()
    offset = 0
    watermark = len(events)
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        cursor_offset = decoded.get("offset")
        cursor_watermark = decoded.get("watermark")
        if (
            decoded.get("kind") != "events"
            or decoded.get("version") != 1
            or type(cursor_offset) is not int
            or cursor_offset < 0
            or type(cursor_watermark) is not int
            or cursor_watermark < 0
        ):
            raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_cursor")
        offset = cursor_offset
        watermark = cursor_watermark
    snapshot = events[:watermark]
    page = snapshot[offset:offset + limit]
    next_cursor = (
        _encode_cursor({"kind": "events", "version": 1, "watermark": watermark,
                        "offset": offset + len(page)})
        if offset + len(page) < len(snapshot)
        else None
    )
    return {"api_version": "v1", "events": page, "next_cursor": next_cursor}


class ApplicationApiServer:
    """Small stdlib HTTP transport; it is separate from sender acquisition."""

    def __init__(self, core: "Core") -> None:
        if core.config.application_api is None:
            raise ValueError("application API is not configured")
        self.core = core
        self.config = core.config.application_api
        self._credentials: dict[str, frozenset[str]] | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("application API server is not started")
        port = int(self._httpd.server_address[1])
        return f"http://{self.config.host}:{port}"

    def start(self) -> None:
        if self._httpd is not None:
            raise RuntimeError("application API server is already started")
        self._credentials = self.config.resolve_credentials(self.core.config.secret_references)
        transport = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                transport._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer((self.config.host, self.config.port), Handler)
        server.daemon_threads = True
        self._httpd = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join()
        self._httpd = None
        self._thread = None
        self._credentials = None

    def serve_forever(self) -> None:
        """Run the configured server in the foreground for the service entry point."""
        if self._httpd is not None:
            raise RuntimeError("application API server is already started")
        self._credentials = self.config.resolve_credentials(self.core.config.secret_references)
        transport = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                transport._handle(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._httpd = ThreadingHTTPServer((self.config.host, self.config.port), Handler)
        self._httpd.daemon_threads = True
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()
            self._httpd = None
            self._credentials = None

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            parsed = urlparse(handler.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.path == "/v1/state":
                self._authorize(handler, "state:read")
                allowed = {"entity_id", "context_id", "actor_id", "limit", "cursor"}
                if set(query) - allowed:
                    raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_request")
                selector = {
                    name: value for name in ("entity_id", "context_id", "actor_id")
                    if (value := _single_query(query, name)) is not None
                }
                if len(selector) != 1:
                    raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_request")
                body = _state_page(self.core, selector, _page_size(query), _single_query(query, "cursor"))
            elif parsed.path == "/v1/events":
                self._authorize(handler, "events:consume")
                if set(query) - {"limit", "cursor"}:
                    raise _ApiError(HTTPStatus.BAD_REQUEST, "invalid_request")
                body = _events_page(self.core, _page_size(query), _single_query(query, "cursor"))
            else:
                self._authorize(handler, "state:read")
                raise _ApiError(HTTPStatus.NOT_FOUND, "not_found")
        except _ApiError as error:
            self._respond(handler, error.status, {"error": error.code})
        except Exception:
            self._respond(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "unavailable"})
        else:
            self._respond(handler, HTTPStatus.OK, body)

    def _authorize(self, handler: BaseHTTPRequestHandler, scope: str) -> None:
        header = handler.headers.get("Authorization")
        if not isinstance(header, str) or not header.startswith("Bearer "):
            raise _ApiError(HTTPStatus.UNAUTHORIZED, "unauthorized")
        token = header.removeprefix("Bearer ")
        if not token or " " in token or self._credentials is None or token not in self._credentials:
            raise _ApiError(HTTPStatus.UNAUTHORIZED, "unauthorized")
        if scope not in self._credentials[token]:
            raise _ApiError(HTTPStatus.FORBIDDEN, "forbidden")

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: HTTPStatus, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(encoded)
