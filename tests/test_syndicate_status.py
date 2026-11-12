"""Probe/conform observations stay per host (syndicate_status), never on
the shared inventory record that every host syncs."""

import json
import shutil
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.support import DatastoreCase

from lib.cli import syndicate as syndicate_cli
from lib.dispatch import (load_syndicates, observed_status,
                          record_observation, with_observations)


class ObservationTest(DatastoreCase):

    def setUp(self):
        shutil.rmtree(self.ds.local_path / "status", ignore_errors=True)
        self.syndicate = next(s for s in load_syndicates(self.ds)
                              if s["slug"] == "mux_nova")
        self.record_path = self.tmp / "syndicates" / f"{self.syndicate['id']}.json"
        self.shared_before = self.record_path.read_text()

    def assertSharedRecordUntouched(self):
        self.assertEqual(self.record_path.read_text(), self.shared_before)

    def test_unprobed_host_falls_back_to_shared_record(self):
        legacy = {**self.syndicate, "status": {"state": "online"}}
        self.assertEqual(observed_status(legacy, self.ds), {"state": "online"})
        self.assertEqual(observed_status(self.syndicate, self.ds), {})

    def test_observation_is_local_and_wins(self):
        record_observation(self.syndicate, {"state": "offline"}, self.ds)
        record_observation(self.syndicate, {"state": "online"}, self.ds)
        legacy = {**self.syndicate, "status": {"state": "unreachable"}}
        self.assertEqual(observed_status(legacy, self.ds)["state"], "online")
        self.assertEqual(with_observations(legacy, self.ds)["status"]["state"],
                         "online")
        self.assertTrue((self.ds.local_path / "status" / "syndicates"
                         / f"{self.syndicate['id']}.json").exists())
        self.assertSharedRecordUntouched()

    def test_conform_records_locally(self):
        card = {"suite": "kat-1", "mode": "harness", "runs": 1, "passed": 2,
                "total": 2, "rate": 1.0, "at": "2026-11-11T00:00:00+00:00",
                "results": []}
        provider = SimpleNamespace(syndicate=self.syndicate,
                                   channel=self.syndicate["channels"][0],
                                   descriptor="stub")
        args = SimpleNamespace(ref="mux_nova", runs=1, timeout=1,
                               no_record=False)
        with mock.patch("lib.cli.syndicate.get_datastore", return_value=self.ds), \
             mock.patch("lib.dispatch.get_provider", return_value=provider), \
             mock.patch("lib.dispatch.conformance.run_suite", return_value=card), \
             mock.patch("lib.config.report_dir", return_value=self.tmp / "log"), \
             mock.patch("builtins.print"):
            self.assertEqual(syndicate_cli.cmd_conform(args), 0)
        conformance = observed_status(self.syndicate, self.ds)["conformance"]
        self.assertEqual(conformance["qwen3_coder"]["passed"], 2)
        self.assertSharedRecordUntouched()

    def test_observation_conforms_to_schema(self):
        if not self.ds.schema_manager.can_validate():
            self.skipTest("jsonschema not installed")
        saved = record_observation(self.syndicate, {
            "state": "online", "last_check": "2026-11-11T00:00:00+00:00",
            "latency_ms": 38.8, "models_reported": ["qwen3-coder:30b"]}, self.ds)
        self.assertEqual(self.ds.schema_manager.validate("syndicate_status", saved), [])


class SharedInventoryTest(unittest.TestCase):

    def test_shipped_records_carry_no_observations(self):
        from tests.support import REPO
        for path in (REPO / "data/agency/data/syndicates").glob("*.json"):
            status = json.loads(path.read_text()).get("status") or {}
            for volatile in ("last_check", "latency_ms", "models_reported",
                             "conformance", "error_message"):
                self.assertNotIn(volatile, status, f"{path.name}: {volatile}")


if __name__ == "__main__":
    unittest.main()
