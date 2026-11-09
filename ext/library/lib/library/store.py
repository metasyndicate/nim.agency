"""Library layout, profile creation, group roster, and PROV manifest.

    <root>/<tag>/group.json                    MusicGroup roster
    <root>/<tag>/profiles/<slug>/profile.json  one subject
                                 sources/{text,images}/   symlinks to raw data
                                 index.json               (written by `index`)
                                 manifest.json            PROV activities
"""

from __future__ import annotations

import os
from pathlib import Path

from .util import (AGENT, SCHEMA_ID, LibraryError, config, dump_json, load_json,
                   now, slug, token)
from .validate import validate

TYPES = {"character": "Person", "group": "MusicGroup"}


class Library:
    def __init__(self, root: Path):
        self.root = root

    # -- addressing ------------------------------------------------------------

    def tag_dir(self, tag: str) -> Path:
        return self.root / tag.lower()

    def profile_dir(self, ref: str) -> Path:
        tag, _, name = ref.partition("/")
        if not name:
            raise LibraryError(f"profile reference must be <tag>/<slug>, got {ref!r}")
        return self.tag_dir(tag) / "profiles" / slug(name)

    def load(self, ref: str) -> dict:
        path = self.profile_dir(ref) / "profile.json"
        if not path.exists():
            raise LibraryError(f"no profile {ref} (looked in {path.parent})")
        return load_json(path)

    def profiles(self, tag: str | None = None) -> list[dict]:
        tags = [self.tag_dir(tag)] if tag else sorted(p for p in self.root.glob("*") if p.is_dir())
        out = []
        for t in tags:
            for path in sorted(t.glob("profiles/*/profile.json")):
                out.append(load_json(path))
        return out

    def group(self, tag: str) -> dict | None:
        path = self.tag_dir(tag) / "group.json"
        return load_json(path) if path.exists() else None

    # -- writes ----------------------------------------------------------------

    def save_profile(self, profile: dict) -> str:
        errs = validate(profile, "profile")
        if errs:
            raise LibraryError("profile does not match the schema:\n  " + "\n  ".join(errs))
        return dump_json(self.profile_dir(profile["id"]) / "profile.json", profile)

    def record(self, ref: str, activity: str, used: list[str], generated: dict[str, str],
               note: str | None = None) -> None:
        """Append a prov:Activity to the profile manifest."""
        pdir = self.profile_dir(ref)
        path = pdir / "manifest.json"
        doc = load_json(path) if path.exists() else {
            "$schema": f"{SCHEMA_ID}#/$defs/manifest", "profile": ref, "activities": []}
        act = {"activity": activity, "at": now(), "agent": AGENT, "used": used,
               "generated": [{"path": p, "sha256": h} for p, h in generated.items()]}
        if note:
            act["note"] = note
        doc["activities"].append(act)
        errs = validate(doc, "manifest")
        if errs:
            raise LibraryError("manifest does not match the schema:\n  " + "\n  ".join(errs))
        dump_json(path, doc)

    def sync_group(self, profile: dict, group_name: str | None = None) -> str:
        """Upsert the profile into <tag>/group.json (members, or the group's own profile)."""
        tag = profile["tag"]
        path = self.tag_dir(tag) / "group.json"
        doc = self.group(tag) or {
            "$schema": f"{SCHEMA_ID}#/$defs/group", "@context": "https://schema.org",
            "@type": "MusicGroup", "tag": tag, "name": group_name or tag, "members": []}
        if group_name:
            doc["name"] = group_name
        if profile["type"] == "group":
            doc["profile"] = profile["slug"]
            if not group_name:
                doc["name"] = profile["name"]
        else:
            member = {"slug": profile["slug"], "name": profile["name"],
                      "roles": [r["roleName"] for r in profile["roles"]]}
            doc["members"] = [m for m in doc["members"] if m["slug"] != profile["slug"]] + [member]
        doc["modified"] = now()
        errs = validate(doc, "group")
        if errs:
            raise LibraryError("group.json does not match the schema:\n  " + "\n  ".join(errs))
        return dump_json(path, doc)


# -- key parsing ---------------------------------------------------------------

def parse_keys(text: str | None) -> list[tuple[str, str]]:
    """'[Instrument:Lead Guitar, Voice:Tenor]' -> [('Instrument', 'Lead Guitar'), ...].
    Brackets optional; pairs split on ',' or ';'; key and value on the first ':' or '='."""
    if not text:
        return []
    body = text.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    pairs = []
    for part in body.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        sep = min((i for i in (part.find(":"), part.find("=")) if i > 0), default=-1)
        if sep < 0:
            raise LibraryError(f"key {part!r} has no value (use Key:Value)")
        key, value = part[:sep].strip(), part[sep + 1:].strip()
        if not key or not value:
            raise LibraryError(f"key {part!r} has an empty side")
        pairs.append((key, value))
    return pairs


def apply_keys(profile: dict, pairs: list[tuple[str, str]]) -> list[str]:
    """Fill typed fields per etc/library.json `keys`; the rest go to attributes.
    Returns the keys that landed in attributes (for the caller to report)."""
    mapping = {k: v for k, v in config()["keys"].items() if not k.startswith("_")}
    loose = []
    for key, value in pairs:
        rule = mapping.get(key.lower())
        if rule is None:
            profile["attributes"][key] = value
            loose.append(key)
            continue
        field = rule["field"]
        if field == "roles":
            role = {"@type": "OrganizationRole", "roleName": value}
            if key.lower() == "instrument":
                role["instrument"] = value
            if all(r["roleName"] != value for r in profile["roles"]):
                profile["roles"].append(role)
        elif field == "alternateName":
            names = profile.setdefault("alternateName", [])
            if value not in names:
                names.append(value)
        else:
            profile[field] = value
    return loose


# -- sources -------------------------------------------------------------------

def link_source(pdir: Path, kind: str, raw: str, existing: list[dict]) -> dict | None:
    """Symlink a raw path into sources/<kind>/. Returns the source record, or
    None when this path is already linked."""
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise LibraryError(f"{kind} source does not exist: {raw}")
    if any(s["path"] == str(path) and s["kind"] == kind for s in existing):
        return None
    base = pdir / "sources" / kind
    base.mkdir(parents=True, exist_ok=True)
    name, n = path.name, 2
    while (base / name).exists() or (base / name).is_symlink():
        name = f"{path.stem}-{n}{path.suffix}"
        n += 1
    os.symlink(path, base / name)
    return {"kind": kind, "path": str(path), "link": f"sources/{kind}/{name}", "added": now()}


def new_profile(lib: Library, *, ptype: str, tag: str, name: str, slug_: str | None = None,
                text: list[str] = (), images: list[str] = (), keys: str | None = None,
                trigger: str | None = None, klass: str | None = None,
                group_name: str | None = None, update: bool = False) -> tuple[dict, list[str]]:
    """Create (or with update=True, extend) a profile. Returns (profile, loose keys)."""
    if ptype not in TYPES:
        raise LibraryError(f"--type must be one of {', '.join(TYPES)}")
    s = slug(slug_ or name)
    ref = f"{tag.lower()}/{s}"
    pdir = lib.profile_dir(ref)
    exists = (pdir / "profile.json").exists()
    if exists and not update:
        raise LibraryError(f"profile {ref} exists; pass --update to add sources or keys")
    if update and not exists:
        raise LibraryError(f"no profile {ref} to update")

    stamp = now()
    if exists:
        profile = lib.load(ref)
        if profile["type"] != ptype:
            raise LibraryError(f"{ref} is a {profile['type']} profile, not {ptype}")
    else:
        profile = {
            "$schema": f"{SCHEMA_ID}#/$defs/profile", "@context": "https://schema.org",
            "@type": TYPES[ptype], "id": ref, "type": ptype, "tag": tag, "slug": s,
            "name": name,
            "trigger": trigger or token(tag) + (token(s) if ptype == "character" else "group"),
            "class": klass or config()["class"][ptype],
            "roles": [], "attributes": {}, "sources": [], "created": stamp, "modified": stamp}
    if trigger:
        profile["trigger"] = token(trigger)
    if klass:
        profile["class"] = klass

    loose = apply_keys(profile, parse_keys(keys))
    added = []
    for kind, paths in (("text", text), ("images", images)):
        for raw in paths:
            src = link_source(pdir, kind, raw, profile["sources"])
            if src:
                profile["sources"].append(src)
                added.append(src["path"])
    profile["modified"] = stamp

    sha = lib.save_profile(profile)
    gsha = lib.sync_group(profile, group_name)
    lib.record(ref, "new", added,
               {"profile.json": sha, f"../../group.json": gsha},
               note="update" if exists else None)
    return profile, loose
