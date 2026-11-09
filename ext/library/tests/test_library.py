"""Offline tests: profile creation, key mapping, group roster, index, check.

    PYTHONPATH=lib python3 -m unittest discover -s tests
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from library.cli import main  # noqa: E402
from library.store import parse_keys  # noqa: E402
from library.util import LibraryError  # noqa: E402


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-library-"))
        self.root = self.tmp / "lib"
        raw = self.tmp / "raw"
        (raw / "img" / "sub").mkdir(parents=True)
        (raw / "img" / "a.jpg").write_bytes(b"A")
        (raw / "img" / "sub" / "b.PNG").write_bytes(b"B")
        (raw / "img" / "copy.jpg").write_bytes(b"A")          # duplicate of a.jpg
        (raw / "img" / "a.txt").write_text("caption")         # wrong kind for images
        (raw / "img" / ".DS_Store").write_bytes(b"x")         # ignored
        (raw / "memoir.md").write_text("Jody played lead.")
        self.raw = raw

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def new_jody(self, *extra):
        return self.run_cli("new", "profile", "--type", "character", "--tag", "LK5",
                            "--name", "Jody", "--images", str(self.raw / "img"),
                            "--text", str(self.raw / "memoir.md"),
                            "--keys", "[Instrument:Lead Guitar,Voice:Tenor,Era:1990s]", *extra)

    def test_keys_parse(self):
        self.assertEqual(parse_keys("[Instrument:Lead Guitar, Voice=Tenor]"),
                         [("Instrument", "Lead Guitar"), ("Voice", "Tenor")])
        with self.assertRaises(LibraryError):
            parse_keys("Instrument")

    def test_new_profile_layout_and_mapping(self):
        rc, out, err = self.new_jody()
        self.assertEqual(rc, 0, err)
        pdir = self.root / "lk5" / "profiles" / "jody"
        p = json.loads((pdir / "profile.json").read_text())
        self.assertEqual(p["id"], "lk5/jody")
        self.assertEqual(p["trigger"], "lk5jody")
        self.assertEqual(p["roles"][0]["roleName"], "Lead Guitar")
        self.assertEqual(p["voiceType"], "Tenor")
        self.assertEqual(p["attributes"], {"Era": "1990s"})
        self.assertTrue((pdir / "sources" / "images" / "img").is_symlink())
        self.assertIn("Era kept in attributes", out)
        g = json.loads((self.root / "lk5" / "group.json").read_text())
        self.assertEqual(g["members"], [{"slug": "jody", "name": "Jody", "roles": ["Lead Guitar"]}])

    def test_new_refuses_existing_unless_update(self):
        self.new_jody()
        rc, _, err = self.new_jody()
        self.assertEqual(rc, 1)
        self.assertIn("--update", err)
        rc, _, err = self.run_cli("new", "profile", "--type", "character", "--tag", "LK5",
                                  "--name", "Jody", "--keys", "Role:Backing Vocals", "--update")
        self.assertEqual(rc, 0, err)
        p = json.loads((self.root / "lk5/profiles/jody/profile.json").read_text())
        self.assertEqual([r["roleName"] for r in p["roles"]], ["Lead Guitar", "Backing Vocals"])
        self.assertEqual(len(p["sources"]), 2)

    def test_group_profile(self):
        self.new_jody()
        rc, _, err = self.run_cli("new", "profile", "--type", "group", "--tag", "LK5", "--name", "LK5")
        self.assertEqual(rc, 0, err)
        g = json.loads((self.root / "lk5" / "group.json").read_text())
        self.assertEqual(g["profile"], "lk5")
        self.assertEqual(len(g["members"]), 1)
        p = json.loads((self.root / "lk5/profiles/lk5/profile.json").read_text())
        self.assertEqual((p["@type"], p["trigger"], p["class"]), ("MusicGroup", "lk5group", "band"))

    def test_index_hashes_dedupes_and_skips(self):
        self.new_jody()
        rc, out, err = self.run_cli("index", "lk5/jody")
        self.assertEqual(rc, 0, err)
        idx = json.loads((self.root / "lk5/profiles/jody/index.json").read_text())
        self.assertEqual(idx["counts"], {"text": 1, "images": 2, "duplicates": 1, "skipped": 1})
        dup = [i for i in idx["items"] if "duplicate_of" in i]
        self.assertEqual(dup[0]["path"], "copy.jpg")
        self.assertTrue(idx["skipped"][0].endswith("a.txt"))
        m = json.loads((self.root / "lk5/profiles/jody/manifest.json").read_text())
        self.assertEqual([a["activity"] for a in m["activities"]], ["new", "index"])

    def test_list_and_check(self):
        self.new_jody()
        self.run_cli("index", "lk5")
        rc, out, _ = self.run_cli("list")
        self.assertIn("lk5/jody", out)
        self.assertIn("Lead Guitar", out)
        rc, out, _ = self.run_cli("list", "lk5/jody")
        self.assertEqual(len(out.strip().splitlines()), 4)
        rc, out, _ = self.run_cli("check")
        self.assertEqual((rc, out.strip()), (0, "ok"))

    def test_check_flags_broken_link(self):
        self.new_jody()
        (self.raw / "memoir.md").unlink()
        rc, out, _ = self.run_cli("check")
        self.assertEqual(rc, 1)
        self.assertIn("broken source link", out)


if __name__ == "__main__":
    unittest.main()
