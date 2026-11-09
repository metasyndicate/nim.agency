"""`prompt`: template rendering, LoRA selection, job grid, record + manifest
(generator mocked; the real worker needs the sd-scripts venv and minutes per image)."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from library import prompt as prompt_mod  # noqa: E402
from test_stage import StageBase  # noqa: E402


def fake_generate(p, out_dir):
    out_dir.mkdir(parents=True)
    for f in [j["file"] for j in p["jobs"]] + [p["sheet"]["file"]]:
        (out_dir / f).write_bytes(b"png")
    return [{"file": j["file"], "seconds": 1.5, "blank": False} for j in p["jobs"]]


class PromptCase(StageBase):
    def setUp(self):
        super().setUp()
        self.lora = self.tmp / "trial.safetensors"
        self.lora.write_bytes(b"lora")
        p = mock.patch.object(prompt_mod, "run_generate", side_effect=fake_generate)
        p.start()
        self.addCleanup(p.stop)

    def train_record(self, status="ok"):
        t = self.pdir / "train"
        t.mkdir(exist_ok=True)
        (t / "20261108T000000Z.json").write_text(json.dumps(
            {"status": status, "run": "20261108T000000Z", "output": str(self.lora)}))

    def test_render(self):
        prof = {"trigger": "lk5jody", "class": "person", "name": "Jody", "roles": [{"roleName": "Lead Guitar"}]}
        self.assertEqual(prompt_mod.render("{trigger} {class} playing {role}", prof),
                         "lk5jody person playing lead guitar")

    def test_needs_lora(self):
        rc, _, err = self.cli("prompt", "lk5/jody", "--dry-run")
        self.assertEqual(rc, 1)
        self.assertIn("no trained LoRA", err)
        self.train_record(status="failed")
        self.assertEqual(self.cli("prompt", "lk5/jody", "--dry-run")[0], 1)

    def test_run(self):
        self.train_record()
        rc, out, err = self.cli("prompt", "lk5/jody", "--prompt", "photo of {trigger} {class}",
                                "--weights", "0,0.5,1", "--seed", "9")
        self.assertEqual(rc, 0, err)
        run = next((self.pdir / "prompt").iterdir())
        doc = json.loads((run / "prompt.json").read_text())
        self.assertEqual([i["file"] for i in doc["images"]], ["p1-w0.png", "p1-w0.5.png", "p1-w1.png"])
        self.assertEqual({i["seed"] for i in doc["images"]}, {9})
        self.assertEqual(doc["images"][0]["prompt"], "photo of lk5jody person")
        self.assertEqual(doc["train_run"], "20261108T000000Z")
        acts = json.loads((self.pdir / "manifest.json").read_text())["activities"]
        self.assertEqual((acts[-1]["activity"], acts[-1]["used"]), ("prompt", ["train/20261108T000000Z.json"]))

    def test_explicit_lora(self):
        rc, out, err = self.cli("prompt", "lk5/jody", "--lora", str(self.lora), "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("4 images", out)      # 2 default prompts x 2 weights


if __name__ == "__main__":
    unittest.main()
