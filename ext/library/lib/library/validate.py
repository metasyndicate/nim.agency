"""Minimal stdlib JSON Schema validator for the subset library.schema.json uses.

Supported: type, enum, const, required, properties, additionalProperties(bool),
items, minItems, pattern, $ref (local '#/...'). Uses jsonschema instead when installed.
"""

from __future__ import annotations

import re

from .util import ROOT, load_json

SCHEMA_PATH = ROOT / "schema" / "library.schema.json"
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def _is(v, t: str) -> bool:
    if t in ("integer", "number") and isinstance(v, bool):
        return False
    return isinstance(v, _TYPES[t])


def _resolve(root: dict, ref: str) -> dict:
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _check(v, s: dict, root: dict, path: str, errs: list[str]) -> None:
    if "$ref" in s:
        _check(v, _resolve(root, s["$ref"]), root, path, errs)
        return
    t = s.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is(v, x) for x in ts):
            errs.append(f"{path}: expected {t}, got {type(v).__name__}")
            return
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {v!r} not in {s['enum']}")
    if "const" in s and v != s["const"]:
        errs.append(f"{path}: must be {s['const']!r}")
    if isinstance(v, str) and "pattern" in s and not re.search(s["pattern"], v):
        errs.append(f"{path}: {v!r} does not match {s['pattern']}")
    if isinstance(v, dict):
        for k in s.get("required", []):
            if k not in v:
                errs.append(f"{path}: missing '{k}'")
        props = s.get("properties", {})
        for k, sub in v.items():
            if k in props:
                _check(sub, props[k], root, f"{path}.{k}", errs)
            elif s.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected '{k}'")
    if isinstance(v, list):
        if len(v) < s.get("minItems", 0):
            errs.append(f"{path}: fewer than {s['minItems']} items")
        if "items" in s:
            for i, item in enumerate(v):
                _check(item, s["items"], root, f"{path}[{i}]", errs)


def validate(doc, defname: str) -> list[str]:
    """Validate doc against $defs/<defname>. Returns error strings (empty = valid)."""
    root = load_json(SCHEMA_PATH)
    try:
        import jsonschema  # type: ignore

        sub = {
            "$schema": root["$schema"],
            "$defs": root["$defs"],
            "$ref": f"#/$defs/{defname}",
        }
        v = jsonschema.Draft202012Validator(sub)
        return [
            f"$.{'.'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(doc)
        ]
    except ImportError:
        errs: list[str] = []
        _check(doc, root["$defs"][defname], root, "$", errs)
        return errs
