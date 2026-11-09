"""`init`: verdicts, captions kept across re-runs, overrides, memoir passages.
Image tests run the real worker and skip when its interpreter is absent."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from library.cli import main  # noqa: E402
from library.init import mentions, paragraphs, verdict  # noqa: E402
from library.util import config  # noqa: E402

PY = Path(config()["init"]["python"])


class InitCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nim-library-init-"))
        self.root = self.tmp / "lib"
        self.raw = self.tmp / "raw"
        self.raw.mkdir()
        (self.raw / "memoir.md").write_text(
            "We met in 1994.\n\nJody tuned the Strat twice.\n\nGreg and J.D. argued.\n")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--root", str(self.root), *args])
        return rc, out.getvalue(), err.getvalue()

    def make(self, images=False):
        args = ["new", "profile", "--type", "character", "--tag", "LK5", "--name", "Jody",
                "--text", str(self.raw / "memoir.md"), "--keys", "Alias:J.D."]
        if images:
            args += ["--images", str(self.raw / "img")]
        self.assertEqual(self.cli(*args)[0], 0)
        self.assertEqual(self.cli("index", "lk5/jody")[0], 0)

    def test_paragraphs_and_mentions(self):
        pars = paragraphs("a\nb\n\n\nJody  here\n")
        self.assertEqual(pars, ["a b", "Jody here"])
        self.assertEqual(mentions("jody and J.D.", ["Jody", "J.D."]), ["Jody", "J.D."])
        self.assertEqual(mentions("Jodyesque", ["Jody"]), [])

    def test_verdicts(self):
        p = {"name": "Jody"}
        self.assertEqual(verdict({"faces": [[0, 0, 9, 9]], "original": [800, 600]}, p, 384), ("ok", []))
        s, r = verdict({"faces": [], "original": [300, 300]}, p, 384)
        self.assertEqual((s, len(r)), ("review", 2))
        self.assertEqual(verdict({"error": "boom"}, p, 384), ("error", ["boom"]))

    def test_text_passages(self):
        self.make()
        rc, out, err = self.cli("init", "lk5/jody", "--only", "text")
        self.assertEqual(rc, 0, err)
        lines = (self.root / "lk5/profiles/jody/stage/text/passages.jsonl").read_text().splitlines()
        got = [json.loads(x) for x in lines]
        self.assertEqual([(p["n"], p["mentions"]) for p in got], [(1, ["Jody"]), (2, ["J.D."])])
        self.assertEqual(self.cli("check", "lk5/jody")[1].strip(), "ok")

    @unittest.skipUnless(PY.exists(), "image worker interpreter not available")
    def test_images_captions_and_overrides(self):
        import subprocess
        (self.raw / "img").mkdir()
        subprocess.run([str(PY), "-c",
                        "from PIL import Image; import sys;"
                        "Image.new('RGB',(1600,1200),(90,120,90)).save(sys.argv[1]);"
                        "Image.new('RGB',(200,200),(10,10,10)).save(sys.argv[2])",
                        str(self.raw / "img/big.jpg"), str(self.raw / "img/small.png")], check=True)
        self.make(images=True)
        rc, out, err = self.cli("init", "lk5/jody")
        self.assertEqual(rc, 0, err)
        stage = self.root / "lk5/profiles/jody/stage"
        doc = json.loads((stage / "init.json").read_text())
        big = next(i for i in doc["images"] if i["source"].endswith("big.jpg"))
        self.assertEqual(big["outputs"][0]["width"], 1024)         # long side capped
        self.assertIn("no frontal face found", big["reasons"])
        txt = stage / "images" / f"{big['id']}.txt"
        self.assertEqual(txt.read_text().strip(), "lk5jody person")

        txt.write_text("lk5jody person, on stage\n")               # hand edit survives
        self.cli("init", "lk5/jody", "--ok", big["id"][:6])
        self.cli("init", "lk5/jody")
        doc = json.loads((stage / "init.json").read_text())
        big = next(i for i in doc["images"] if i["id"] == big["id"])
        self.assertEqual(txt.read_text().strip(), "lk5jody person, on stage")
        self.assertEqual(big["outputs"][0]["caption_source"], "user")
        self.assertEqual(big["override"], "ok")
        self.assertEqual(doc["counts"]["ok"], 1)
        m = json.loads((self.root / "lk5/profiles/jody/manifest.json").read_text())
        self.assertEqual([a["activity"] for a in m["activities"]].count("init"), 3)


if __name__ == "__main__":
    unittest.main()
