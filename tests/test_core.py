import tempfile
import unittest
from pathlib import Path

from piecetogether.core import Bootstrap, CandidateClaim, Core, SemanticProposal


class CoreTests(unittest.TestCase):
    def test_maximum_size_text_has_a_durable_candidate_only_interpretation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )
            result = Core(config).accept(
                {
                    "channel": "development",
                    "sender": "s1",
                    "idempotency_key": "m1",
                    "text": "x" * 32768,
                    "sent_at": "2026-01-01T12:00:00Z",
                }
            )
            self.assertEqual(result["status"], "completed")
            restored = Core(config).inspect(result["communication_id"])
            self.assertTrue(restored["outbound"]["text"])
            self.assertEqual(restored["trace"]["commit_result"], "not_requested")

    def test_text_interpretation_is_durable_but_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                database=Path(directory) / "core.sqlite3",
                identities={"development:sender-1": "actor-1"},
            )
            core = Core(config)
            result = core.accept(
                {
                    "channel": "development",
                    "sender": "sender-1",
                    "idempotency_key": "message-1",
                    "text": "Keep the original option.",
                    "sent_at": "2026-01-01T12:00:00Z",
                }
            )
            restored = Core(config).inspect(result["communication_id"])
            self.assertEqual(restored["inbound"]["actor_id"], "actor-1")
            self.assertEqual(restored["inbound"]["text"], "Keep the original option.")
            self.assertEqual(
                restored["outbound"]["reply_to"], result["communication_id"]
            )
            self.assertEqual(restored["status"], "completed")
            self.assertEqual(restored["trace"]["commit_result"], "not_requested")
            self.assertEqual(
                restored["proposal"]["candidate_claims"][0]["status"], "candidate"
            )
            self.assertTrue(restored["outbound"]["text"])
            self.assertEqual(restored["trace"]["delivery_result"], "accepted")

    def test_redelivery_reuses_the_same_durable_outcome_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )
            message = {
                "channel": "development",
                "sender": "s1",
                "idempotency_key": "m1",
                "text": "Original statement",
                "sent_at": "2026-01-01T12:00:00Z",
            }
            first = Core(config).accept(message)
            second = Core(config).accept(message)
            self.assertEqual(second, first)
            self.assertEqual(
                Core(config).inspect(first["communication_id"])["status"], "completed"
            )
            with self.assertRaisesRegex(ValueError, "idempotency"):
                Core(config).accept({**message, "text": "Changed statement"})

    def test_model_failure_preserves_inbound_and_records_retryable_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )
            observed = []

            class FailingModel:
                def propose(self, inbound, contract_version, context_pack):
                    observed.append(Core(config).inspect(inbound.id))
                    raise OSError("temporary provider failure")

            message = {
                "channel": "development",
                "sender": "s1",
                "idempotency_key": "m1",
                "text": "A statement",
                "sent_at": "2026-01-01T12:00:00Z",
            }
            result = Core(config, model=FailingModel()).accept(message)
            self.assertEqual(observed[0]["status"], "pending")
            self.assertEqual(result["status"], "retryable")
            self.assertIsNone(result["reply"])
            trace = Core(config).inspect(result["communication_id"])["trace"]
            self.assertEqual(trace["failure_stage"], "model")
            retried = Core(config).accept(message)
            self.assertEqual(retried["communication_id"], result["communication_id"])
            self.assertEqual(retried["status"], "completed")

    def test_indeterminate_delivery_reuses_durable_reply_without_reinvoking_model(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )
            deliveries = []

            class UncertainChannel:
                def deliver(self, outbound):
                    deliveries.append(outbound.id)
                    if len(deliveries) == 1:
                        raise OSError("lost receipt")
                    return True

            class MustNotRunModel:
                def propose(self, inbound, contract_version, context_pack):
                    raise AssertionError("stored proposal must be reused")

            message = {
                "channel": "development",
                "sender": "s1",
                "idempotency_key": "m1",
                "text": "A statement",
                "sent_at": "2026-01-01T12:00:00Z",
            }
            channel = UncertainChannel()
            first = Core(config, channel=channel).accept(message)
            self.assertEqual(first["status"], "retryable")
            self.assertEqual(
                Core(config).inspect(first["communication_id"])["trace"][
                    "delivery_result"
                ],
                "indeterminate",
            )
            second = Core(config, model=MustNotRunModel(), channel=channel).accept(
                message
            )
            self.assertEqual(second["status"], "completed")
            self.assertEqual(deliveries[1], deliveries[0])
            self.assertEqual(second["communication_id"], first["communication_id"])

    def test_invalid_ingress_and_unmapped_identity_never_reach_model(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )
            core = Core(config)
            message = {
                "channel": "development",
                "sender": "s1",
                "idempotency_key": "m1",
                "text": "A statement",
                "sent_at": "2026-01-01T12:00:00Z",
            }
            for change in (
                {"text": ""},
                {"text": 42},
                {"sender": "unknown"},
                {"channel": "disabled"},
                {"sent_at": "yesterday"},
                {"sent_at": "2026-01-01T12:00:00"},
                {"actor_id": "spoofed"},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    core.accept({**message, **change})
            with self.assertRaises(ValueError):
                core.accept([])

    def test_bootstrap_rejects_dynamic_providers_and_incomplete_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            for change in (
                {"model": "import:untrusted"},
                {"channel": "email"},
                {"capabilities": ("receive",)},
                {"identities": {"development:s1": ""}},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    Bootstrap(
                        **{
                            "database": Path(directory) / "core.sqlite3",
                            "identities": {"development:s1": "a1"},
                            **change,
                        }
                    )

    def test_wrong_proposal_provenance_cannot_be_exposed_as_an_interpretation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )

            class InvalidModel:
                def propose(self, inbound, contract_version, context_pack):
                    return SemanticProposal(
                        1,
                        contract_version,
                        "wrong-input",
                        (CandidateClaim("wrong-input", "not grounded", "grounded"),),
                        "Unsafe interpretation",
                    )

            result = Core(config, model=InvalidModel()).accept(
                {
                    "channel": "development",
                    "sender": "s1",
                    "idempotency_key": "m1",
                    "text": "A statement",
                    "sent_at": "2026-01-01T12:00:00Z",
                }
            )
            outcome = Core(config).inspect(result["communication_id"])
            self.assertIsNone(outcome["outbound"])
            self.assertIsNone(result["reply"])
            self.assertEqual(outcome["trace"]["delivery_result"], "not_attempted")

    def test_malformed_candidate_envelope_records_failure_without_exposure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Bootstrap(
                Path(directory) / "core.sqlite3", {"development:s1": "a1"}
            )

            class MalformedModel:
                def propose(self, inbound, contract_version, context_pack):
                    return SemanticProposal(
                        1, contract_version, inbound.id, ({},), "Unsafe interpretation"
                    )

            result = Core(config, model=MalformedModel()).accept(
                {
                    "channel": "development",
                    "sender": "s1",
                    "idempotency_key": "m1",
                    "text": "A statement",
                    "sent_at": "2026-01-01T12:00:00Z",
                }
            )
            self.assertEqual(result["status"], "retryable")
            self.assertIsNone(result["reply"])
            outcome = Core(config).inspect(result["communication_id"])
            self.assertIsNone(outcome["outbound"])
            self.assertEqual(outcome["trace"]["failure_stage"], "model")
            self.assertEqual(outcome["trace"]["delivery_result"], "not_attempted")


if __name__ == "__main__":
    unittest.main()
