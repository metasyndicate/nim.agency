"""`init`: turn indexed raw files into reviewable, training-ready material.

Images (via workers/images.py): resized PNG + optional face crop + caption .txt
in stage/images/, each with a verdict: ok, review (needs a human look) or error.
Text: plain text extracted, split into paragraphs, and paragraphs that mention the
subject written to stage/text/passages.jsonl.

Hand edits survive re-runs: an `override` (ok|exclude) on an image in
stage/init.json, and any caption .txt changed by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from .store import Library
from .util import SCHEMA_ID, LibraryError, config, dump_json, load_json, now
from .validate import validate

WORKER = Path(__file__).resolve().parent / "workers" / "images.py"
TEXTUTIL = ("docx", "rtf", "odt", "html", "doc")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def caption_for(profile: dict, template: str) -> str:
    return template.format(trigger=profile["trigger"], name=profile["name"],
                           roles=", ".join(r["roleName"].lower() for r in profile["roles"]),
                           **{"class": profile["class"]}).strip(", ")


# -- text ------------------------------------------------------------------------

def extract_text(path: Path, fmt: str) -> str:
    if fmt in ("txt", "md"):
        return path.read_text(encoding="utf-8", errors="replace")
    if fmt in TEXTUTIL:
        r = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                           capture_output=True, text=True)
        if r.returncode:
            raise LibraryError(f"textutil failed: {r.stderr.strip()}")
        return r.stdout
    raise LibraryError(f"no extractor for .{fmt} yet")


def paragraphs(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def mentions(par: str, names: list[str]) -> list[str]:
    # lookarounds, not \b: names may start or end in punctuation ("J.D.")
    return [n for n in names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", par, re.IGNORECASE)]


# -- images ----------------------------------------------------------------------

def run_worker(jobs: list[dict], settings: dict) -> list[dict]:
    python = settings["python"]
    if not Path(python).exists():
        raise LibraryError(f"image worker interpreter not found: {python} "
                           f"(set init.python in etc/library.json)")
    r = subprocess.run([python, str(WORKER)], input=json.dumps({"settings": settings, "jobs": jobs}),
                       capture_output=True, text=True)
    if r.returncode:
        raise LibraryError(f"image worker failed:\n{r.stderr.strip()[-2000:]}")
    return json.loads(r.stdout)


def verdict(res: dict, profile: dict, min_side: int) -> tuple[str, list[str]]:
    if "error" in res:
        return "error", [res["error"]]
    reasons = []
    n = len(res["faces"])
    if n == 0:
        reasons.append("no frontal face found")
    elif n > 1:
        reasons.append(f"{n} faces: confirm which is {profile['name']}, crop, or exclude")
    w, h = res["original"]
    if min(w, h) < min_side:
        reasons.append(f"low resolution ({w}x{h})")
    return ("review" if reasons else "ok"), reasons


def write_caption(txt: Path, caption: str, previous: dict | None) -> tuple[str, str]:
    """Write the caption unless the existing .txt was edited by hand.
    Returns (caption in effect, source)."""
    if txt.exists():
        current = txt.read_text(encoding="utf-8").strip()
        if previous is not None and _sha(current) != _sha(previous.get("caption", "")):
            return current, "user"
        if previous is not None and previous.get("caption_source") == "user":
            return current, "user"
    txt.write_text(caption + "\n", encoding="utf-8")
    return caption, "template"


def build_init(lib: Library, ref: str, only: str | None = None) -> dict:
    profile = lib.load(ref)
    pdir = lib.profile_dir(ref)
    idx_path = pdir / "index.json"
    if not idx_path.exists():
        raise LibraryError(f"{ref} is not indexed yet: nim.library index {ref}")
    index = load_json(idx_path)
    settings = {k: v for k, v in config()["init"].items() if not k.startswith("_")}
    stage = pdir / "stage"
    prev_path = stage / "init.json"
    prev = load_json(prev_path) if prev_path.exists() else {"images": [], "text": []}
    prev_images = {i["id"]: i for i in prev["images"]}
    prev_outputs = {o["file"]: o for i in prev["images"] for o in i.get("outputs", [])}

    live = [i for i in index["items"] if "duplicate_of" not in i]
    images_out, text_out = [], []
    generated = {}

    # images
    if only in (None, "images"):
        img_items = [i for i in live if i["kind"] == "images"]
        jobs = [{"id": i["id"], "src": str(pdir / i["source"] / i["path"])
                 if (pdir / i["source"]).is_dir() else str(pdir / i["source"]),
                 "out_dir": str(stage / "images")} for i in img_items]
        results = {r["id"]: r for r in run_worker(jobs, settings)} if jobs else {}
        for item in img_items:
            res = results[item["id"]]
            status, reasons = verdict(res, profile, settings["min_side"])
            rec = {"id": item["id"], "source": f"{item['source']}/{item['path']}",
                   "status": status, "reasons": reasons}
            if "error" not in res:
                rec["original"] = res["original"]
                rec["faces"] = res["faces"]
                rec["outputs"] = []
                for o in res["outputs"]:
                    tmpl = settings["caption_face"] if o["variant"] == "face" else settings["caption"]
                    txt = stage / "images" / (Path(o["file"]).stem + ".txt")
                    cap, src = write_caption(txt, caption_for(profile, tmpl), prev_outputs.get(o["file"]))
                    rec["outputs"].append({**o, "caption": cap, "caption_source": src})
            old = prev_images.get(item["id"], {})
            if "override" in old:
                rec["override"] = old["override"]
            images_out.append(rec)
    else:
        images_out = prev["images"]

    # text
    if only in (None, "text"):
        names = [profile["name"], *profile.get("alternateName", [])]
        passages = []
        for item in (i for i in live if i["kind"] == "text"):
            src = pdir / item["source"]
            path = src / item["path"] if src.is_dir() else src
            rec = {"id": item["id"], "source": f"{item['source']}/{item['path']}"}
            try:
                pars = paragraphs(extract_text(path, item["format"]))
            except (LibraryError, OSError) as e:
                text_out.append({**rec, "status": "error", "reason": str(e)})
                continue
            hits = 0
            for n, par in enumerate(pars):
                m = mentions(par, names)
                if m:
                    passages.append({"item": item["id"], "n": n, "text": par, "mentions": m})
                    hits += 1
            text_out.append({**rec, "status": "ok", "paragraphs": len(pars), "passages": hits})
        for p in passages:
            errs = validate(p, "passage")
            if errs:
                raise LibraryError("passage does not match the schema: " + "; ".join(errs))
        pfile = stage / "text" / "passages.jsonl"
        pfile.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in passages)
        pfile.write_text(body, encoding="utf-8")
        generated["stage/text/passages.jsonl"] = _sha(body)
    else:
        text_out = prev["text"]

    def effective(i):
        return i.get("override") or i["status"]

    doc = {
        "$schema": f"{SCHEMA_ID}#/$defs/init", "profile": ref, "generated": now(),
        "settings": settings,
        "counts": {
            "images": len(images_out),
            "ok": sum(1 for i in images_out if effective(i) == "ok"),
            "review": sum(1 for i in images_out if effective(i) == "review"),
            "excluded": sum(1 for i in images_out if effective(i) == "exclude"),
            "errors": sum(1 for i in images_out if effective(i) == "error"),
            "face_crops": sum(1 for i in images_out for o in i.get("outputs", []) if o["variant"] == "face"),
            "text_files": len(text_out),
            "passages": sum(t.get("passages", 0) for t in text_out)},
        "images": images_out, "text": text_out}
    errs = validate(doc, "init")
    if errs:
        raise LibraryError("init.json does not match the schema:\n  " + "\n  ".join(errs))
    generated["stage/init.json"] = dump_json(prev_path, doc)
    lib.record(ref, "init", ["index.json"], generated)
    return doc


def set_override(lib: Library, ref: str, ids: list[str], value: str | None) -> list[str]:
    """Mark images ok/exclude by hand (value None clears). Accepts id prefixes."""
    path = lib.profile_dir(ref) / "stage" / "init.json"
    if not path.exists():
        raise LibraryError(f"{ref} has no init yet: nim.library init {ref}")
    doc = load_json(path)
    changed = []
    for want in ids:
        hit = [i for i in doc["images"] if i["id"].startswith(want)]
        if len(hit) != 1:
            raise LibraryError(f"{want!r} matches {len(hit)} images")
        if value:
            hit[0]["override"] = value
        else:
            hit[0].pop("override", None)
        changed.append(hit[0]["id"])
    eff = [i.get("override") or i["status"] for i in doc["images"]]
    doc["counts"].update(ok=eff.count("ok"), review=eff.count("review"),
                         excluded=eff.count("exclude"), errors=eff.count("error"))
    sha = dump_json(path, doc)
    lib.record(ref, "init", ["stage/init.json"], {"stage/init.json": sha},
               note=f"override {value or 'cleared'}: {', '.join(changed)}")
    return changed


if __name__ == "__main__":  # pragma: no cover
    sys.exit("use: nim.library init")
