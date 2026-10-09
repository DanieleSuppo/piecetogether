"""Authenticated application State API through a local loopback transport."""

import json
import os
import unittest
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from pathlib import Path

import test_history as fixtures
from piecetogether.application_api import (
    ApplicationApiConfig,
    ApplicationApiServer,
    ApplicationCredential,
)
from piecetogether.core import Bootstrap, Core


class ApplicationApiTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixtures.HistoryTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.core, _, result = self.flow.accept_items(self.flow.expose())
        self.assertEqual(result["status"], "completed")
        self.entity_id = self.core.inspect_history()["entities"][0]["id"]
        self.context_id = self.core.inspect_history()["contexts"][0]["id"]
        self.api_config = ApplicationApiConfig(
            "127.0.0.1",
            0,
            (
                ApplicationCredential("reader", ("state:read",)),
                ApplicationCredential("consumer", ("events:consume",)),
                ApplicationCredential("both", ("state:read", "events:consume")),
            ),
        )

    def server(self):
        config = replace(
            self.flow.config,
            application_api=self.api_config,
            secret_references={
                "reader": "PT_API_READER",
                "consumer": "PT_API_CONSUMER",
                "both": "PT_API_BOTH",
            },
        )
        core = Core(
            config,
            model=fixtures.MustNotRunModel(),
            contract_provider=fixtures.ContractProvider(self.flow.contract),
        )
        server = ApplicationApiServer(core)
        server.start()
        self.addCleanup(server.close)
        return server

    @staticmethod
    def request(server, path, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        request = Request(server.base_url + path, headers=headers)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_state_queries_return_only_trusted_provenance_aware_state(self):
        with patch.dict(os.environ, {
            "PT_API_READER": "reader-secret", "PT_API_CONSUMER": "consumer-secret",
            "PT_API_BOTH": "both-secret",
        }, clear=True):
            server = self.server()
            status, body = self.request(server, f"/v1/state?entity_id={self.entity_id}", "reader-secret")

        self.assertEqual(status, 200)
        self.assertEqual(body["api_version"], "v1")
        self.assertEqual(body["revision"], 1)
        self.assertEqual(body["query"], {"entity_id": self.entity_id})
        self.assertEqual([entity["id"] for entity in body["entities"]], [self.entity_id])
        self.assertEqual([group["target_id"] for group in body["assertion_sets"]], [self.entity_id])
        head = body["assertion_sets"][0]["heads"][0]
        self.assertTrue(head["current"])
        self.assertEqual(head["provenance_class"], "grounded")
        self.assertIn("grounding_item_id", head["provenance"])
        self.assertNotIn("candidate_claims", json.dumps(body))
        self.assertNotIn("trace", json.dumps(body))
        self.assertNotIn("proposal", json.dumps(body))

    def test_scopes_default_deny_and_events_require_their_own_scope(self):
        with patch.dict(os.environ, {
            "PT_API_READER": "reader-secret", "PT_API_CONSUMER": "consumer-secret",
            "PT_API_BOTH": "both-secret",
        }, clear=True):
            server = self.server()
            self.assertEqual(self.request(server, f"/v1/state?entity_id={self.entity_id}")[0], 401)
            self.assertEqual(self.request(server, f"/v1/state?entity_id={self.entity_id}", "consumer-secret")[0], 403)
            self.assertEqual(self.request(server, "/v1/events", "reader-secret")[0], 403)
            status, events = self.request(server, "/v1/events", "consumer-secret")

        self.assertEqual(status, 200)
        self.assertEqual(events["api_version"], "v1")
        self.assertEqual(events["events"], self.core.inspect_history()["events"])
        self.assertNotIn("proposal", json.dumps(events))
        self.assertNotIn("trace", json.dumps(events))

    def test_actor_context_and_cursor_queries_are_bounded_and_consistent(self):
        exposed = self.flow.expose()
        self.core, _, result = self.flow.accept_items(exposed, revision=1)
        self.assertEqual(result["status"], "completed")
        with patch.dict(os.environ, {
            "PT_API_READER": "reader-secret", "PT_API_CONSUMER": "consumer-secret",
            "PT_API_BOTH": "both-secret",
        }, clear=True):
            server = self.server()
            status, first = self.request(server, "/v1/state?actor_id=a1&limit=1", "reader-secret")
            self.assertEqual(status, 200)
            self.assertEqual(len(first["entities"]) + len(first["contexts"]), 1)
            pages = [first]
            while pages[-1]["next_cursor"] is not None:
                status, page = self.request(
                    server, "/v1/state?actor_id=a1&limit=1&cursor=" + pages[-1]["next_cursor"], "reader-secret"
                )
                self.assertEqual(status, 200)
                pages.append(page)
            context_status, context = self.request(
                server, f"/v1/state?context_id={self.context_id}", "reader-secret"
            )
            event_status, event_first = self.request(server, "/v1/events?limit=1", "both-secret")
            event_next_status, event_second = self.request(
                server, "/v1/events?limit=1&cursor=" + event_first["next_cursor"], "both-secret"
            )

        self.assertEqual(sum(len(page["assertion_sets"]) for page in pages), 2)
        self.assertTrue(all(page["revision"] == first["revision"] for page in pages))
        self.assertIsNone(pages[-1]["next_cursor"])
        self.assertEqual(context_status, 200)
        self.assertEqual(context["contexts"][0]["id"], self.context_id)
        self.assertEqual(event_status, 200)
        self.assertEqual(event_next_status, 200)
        self.assertEqual(len(event_first["events"]), 1)
        self.assertEqual(len(event_second["events"]), 1)
        self.assertIsNone(event_second["next_cursor"])
        self.assertEqual(context["entities"][0]["id"], self.entity_id)

    def test_bootstrap_accepts_only_declared_scoped_secret_credentials(self):
        config_path = Path(self.flow.directory.name) / "deployment.json"
        base = {
            "database": "api.sqlite3",
            "identities": {"development:s1": "a1"},
            "secret_references": {"reader": "PT_API_READER"},
            "application_api": {
                "host": "127.0.0.1", "port": 8080,
                "credentials": [{"secret_reference": "reader", "scopes": ["state:read"]}],
            },
        }
        with patch.dict(os.environ, {"PT_API_READER": "reader-secret"}, clear=True):
            config_path.write_text(json.dumps(base))
            bootstrap = Bootstrap.from_file(config_path)
        self.assertIsNotNone(bootstrap.application_api)
        self.assertNotIn("reader-secret", repr(bootstrap))
        invalid = json.loads(json.dumps(base))
        invalid["application_api"]["credentials"].append({"secret_reference": "reader"})
        config_path.write_text(json.dumps(invalid))
        with patch.dict(os.environ, {"PT_API_READER": "reader-secret"}, clear=True):
            with self.assertRaises(ValueError):
                Bootstrap.from_file(config_path)

    def test_credentials_rotate_only_by_changed_deployment_configuration_and_restart(self):
        with patch.dict(os.environ, {
            "PT_API_READER": "old-reader-secret", "PT_API_CONSUMER": "consumer-secret",
            "PT_API_BOTH": "both-secret",
        }, clear=True):
            old_server = self.server()
            self.assertEqual(self.request(old_server, f"/v1/state?entity_id={self.entity_id}", "old-reader-secret")[0], 200)
            old_server.close()
            self.api_config = ApplicationApiConfig(
                "127.0.0.1", 0, (ApplicationCredential("rotated", ("state:read",)),)
            )
            with patch.dict(os.environ, {"PT_API_ROTATED": "new-reader-secret"}, clear=True):
                config = replace(
                    self.flow.config,
                    application_api=self.api_config,
                    secret_references={"rotated": "PT_API_ROTATED"},
                )
                core = Core(config, model=fixtures.MustNotRunModel(),
                            contract_provider=fixtures.ContractProvider(self.flow.contract))
                server = ApplicationApiServer(core)
                server.start()
                self.addCleanup(server.close)
                self.assertEqual(self.request(server, f"/v1/state?entity_id={self.entity_id}", "old-reader-secret")[0], 401)
                self.assertEqual(self.request(server, f"/v1/state?entity_id={self.entity_id}", "new-reader-secret")[0], 200)


if __name__ == "__main__":
    unittest.main()
