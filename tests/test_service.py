import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from piecetogether.core import Bootstrap


class ServiceTests(unittest.TestCase):
    def test_deeply_nested_json_does_not_stop_later_communications(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "deployment.json"
            config.write_text(
                json.dumps(
                    {
                        "database": "state.sqlite3",
                        "identities": {"development:s1": "a1"},
                    }
                )
            )
            message = json.dumps(
                {
                    "channel": "development",
                    "sender": "s1",
                    "idempotency_key": "m1",
                    "text": "A statement",
                    "sent_at": "2026-01-01T12:00:00Z",
                }
            )
            process = subprocess.run(
                [sys.executable, "-m", "piecetogether", "--config", str(config)],
                input="[" * 20000 + "]" * 20000 + "\n" + message + "\n",
                text=True,
                capture_output=True,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            replies = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(replies[0], {"error": "invalid_communication"})
            self.assertEqual(replies[1]["status"], "completed")

    def test_standalone_bootstrap_and_redelivery_do_not_disclose_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "deployment.json"
            config.write_text(
                json.dumps(
                    {
                        "database": "state/core.sqlite3",
                        "identities": {"development:s1": "a1"},
                        "channel": "development",
                        "model": "deterministic",
                        "capabilities": ["receive", "reply"],
                        "contract_version": "test-v1",
                        "secret_references": {},
                    }
                )
            )
            message = json.dumps(
                {
                    "channel": "development",
                    "sender": "s1",
                    "idempotency_key": "m1",
                    "text": "A statement",
                    "sent_at": "2026-01-01T12:00:00Z",
                }
            )
            command = [sys.executable, "-m", "piecetogether", "--config", str(config)]
            first = subprocess.run(
                command,
                input=message + "\n",
                text=True,
                capture_output=True,
                check=True,
            )
            second = subprocess.run(
                command,
                input="not json\n" + message + "\n",
                text=True,
                capture_output=True,
                check=True,
            )
            replies = [json.loads(line) for line in second.stdout.splitlines()]
            self.assertEqual(replies[0], {"error": "invalid_communication"})
            self.assertEqual(replies[1], json.loads(first.stdout))
            self.assertEqual(set(replies[1]), {"communication_id", "reply", "status"})
            inspected = subprocess.run(
                command + ["--inspect", replies[1]["communication_id"]],
                text=True,
                capture_output=True,
                check=True,
            )
            trace = json.loads(inspected.stdout)["trace"]
            self.assertEqual(trace["contract_version"], "test-v1")
            self.assertEqual(trace["attempt"], 1)

    def test_bootstrap_resolves_environment_secret_references_without_printing_values(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "deployment.json"
            config.write_text(
                json.dumps(
                    {
                        "database": "state.sqlite3",
                        "identities": {"development:s1": "a1"},
                        "secret_references": {"provider": "PT_TEST_SECRET"},
                    }
                )
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                self.assertRaisesRegex(ValueError, "secret"),
            ):
                Bootstrap.from_file(config)
            with patch.dict(
                os.environ, {"PT_TEST_SECRET": "not-for-output"}, clear=True
            ):
                bootstrap = Bootstrap.from_file(config)
                self.assertEqual(
                    bootstrap.secret_references, {"provider": "PT_TEST_SECRET"}
                )
                self.assertNotIn("not-for-output", repr(bootstrap))


if __name__ == "__main__":
    unittest.main()
