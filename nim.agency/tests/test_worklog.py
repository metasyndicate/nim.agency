"""Work-queue access observability: audit trail + ceremony formats."""

import json
import unittest

from tests.support import DatastoreCase

from lib.config import get_config
from lib.data.project import create_project, find_project
from lib.data.worklog import (
    log_queue_access, project_record_path, queue_banner, queue_log_path,
    repo_relative, source_line,
)


class TestWorklog(DatastoreCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cfg = get_config()
        cls.original_dir = cls.cfg["logging"]["dir"]
        cls.cfg["logging"]["dir"] = str(cls.tmp / "oplogs")
        cls.project = create_project(cls.ds, "worklog-test", "Worklog",
                                     worktree="/tmp")

    @classmethod
    def tearDownClass(cls):
        cls.cfg["logging"]["dir"] = cls.original_dir
        super().tearDownClass()

    def _entries(self):
        path = queue_log_path()
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text().splitlines() if line.strip()]

    def test_banner_convention(self):
        record = project_record_path(self.ds, self.project)
        line = queue_banner(self.project, record)
        self.assertTrue(line.startswith(
            "Processing Work Queue: PROJECT worklog-test [pri 5] ("))
        self.assertIn(self.project["id"], line)
        self.assertIn("reading queue source: spec:x (",
                      source_line("spec:x", "/tmp/spec.md"))

    def test_access_entries_are_comprehensive(self):
        before = len(self._entries())
        log_queue_access("cycle", project=self.project,
                         path=project_record_path(self.ds, self.project),
                         detail="composing cycle 1")
        entries = self._entries()
        self.assertEqual(len(entries), before + 1)
        entry = entries[-1]
        for key in ("at", "operation", "operator", "pid", "project",
                    "project_id", "priority", "path", "detail"):
            self.assertIn(key, entry)
        self.assertEqual(entry["operation"], "cycle")
        self.assertEqual(entry["project"], "worklog-test")

    def test_resolution_is_audited(self):
        before = len(self._entries())
        find_project(self.ds, "worklog-test")
        entries = self._entries()
        self.assertGreater(len(entries), before)
        self.assertEqual(entries[-1]["operation"], "resolve")
        self.assertEqual(entries[-1]["detail"], "ref 'worklog-test'")

    def test_repo_relative_falls_back_to_absolute(self):
        self.assertEqual(repo_relative("/somewhere/else/x.json"),
                         "/somewhere/else/x.json")


if __name__ == "__main__":
    unittest.main()
