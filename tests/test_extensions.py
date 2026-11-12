"""Extension discovery (lib/extensions.py) and CLI mounting (lib/cli/ext.py)."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lib import extensions
from lib.cli import CORE, build_parser
from lib.cli import ext as ext_cmd


def make_ext(base: Path, dirname: str, executable: bool = True, exit_code: int = 0, **doc) -> Path:
    root = base / dirname
    (root / "bin").mkdir(parents=True)
    cmd = root / "bin" / "run"
    cmd.write_text(f"#!/bin/sh\necho \"$@\" > \"$(dirname \"$0\")/../args\"\nexit {exit_code}\n")
    if executable:
        cmd.chmod(cmd.stat().st_mode | stat.S_IXUSR)
    manifest = {"name": dirname, "version": "0.1.0", "summary": f"{dirname} ext", "command": "bin/run", **doc}
    (root / "extension.json").write_text(json.dumps(manifest))
    return root


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_truth_table(self):
        make_ext(self.base, "good", tests="tests")
        make_ext(self.base, "noexec", executable=False)
        make_ext(self.base, "renamed", name="other")
        make_ext(self.base, "partial", summary="")
        (self.base / "broken").mkdir()
        (self.base / "broken" / "extension.json").write_text("{not json")
        (self.base / "plain").mkdir()  # no manifest: not an extension, not a problem
        found, problems = extensions.discover(self.base)
        self.assertEqual([e.name for e in found], ["good"])
        self.assertEqual(found[0].tests, self.base / "good" / "tests")
        text = "\n".join(problems)
        self.assertEqual(len(problems), 4)
        for needle in ("not executable", "does not match directory", "missing summary", "broken"):
            self.assertIn(needle, text)

    def test_empty(self):
        self.assertEqual(extensions.discover(self.base / "absent"), ([], []))


class MountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.patch = mock.patch.object(extensions, "EXT_DIR", self.base)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_core_matches_parser(self):
        choices = next(a for a in build_parser()._actions if a.dest == "component").choices
        self.assertEqual(set(choices), set(CORE))

    def test_forward_passes_args_and_exit_code(self):
        root = make_ext(self.base, "demo", exit_code=3)
        self.assertEqual(ext_cmd.forward(["demo", "--root", "x", "list"], set(CORE)), 3)
        self.assertEqual((root / "args").read_text().strip(), "--root x list")

    def test_forward_ignores_core_and_unknown(self):
        make_ext(self.base, "lex")
        self.assertIsNone(ext_cmd.forward(["lex", "groups"], set(CORE)))
        self.assertIsNone(ext_cmd.forward(["nothing"], set(CORE)))
        self.assertIsNone(ext_cmd.forward([], set(CORE)))

    def test_shadowing_extension_not_mounted(self):
        make_ext(self.base, "lex")
        make_ext(self.base, "demo")
        with mock.patch("sys.stderr"):
            choices = next(a for a in build_parser()._actions if a.dest == "component").choices
        self.assertIn("demo", choices)
        self.assertEqual(set(choices) - {"demo"}, set(CORE))


if __name__ == "__main__":
    unittest.main()
