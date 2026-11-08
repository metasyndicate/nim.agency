"""`index`: walk a profile's linked sources, hash every file, mark duplicates,
and write profiles/<slug>/index.json. Reads raw data; writes only inside the profile."""

from __future__ import annotations

import os
from pathlib import Path

from .store import Library
from .util import SCHEMA_ID, LibraryError, config, dump_json, now, sha256_file
from .validate import validate


def _walk(link: Path):
    """Yield files under a source link (a file or a directory), following symlinks."""
    if link.is_file():
        yield link, link.name
        return
    for dirpath, dirnames, filenames in os.walk(link, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in sorted(filenames):
            full = Path(dirpath) / f
            yield full, str(full.relative_to(link))


def build_index(lib: Library, ref: str) -> dict:
    profile = lib.load(ref)
    pdir = lib.profile_dir(ref)
    formats = config()["formats"]
    ignore = set(config()["ignore"])

    items, skipped, seen = [], [], {}
    for src in profile["sources"]:
        link = pdir / src["link"]
        if not link.exists():
            raise LibraryError(f"source link is broken: {src['link']} -> {src['path']}")
        for full, rel in _walk(link):
            if full.name in ignore or full.name.startswith("._"):
                continue
            fmt = full.suffix.lower().lstrip(".")
            if fmt not in formats[src["kind"]]:
                skipped.append(f"{src['link']}/{rel}" if link.is_dir() else src["link"])
                continue
            sha = sha256_file(full)
            item = {"id": sha[:12], "kind": src["kind"], "source": src["link"], "path": rel,
                    "bytes": full.stat().st_size, "sha256": sha, "format": fmt}
            if sha in seen:
                item["duplicate_of"] = seen[sha]
            else:
                seen[sha] = item["id"]
            items.append(item)

    doc = {
        "$schema": f"{SCHEMA_ID}#/$defs/index", "profile": ref, "generated": now(),
        "counts": {
            "text": sum(1 for i in items if i["kind"] == "text" and "duplicate_of" not in i),
            "images": sum(1 for i in items if i["kind"] == "images" and "duplicate_of" not in i),
            "duplicates": sum(1 for i in items if "duplicate_of" in i),
            "skipped": len(skipped)},
        "items": items, "skipped": skipped}
    errs = validate(doc, "index")
    if errs:
        raise LibraryError("index does not match the schema:\n  " + "\n  ".join(errs))
    sha = dump_json(pdir / "index.json", doc)
    lib.record(ref, "index", [s["path"] for s in profile["sources"]], {"index.json": sha})
    return doc
