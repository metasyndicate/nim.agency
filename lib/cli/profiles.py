"""
`nim.agency profiles` - inventory a target's agent-facing documents and sweep
them for drift (plan.101526.profiles.md).

    profiles scan PATH [--tag T] [--role R] [--reference DIR ...] [--index-only GLOB ...]
                       [--exclude GLOB ...] [--dry-run] [--report FILE]
    profiles list
    profiles show TAG [PATH]
    profiles findings TAG [--kind K] [--status S] [--severity S] [-v]
    profiles resolve FINDING [--status accepted|resolved|wontfix|open] [--note TEXT]

A scan never edits the target. Without --dry-run it replaces the tag's
documents and open findings in the instance root (install couple) and
writes a report to <root>/state/reports/; findings the operator decided
are kept and not raised again.
"""

import json
import re
import sys
from pathlib import Path

from ..data.crud import get_datastore
from ..data.profiles import Profiles, ProfilesError
from ..install.root import RootError

RED, RESET = "\033[91m", "\033[0m"
MARK = {"error": "\033[91merror\033[0m", "warn": "\033[93mwarn \033[0m", "info": "\033[96minfo \033[0m"}


def _fail(message: str) -> int:
    print(f"{RED}profiles: {message}{RESET}", file=sys.stderr)
    return 1


def _tag(args) -> str:
    return args.tag or re.sub(r"[^a-z0-9]+", "-", Path(args.path).resolve().name.lower()).strip("-")


def _ceremony(result: dict) -> None:
    s = Profiles.Report.summary(result)
    print(f"  walked {s['files']} files; {s['documents']} documents, {s['swept']} swept"
          f"  (git: {'yes' if result['git'] else 'not a work tree'})")
    if s["index_only"]:
        print("  index-only " + ", ".join(s["index_only"]) + "  (matched for duplicates, never swept)")
    if s["excluded"]:
        print("  excluded  " + ", ".join(f"{p} x{n}" for p, n in s["excluded"].items()))
    for path, why in s["skipped"]:
        print(f"  skipped   {path}: {why}")
    print("\nDOCUMENTS BY KIND")
    for kind, n in s["by_kind"].items():
        print(f"  {kind:<22} {n:>5}")
    print("\nGIT STATE")
    for state, n in s["by_git_state"].items():
        print(f"  {state:<22} {n:>5}")
    print("\nFINDINGS")
    if not result["findings"]:
        print("  none")
    order = {"error": 0, "warn": 1, "info": 2}
    counted = {}
    for f in result["findings"]:
        key = (f["severity"], f["kind_slug"])
        counted[key] = counted.get(key, 0) + 1
    for (severity, kind), n in sorted(counted.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
        print(f"  [{MARK[severity]}] {kind:<22} {n:>5}")


def _write_report(result: dict, path: Path) -> tuple:
    path.parent.mkdir(parents=True, exist_ok=True)
    md, js = path.with_suffix(".md"), path.with_suffix(".json")
    md.write_text(Profiles.Report.as_markdown(result))
    js.write_text(json.dumps(Profiles.Report.as_json(result), indent=2) + "\n")
    return md, js


def cmd_scan(args) -> int:
    tag = _tag(args)
    ds = get_datastore()
    try:
        profiles = Profiles(ds, args.path, tag, role=args.role, exclude=args.exclude,
                            reference=args.reference, index_only=args.index_only)
        print(f"SCAN {tag}  {profiles.root}" + ("  (dry run)" if args.dry_run else ""))
        print("  reading the target; nothing in it is changed")
        result = profiles.run()
    except ProfilesError as exc:
        return _fail(str(exc))
    _ceremony(result)
    if args.dry_run:
        if args.report:
            md, js = _write_report(result, Path(args.report))
            print(f"\nreport    {md}\n          {js}")
        print("\ndry run: nothing recorded")
        return 0
    try:
        saved = profiles.save(result)
    except RootError as exc:
        return _fail(f"{exc}  (or rerun with --dry-run --report FILE)")
    reports = ds.instance_path.parent / "state" / "reports"
    md, js = _write_report(result, Path(args.report) if args.report
                           else reports / f"profiles-{tag}-{result['scan_id']}")
    print(f"\nRECORDED  {saved['documents']} documents, {saved['findings']} findings"
          f"  ({saved['decided_kept']} already decided, kept)")
    print(f"report    {md}\n          {js}")
    print(f"next      nim.agency profiles findings {tag}")
    return 0


def _by_tag(ds) -> dict:
    tags = {}
    for doc in ds.query("document").data:
        tags.setdefault(doc["tag"], {"documents": 0, "open": {}, "scan": doc.get("scan_id")})
        tags[doc["tag"]]["documents"] += 1
    for f in ds.query("finding").data:
        entry = tags.setdefault(f["tag"], {"documents": 0, "open": {}, "scan": f.get("scan_id")})
        if f.get("status") == "open":
            entry["open"][f["severity"]] = entry["open"].get(f["severity"], 0) + 1
    return tags


def cmd_list(args) -> int:
    ds = get_datastore()
    if ds.instance_path is None:
        return _fail("no instance root is coupled to this checkout: nim.agency install couple")
    tags = _by_tag(ds)
    if not tags:
        print("no scans recorded; start with: nim.agency profiles scan PATH --tag TAG")
        return 0
    print(f"  {'tag':<24} {'documents':>9}  {'error':>5} {'warn':>5} {'info':>5}  scan")
    for tag, e in sorted(tags.items()):
        o = e["open"]
        print(f"  {tag:<24} {e['documents']:>9}  {o.get('error', 0):>5} {o.get('warn', 0):>5} "
              f"{o.get('info', 0):>5}  {e['scan']}")
    return 0


def cmd_show(args) -> int:
    ds = get_datastore()
    docs = ds.query("document", lambda r: r.get("tag") == args.tag, sort_key="path").data
    if not docs:
        return _fail(f"no documents recorded for tag {args.tag!r}")
    if args.doc:
        match = [d for d in docs if d["path"] == args.doc]
        if not match:
            return _fail(f"{args.doc!r} is not a recorded document of {args.tag!r}")
        print(json.dumps(match[0], indent=2))
        return 0
    for d in docs:
        print(f"  {d['doc_kind_slug']:<22} {d['git_state']:<9} {d['dated']['modified'][:10]} "
              f"{d['dated']['source']:<5} {d['path']}")
    print(f"{len(docs)} document(s)")
    return 0


def cmd_findings(args) -> int:
    ds = get_datastore()

    def wanted(f: dict) -> bool:
        return (f.get("tag") == args.tag
                and (args.kind is None or f["kind_slug"] == args.kind)
                and (args.status == "all" or f.get("status") == args.status)
                and (args.severity is None or f["severity"] == args.severity))

    order = {"error": 0, "warn": 1, "info": 2}
    found = sorted(ds.query("finding", wanted).data,
                   key=lambda f: (order[f["severity"]], f["kind_slug"], f["title"]))
    for f in found:
        print(f"  {f['id'][:8]}  [{MARK[f['severity']]}] {f['kind_slug']:<20} {f['status']:<8} {f['title']}")
        if args.verbose:
            print(f"            {f.get('detail', '')}")
            if f.get("resolution"):
                print(f"            resolution: {f['resolution']}")
    print(f"{len(found)} finding(s)")
    return 0


def cmd_resolve(args) -> int:
    ds = get_datastore()
    matches = [f for f in ds.query("finding").data if f["id"].startswith(args.finding)]
    if len(matches) != 1:
        return _fail(f"{len(matches)} findings match {args.finding!r}; give more of the id")
    finding = matches[0]
    ds.update("finding", finding["id"], {"status": args.status, "resolution": args.note})
    print(f"{finding['id'][:8]}  {finding['kind_slug']}  {finding['status']} -> {args.status}"
          + (f"  ({args.note})" if args.note else ""))
    return 0


def add_parser(subparsers):
    p = subparsers.add_parser("profiles", help="Inventory agent-facing documents and sweep them "
                                               "for drift (scan, list, show, findings, resolve)")
    actions = p.add_subparsers(dest="action", required=True)

    scan = actions.add_parser("scan", help="Scan a target (read only) and record its documents "
                                           "and findings")
    scan.add_argument("path", help="Target root")
    scan.add_argument("--tag", help="Target slug (default: the directory name)")
    scan.add_argument("--role", default="candidate", choices=["candidate", "authoritative"])
    scan.add_argument("--reference", action="append", default=[], metavar="DIR",
                      help="Reference library matched for duplicates, never swept (repeatable)")
    scan.add_argument("--index-only", action="append", default=[], metavar="GLOB",
                      help="Archive-like paths: matched for duplicates, never swept, beyond "
                           "profiles.index_only (repeatable)")
    scan.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                      help="Exclude by name or relative path, beyond profiles.exclude (repeatable)")
    scan.add_argument("--dry-run", action="store_true", help="Scan and report; record nothing")
    scan.add_argument("--report", metavar="FILE", help="Write the report here (.md and .json)")
    scan.set_defaults(func=cmd_scan)

    lst = actions.add_parser("list", help="Recorded tags with document and open finding counts")
    lst.set_defaults(func=cmd_list)

    show = actions.add_parser("show", help="A tag's documents, or one document's record")
    show.add_argument("tag")
    show.add_argument("doc", nargs="?", help="Document path relative to the target root")
    show.set_defaults(func=cmd_show)

    fnd = actions.add_parser("findings", help="A tag's findings")
    fnd.add_argument("tag")
    fnd.add_argument("--kind")
    fnd.add_argument("--status", default="open",
                     choices=["open", "accepted", "resolved", "wontfix", "all"])
    fnd.add_argument("--severity", choices=["error", "warn", "info"])
    fnd.add_argument("-v", "--verbose", action="store_true")
    fnd.set_defaults(func=cmd_findings)

    res = actions.add_parser("resolve", help="Record the operator's decision on a finding")
    res.add_argument("finding", help="Finding id (a unique prefix is enough)")
    res.add_argument("--status", default="resolved",
                     choices=["accepted", "resolved", "wontfix", "open"])
    res.add_argument("--note", help="Why (kept with the finding)")
    res.set_defaults(func=cmd_resolve)
