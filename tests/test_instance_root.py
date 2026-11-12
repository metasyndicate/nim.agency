"""The instance root (plan.101526.instance.md N0): scope bases per platform,
enumeration order, explicit values validated and refused with a
suggestion, the checkout coupling, and every validation check's truth
table. Each test runs against temp scope bases and a temp checkout."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO  # noqa: F401  (sys.path)

from lib.install import paths
from lib.install.root import Root


class RootCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-root-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.checkout = self.tmp / "checkout"
        (self.checkout / "data").mkdir(parents=True)
        self.bases = {"system": self.tmp / "system", "user": self.tmp / "user"}
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("NIM_HOME", None)

    def root(self, **kw) -> Root:
        return Root("default", checkout=self.checkout, bases=self.bases, **kw)

    def statuses(self, candidate) -> dict:
        return {c.id: c.status for c in candidate.checks}


class TestScopes(unittest.TestCase):

    def test_system_bases(self):
        self.assertEqual(Root.Scopes.system("linux"), Path("/var/local/nim/agency"))
        self.assertEqual(Root.Scopes.system("darwin"),
                         Path("/Library/Application Support/nim/agency"))
        with mock.patch.dict(os.environ, {"PROGRAMDATA": "/pd"}):
            self.assertEqual(Root.Scopes.system("win32"), Path("/pd/nim/agency"))

    def test_user_bases_follow_platform_dirs(self):
        for platform in ("linux", "darwin", "win32"):
            self.assertEqual(Root.Scopes.user(platform),
                             paths.platform_dirs(platform=platform)["data"] / "agency")

    def test_names_are_appended_except_project(self):
        root = Root("lab", checkout=Path("/co"), platform="linux")
        self.assertEqual([str(c.path) for c in root.candidates()],
                         ["/var/local/nim/agency/lab",
                          str(paths.platform_dirs(platform="linux")["data"] / "agency/lab"),
                          "/co/data/local"])


class TestEnumeration(RootCase):

    def test_order_and_first_valid_wins(self):
        result = self.root().resolve()
        self.assertEqual(result.root, self.tmp / "system" / "default")
        self.assertEqual(result.scope, "system")
        self.assertEqual([c.scope for c in result.candidates], ["system", "user", "project"])
        self.assertEqual([c.verdict for c in result.candidates], ["chosen", "valid", "valid"])

    def test_invalid_system_falls_to_user(self):
        self.bases["system"].write_text("not a directory")
        result = self.root().resolve()
        self.assertEqual(result.scope, "user")
        self.assertEqual(result.candidates[0].verdict, "passed over")
        self.assertEqual(self.statuses(result.candidates[0])["directory"], "fail")

    def test_project_scope_is_the_last_resort(self):
        self.bases["system"].write_text("x")
        self.bases["user"].write_text("x")
        result = self.root().resolve()
        self.assertEqual(result.root, self.checkout / "data" / "local")
        self.assertEqual(result.source, "project scope")

    def test_nothing_valid(self):
        for base in self.bases.values():
            base.write_text("x")
        (self.checkout / "data" / "local").write_text("x")
        result = self.root().resolve()
        self.assertIsNone(result.root)
        self.assertEqual(result.source, "none valid")


class TestExplicit(RootCase):

    def test_flag_beats_env(self):
        os.environ["NIM_HOME"] = str(self.tmp / "env")
        result = self.root().resolve(str(self.tmp / "flag"))
        self.assertEqual((result.root, result.source), (self.tmp / "flag", "flag --home"))
        self.assertEqual(result.scope, "custom")

    def test_env_when_no_flag(self):
        os.environ["NIM_HOME"] = str(self.tmp / "env")
        self.assertEqual(self.root().resolve().source, "env NIM_HOME")

    def test_relative_flag_is_refused_with_a_suggestion(self):
        result = self.root().resolve("relative/root")
        self.assertTrue(result.refused)
        self.assertIsNone(result.root)
        self.assertEqual(result.candidates[0].failures[0].id, "absolute")
        self.assertEqual(result.suggestion.root, self.tmp / "system" / "default")

    def test_refusal_never_uses_the_suggestion(self):
        bad = self.tmp / "bad"
        bad.write_text("file")
        result = self.root().resolve(str(bad))
        self.assertTrue(result.refused)
        self.assertIsNone(result.root)
        self.assertIsNotNone(result.suggestion.root)

    def test_explicit_scope_is_named_when_it_matches(self):
        result = self.root().resolve(str(self.tmp / "user" / "default"))
        self.assertEqual(result.scope, "user")


class TestCoupling(RootCase):

    def test_link(self):
        target = self.tmp / "elsewhere"
        target.mkdir()
        (self.checkout / "data" / "local").symlink_to(target)
        result = self.root().resolve()
        self.assertEqual((result.root, result.source), (target, "coupling data/local (link)"))

    def test_pointer(self):
        target = self.tmp / "pointed"
        (self.checkout / "data" / "local.json").write_text(json.dumps({"root": str(target)}))
        result = self.root().resolve()
        self.assertEqual((result.root, result.source), (target, "coupling data/local.json"))

    def test_project_root_directory(self):
        (self.checkout / "data" / "local").mkdir()
        result = self.root().resolve()
        self.assertEqual(result.source, "coupling data/local (project root)")
        self.assertEqual(result.scope, "project")

    def test_explicit_values_beat_the_coupling(self):
        (self.checkout / "data" / "local").mkdir()
        os.environ["NIM_HOME"] = str(self.tmp / "env")
        self.assertEqual(self.root().resolve().source, "env NIM_HOME")


class TestChecks(RootCase):

    def test_world_writable_is_refused(self):
        loose = self.tmp / "loose"
        loose.mkdir()
        loose.chmod(0o777)
        result = self.root().resolve(str(loose))
        self.assertTrue(result.refused)
        self.assertEqual(self.statuses(result.candidates[0])["mode"], "fail")

    def test_claimed_by_another_checkout(self):
        taken = self.tmp / "taken"
        taken.mkdir(mode=0o700)
        (taken / "instance.json").write_text(json.dumps({"id": "x", "code_root": "/other"}))
        result = self.root().resolve(str(taken))
        self.assertEqual(result.candidates[0].failures[0].id, "claimed")

    def test_claimed_by_this_checkout(self):
        mine = self.tmp / "mine"
        mine.mkdir(mode=0o700)
        (mine / "instance.json").write_text(
            json.dumps({"id": "y", "code_root": str(self.checkout)}))
        self.assertFalse(self.root().resolve(str(mine)).refused)

    def test_permissions_honored_here(self):
        if os.name == "nt":
            self.skipTest("POSIX modes")
        result = self.root().resolve(str(self.tmp / "fresh"))
        self.assertEqual(self.statuses(result.candidates[0])["permissions"], "ok")

    def test_validation_creates_nothing(self):
        target = self.tmp / "deep" / "not" / "here"
        self.root().resolve(str(target))
        self.assertFalse((self.tmp / "deep").exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".nim")], [])

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_inside_git_must_be_ignored(self):
        subprocess.run(["git", "init", "-q", str(self.checkout)], check=True)
        local = self.checkout / "data" / "local"
        self.assertEqual(self.statuses(self.root().resolve(str(local)).candidates[0])
                         ["shippable"], "fail")
        (self.checkout / ".gitignore").write_text("data/local\n")
        self.assertEqual(self.statuses(self.root().resolve(str(local)).candidates[0])
                         ["shippable"], "ok")


class TestEstablish(RootCase):
    """N1: identity, class directories, coupling; idempotent; adoption."""

    def couple(self, cli=None, adopt=False, root=None):
        root = root or self.root()
        return root.establish(root.resolve(cli, adopt=adopt))

    def test_first_couple_builds_everything(self):
        record, actions = self.couple()
        target = self.tmp / "system" / "default"
        for name in Root.CLASSES:
            self.assertTrue((target / name).is_dir(), name)
            self.assertEqual((target / name).stat().st_mode & 0o777, 0o700)
        on_disk = json.loads((target / "instance.json").read_text())
        self.assertEqual(on_disk, record)
        self.assertEqual(record["code_root"], str(self.checkout))
        self.assertEqual((record["scope"], record["name"]), ("system", "default"))
        link = self.checkout / "data" / "local"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), target.resolve())
        self.assertTrue(any(a.startswith("link") for a in actions))

    def test_recouple_is_idempotent_and_follows_the_link(self):
        first, _ = self.couple()
        again, actions = self.couple()
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(again["source"], "coupling data/local (link)")
        self.assertEqual([a.split()[0] for a in actions], ["update"])

    def test_dry_run_changes_nothing(self):
        root = self.root()
        record, actions = root.establish(root.resolve(), dry_run=True)
        self.assertTrue(actions)
        self.assertFalse((self.tmp / "system").exists())
        self.assertFalse((self.checkout / "data" / "local").exists())

    def test_refused_resolution_raises_with_the_suggestion(self):
        from lib.install.root import RootError
        root = self.root()
        with self.assertRaisesRegex(RootError, "pass it with --home"):
            root.establish(root.resolve("relative"))

    def test_another_checkout_must_adopt(self):
        record, _ = self.couple(str(self.tmp / "shared"))
        other = self.tmp / "other"
        (other / "data").mkdir(parents=True)
        theirs = Root("default", checkout=other, bases=self.bases)
        self.assertTrue(theirs.resolve(str(self.tmp / "shared")).refused)
        adopted, actions = self.couple(str(self.tmp / "shared"), adopt=True, root=theirs)
        self.assertEqual(adopted["id"], record["id"])
        self.assertEqual(adopted["code_root"], str(other))
        self.assertEqual(adopted["history"][0]["code_root"], str(self.checkout))
        self.assertTrue(any(a.startswith("adopt") for a in actions))

    def test_relink_to_a_new_root(self):
        self.couple(str(self.tmp / "first"))
        _, actions = self.couple(str(self.tmp / "second"))
        self.assertTrue(actions[-1].startswith("relink"))
        self.assertEqual((self.checkout / "data" / "local").resolve(),
                         (self.tmp / "second").resolve())

    def test_project_scope_needs_no_link(self):
        self.bases["system"].write_text("x")
        self.bases["user"].write_text("x")
        record, actions = self.couple()
        local = self.checkout / "data" / "local"
        self.assertEqual(record["scope"], "project")
        self.assertTrue(local.is_dir() and not local.is_symlink())
        self.assertFalse(any(a.startswith("link") for a in actions))

    def test_project_root_blocks_coupling_elsewhere(self):
        from lib.install.root import RootError
        (self.checkout / "data" / "local").mkdir()
        with self.assertRaisesRegex(RootError, "project-scope root"):
            self.couple(str(self.tmp / "elsewhere"))

    def test_pointer_when_symlinks_are_unavailable(self):
        with mock.patch.object(Path, "symlink_to", side_effect=OSError(1, "not permitted")):
            _, actions = self.couple(str(self.tmp / "pointed"))
        pointer = self.checkout / "data" / "local.json"
        self.assertEqual(json.loads(pointer.read_text())["root"], str(self.tmp / "pointed"))
        self.assertIn("data/local.json", actions[-1])
        self.assertEqual(self.root().resolve().source, "coupling data/local.json")


class TestLayout(unittest.TestCase):

    def test_classes_and_venv(self):
        layout = Root.layout(Path("/r"))
        self.assertEqual(sorted(layout), sorted(Root.CLASSES + ("identity", "venv")))
        self.assertEqual(layout["venv"], Path("/r/runtime/venv"))
        self.assertEqual(layout["identity"], Path("/r/instance.json"))


if __name__ == "__main__":
    unittest.main()
