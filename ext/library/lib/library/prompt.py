"""`prompt`: fixed comparison prompts rendered with and without a profile's LoRA.

Each prompt template (etc/library.json prompt.prompts, or --prompt) is rendered at
every LoRA weight (default 0 and 1) with the same seed, so each row of the
contact sheet differs only by the LoRA. Writes prompt/<run>/: the PNGs,
sheet.png, and prompt.json (settings, LoRA + sha256, per-image timing), and
appends a `prompt` activity.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .store import Library
from .util import SCHEMA_ID, LibraryError, config, dump_json, load_json, now, sha256_file
from .validate import validate

WORKER = Path(__file__).resolve().parent / "workers" / "generate.py"


def settings() -> dict:
    return {k: v for k, v in config()["prompt"].items() if not k.startswith("_")}


def render(template: str, profile: dict) -> str:
    roles = [r["roleName"].lower() for r in profile["roles"]]
    return template.format(trigger=profile["trigger"], name=profile["name"], **{"class": profile["class"]},
                           role=roles[0] if roles else "musician").strip(", ")


def latest_lora(pdir: Path) -> dict | None:
    for path in sorted((pdir / "train").glob("*.json"), reverse=True):
        rec = load_json(path)
        if rec["status"] == "ok" and Path(rec.get("output", "")).exists():
            return rec
    return None


def plan(lib: Library, ref: str, *, lora: str | None = None, prompts: list[str] | None = None,
         weights: list[float] | None = None, seed: int | None = None, steps: int | None = None,
         size: int | None = None) -> dict:
    profile = lib.load(ref)
    pdir = lib.profile_dir(ref)
    s = settings()
    if lora:
        lora_path, train_run = Path(lora).expanduser().resolve(), None
    else:
        rec = latest_lora(pdir)
        if rec is None:
            raise LibraryError(f"{ref} has no trained LoRA: nim.library train {ref} (or pass --lora FILE)")
        lora_path, train_run = Path(rec["output"]), rec["run"]
    if not lora_path.exists():
        raise LibraryError(f"LoRA not found: {lora_path}")
    texts = [render(t, profile) for t in (prompts or s["prompts"])]
    weights = weights if weights is not None else s["weights"]
    seed = s["seed"] if seed is None else seed
    jobs, rows = [], []
    for i, text in enumerate(texts, 1):
        row = []
        for w in weights:
            f = f"p{i}-w{w:g}.png"
            jobs.append({"file": f, "prompt": text, "negative": s["negative"], "weight": w, "seed": seed + i - 1})
            row.append(f)
        rows.append(row)
    return {
        "lora": str(lora_path), "train_run": train_run, "jobs": jobs,
        "sheet": {"file": "sheet.png", "rows": rows,
                  "labels": [f"{i}: {t}   (weights {', '.join(f'{w:g}' for w in weights)})"
                             for i, t in enumerate(texts, 1)]},
        "settings": {"sd_scripts": s["sd_scripts"], "ckpt": s["ckpt"], "lora": str(lora_path),
                     "dtype": s["dtype"], "steps": steps or s["steps"], "guidance": s["guidance"],
                     "size": size or s["size"]}}


def run_generate(p: dict, out_dir: Path) -> list[dict]:
    python = settings()["python"]
    if not Path(python).exists():
        raise LibraryError(f"generator interpreter not found: {python} (set prompt.python)")
    req = {"settings": p["settings"], "out_dir": str(out_dir), "jobs": p["jobs"], "sheet": p["sheet"]}
    r = subprocess.run([python, str(WORKER)], input=json.dumps(req), stdout=subprocess.PIPE, text=True)
    if r.returncode:
        raise LibraryError(f"image generator failed (exit {r.returncode}); its output is above")
    return json.loads(r.stdout)


def build_prompt(lib: Library, ref: str, p: dict) -> dict:
    pdir = lib.profile_dir(ref)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = pdir / "prompt" / run
    started = now()
    results = {r["file"]: r for r in run_generate(p, out_dir)}
    images = [{**{k: j[k] for k in ("file", "prompt", "negative", "weight", "seed")},
               "seconds": results[j["file"]]["seconds"], "blank": results[j["file"]]["blank"]}
              for j in p["jobs"]]
    st = {k: v for k, v in p["settings"].items() if k not in ("sd_scripts", "lora")}
    doc = {"$schema": f"{SCHEMA_ID}#/$defs/prompt", "profile": ref, "run": run,
           "started": started, "finished": now(), "lora": p["lora"],
           "lora_sha256": sha256_file(Path(p["lora"])), "settings": st,
           "sheet": p["sheet"]["file"], "images": images}
    if p["train_run"]:
        doc["train_run"] = p["train_run"]
    errs = validate(doc, "prompt")
    if errs:
        raise LibraryError("prompt.json does not match the schema:\n  " + "\n  ".join(errs))
    rel = f"prompt/{run}"
    generated = {f"{rel}/prompt.json": dump_json(out_dir / "prompt.json", doc)}
    for f in [i["file"] for i in images] + [p["sheet"]["file"]]:
        generated[f"{rel}/{f}"] = sha256_file(out_dir / f)
    used = [f"train/{p['train_run']}.json"] if p["train_run"] else [p["lora"]]
    lib.record(ref, "prompt", used, generated,
               note=f"{len(images)} images, LoRA {Path(p['lora']).name}")
    return doc
