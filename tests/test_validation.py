"""Validation on datastore writes, under per-entity modes (enforce / warn,
after Kubernetes Pod Security Admission). Requires jsonschema; without
it the core stays stdlib-only and nothing is validated."""

import json
import unittest
from unittest import mock

from tests.support import DatastoreCase

from lib.data.crud import ValidationError
from lib.data.schema import HAS_JSONSCHEMA


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class TestValidationModes(DatastoreCase):

    def syndicate(self, slug):
        record = json.loads(json.dumps(self.ds.query("syndicate").first()))
        record.update(id=None, slug=slug)
        return record

    def test_enforce_refuses_a_nonconforming_create(self):
        record = self.syndicate("bad_endpoint")
        record["endpoint"]["egress"] = "everywhere"
        with self.assertRaises(ValidationError) as caught:
            self.ds.create("syndicate", record)
        self.assertIn("egress", str(caught.exception))
        self.assertIsNone(self.ds.query(
            "syndicate", lambda s: s["slug"] == "bad_endpoint").first())

    def test_enforce_refuses_a_nonconforming_update(self):
        created = self.ds.create("syndicate", self.syndicate("upd_probe"))
        try:
            with self.assertRaises(ValidationError):
                self.ds.update("syndicate", created["id"],
                               {"endpoint": {"egress": "everywhere"}})
            stored = self.ds.read("syndicate", created["id"])
            self.assertNotEqual(stored["endpoint"]["egress"], "everywhere")
        finally:
            self.ds.delete("syndicate", created["id"], hard=True)

    def test_warn_writes_and_reports(self):
        record = self.syndicate("warn_probe")
        record["endpoint"]["egress"] = "everywhere"
        with mock.patch("lib.config.validation_mode", return_value="warn"), \
                self.assertLogs("nim.data", level="WARNING") as logs:
            created = self.ds.create("syndicate", record)
        try:
            self.assertIn("schema error", logs.output[0])
            self.assertIsNotNone(self.ds.read("syndicate", created["id"]))
        finally:
            self.ds.delete("syndicate", created["id"], hard=True)

    def test_reference_entries_are_validated(self):
        with self.assertRaises(ValidationError):
            self.ds.add_reference_entry("ops_domain", {
                "label": "Orbital", "layer": 99})
        row = self.ds.load_reference("ops_domain")[0]
        with self.assertRaises(ValidationError):
            self.ds.update_reference_entry("ops_domain", row["id"],
                                           {"layer": -1})
        self.assertEqual(self.ds.get_reference_by_id("ops_domain", row["id"])
                         ["layer"], row["layer"])


if __name__ == "__main__":
    unittest.main()
