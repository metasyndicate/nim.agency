"""The installer subsystem: path resolution, dependency groups, the
launcher's interpreter choice, and a real create / doctor / remove cycle
in a temporary home (no network: an instance with no groups)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import REPO  # noqa: F401  (sys.path)

from lib.install import doctor, instance, launch, paths


class HomeCase(unittest.TestCase):
    """Each test gets its own NIM_HOME and a fresh home resolution."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-install-"))
        env = mock.patch.dict(os.environ, {"NIM_HOME": str(self.tmp / "home")})
        env.start()
        for name in ("NIM_VENV", "NIM_NO_VENV", "NIM_IN_VENV"):
            os.environ.pop(name, None)
        self.addCleanup(env.stop)
        paths.reset()
        self.addCleanup(paths.reset)
        self.addCleanup(shutil.rmtree, self.tmp, True)


class TestPlatformDirs(unittest.TestCase):

    def test_conventions(self):
        home = Path.home()
        mac = paths.platform_dirs(platform="darwin")
        self.assertEqual(mac["data"], home / "Library/Application Support/nim")
        self.assertEqual(mac["log"], home / "Library/Logs/nim")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg/data",
                                          "XDG_STATE_HOME": "relative/ignored"}):
            linux = paths.platform_dirs(platform="linux")
        self.assertEqual(linux["data"], Path("/xdg/data/nim"))
        self.assertEqual(linux["state"], home / ".local/state/nim")   # relative ignored
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "/win/local"}):
            win = paths.platform_dirs(platform="win32")
        self.assertEqual(win["data"], Path("/win/local/nim"))
        self.assertEqual(win["cache"], Path("/win/local/nim/Cache"))


class TestResolution(HomeCase):

    def test_env_wins_and_is_reported(self):
        resolved = paths.resolve_home()
        self.assertEqual(resolved.path, self.tmp / "home")
        self.assertEqual(resolved.source, "env NIM_HOME")
        self.assertEqual(paths.resolve_home(str(self.tmp / "flag")).source, "flag --home")

    def test_unwritable_candidate_is_skipped(self):
        locked = self.tmp / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        if os.access(locked, os.W_OK):           # running as root: cannot lock
            self.skipTest("directory permissions not enforced")
        resolved = paths.resolve_home(str(locked / "home"))
        self.assertIn("skipped", resolved.candidates[0].verdict)
        self.assertNotEqual(resolved.path, locked / "home")

    def test_probing_creates_nothing(self):
        target = self.tmp / "deep" / "not" / "here"
        self.assertIsNone(paths.probe(target))
        self.assertFalse((self.tmp / "deep").exists())

    def test_venv_resolution(self):
        resolved = paths.resolve_venv(self.tmp / "home", "x")
        self.assertEqual(resolved.path, self.tmp / "home" / "venvs" / "x")
        self.assertTrue(str(paths.venv_python(resolved.path)).startswith(
            str(resolved.path)))


class TestDependencyGroups(unittest.TestCase):

    def test_groups_expand_includes(self):
        groups = instance.dependency_groups()
        self.assertIn("jsonschema>=4", groups["validation"])
        self.assertEqual(set(groups["all"]),
                         set(groups["validation"]) | set(groups["remote"]))

    def test_requirement_names(self):
        self.assertEqual(instance.requirement_name("jsonschema>=4"), "jsonschema")
        self.assertEqual(instance.requirement_name("asyncssh"), "asyncssh")

    def test_unknown_group_is_refused(self):
        with self.assertRaisesRegex(instance.InstallError, "no dependency group"):
            instance.plan_create("t", ("telepathy",))


class TestLauncher(HomeCase):

    def write_receipt(self, python: Path):
        folder = self.tmp / "home" / "installs"
        folder.mkdir(parents=True)
        (folder / "r.json").write_text(json.dumps({
            "name": "default", "paths": {"python": str(python)}}))

    def test_runs_here_without_an_instance(self):
        self.assertIsNone(launch.target(["nim.agency", "data", "check"]))

    def test_reexecs_into_the_instance(self):
        fake = self.tmp / "venv" / "bin" / "python"
        fake.parent.mkdir(parents=True)
        fake.write_text("")
        self.write_receipt(fake)
        self.assertEqual(launch.target(["nim.agency", "data"]), fake)
        self.assertIsNone(launch.target(["nim.agency", "install", "doctor"]))
        with mock.patch.dict(os.environ, {"NIM_NO_VENV": "1"}):
            self.assertIsNone(launch.target(["nim.agency", "data"]))
        with mock.patch.dict(os.environ, {"NIM_IN_VENV": "1"}):
            self.assertIsNone(launch.target(["nim.agency", "data"]))

    def test_missing_interpreter_runs_here(self):
        self.write_receipt(self.tmp / "gone" / "python")
        self.assertIsNone(launch.target(["nim.agency", "data"]))


@unittest.skipIf(sys.version_info < instance.MIN_PYTHON, "installer needs 3.11+")
class TestInstanceCycle(HomeCase):
    """A real venv (with pip from ensurepip; no network: no groups)."""

    def test_create_doctor_remove(self):
        plan = instance.plan_create("default", ())
        self.assertEqual(plan["home"].path, self.tmp / "home")
        receipt = instance.create(plan, echo=lambda *_: None)
        venv_dir = Path(receipt["paths"]["venv"])
        self.assertTrue((venv_dir / "pyvenv.cfg").exists())
        mirror = json.loads((venv_dir / instance.RECEIPT_NAME).read_text())
        self.assertEqual(mirror["id"], receipt["id"])
        self.assertEqual(receipt["resolution"]["home"], "env NIM_HOME")

        results = {c.id: c for c in doctor.run("default")}
        self.assertEqual(results["venv"].status, "ok", results["venv"].detail)
        self.assertEqual(results["receipt"].status, "ok")
        self.assertEqual(launch.instance_python(), Path(receipt["paths"]["python"]))

        with self.assertRaisesRegex(instance.InstallError, "exists"):
            instance.create(plan, echo=lambda *_: None)

        instance.remove("default", echo=lambda *_: None)
        self.assertFalse(venv_dir.exists())
        self.assertIsNone(instance.find("default"))

    def test_remove_refuses_a_non_venv(self):
        plan = instance.plan_create("default", ())
        receipt = instance.create(plan, echo=lambda *_: None)
        venv_dir = Path(receipt["paths"]["venv"])
        (venv_dir / "pyvenv.cfg").unlink()
        with self.assertRaisesRegex(instance.InstallError, "not a virtual environment"):
            instance.remove("default", echo=lambda *_: None)
        self.assertTrue(venv_dir.exists())


if __name__ == "__main__":
    unittest.main()
