import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from piecetogether.core import Bootstrap, Communication, Core, SemanticProposal
from piecetogether.contracts import DomainContract
from piecetogether.proposals import (
    ArtifactOperation, ClaimOperation, ContextOperation, EmergentConceptOperation,
    EntityOperation, GroundingPlanOperation, RelationshipOperation,
)


class ForbiddenModel:
    def propose(self, inbound, contract_version, context_pack):
        return SemanticProposal(
            1, contract_version, inbound.id, (), "Unsafe draft",
            (EntityOperation("e1", "Forbidden"),),
        )


class MustNotRunModel:
    def propose(self, inbound, contract_version, context_pack):
        raise AssertionError("a durable outcome must not invoke the model again")


class StaticContractProvider:
    def __init__(self, contract):
        self.contract = contract

    def get(self, version):
        return self.contract


class SubjectModel:
    def propose(self, inbound, contract_version, context_pack):
        return SemanticProposal(
            1, contract_version, inbound.id, (), "Subject interpretation",
            (EntityOperation("s1", "Subject"),),
        )


class RefusingChannel:
    def deliver(self, outbound):
        return False


class RecordingChannel:
    def __init__(self):
        self.deliveries = []

    def deliver(self, outbound):
        self.deliveries.append(outbound)
        return True


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Bootstrap(
            Path(self.directory.name) / "core.sqlite3", {"development:s1": "a1"}
        )
        self.message = {
            "channel": "development", "sender": "s1", "idempotency_key": "m1",
            "text": "An interpretation", "sent_at": "2026-01-01T12:00:00Z",
        }

    def database_fault(self, trigger):
        # Arrange faults in the real SQLite adapter; observe only Core outcomes.
        with sqlite3.connect(self.config.database) as db:
            db.executescript(trigger)

    def legacy_retry(self, schema_version):
        # A fixture from the #13 persisted format, not a storage-internal assertion.
        Core(self.config)
        inbound = Communication(
            id="legacy-input", actor_id="a1", direction="inbound",
            received_at=self.message["sent_at"], **self.message,
        )
        outbound = replace(
            inbound, id="legacy-output", direction="outbound",
            idempotency_key=inbound.id, text="Legacy interpretation", reply_to=inbound.id,
        )
        proposal = {
            "schema_version": schema_version, "contract_version": self.config.contract_version,
            "communication_id": inbound.id, "candidate_claims": [],
            "draft_response": outbound.text,
        }
        trace = {
            "attempt": 1, "contract_version": self.config.contract_version,
            "validation_result": "candidate_only", "delivery_result": "retryable",
        }
        with sqlite3.connect(self.config.database) as db:
            db.execute("INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                inbound.id, inbound.channel, inbound.sender, inbound.idempotency_key,
                json.dumps(asdict(inbound)), json.dumps(proposal),
                json.dumps(asdict(outbound)), json.dumps(trace), "retryable",
            ))
        return inbound.id, outbound.id

    def test_rejection_capture_failure_is_retryable_and_recovers_after_restart(self):
        core = Core(self.config, model=ForbiddenModel())
        self.database_fault("""
            CREATE TRIGGER fail_capture BEFORE UPDATE OF proposal ON turns
            BEGIN SELECT RAISE(ABORT, 'temporary write failure'); END;
        """)
        result = core.accept(self.message)
        self.assertEqual(result["status"], "retryable")
        outcome = core.inspect(result["communication_id"])
        self.assertIsNone(outcome["proposal"])
        self.assertEqual(outcome["trace"]["failure_stage"], "storage")
        self.assertIsNone(result["reply"])
        self.database_fault("DROP TRIGGER fail_capture;")
        recovered = Core(self.config).accept(self.message)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["communication_id"], result["communication_id"])

    def test_rejection_and_its_evidence_are_durable_before_any_later_write(self):
        core = Core(self.config, model=ForbiddenModel())
        self.database_fault("""
            CREATE TRIGGER interrupt_after_capture BEFORE UPDATE OF status ON turns
            WHEN OLD.proposal IS NOT NULL AND NEW.status = 'rejected'
            BEGIN SELECT RAISE(ABORT, 'interrupted final write'); END;
        """)
        try:
            rejected = core.accept(self.message)
        except sqlite3.Error:
            self.fail("terminal rejection must be committed with its evidence")
        self.assertEqual(rejected["status"], "rejected")
        outcome = Core(self.config).inspect(rejected["communication_id"])
        self.assertEqual(outcome["status"], "rejected")
        self.assertEqual(outcome["trace"]["validation_result"], "rejected")
        self.assertEqual(outcome["proposal"]["operations"][0]["entity_type"], "Forbidden")
        self.assertIn("finished_at", outcome["trace"])
        repeated = Core(self.config, model=MustNotRunModel()).accept(self.message)
        self.assertEqual(repeated, rejected)

    def test_cached_reply_under_old_contract_requires_reprocessing_before_handoff(self):
        contract = DomainContract(self.config.contract_version, entity_types={
            "Subject": {"creation": True, "attributes": {}},
        })
        first = Core(
            self.config, model=SubjectModel(), channel=RefusingChannel(),
            contract_provider=StaticContractProvider(contract),
        ).accept(self.message)
        self.assertEqual(first["status"], "retryable")
        new_config = replace(self.config, contract_version="new-v2")
        channel = RecordingChannel()
        core = Core(new_config, model=MustNotRunModel(), channel=channel)
        stale = core.accept(self.message)
        self.assertEqual(stale["status"], "reprocess_required")
        self.assertEqual(stale["communication_id"], first["communication_id"])
        self.assertEqual(channel.deliveries, [])
        outcome = core.inspect(stale["communication_id"])
        self.assertEqual(outcome["trace"]["validation_result"], "stale")
        self.assertEqual(outcome["trace"]["contract_version"], "new-v2")
        self.assertEqual(outcome["proposal"]["contract_version"], "development-v1")
        self.assertIsNone(outcome["outbound"])
        fresh = Core(new_config, channel=channel).accept(self.message)
        self.assertEqual(fresh["status"], "completed")
        self.assertEqual(len(channel.deliveries), 1)
        self.assertNotEqual(fresh["reply"], "Subject interpretation")

    def test_legacy_boolean_schema_is_rejected_without_delivering_cached_reply(self):
        communication_id, _ = self.legacy_retry(True)
        channel = RecordingChannel()
        core = Core(self.config, model=MustNotRunModel(), channel=channel)
        rejected = core.accept(self.message)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(channel.deliveries, [])
        outcome = core.inspect(communication_id)
        self.assertEqual(outcome["trace"]["validation_result"], "rejected")
        self.assertEqual(outcome["trace"]["validation_reasons"], ["unsupported_proposal_version"])

    def test_valid_legacy_reply_is_validated_and_reuses_its_outbound_id(self):
        communication_id, outbound_id = self.legacy_retry(1)
        channel = RecordingChannel()
        core = Core(self.config, model=MustNotRunModel(), channel=channel)
        result = core.accept(self.message)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(channel.deliveries[0].id, outbound_id)
        trace = core.inspect(communication_id)["trace"]
        self.assertEqual(trace["validation_result"], "accepted")
        self.assertEqual(trace["proposal_contract_version"], "development-v1")
        self.assertEqual(trace["proposal_intent"], "candidate")
        attempts = core.inspect(communication_id)["attempts"]
        self.assertEqual([item["trace"]["validation_result"] for item in attempts], ["candidate_only", "accepted"])
        self.assertEqual(attempts[0]["outbound"]["id"], outbound_id)
        self.assertEqual(Core(self.config).inspect(communication_id)["attempts"], attempts)

    def test_typed_candidate_graph_survives_delivery_retry_without_model_call(self):
        contract = DomainContract(
            self.config.contract_version,
            entity_types={"Subject": {"creation": True, "attributes": {}}},
            relationship_types={"related": {"source_types": ["Subject"], "target_types": ["$context"]}},
            grounding_policies={"p": {"acceptance": ["explicit"]}},
            claim_concepts={"note": {"target_types": ["Subject"], "value": {"type": "string"}, "grounding_policy": "p"}},
            emergent_concepts={"allowed": True, "target_types": ["Subject"], "value": {"type": "string"}, "grounding_policy": "p"},
            artifact_types={"Evidence": {
                "roles": ["source-evidence"], "persistence": "forbidden",
                "retention": {"metadata": "retain", "bytes": "delete", "provenance": "retain"},
                "supersession": False,
            }},
        )

        class GraphModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), "A graph draft", (
                    EntityOperation("s1", "Subject"), ContextOperation("ctx", ("s1",)),
                    RelationshipOperation("related", "s1", "ctx"),
                    EmergentConceptOperation("preference", "A preference"),
                    ClaimOperation("c1", "s1", "note", "A note", "p"),
                    ClaimOperation("c2", "s1", "preference", "An option", "p"),
                    GroundingPlanOperation("p", ("c1", "c2"), "explicit"),
                    ArtifactOperation("a1", "Evidence", ("source-evidence",)),
                ))

        provider = StaticContractProvider(contract)
        first_core = Core(self.config, model=GraphModel(), channel=RefusingChannel(), contract_provider=provider)
        first = first_core.accept(self.message)
        self.assertEqual(first["status"], "retryable")
        original = first_core.inspect(first["communication_id"])
        channel = RecordingChannel()
        core = Core(self.config, model=MustNotRunModel(), channel=channel, contract_provider=provider)
        result = core.accept(self.message)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(channel.deliveries[0].id, original["outbound"]["id"])
        self.assertEqual(core.inspect(result["communication_id"])["proposal"], original["proposal"])

    def test_cached_graph_is_rejected_when_active_policy_forbids_its_entity(self):
        contract = DomainContract(self.config.contract_version, entity_types={
            "Subject": {"creation": True, "attributes": {}},
        })
        Core(self.config, model=SubjectModel(), channel=RefusingChannel(), contract_provider=StaticContractProvider(contract)).accept(self.message)
        channel = RecordingChannel()
        core = Core(self.config, model=MustNotRunModel(), channel=channel)
        result = core.accept(self.message)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(channel.deliveries, [])
        self.assertEqual(core.inspect(result["communication_id"])["trace"]["validation_reasons"], ["entity_type_not_allowed"])

    def test_interrupted_legacy_rejection_is_recovered_without_a_new_model_call(self):
        rejected = Core(self.config, model=ForbiddenModel()).accept(self.message)
        # Arrange the old split-write failure window: evidence saved, status pending.
        with sqlite3.connect(self.config.database) as db:
            db.execute("UPDATE turns SET status='pending' WHERE id=?", (rejected["communication_id"],))
        core = Core(self.config, model=MustNotRunModel())
        repeated = core.accept(self.message)
        self.assertEqual(repeated, rejected)
        self.assertEqual(core.inspect(rejected["communication_id"])["status"], "rejected")

    def test_interrupted_legacy_unserializable_rejection_keeps_its_decision(self):
        class UnserializableModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), "Unsafe draft", (
                    EntityOperation("e1", "Forbidden", {"raw": object()}),
                ))

        rejected = Core(self.config, model=UnserializableModel()).accept(self.message)
        core = Core(self.config)
        original = core.inspect(rejected["communication_id"])
        self.assertIsNone(original["proposal"])
        self.assertEqual(original["trace"]["proposal_capture"], "unserializable")
        with sqlite3.connect(self.config.database) as db:
            db.execute("UPDATE turns SET status='pending' WHERE id=?", (rejected["communication_id"],))
        recovered = Core(self.config, model=MustNotRunModel()).accept(self.message)
        self.assertEqual(recovered, rejected)
        self.assertEqual(core.inspect(rejected["communication_id"])["trace"], original["trace"])

    def test_unserializable_capture_with_storage_failure_remains_retryable(self):
        class UnserializableModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, version, inbound.id, (), "Unsafe draft", (
                    EntityOperation("e1", "Forbidden", {"raw": object()}),
                ))

        core = Core(self.config, model=UnserializableModel())
        self.database_fault("""
            CREATE TRIGGER fail_capture BEFORE UPDATE OF proposal ON turns
            BEGIN SELECT RAISE(ABORT, 'temporary write failure'); END;
        """)
        failed = core.accept(self.message)
        self.assertEqual(failed["status"], "retryable")
        trace = core.inspect(failed["communication_id"])["trace"]
        self.assertEqual(trace["proposal_capture"], "unserializable")
        self.assertEqual(trace["failure_stage"], "storage")
        self.database_fault("DROP TRIGGER fail_capture;")
        self.assertEqual(Core(self.config).accept(self.message)["status"], "completed")

    def test_stale_and_fresh_attempts_remain_inspectable_after_restart(self):
        class StaleModel:
            def propose(self, inbound, version, context_pack):
                return SemanticProposal(1, "obsolete-v1", inbound.id, (), "Old draft")

        first_core = Core(self.config, model=StaleModel())
        stale = first_core.accept(self.message)
        stale_outcome = first_core.inspect(stale["communication_id"])
        fresh = Core(self.config).accept(self.message)
        core = Core(self.config, model=MustNotRunModel())
        outcome = core.inspect(fresh["communication_id"])
        self.assertIn("attempts", outcome)
        attempts = outcome["attempts"]
        self.assertEqual([item["attempt"] for item in attempts], [1, 2])
        self.assertEqual([item["trace"]["validation_result"] for item in attempts], ["stale", "accepted"])
        self.assertEqual([item["status"] for item in attempts], ["reprocess_required", "completed"])
        self.assertEqual(attempts[0]["proposal"]["contract_version"], "obsolete-v1")
        self.assertEqual(attempts[0]["trace"], stale_outcome["trace"])
        self.assertIsNone(attempts[0]["outbound"])
        self.assertEqual(core.accept(self.message), fresh)
        self.assertEqual(core.inspect(fresh["communication_id"])["attempts"], attempts)

    def test_attempt_record_failure_cannot_leave_a_partial_terminal_rejection(self):
        core = Core(self.config, model=ForbiddenModel())
        self.database_fault("""
            CREATE TRIGGER fail_attempt BEFORE INSERT ON processing_attempts
            BEGIN SELECT RAISE(ABORT, 'attempt storage unavailable'); END;
        """)
        with patch('piecetogether.core.uuid4', return_value='faulted-input'), self.assertRaises(sqlite3.Error):
            core.accept(self.message)
        self.database_fault("DROP TRIGGER fail_attempt;")
        outcome = Core(self.config).inspect('faulted-input')
        self.assertEqual(outcome["status"], "pending")
        self.assertIsNone(outcome["proposal"])
        self.assertIsNone(outcome["trace"])
        self.assertEqual(outcome["attempts"], [])
        self.assertEqual(Core(self.config).accept(self.message)["status"], "completed")

    def test_completion_write_failure_reuses_durable_outbound_on_restart(self):
        channel = RecordingChannel()
        core = Core(self.config, channel=channel)
        self.database_fault("""
            CREATE TRIGGER fail_completion BEFORE UPDATE OF status ON turns
            WHEN NEW.status = 'completed'
            BEGIN SELECT RAISE(ABORT, 'completion write unavailable'); END;
        """)
        with self.assertRaises(sqlite3.Error):
            core.accept(self.message)
        original = channel.deliveries[0]
        self.assertEqual(core.inspect(original.reply_to)["status"], "retryable")
        self.database_fault("DROP TRIGGER fail_completion;")
        recovered = Core(self.config, model=MustNotRunModel(), channel=channel).accept(self.message)
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual([item.id for item in channel.deliveries], [original.id, original.id])
        attempts = core.inspect(recovered["communication_id"])["attempts"]
        self.assertEqual([item["status"] for item in attempts], ["retryable", "completed"])

    def test_malformed_cached_envelope_is_not_delivered_and_can_be_reprocessed(self):
        first = Core(self.config, channel=RefusingChannel()).accept(self.message)
        with sqlite3.connect(self.config.database) as db:
            db.execute("UPDATE turns SET proposal=? WHERE id=?", ("null", first["communication_id"]))
        channel = RecordingChannel()
        core = Core(self.config, model=MustNotRunModel(), channel=channel)
        failed = core.accept(self.message)
        self.assertEqual(failed["status"], "retryable")
        self.assertEqual(channel.deliveries, [])
        outcome = core.inspect(first["communication_id"])
        self.assertEqual(outcome["trace"]["failure_stage"], "validation")
        self.assertIsNone(outcome["outbound"])
        self.assertEqual(Core(self.config, channel=channel).accept(self.message)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
