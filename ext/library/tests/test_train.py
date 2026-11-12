"""`train`: command assembly, dry run, run record and manifest, using a fake
train_lora.sh that writes the LoRA file it is told to."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from library import train as train_mod  # noqa: E402
from test_stage import StageBase  # noqa: E402

FAKE = """#!/bin/bash
name=$1; shift; out=
while [[ $# -gt 0 ]]; do [[ $1 == --output_name ]] && out=$2; shift; done
echo "steps: 100%"; [[ $FAIL ]] && exit 3
printf 'lora' > "$LORA_DIR/$out.safetensors"
"""


class TrainCase(StageBase):
    def setUp(self):
        super().setUp()
        script = self.tmp / "train_lora.sh"
        script.write_text(FAKE)
        script.chmod(0o755)
        lora = self.tmp / "lora"
        lora.mkdir()
        self.cfg["train"].update(script=str(script), lora_dir=str(lora))
        os.environ["LORA_DIR"] = str(lora)
        for target in ("config", "busy"):
            p = mock.patch.object(train_mod, target,
                                  return_value=self.cfg if target == "config" else [])
            p.start()
            self.addCleanup(p.stop)
        self.fake_init({"a" * 12: ("ok", None, False), "b" * 12: ("ok", None, False)})
        self.assertEqual(self.cli("stage", "lk5/jody", "--repeats", "3")[0], 0)

    def test_dry_run(self):
        rc, out, err = self.cli("train", "lk5/jody", "--epochs", "2", "--dry-run")
        self.assertEqual(rc, 0, err)
        self.assertIn("12 steps (2 epochs)", out)
        self.assertIn("lk5-jody --max_train_epochs 2 --output_name lk5-jody", out)
        self.assertFalse((self.pdir / "train").exists())

    def test_run_records(self):
        rc, out, err = self.cli("train", "lk5/jody", "--epochs", "1", "--name", "trial",
                                "--", "--seed", "7")
        self.assertEqual(rc, 0, err)
        rec = json.loads(next((self.pdir / "train").glob("*.json")).read_text())
        self.assertEqual((rec["status"], rec["steps"], rec["name"]), ("ok", 6, "trial"))
        self.assertEqual(rec["command"][-2:], ["--seed", "7"])
        self.assertTrue(Path(rec["output"]).exists())
        self.assertIn("steps: 100%", (self.pdir / rec["log"]).read_text())
        acts = json.loads((self.pdir / "manifest.json").read_text())["activities"]
        self.assertEqual(acts[-1]["activity"], "train")
        # next estimate uses the measured speed
        self.assertEqual(train_mod.last_speed(self.pdir), rec["sec_per_step"])

    def test_failure_recorded(self):
        os.environ["FAIL"] = "1"
        self.addCleanup(os.environ.pop, "FAIL")
        rc, _, err = self.cli("train", "lk5/jody", "--epochs", "1")
        self.assertEqual(rc, 1)
        self.assertIn("exit 3", err)
        rec = json.loads(next((self.pdir / "train").glob("*.json")).read_text())
        self.assertEqual(rec["status"], "failed")


if __name__ == "__main__":
    unittest.main()
