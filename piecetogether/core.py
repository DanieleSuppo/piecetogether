"""Development Core: durable acquisition and candidate-only interpretation."""

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from .contracts import DeclarativeContractProvider, DomainContractProvider
from .proposals import CandidateClaim, SemanticProposal, validate


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Bootstrap:
    database: Path
    identities: dict[str, str]
    channel: str = "development"
    model: str = "deterministic"
    contract_version: str = "development-v1"
    capabilities: tuple[str, ...] = ("receive", "reply")
    secret_references: dict[str, str] = field(default_factory=dict)
    contract: Path | None = None

    def __post_init__(self) -> None:
        if self.contract is not None and not isinstance(self.contract, Path):
            raise ValueError("contract must name a declarative file")
        if self.channel != "development" or self.model != "deterministic":
            raise ValueError("only static development adapters are available")
        if set(self.capabilities) != {"receive", "reply"}:
            raise ValueError(
                "development channel requires receive and reply capabilities"
            )
        if (
            not isinstance(self.contract_version, str)
            or not self.contract_version.strip()
        ):
            raise ValueError("contract_version must be a nonempty string")
        if not isinstance(self.database, Path) or self.database.name == ":memory:":
            raise ValueError("database must name a persistent file")
        if (
            not isinstance(self.identities, dict)
            or not self.identities
            or any(
                not isinstance(identity, str)
                or not identity.startswith("development:")
                or not identity.removeprefix("development:").strip()
                or not isinstance(actor, str)
                or not actor.strip()
                for identity, actor in self.identities.items()
            )
        ):
            raise ValueError(
                "identities must map channel identities to nonempty Actors"
            )
        if not isinstance(self.secret_references, dict) or any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(reference, str)
            or not reference.strip()
            for name, reference in self.secret_references.items()
        ):
            raise ValueError(
                "secret_references must contain environment variable names"
            )

    @classmethod
    def from_file(cls, path: Path) -> "Bootstrap":
        data = json.loads(path.read_text())
        allowed = {
            "database",
            "identities",
            "channel",
            "model",
            "contract_version",
            "capabilities",
            "secret_references",
            "contract",
        }
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("invalid bootstrap configuration")
        if not isinstance(data.get("database"), str) or not data["database"].strip():
            raise ValueError("database is required")
        if not isinstance(data.get("capabilities", ["receive", "reply"]), list) or any(
            not isinstance(value, str) for value in data.get("capabilities", [])
        ):
            raise ValueError("capabilities must be a string array")
        database = Path(data.pop("database"))
        if "contract" in data:
            if not isinstance(data["contract"], str) or not data["contract"].strip():
                raise ValueError("contract must name a declarative file")
            data["contract"] = path.parent / data["contract"]
        data["capabilities"] = tuple(data.get("capabilities", ["receive", "reply"]))
        config = cls(database=path.parent / database, **data)
        if any(
            not os.environ.get(reference)
            for reference in config.secret_references.values()
        ):
            raise ValueError("a deployment secret reference is unresolved")
        return config


@dataclass(frozen=True)
class Communication:
    id: str
    channel: str
    sender: str
    actor_id: str
    direction: str
    idempotency_key: str
    text: str
    sent_at: str
    received_at: str
    thread_id: str | None = None
    reply_to: str | None = None


class ModelProvider(Protocol):
    def propose(
        self, inbound: Communication, contract_version: str
    ) -> SemanticProposal: ...


class ChannelPlugin(Protocol):
    def deliver(self, outbound: Communication) -> bool: ...


class DeterministicModel:
    def propose(
        self, inbound: Communication, contract_version: str
    ) -> SemanticProposal:
        return SemanticProposal(
            schema_version=1,
            contract_version=contract_version,
            communication_id=inbound.id,
            candidate_claims=(CandidateClaim(inbound.id, inbound.text),),
            draft_response=f"I understood: {inbound.text}",
        )


class DevelopmentChannel:
    """A durable development mailbox, not a real Email/Telegram transport."""

    def __init__(self, database: Path):
        self.database = database

    def deliver(self, outbound: Communication) -> bool:
        with connect(self.database) as db:
            db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?)",
                (outbound.id, json.dumps(asdict(outbound))),
            )
        return True


@contextmanager
def connect(database: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def _record_attempt(
    db: sqlite3.Connection, communication_id: str, proposal: str | None,
    outbound: str | None, trace: dict[str, Any], status: str,
) -> None:
    db.execute(
        "INSERT INTO processing_attempts VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(communication_id, attempt) DO UPDATE SET "
        "proposal=excluded.proposal, outbound=excluded.outbound, "
        "trace=excluded.trace, status=excluded.status",
        (communication_id, trace.get("attempt", 0), proposal, outbound, json.dumps(trace), status),
    )


class Core:
    def __init__(
        self,
        config: Bootstrap,
        model: ModelProvider | None = None,
        channel: ChannelPlugin | None = None,
        contract_provider: DomainContractProvider | None = None,
    ):
        self.config = config
        provider = contract_provider or DeclarativeContractProvider(config.contract)
        self.contract = provider.get(config.contract_version)
        if self.contract.version != config.contract_version:
            raise ValueError("provider returned a different Contract version")
        self.model = model or DeterministicModel()
        self.channel = channel or DevelopmentChannel(config.database)
        config.database.parent.mkdir(parents=True, exist_ok=True)
        with connect(config.database) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    inbound TEXT NOT NULL,
                    proposal TEXT,
                    outbound TEXT,
                    trace TEXT,
                    status TEXT NOT NULL,
                    UNIQUE(channel, sender, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, outbound TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processing_attempts (
                    communication_id TEXT NOT NULL REFERENCES turns(id),
                    attempt INTEGER NOT NULL,
                    proposal TEXT,
                    outbound TEXT,
                    trace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(communication_id, attempt)
                );
            """)
            # Import the last available checkpoint from pre-history deployments once.
            legacy_rows = db.execute(
                "SELECT * FROM turns WHERE trace IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM processing_attempts WHERE communication_id=turns.id)"
            ).fetchall()
            for row in legacy_rows:
                _record_attempt(
                    db, row["id"], row["proposal"], row["outbound"],
                    json.loads(row["trace"]), row["status"],
                )

    def accept(self, payload: Any) -> dict[str, Any]:
        required = {"channel", "sender", "idempotency_key", "text", "sent_at"}
        optional = {"thread_id", "reply_to"}
        if (
            not isinstance(payload, dict)
            or not required <= payload.keys()
            or set(payload) - required - optional
        ):
            raise ValueError("expected a normalized text Communication")
        if any(
            not isinstance(payload[name], str)
            or not payload[name].strip()
            or len(payload[name]) > (32768 if name == "text" else 256)
            for name in required
        ):
            raise ValueError("Communication fields must be bounded nonempty strings")
        if any(
            payload.get(name) is not None
            and (
                not isinstance(payload[name], str)
                or not payload[name].strip()
                or len(payload[name]) > 256
            )
            for name in optional
        ):
            raise ValueError("thread and reply references must be bounded strings")
        try:
            timestamp = datetime.fromisoformat(
                payload["sent_at"].replace("Z", "+00:00")
            )
        except ValueError:
            raise ValueError("sent_at must be an ISO-8601 timestamp") from None
        if timestamp.tzinfo is None:
            raise ValueError("sent_at must include a timezone")
        if payload["channel"] != self.config.channel:
            raise ValueError("channel is disabled")
        actor = self.config.identities.get(f"{payload['channel']}:{payload['sender']}")
        if actor is None:
            raise ValueError("unmapped channel identity")
        inbound = Communication(
            id=str(uuid4()),
            actor_id=actor,
            direction="inbound",
            received_at=now(),
            **payload,
        )
        # Ingress commits before calling the model or handing off a reply.
        with connect(self.config.database) as db:
            db.execute(
                "INSERT INTO turns (id, channel, sender, idempotency_key, inbound, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending') "
                "ON CONFLICT(channel, sender, idempotency_key) DO NOTHING",
                (
                    inbound.id,
                    inbound.channel,
                    inbound.sender,
                    inbound.idempotency_key,
                    json.dumps(asdict(inbound)),
                ),
            )
            row = db.execute(
                "SELECT * FROM turns WHERE channel=? AND sender=? AND idempotency_key=?",
                (inbound.channel, inbound.sender, inbound.idempotency_key),
            ).fetchone()
        assert row is not None
        stored = json.loads(row["inbound"])
        incoming = asdict(inbound)
        if any(
            stored[name] != incoming[name]
            for name in incoming
            if name not in ("id", "received_at")
        ):
            raise ValueError(
                "idempotency key already belongs to a different Communication"
            )
        inbound = Communication(**stored)
        if row["status"] == "completed":
            return {
                "communication_id": inbound.id,
                "reply": json.loads(row["outbound"])["text"],
                "status": "completed",
            }
        previous_trace = json.loads(row["trace"]) if row["trace"] else {}
        if (
            row["status"] != "rejected"
            and (row["proposal"] or previous_trace.get("proposal_capture") == "unserializable")
            and previous_trace.get("validation_result") == "rejected"
            and not previous_trace.get("failure_stage")
        ):
            # Recover the pre-atomic #14 checkpoint without replacing its decision.
            with connect(self.config.database) as db:
                db.execute("UPDATE turns SET status='rejected' WHERE id=?", (inbound.id,))
                _record_attempt(
                    db, inbound.id, row["proposal"], row["outbound"], previous_trace, "rejected",
                )
            return {"communication_id": inbound.id, "reply": None, "status": "rejected"}
        if row["status"] == "rejected":
            return {"communication_id": inbound.id, "reply": None, "status": "rejected"}
        started = monotonic()
        outbound = (
            Communication(**json.loads(row["outbound"])) if row["outbound"] else None
        )
        trace: dict[str, Any] = {
            "input_communication_id": inbound.id,
            "contract_version": self.config.contract_version,
            "model_provider": self.config.model,
            "attempt": previous_trace.get("attempt", 0) + 1,
            "started_at": now(),
            "commit_result": "not_requested",
            "delivery_result": "pending",
            "model_usage": None,
            "validation_result": None,
            "validation_reasons": [],
        }
        accepted = False
        status = "retryable"
        saved_outbound = row["outbound"]
        captured = row["proposal"] if outbound else None
        stage = "model"
        try:
            if outbound is None:
                proposal = self.model.propose(inbound, self.config.contract_version)
            else:
                stage = "validation"
                proposal = SemanticProposal.from_dict(json.loads(row["proposal"]))
            validation = validate(proposal, inbound.id, self.contract)
            trace["validation_result"] = validation.outcome
            trace["validation_reasons"] = list(validation.reasons)
            trace["proposal_contract_version"] = proposal.contract_version
            trace["proposal_intent"] = proposal.intent
            if proposal.intent == "semantic_commit":
                trace["commit_result"] = "rejected"
            status = {
                "accepted": "retryable",
                "rejected": "rejected",
                "stale": "reprocess_required",
            }[validation.outcome]
            try:
                captured = json.dumps(asdict(proposal), allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                captured = None
                trace["proposal_capture"] = "unserializable"
                if validation.outcome == "accepted":
                    raise ValueError("accepted proposal must be JSON serializable") from None
            if validation.outcome == "accepted":
                if outbound is None:
                    outbound = Communication(
                        id=str(uuid4()),
                        channel=inbound.channel,
                        sender=inbound.sender,
                        actor_id=inbound.actor_id,
                        direction="outbound",
                        idempotency_key=inbound.id,
                        text=proposal.draft_response,
                        sent_at=now(),
                        received_at=now(),
                        thread_id=inbound.thread_id,
                        reply_to=inbound.id,
                    )
            else:
                outbound = None
                trace["delivery_result"] = "not_attempted"
                trace["finished_at"] = now()
                trace["duration_ms"] = round((monotonic() - started) * 1000, 3)
            stage = "storage"
            encoded_outbound = json.dumps(asdict(outbound)) if outbound else None
            with connect(self.config.database) as db:
                db.execute(
                    "UPDATE turns SET proposal=?, outbound=?, trace=?, status=? WHERE id=?",
                    (captured, encoded_outbound, json.dumps(trace), status, inbound.id),
                )
                _record_attempt(db, inbound.id, captured, encoded_outbound, trace, status)
            saved_outbound = encoded_outbound
            if outbound is None:
                return {"communication_id": inbound.id, "reply": None, "status": status}
            if outbound is not None:
                stage = "channel"
                accepted = self.channel.deliver(outbound)
                trace["delivery_result"] = "accepted" if accepted else "retryable"
        except (OSError, ValueError, TypeError, RecursionError, sqlite3.Error) as error:
            status = "retryable"
            if stage == "validation":
                saved_outbound = None
            trace["failure_stage"] = stage
            trace["error_type"] = type(error).__name__
            trace["delivery_result"] = (
                "indeterminate" if stage == "channel" else "not_attempted"
            )
        trace["finished_at"] = now()
        trace["duration_ms"] = round((monotonic() - started) * 1000, 3)
        status = "completed" if accepted else status
        with connect(self.config.database) as db:
            db.execute(
                "UPDATE turns SET status=?, trace=?, outbound=? WHERE id=?",
                (status, json.dumps(trace), saved_outbound, inbound.id),
            )
            _record_attempt(db, inbound.id, captured, saved_outbound, trace, status)
        return {
            "communication_id": inbound.id,
            "reply": outbound.text if accepted and outbound else None,
            "status": status,
        }

    def inspect(self, communication_id: str) -> dict[str, Any]:
        """Operator-only in-process inspection; never part of sender replies."""
        with connect(self.config.database) as db:
            row = db.execute(
                "SELECT * FROM turns WHERE id=?", (communication_id,)
            ).fetchone()
            attempts = db.execute(
                "SELECT * FROM processing_attempts WHERE communication_id=? ORDER BY attempt",
                (communication_id,),
            ).fetchall()
        if row is None:
            raise KeyError(communication_id)
        return {
            "status": row["status"],
            "attempts": [
                {
                    "attempt": item["attempt"], "status": item["status"],
                    **{
                        name: json.loads(item[name]) if item[name] else None
                        for name in ("proposal", "outbound", "trace")
                    },
                }
                for item in attempts
            ],
            **{
                name: json.loads(row[name]) if row[name] else None
                for name in ("inbound", "proposal", "outbound", "trace")
            },
        }
