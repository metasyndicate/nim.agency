"""Shared helpers: paths, config, JSON I/O, hashing, naming."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.0"
AGENT = f"nim.library/{VERSION}"
SCHEMA_ID = "urn:nim:library:1"


class LibraryError(Exception):
    """A user-facing failure: printed without a traceback, exit 1."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, doc) -> str:
    """Write atomically; return the sha256 of what was written."""
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        raise LibraryError(f"cannot make a slug from {text!r}")
    return s


def token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


_config = None


def config() -> dict:
    global _config
    if _config is None:
        _config = load_json(ROOT / "etc" / "library.json")
    return _config


def library_root(override: str | None = None) -> Path:
    """--root, then $NIM_LIBRARY_ROOT, then etc/library.json."""
    return Path(override or os.environ.get("NIM_LIBRARY_ROOT") or config()["root"]).expanduser()
