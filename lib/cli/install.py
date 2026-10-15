"""
`nim.agency install` - set up, check, and manage agency instances.

    install env                         resolved paths, why each was chosen, NIM_* variables
    install doctor [--json] [--list-checks]
    install create [--name N] [--group G ...] [--home P] [--venv P] [--recreate] [--dry-run]
    install list | show [NAME] | remove NAME --yes

An instance is a virtual environment carrying pyproject dependency groups
(PEP 735) plus a receipt; bin/nim.agency runs inside the `default`
instance's venv when one exists (NIM_NO_VENV=1 opts out). Nothing here
edits shell rc files, uses sudo, or installs into the base interpreter.
Plan of record: .nim/work/plan.101426.installer.md
"""

import json
import os
import sys

from ..install import doctor as doctor_mod
from ..install import instance
from ..install.paths import DEFAULT_INSTANCE, platform_dirs, resolve_home, resolve_venv

ENV_VARS = ("NIM_HOME", "NIM_VENV", "NIM_NO_VENV", "NIM_IN_VENV",
            "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "LOCALAPPDATA")
MARK = {"ok": "\033[92m ok \033[0m", "warn": "\033[93mwarn\033[0m",
        "fail": "\033[91mfail\033[0m"}


def _show_resolution(resolved) -> None:
    print(f"  {resolved.what:<5} {resolved.path}   <- {resolved.source}")
    for c in resolved.candidates:
        verdict = c.verdict or "not tried"
        print(f"          {'*' if verdict == 'chosen' else ' '} {c.source:<34} "
              f"{c.path}  [{verdict}]")


def cmd_env(args) -> int:
    home = resolve_home(args.home)
    print("RESOLUTION (first usable candidate wins)")
    _show_resolution(home)
    if home.path:
        _show_resolution(resolve_venv(home.path, args.name, args.venv))
    print("\nPLATFORM DIRECTORIES (app 'nim')")
    for key, path in platform_dirs().items():
        print(f"  {key:<7} {path}")
    print("\nENVIRONMENT")
    for name in ENV_VARS:
        print(f"  {name:<16} {os.environ.get(name, '(unset)')}")
    print(f"\nINTERPRETER\n  {sys.executable}  (venv: "
          f"{sys.prefix != getattr(sys, 'base_prefix', sys.prefix)})")
    return 0


def cmd_doctor(args) -> int:
    if args.list_checks:
        for name, doc in doctor_mod.listing():
            print(f"  {name:<20} {doc}")
        return 0
    results = doctor_mod.run(args.name)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        for r in results:
            print(f"[{MARK.get(r.status, r.status)}] {r.id:<18} {r.detail}")
            if r.remedy and r.status != "ok":
                print(f"         -> {r.remedy}")
    worst = ("fail" if any(r.status == "fail" for r in results)
             else "warn" if any(r.status == "warn" for r in results) else "ok")
    state = {"ok": "ok", "warn": "degraded", "fail": "broken"}[worst]
    instance.record_status(args.name, state,
                           "; ".join(f"{r.id}: {r.detail}" for r in results
                                     if r.status != "ok")[:400] or "all checks ok")
    return 1 if worst == "fail" else 0


def cmd_create(args) -> int:
    try:
        plan = instance.plan_create(args.name, tuple(args.group or ()),
                                    args.home, args.venv)
    except instance.InstallError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"instance  {plan['name']}")
    print(f"home      {plan['home'].path}   <- {plan['home'].source}")
    print(f"venv      {plan['venv'].path}   <- {plan['venv'].source}")
    print(f"python    {plan['python']}")
    print(f"groups    {', '.join(plan['groups']) or '(none)'}"
          f"{'  -> ' + ' '.join(plan['requirements']) if plan['requirements'] else ''}")
    if instance.externally_managed():
        print("note      base interpreter is externally managed (PEP 668); "
              "everything installs into the venv")
    if args.dry_run:
        print("dry run: nothing created")
        return 0
    try:
        receipt = instance.create(plan, recreate=args.recreate)
    except instance.InstallError as exc:
        print(f"failed: {exc}")
        return 1
    print(f"\ninstance {receipt['name']} ready: {receipt['paths']['python']}")
    for name, version in (receipt.get("packages") or {}).items():
        print(f"  {name} {version}")
    if receipt["name"] == DEFAULT_INSTANCE:
        print("bin/nim.agency now runs inside this venv (NIM_NO_VENV=1 to opt out).")
    print("check it: nim.agency install doctor")
    return 0


def cmd_list(args) -> int:
    rows = instance.receipts()
    for r in rows:
        status = (r.get("status") or {}).get("state", "unknown")
        print(f"  {r['name']:<12} {status:<9} {r['python']['version']:<8} "
              f"{','.join(r.get('groups') or []) or '-':<20} {r['paths']['venv']}")
    print(f"{len(rows)} instance(s)")
    return 0


def cmd_show(args) -> int:
    receipt = instance.find(args.name)
    if receipt is None:
        print(f"no instance named {args.name!r}")
        return 1
    print(json.dumps(receipt, indent=2))
    return 0


def cmd_remove(args) -> int:
    if not args.yes:
        print(f"refused: removing deletes instance {args.name!r}'s venv; add --yes")
        return 1
    try:
        receipt = instance.remove(args.name)
    except instance.InstallError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"removed instance {receipt['name']}")
    return 0


def add_parser(subparsers):
    p = subparsers.add_parser(
        "install", help="Set up and manage agency instances (env, doctor, create, "
                        "list, show, remove)")
    actions = p.add_subparsers(dest="action", required=True)

    env = actions.add_parser("env", help="Resolved paths, their sources, NIM_* variables")
    env.add_argument("--name", default=DEFAULT_INSTANCE)
    env.add_argument("--home")
    env.add_argument("--venv")
    env.set_defaults(func=cmd_env)

    doc = actions.add_parser("doctor", help="Check this machine and an instance")
    doc.add_argument("--name", default=DEFAULT_INSTANCE)
    doc.add_argument("--json", action="store_true")
    doc.add_argument("--list-checks", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    create = actions.add_parser("create", help="Create an instance: venv + "
                                "dependency groups + receipt")
    create.add_argument("--name", default=DEFAULT_INSTANCE)
    create.add_argument("--group", action="append", metavar="GROUP",
                        help="pyproject dependency group (repeatable): "
                             "validation, remote, all")
    create.add_argument("--home", help="Instance home (overrides NIM_HOME)")
    create.add_argument("--venv", help="Venv location (overrides NIM_VENV)")
    create.add_argument("--recreate", action="store_true",
                        help="Rebuild an existing instance's venv")
    create.add_argument("--dry-run", action="store_true")
    create.set_defaults(func=cmd_create)

    lst = actions.add_parser("list", help="List instances")
    lst.set_defaults(func=cmd_list)

    show = actions.add_parser("show", help="Show an instance receipt")
    show.add_argument("name", nargs="?", default=DEFAULT_INSTANCE)
    show.set_defaults(func=cmd_show)

    rm = actions.add_parser("remove", help="Remove an instance (venv + receipt)")
    rm.add_argument("name")
    rm.add_argument("--yes", action="store_true")
    rm.set_defaults(func=cmd_remove)
