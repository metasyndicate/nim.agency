"""`agent generate --dry-run` and `agent show --from`: extraction without a write."""

import contextlib
import io
import json
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest import mock

from tests.support import DatastoreCase

from lib.cli import agent as agent_cmd


class TestAgentDryRun(DatastoreCase):
    def run_cmd(self, func, **kw):
        out = io.StringIO()
        with mock.patch.object(agent_cmd, "get_datastore", return_value=self.ds), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = func(Namespace(**kw))
        return rc, out.getvalue()

    def gen(self, **kw):
        args = dict(n=1, dry_run=True, seed=11, format="json") | kw
        return self.run_cmd(agent_cmd.cmd_generate, **args)

    def test_dry_run_saves_nothing(self):
        rc, out = self.gen(n=2)
        self.assertEqual(rc, 0)
        self.assertEqual(len(json.loads(out)), 2)
        self.assertEqual(self.ds.query("agent").data, [])

    def test_seed_is_reproducible(self):
        a = json.loads(self.gen()[1])["identity"]["codename"]
        b = json.loads(self.gen()[1])["identity"]["codename"]
        self.assertEqual(a, b)

    def test_show_from_file_renders_unsaved(self):
        _, doc = self.gen()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.json"
            path.write_text(doc)
            rc, out = self.run_cmd(agent_cmd.cmd_show, codename=None,
                                   from_file=str(path), format="text")
        self.assertEqual(rc, 0)
        self.assertIn(json.loads(doc)["identity"]["codename"], out)
        self.assertIn("TONE:", out)

    def test_show_needs_exactly_one_source(self):
        rc, _ = self.run_cmd(agent_cmd.cmd_show, codename=None,
                             from_file=None, format="text")
        self.assertEqual(rc, 1)
