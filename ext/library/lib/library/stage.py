"""`stage`: assemble a kohya sd-scripts dataset from a profile's accepted images.

Every image whose effective verdict (override, else status) is `ok` contributes
its staged outputs (full frame and face crop) with their caption .txt files:

    stage/dataset/img/<repeats>_<trigger> <class>/<file>.png + .txt
    stage/stage.json                 what went in, repeats, step estimate

and the dataset is linked as <training datasets>/<tag>-<slug>, so the existing
`train_lora.sh <tag>-<slug>` trains it unchanged. The dataset dir is rebuilt on
every run, so it always matches init.json.
"""

from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

from .store import Library
from .util import SCHEMA_ID, LibraryError, config, dump_json, load_json, now, sha256_file
from .validate import validate


def repeats_for(n: int, s: dict) -> int:
    """kohya repeats so one epoch is about steps_per_epoch images."""
    return max(1, min(s["max_repeats"], math.ceil(s["steps_per_epoch"] / max(n, 1))))


def build_stage(lib: Library, ref: str, repeats: int | None = None, link: bool = True) -> dict:
    profile = lib.load(ref)
    pdir = lib.profile_dir(ref)
    init_path = pdir / "stage" / "init.json"
    if not init_path.exists():
        raise LibraryError(f"{ref} has no init yet: nim.library init {ref}")
    init = load_json(init_path)
    s = {k: v for k, v in config()["stage"].items() if not k.startswith("_")}

    pending = [i["id"] for i in init["images"] if (i.get("override") or i["status"]) == "review"]
    accepted = [i for i in init["images"] if (i.get("override") or i["status"]) == "ok"]
    files = [(i["id"], o) for i in accepted for o in i.get("outputs", [])]
    if not files:
        raise LibraryError(f"{ref}: no accepted images to stage"
                           + (f" ({len(pending)} still in review)" if pending else ""))

    target = Path(s["datasets"]) / f"{profile['tag'].lower()}-{profile['slug']}"
    if link and target.exists() and not target.is_symlink():
        raise LibraryError(f"{target} exists and is not a link; move it or stage with --no-link")

    src_dir = pdir / "stage" / "images"
    reps = repeats or repeats_for(len(files), s)
    folder = f"{reps}_{profile['trigger']} {profile['class']}"
    dataset = pdir / "stage" / "dataset"
    if dataset.exists():
        shutil.rmtree(dataset)
    img_dir = dataset / "img" / folder
    img_dir.mkdir(parents=True)

    items, generated = [], {}
    for item_id, o in files:
        png = src_dir / o["file"]
        txt = png.with_suffix(".txt")
        if not png.exists() or not txt.exists():
            raise LibraryError(f"{ref}: {o['file']} or its caption is missing; re-run nim.library init {ref}")
        shutil.copy2(png, img_dir / png.name)
        shutil.copy2(txt, img_dir / txt.name)
        caption = txt.read_text(encoding="utf-8").strip()
        items.append({"item": item_id, "file": png.name, "variant": o["variant"], "caption": caption})
        generated[f"stage/dataset/img/{folder}/{png.name}"] = sha256_file(png)

    warnings = []
    if len(files) < s["min_images"]:
        warnings.append(f"{len(files)} training images; {s['min_images']}-40 is the usual range "
                        f"for a recognisable likeness")
    if pending:
        warnings.append(f"{len(pending)} image(s) still in review and left out: {', '.join(pending)}")
    if not any(o["variant"] == "face" for _, o in files):
        warnings.append("no face crops: every image is a full frame")

    linked = None
    if link:
        if target.is_symlink():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(dataset, target)
        linked = str(target)

    steps = len(files) * reps * s["epochs"]
    doc = {
        "$schema": f"{SCHEMA_ID}#/$defs/stage", "profile": ref, "generated": now(),
        "dataset": str(dataset), "folder": folder, "repeats": reps,
        "counts": {"images": len(files), "full": sum(1 for _, o in files if o["variant"] == "full"),
                   "face": sum(1 for _, o in files if o["variant"] == "face"),
                   "epochs": s["epochs"], "steps": steps},
        "items": items, "warnings": warnings}
    if linked:
        doc["link"] = linked
    errs = validate(doc, "stage")
    if errs:
        raise LibraryError("stage.json does not match the schema:\n  " + "\n  ".join(errs))
    generated["stage/stage.json"] = dump_json(pdir / "stage" / "stage.json", doc)
    lib.record(ref, "stage", ["stage/init.json"], generated,
               note=f"{len(files)} images x {reps} repeats" + (f", linked {linked}" if linked else ""))
    return doc
