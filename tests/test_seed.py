"""Tracked seed fixtures: safe to ship, installable, idempotent."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.support import REPO, DatastoreCase

from lib.data.seed import SEED_AGENTS_DIR, seed_fixture_agents

KEY_MARKERS = ("PRIVATE KEY", "ssh-ed25519", "ssh-rsa", "fingerprint",
               "vault_path")


class FixtureHygieneTest(unittest.TestCase):
    """The seed directory ships in git; key material must never land there."""

    def test_seed_dir_is_the_tracked_location(self):
        self.assertEqual(SEED_AGENTS_DIR,
                         REPO / "data/agency/seed/agents")

    def test_fixtures_exist(self):
        self.assertGreaterEqual(len(list(SEED_AGENTS_DIR.glob("*.json"))), 1)

    def test_no_key_material_anywhere_in_seed(self):
        for path in (REPO / "data/agency/seed").rglob("*"):
            if not path.is_file():
                continue
            self.assertNotRegex(path.name, r"^id_|\.pem$|\.key$",
                                f"key-like file shipped: {path}")
            text = path.read_text(errors="replace")
            if path.suffix == ".json":
                self.assertNotIn("crypto", json.loads(text), path.name)
                for marker in KEY_MARKERS:
                    self.assertNotIn(marker, text,
                                     f"{marker!r} in {path.name}")

    def test_fixtures_conform_to_agent_schema(self):
        from lib.data.schema import get_schema_manager
        sm = get_schema_manager()
        if not sm.can_validate():
            self.skipTest("jsonschema not installed")
        for path in SEED_AGENTS_DIR.glob("*.json"):
            with open(path) as f:
                self.assertEqual(sm.validate("agent", json.load(f)), [],
                                 path.name)

    def test_fixture_filename_matches_id(self):
        for path in SEED_AGENTS_DIR.glob("*.json"):
            with open(path) as f:
                self.assertEqual(json.load(f)["id"], path.stem)


class SeedInstallTest(DatastoreCase):
    """Installation into an isolated datastore - no enrollment here, since
    the vault is rooted at the project, not the temp datastore."""

    def setUp(self):
        # Fresh roster per test: install counts depend on an empty start.
        shutil.rmtree(self.tmp / "agents", ignore_errors=True)

    def test_install_then_reinstall_skips(self):
        expected = len(list(SEED_AGENTS_DIR.glob("*.json")))
        seeded, skipped = seed_fixture_agents(self.ds)
        self.assertEqual((len(seeded), len(skipped)), (expected, 0))
        self.assertEqual(self.ds.count("agent"), expected)

        seeded, skipped = seed_fixture_agents(self.ds)
        self.assertEqual((len(seeded), len(skipped)), (0, expected))
        self.assertEqual(self.ds.count("agent"), expected)

    def test_reinstall_preserves_accrued_state(self):
        seeded, _ = seed_fixture_agents(self.ds)
        agent_id = seeded[0]["id"]
        self.ds.update("agent", agent_id, {"economy": {"xp": 1}})
        seed_fixture_agents(self.ds)
        self.assertEqual(self.ds.read("agent", agent_id)["economy"]["xp"], 1)

    def test_fixture_with_crypto_is_refused(self):
        bad = Path(tempfile.mkdtemp(prefix="nim-seed-"))
        self.addCleanup(shutil.rmtree, bad, ignore_errors=True)
        src = next(SEED_AGENTS_DIR.glob("*.json"))
        record = json.loads(src.read_text())
        record["crypto"] = {"public_key": "x"}
        (bad / src.name).write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            seed_fixture_agents(self.ds, bad)


if __name__ == "__main__":
    unittest.main()
