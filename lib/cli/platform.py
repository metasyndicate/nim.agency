"""
`nim.agency platform` - inference software on a host, as data.

    platform list                      tracked platforms, host, version, bind
    platform show SLUG                 the full record (JSON)
    platform capture VENDOR --slug S   read this host's installation; prints
                                       the record and its drift from the
                                       tracked one; --write saves it
    platform render SLUG               the service unit the record describes
    platform apply SLUG [-y]           make this host run the record (gated)
    platform verify SLUG               capture again; exit 1 on drift

The record is the source of truth: settings are changed by editing it
(`platform edit`, or the file) and applying, never by hand on the host.
"""

import getpass
import json
import sys
import time

from ..data import get_datastore
from .. import platform as platforms
from ..platform import PlatformError
from .common import confirm_dispatch


def _err(message: str) -> int:
    print(f"\033[91mERROR: {message}\033[0m", file=sys.stderr)
    return 1


def _find(ds, slug: str):
    record = ds.query("platform", lambda r: r.get("slug") == slug).first()
    if record is None:
        known = ", ".join(sorted(r["slug"] for r in ds.query("platform").data))
        raise PlatformError(f"no platform '{slug}' (known: {known or 'none'}) "
                            f"- capture one: nim.agency platform capture "
                            f"<vendor> --slug {slug} --write")
    return record


def _capture_like(record: dict) -> dict:
    """Capture this host the way the record is configured (its unit label
    first), for verify."""
    return platforms.capture(record["vendor_slug"], record["slug"],
                             host_name=(record.get("host") or {}).get("name"),
                             prefer_label=(record.get("service") or {}).get("label"))


def cmd_list(args) -> int:
    ds = get_datastore()
    rows = sorted(ds.query("platform").data, key=lambda r: r["slug"])
    if not rows:
        print("No platforms tracked. Capture this host's: "
              "nim.agency platform capture ollama --slug <host>_ollama --write")
        return 0
    here = platforms.this_host()
    print(f"PLATFORMS  (this host: {here})")
    print("-" * 78)
    for r in rows:
        host = r.get("host") or {}
        mark = "  <- here" if host.get("hostname") == here else ""
        print(f"{r['slug']:<20} {r['vendor_slug']:<8} "
              f"{r['software']['version']:<10} {host.get('name', '?')}{mark}")
        print(f"{'':<20} bind {r['exposure']['bind']}   unit "
              f"{r['service'].get('label') or '(manual)'}   "
              f"artifacts {len(r.get('artifacts') or [])}   "
              f"ports {', '.join(r.get('syndicates') or []) or '-'}")
    return 0


def cmd_show(args) -> int:
    try:
        record = _find(get_datastore(), args.slug)
    except PlatformError as exc:
        return _err(str(exc))
    print(json.dumps(record, indent=2))
    return 0


def cmd_capture(args) -> int:
    ds = get_datastore()
    try:
        live = platforms.capture(args.vendor, args.slug, host_name=args.host_name)
    except PlatformError as exc:
        return _err(str(exc))
    existing = ds.query("platform", lambda r: r.get("slug") == args.slug).first()
    print(json.dumps(live, indent=2))
    if existing:
        problems = platforms.drift(existing, live)
        print(f"\nDRIFT vs tracked {args.slug}: "
              f"{len(problems) or 'none'}", file=sys.stderr)
        for line in problems:
            print(f"  ~ {line}", file=sys.stderr)
    if not args.write:
        print("\n(not saved; --write records this capture)", file=sys.stderr)
        return 0
    live["captured"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "by": getpass.getuser(),
                        "from": platforms.this_host()}
    if existing:
        # A capture is an observation of the host; links the host cannot
        # report (ports, notes) are the record's and survive it
        for keep in ("syndicates", "notes"):
            if existing.get(keep):
                live[keep] = existing[keep]
        saved = ds.replace("platform", existing["id"], {**live, "id": existing["id"]})
        print(f"CAPTURED  {args.slug}  updated {saved['id']}", file=sys.stderr)
    else:
        saved = ds.create("platform", live)
        print(f"CAPTURED  {args.slug}  created {saved['id']}", file=sys.stderr)
    return 0


def cmd_render(args) -> int:
    try:
        path, text = platforms.render(_find(get_datastore(), args.slug))
    except PlatformError as exc:
        return _err(str(exc))
    print(f"# {path}")
    print(text, end="")
    return 0


def cmd_verify(args) -> int:
    try:
        record = _find(get_datastore(), args.slug)
    except PlatformError as exc:
        return _err(str(exc))
    if not platforms.is_local(record):
        return _err(f"{args.slug} runs on {record['host']['hostname']}; "
                    f"this is {platforms.this_host()} - verify on that host")
    problems = platforms.drift(record, _capture_like(record))
    if not problems:
        print(f"VERIFIED  {args.slug}: host matches the record")
        return 0
    print(f"DRIFT  {args.slug}: {len(problems)} difference(s)")
    for line in problems:
        print(f"  ~ {line}")
    if any(line.startswith("guard ") and "not enforced" in line for line in problems):
        print("  guards need root; run:")
        for command in platforms.guard_commands(record):
            print(f"    {command}")
    return 1


def _wait_healthy(record: dict, seconds: int = 30) -> bool:
    from ..platform import host as hostlib, ollama
    base = ollama.api_base(record["exposure"]["bind"])
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if hostlib.fetch(f"{base}/api/version", timeout=2):
            return True
        time.sleep(1)
    return False


def cmd_apply(args) -> int:
    try:
        record = _find(get_datastore(), args.slug)
        manager = platforms.manager_of(record)
    except PlatformError as exc:
        return _err(str(exc))
    if not platforms.is_local(record):
        return _err(f"{args.slug} runs on {record['host']['hostname']}; "
                    f"this is {platforms.this_host()} - apply on that host")
    print(f"APPLY PLATFORM  {args.slug}  ({record['vendor_slug']} "
          f"{record['software']['version']} on {record['host']['name']})")
    print(f"  bind:     {record['exposure']['bind']}")
    for p in record["exposure"].get("proxies") or []:
        print(f"  fronted:  {p['url']} -> {p['target']} ({p['scope']})")
    print(f"  settings: " + ", ".join(f"{k}={v}" for k, v in
                                      sorted(record["service"].get("environment", {}).items())))
    for g in record["exposure"].get("guards") or []:
        print(f"  guard:    {g['kind']} {g['target']} (root: operator applies)")
    print("  steps:")
    for step in manager.plan(record):
        print(f"    - {step}")
    if not confirm_dispatch(args.yes, "Apply this platform configuration?", "apply"):
        return 2
    try:
        for line in manager.apply(record):
            print(f"  + {line}")
    except RuntimeError as exc:
        return _err(str(exc))
    if not _wait_healthy(record):
        return _err(f"service did not answer on {record['exposure']['bind']} "
                    f"within 30s - see {record['service'].get('stderr_path', 'the unit log')}")
    print("  + service answering")
    return cmd_verify(args)


def add_parser(subparsers):
    p = subparsers.add_parser(
        "platform", help="Inference software on a host: capture, render, apply, verify")
    actions = p.add_subparsers(dest="action", required=True)
    from . import compose
    compose.add_verbs(actions, "platform")

    actions.add_parser("list", help="Tracked platforms").set_defaults(func=cmd_list)

    show = actions.add_parser("show", help="The full record (JSON)")
    show.add_argument("slug")
    show.set_defaults(func=cmd_show)

    capture = actions.add_parser(
        "capture", help="Read this host's installation into a record")
    capture.add_argument("vendor", help="Vendor slug (reference/vendor.json), e.g. ollama")
    capture.add_argument("--slug", required=True, help="Platform slug, e.g. bonsai_ollama")
    capture.add_argument("--host-name", help="Agency name for this host "
                         "(default: the hostname)")
    capture.add_argument("--write", action="store_true",
                         help="Save (create or replace) the tracked record")
    capture.set_defaults(func=cmd_capture)

    render = actions.add_parser("render", help="The service unit the record describes")
    render.add_argument("slug")
    render.set_defaults(func=cmd_render)

    apply = actions.add_parser(
        "apply", help="Make this host run the record (briefing + confirmation)")
    apply.add_argument("slug")
    apply.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    apply.set_defaults(func=cmd_apply)

    verify = actions.add_parser("verify", help="Capture again; exit 1 on drift")
    verify.add_argument("slug")
    verify.set_defaults(func=cmd_verify)
