"""`stage`: kohya folder layout, accepted-only selection, repeats, link, schema."""

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from library import stage as stage_mod  # noqa: E402
from library.cli import main  # noqa: E402
from library.util import config  # noqa: E402


class StageBase(unittest.TestCase):
    """profile + fake init fixture; no tests (shared with test_train)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-library-stage-"))
        self.root = self.tmp / "lib"
        self.cfg = copy.deepcopy(config())
        self.cfg["stage"]["datasets"] = str(self.tmp / "datasets")
        patcher = mock.patch.object(stage_mod, "config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertEqual(self.cli("new", "profile", "--type", "character", "--tag", "LK5",
                                  "--name", "Jody")[0], 0)
        self.pdir = self.root / "lk5" / "profiles" / "jody"

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def fake_init(self, verdicts):
        """verdicts: {id: (status, override, with_face)}"""
        imgs = self.pdir / "stage" / "images"
        imgs.mkdir(parents=True)
        images = []
        for n, (status, override, face) in verdicts.items():
            outs = []
            for f, variant in [(f"{n}.png", "full")] + ([(f"{n}-face.png", "face")] if face else []):
                (imgs / f).write_bytes(b"png" + f.encode())
                cap = "lk5jody person" + (", close-up portrait" if variant == "face" else "")
                (imgs / f).with_suffix(".txt").write_text(cap + "\n")
                outs.append({"file": f, "variant": variant, "width": 512, "height": 512, "caption": cap})
            rec = {"id": n, "source": f"sources/images/raw/{n}.jpg", "status": status, "reasons": [],
                   "outputs": outs}
            if override:
                rec["override"] = override
            images.append(rec)
        doc = {"$schema": "urn:nim:library:1#/$defs/init", "profile": "lk5/jody",
               "generated": "2026-11-08T00:00:00+00:00", "settings": {}, "counts": {},
               "images": images, "text": []}
        (self.pdir / "stage" / "init.json").write_text(json.dumps(doc))


class StageCase(StageBase):
    def test_accepted_only_and_layout(self):
        self.fake_init({"a" * 12: ("ok", None, True), "b" * 12: ("review", "ok", False),
                        "c" * 12: ("review", None, False), "d" * 12: ("ok", "exclude", False)})
        rc, out, err = self.cli("stage", "lk5/jody")
        self.assertEqual(rc, 0, err)
        doc = json.loads((self.pdir / "stage" / "stage.json").read_text())
        self.assertEqual(doc["counts"]["images"], 3)          # a full, a face, b full
        self.assertEqual(doc["repeats"], 34)                  # ceil(100 / 3)
        self.assertEqual(doc["folder"], "34_lk5jody person")
        files = sorted(p.name for p in (self.pdir / "stage/dataset/img" / doc["folder"]).iterdir())
        self.assertEqual(files, ["aaaaaaaaaaaa-face.png", "aaaaaaaaaaaa-face.txt", "aaaaaaaaaaaa.png",
                                 "aaaaaaaaaaaa.txt", "bbbbbbbbbbbb.png", "bbbbbbbbbbbb.txt"])
        self.assertIn("still in review", " ".join(doc["warnings"]))
        link = self.tmp / "datasets" / "lk5-jody"
        self.assertTrue(link.is_symlink())
        self.assertTrue((link / "img" / doc["folder"] / "bbbbbbbbbbbb.png").exists())
        self.assertEqual(self.cli("check", "lk5/jody")[0], 0)

    def test_repeats_override_and_rebuild(self):
        self.fake_init({"a" * 12: ("ok", None, False)})
        self.assertEqual(self.cli("stage", "lk5/jody", "--repeats", "40")[0], 0)
        self.assertEqual(self.cli("stage", "lk5/jody", "--repeats", "5", "--no-link")[0], 0)
        dirs = [p.name for p in (self.pdir / "stage/dataset/img").iterdir()]
        self.assertEqual(dirs, ["5_lk5jody person"])           # old folder gone
        doc = json.loads((self.pdir / "stage" / "stage.json").read_text())
        self.assertEqual(doc["counts"]["steps"], 1 * 5 * 10)
        self.assertNotIn("link", doc)

    def test_nothing_accepted(self):
        self.fake_init({"a" * 12: ("review", None, False)})
        rc, _, err = self.cli("stage", "lk5/jody")
        self.assertEqual(rc, 1)
        self.assertIn("1 still in review", err)

    def test_refuses_to_replace_real_dir(self):
        self.fake_init({"a" * 12: ("ok", None, False)})
        (self.tmp / "datasets" / "lk5-jody").mkdir(parents=True)
        rc, _, err = self.cli("stage", "lk5/jody")
        self.assertEqual(rc, 1)
        self.assertIn("not a link", err)


if __name__ == "__main__":
    unittest.main()
