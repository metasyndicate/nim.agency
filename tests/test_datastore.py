"""Datastore: reference tables, schema discovery, validation posture."""

import unittest

from tests.support import DatastoreCase

try:
    import jsonschema  # noqa: F401
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


class TestReferenceTables(DatastoreCase):

    def test_project_status_vocabulary(self):
        rows = self.ds.load_reference("project_status")
        self.assertEqual(len(rows), 7)
        self.assertEqual({r["slug"] for r in rows if r["is_terminal"]},
                         {"complete", "archived"})
        self.assertEqual({r["slug"] for r in rows if r["is_active"]},
                         {"active"})

    def test_assignment_status_vocabulary(self):
        rows = self.ds.load_reference("assignment_status")
        self.assertEqual(len(rows), 11)
        self.assertEqual(
            self.ds.get_reference_by_id("assignment_status", 2)["slug"],
            "queued")

    def test_operator_defined_tables_registered(self):
        self.assertGreaterEqual(len(self.ds.load_reference("role_title")), 3)
        presets = self.ds.load_reference("task_preset")
        self.assertGreaterEqual(len(presets), 3)
        preset = self.ds.get_reference_by_slug("task_preset",
                                               "inventory_todos")
        self.assertTrue(preset["prompt"])

    def test_schema_discovery(self):
        schemas = self.ds.schema_manager.list_schemas()
        for name in ("project", "project_status", "role_title",
                     "task_preset"):
            self.assertIn(name, schemas)


class TestValidationPosture(DatastoreCase):

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validation_rejects_bad_documents(self):
        with self.assertRaises(ValueError):
            self.ds.create("project", {"slug": "Bad Slug!", "title": "x",
                                       "status_id": 1})
        with self.assertRaises(ValueError):
            self.ds.create("project", {"slug": "no-title", "status_id": 1})

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_https_refs_resolve_locally(self):
        doc = {"slug": "ref-check", "title": "Ref Check", "status_id": 1}
        created = self.ds.create("project", doc)
        errors = self.ds.schema_manager.validate("project", created)
        self.assertEqual(errors, [])

    @unittest.skipIf(HAS_JSONSCHEMA, "stdlib-only path")
    def test_stdlib_only_create_degrades_gracefully(self):
        created = self.ds.create("project", {"slug": "stdlib-only",
                                             "title": "x", "status_id": 1})
        self.assertTrue(created["id"])


if __name__ == "__main__":
    unittest.main()
