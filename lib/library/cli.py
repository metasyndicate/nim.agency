"""nim.library command line.

    new profile --type character|group --tag TAG --name NAME [--text P]... [--images P]...
                [--keys "[K:V,...]"] [--trigger T] [--class C] [--group-name N] [--update]
    index REF            hash + de-duplicate a profile's sources -> index.json
    init REF [--only images|text]
                         resize, face-crop, caption images; pull memoir passages
    init REF --ok ID.. | --exclude ID.. | --clear ID..
                         hand verdicts on images (kept across re-runs)
    stage REF [--repeats N] [--no-link]
                         kohya dataset from accepted images, linked into training/datasets
    list [TAG|REF]       profiles (all, or one tag), or one profile's indexed files
    show REF             profile.json
    check [REF]          validate profile, group, index and manifest files

REF is <tag>/<slug>, e.g. lk5/jody. Global: --root DIR (default $NIM_LIBRARY_ROOT
or etc/library.json).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .index import build_index
from .init import build_init, set_override
from .stage import build_stage
from .store import Library, new_profile
from .util import VERSION, LibraryError, library_root, load_json
from .validate import validate


def _err(msg: str) -> None:
    print(f"nim.library: {msg}", file=sys.stderr)


def cmd_new(lib: Library, a) -> int:
    profile, loose = new_profile(
        lib, ptype=a.type, tag=a.tag, name=a.name, slug_=a.slug, text=a.text or [],
        images=a.images or [], keys=a.keys, trigger=a.trigger, klass=a.klass,
        group_name=a.group_name, update=a.update)
    pdir = lib.profile_dir(profile["id"])
    verb = "updated" if a.update else "created"
    print(f"{verb} {profile['id']}  ({profile['type']}, trigger '{profile['trigger']}', "
          f"class '{profile['class']}')")
    print(f"  dir      {pdir}")
    if profile["roles"]:
        print(f"  roles    {', '.join(r['roleName'] for r in profile['roles'])}")
    if profile.get("voiceType"):
        print(f"  voice    {profile['voiceType']}")
    for s in profile["sources"]:
        print(f"  source   {s['link']} -> {s['path']}")
    if loose:
        print(f"  note: {', '.join(loose)} kept in attributes (no typed field yet; "
              f"see etc/library.json keys)")
    print(f"  next     nim.library index {profile['id']}")
    return 0


def cmd_index(lib: Library, a) -> int:
    refs = [p["id"] for p in lib.profiles(a.ref)] if "/" not in a.ref else [a.ref]
    if not refs:
        raise LibraryError(f"no profiles under {a.ref}")
    for ref in refs:
        doc = build_index(lib, ref)
        c = doc["counts"]
        print(f"indexed {ref}: {c['images']} images, {c['text']} text, "
              f"{c['duplicates']} duplicates, {c['skipped']} skipped")
        for s in doc["skipped"][:10]:
            print(f"  skipped {s}")
        if len(doc["skipped"]) > 10:
            print(f"  ... {len(doc['skipped']) - 10} more in index.json")
    return 0


def cmd_init(lib: Library, a) -> int:
    marks = [(v, ids) for v, ids in (("ok", a.ok), ("exclude", a.exclude), (None, a.clear)) if ids]
    if marks:
        for value, ids in marks:
            for i in set_override(lib, a.ref, ids, value):
                print(f"{i}  override {value or 'cleared'}")
        return 0
    doc = build_init(lib, a.ref, a.only)
    c = doc["counts"]
    print(f"init {a.ref}: {c['images']} images -> {c['ok']} ok, {c['review']} review, "
          f"{c['excluded']} excluded, {c['errors']} errors; {c['face_crops']} face crops; "
          f"{c['passages']} passages from {c['text_files']} text files")
    for i in doc["images"]:
        eff = i.get("override") or i["status"]
        if eff != "ok":
            print(f"  {eff:<8} {i['id']}  {i['source']}")
            for r in i["reasons"]:
                print(f"           - {r}")
    for t in doc["text"]:
        if t["status"] == "error":
            print(f"  error    {t['id']}  {t['source']}: {t['reason']}")
    stage = lib.profile_dir(a.ref) / "stage"
    print(f"  review   {stage}/images  (edit .txt captions freely; they are kept)")
    print(f"  decide   nim.library init {a.ref} --ok ID | --exclude ID")
    return 0


def cmd_stage(lib: Library, a) -> int:
    doc = build_stage(lib, a.ref, repeats=a.repeats, link=not a.no_link)
    c = doc["counts"]
    print(f"staged {a.ref}: {c['images']} images ({c['full']} full, {c['face']} face) "
          f"x {doc['repeats']} repeats x {c['epochs']} epochs = {c['steps']} steps")
    print(f"  dataset  {doc['dataset']}/img/{doc['folder']}")
    if "link" in doc:
        print(f"  linked   {doc['link']}")
    for w in doc["warnings"]:
        print(f"  warning  {w}")
    if "link" in doc:
        link = Path(doc["link"])
        print(f"  train    {link.parent.parent / 'train_lora.sh'} {link.name}")
    return 0


def cmd_list(lib: Library, a) -> int:
    if a.ref and "/" in a.ref:
        path = lib.profile_dir(a.ref) / "index.json"
        if not path.exists():
            raise LibraryError(f"{a.ref} is not indexed yet: nim.library index {a.ref}")
        doc = load_json(path)
        if a.json:
            print(json.dumps(doc["items"], indent=2))
            return 0
        for i in doc["items"]:
            dup = f"  (dup of {i['duplicate_of']})" if "duplicate_of" in i else ""
            print(f"{i['id']}  {i['kind']:<6} {i['bytes']:>10}  {i['source']}/{i['path']}{dup}")
        return 0

    profiles = lib.profiles(a.ref)
    if a.json:
        print(json.dumps(profiles, indent=2, ensure_ascii=False))
        return 0
    if not profiles:
        print(f"no profiles in {lib.root}" + (f"/{a.ref.lower()}" if a.ref else ""))
        return 0
    print(f"{'PROFILE':<24} {'TYPE':<10} {'NAME':<18} {'TRIGGER':<14} {'IMG':>5} {'TXT':>5}  ROLES")
    for p in profiles:
        idx = lib.profile_dir(p["id"]) / "index.json"
        c = load_json(idx)["counts"] if idx.exists() else {}
        print(f"{p['id']:<24} {p['type']:<10} {p['name']:<18} {p['trigger']:<14} "
              f"{c.get('images', '-'):>5} {c.get('text', '-'):>5}  "
              f"{', '.join(r['roleName'] for r in p['roles'])}")
    return 0


def cmd_show(lib: Library, a) -> int:
    print(json.dumps(lib.load(a.ref), indent=2, ensure_ascii=False))
    return 0


def cmd_check(lib: Library, a) -> int:
    profiles = [lib.load(a.ref)] if a.ref and "/" in a.ref else lib.profiles(a.ref)
    bad = 0
    tags = set()
    for p in profiles:
        pdir = lib.profile_dir(p["id"])
        tags.add(p["tag"])
        for fname, defn in (("profile.json", "profile"), ("index.json", "index"),
                            ("manifest.json", "manifest"), ("stage/init.json", "init"),
                            ("stage/stage.json", "stage")):
            path = pdir / fname
            if not path.exists():
                continue
            for e in validate(load_json(path), defn):
                print(f"{p['id']}/{fname}: {e}")
                bad += 1
        for s in p["sources"]:
            if not (pdir / s["link"]).exists():
                print(f"{p['id']}: broken source link {s['link']} -> {s['path']}")
                bad += 1
    for tag in tags:
        g = lib.group(tag)
        for e in validate(g, "group") if g else []:
            print(f"{tag}/group.json: {e}")
            bad += 1
    print("ok" if not bad else f"{bad} problem(s)")
    return 0 if not bad else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nim.library", description="Profile library for generative character work.")
    p.add_argument("--root", help="library root (default $NIM_LIBRARY_ROOT or etc/library.json)")
    p.add_argument("--version", action="version", version=f"nim.library {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="create a library object")
    new.add_argument("what", choices=["profile"])
    new.add_argument("--type", required=True, choices=["character", "group"])
    new.add_argument("--tag", required=True, help="collection tag, e.g. LK5")
    new.add_argument("--name", required=True)
    new.add_argument("--slug", help="directory name (default: from --name)")
    new.add_argument("--text", action="append", metavar="PATH", help="raw text file or dir (repeatable)")
    new.add_argument("--images", action="append", metavar="PATH", help="raw image file or dir (repeatable)")
    new.add_argument("--keys", help='"[Instrument:Lead Guitar,Voice:Tenor,...]"')
    new.add_argument("--trigger", help="training token (default: tag + slug)")
    new.add_argument("--class", dest="klass", help="class word (default: person / band)")
    new.add_argument("--group-name", help="display name for <tag>/group.json")
    new.add_argument("--update", action="store_true", help="add sources/keys to an existing profile")
    new.set_defaults(func=cmd_new)

    idx = sub.add_parser("index", help="hash and de-duplicate a profile's sources")
    idx.add_argument("ref", help="<tag>/<slug>, or <tag> for every profile in it")
    idx.set_defaults(func=cmd_index)

    ini = sub.add_parser("init", help="prepare images and text for staging")
    ini.add_argument("ref", help="<tag>/<slug>")
    ini.add_argument("--only", choices=["images", "text"], help="redo one half only")
    ini.add_argument("--ok", nargs="+", metavar="ID", help="accept images (id or prefix)")
    ini.add_argument("--exclude", nargs="+", metavar="ID", help="leave images out of staging")
    ini.add_argument("--clear", nargs="+", metavar="ID", help="drop a hand verdict")
    ini.set_defaults(func=cmd_init)

    stg = sub.add_parser("stage", help="build the kohya dataset from accepted images")
    stg.add_argument("ref", help="<tag>/<slug>")
    stg.add_argument("--repeats", type=int, help="kohya repeats per image (default: from etc/library.json stage)")
    stg.add_argument("--no-link", action="store_true", help="do not link into the training datasets dir")
    stg.set_defaults(func=cmd_stage)

    lst = sub.add_parser("list", help="list profiles, or a profile's indexed files")
    lst.add_argument("ref", nargs="?", help="<tag> or <tag>/<slug>")
    lst.add_argument("--json", action="store_true")
    lst.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="print a profile")
    show.add_argument("ref")
    show.set_defaults(func=cmd_show)

    chk = sub.add_parser("check", help="validate library files")
    chk.add_argument("ref", nargs="?")
    chk.set_defaults(func=cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        return a.func(Library(library_root(a.root)), a)
    except LibraryError as e:
        _err(str(e))
        return 1
