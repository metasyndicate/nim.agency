"""`train`: run the existing train_lora.sh on a profile's staged dataset.

The dataset name is the stage link (<tag>-<slug>); epochs and the output name are
appended as sd-scripts flags (argparse: last one wins over the script's defaults).
Each run writes train/<run>.log (full output) and train/<run>.json (command,
steps, timing, output file + sha256), and appends a `train` activity.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .store import Library
from .util import SCHEMA_ID, LibraryError, config, dump_json, load_json, now, sha256_file
from .validate import validate


def settings() -> dict:
    return {k: v for k, v in config()["train"].items() if not k.startswith("_")}


def last_speed(pdir: Path) -> float | None:
    """seconds per step from this profile's latest finished run, if any."""
    runs = sorted((pdir / "train").glob("*.json"))
    for path in reversed(runs):
        rec = load_json(path)
        if rec.get("sec_per_step") is not None:
            return rec["sec_per_step"]
    return None


def busy() -> list[str]:
    """Other GPU/memory users train_lora.sh asks you to stop first."""
    found = []
    r = subprocess.run(["pgrep", "-f", "launch.py"], capture_output=True, text=True)
    if r.stdout.split():
        found.append(f"Forge/webui running ({len(r.stdout.split())} process(es))")
    r = subprocess.run(["ollama", "ps"], capture_output=True, text=True) if _has("ollama") else None
    if r and len(r.stdout.strip().splitlines()) > 1:
        found.append("Ollama has a model loaded")
    return found


def _has(cmd: str) -> bool:
    return subprocess.run(["/usr/bin/which", cmd], capture_output=True).returncode == 0


def plan(lib: Library, ref: str, epochs: int | None, name: str | None, extra: list[str]) -> dict:
    pdir = lib.profile_dir(ref)
    stage_path = pdir / "stage" / "stage.json"
    if not stage_path.exists():
        raise LibraryError(f"{ref} is not staged yet: nim.library stage {ref}")
    stage = load_json(stage_path)
    if "link" not in stage:
        raise LibraryError(f"{ref} was staged with --no-link; train_lora.sh needs the dataset link")
    s = settings()
    epochs = epochs or s["epochs"]
    dataset = Path(stage["link"]).name
    name = name or dataset
    steps = stage["counts"]["images"] * stage["repeats"] * epochs
    speed = last_speed(pdir) or s["sec_per_step"]
    return {
        "dataset": dataset, "name": name, "epochs": epochs, "steps": steps,
        "estimate_seconds": round(steps * speed), "sec_per_step_assumed": speed,
        "output": str(Path(s["lora_dir"]) / f"{name}.safetensors"),
        "command": [s["script"], dataset, "--max_train_epochs", str(epochs),
                    "--output_name", name, *extra]}


def run_train(lib: Library, ref: str, p: dict, stream=sys.stdout) -> dict:
    pdir = lib.profile_dir(ref)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tdir = pdir / "train"
    tdir.mkdir(exist_ok=True)
    log = tdir / f"{run}.log"
    started, t0 = now(), time.monotonic()
    with open(log, "wb") as fh:
        proc = subprocess.Popen(p["command"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            fh.write(chunk)
            fh.flush()
            stream.write(chunk.decode(errors="replace"))
            stream.flush()
        rc = proc.wait()
    seconds = round(time.monotonic() - t0)
    out = Path(p["output"])
    ok = rc == 0 and out.exists()
    rec = {"$schema": f"{SCHEMA_ID}#/$defs/train", "profile": ref, "run": run,
           "started": started, "finished": now(), "status": "ok" if ok else "failed",
           "returncode": rc, "command": p["command"], "dataset": p["dataset"], "name": p["name"],
           "epochs": p["epochs"], "steps": p["steps"], "seconds": seconds,
           "log": f"train/{run}.log"}
    if ok:
        rec["output"] = str(out)
        rec["sha256"] = sha256_file(out)
        rec["sec_per_step"] = round(seconds / max(p["steps"], 1), 1)
    errs = validate(rec, "train")
    if errs:
        raise LibraryError("train record does not match the schema:\n  " + "\n  ".join(errs))
    generated = {f"train/{run}.json": dump_json(tdir / f"{run}.json", rec),
                 f"train/{run}.log": sha256_file(log)}
    if ok:
        generated[str(out)] = rec["sha256"]
    lib.record(ref, "train", ["stage/stage.json"], generated,
               note=f"{rec['status']}: {p['steps']} steps in {seconds}s -> {p['name']}")
    return rec
