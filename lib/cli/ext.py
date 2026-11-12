"""
`nim.agency ext` - optional extensions under ext/ (lib/extensions.py).

    ext list                     installed extensions, version, entry point
    nim.agency <name> [ARGS...]  run an extension; ARGS pass through untouched

Extensions are forwarded before argparse runs (see forward()), so their own
flags and help are theirs; the subparsers registered here only list them in
`nim.agency --help`.
"""

import subprocess
import sys
from typing import Optional

from .. import extensions


def _warn(problems: list[str]) -> None:
    for line in problems:
        print(f"\033[91mext: skipped {line}\033[0m", file=sys.stderr)


def cmd_list(args) -> int:
    found, problems = extensions.discover()
    _warn(problems)
    if not found:
        print(f"No extensions in {extensions.EXT_DIR}")
        return 1 if problems else 0
    for e in found:
        print(f"{e.name:<12} {e.version:<8} {e.summary}")
        print(f"{'':<12} {e.command}")
    return 1 if problems else 0


def forward(argv: list[str], reserved: set[str]) -> Optional[int]:
    """Run `nim.agency <extension> ...` if argv names one; None means not an extension."""
    if not argv or argv[0] in reserved:
        return None
    found, problems = extensions.discover()
    ext = next((e for e in found if e.name == argv[0]), None)
    if ext is None:
        return None
    _warn(problems)
    return subprocess.run([str(ext.command), *argv[1:]]).returncode


def add_parser(subparsers, reserved: set[str]) -> None:
    p = subparsers.add_parser("ext", help="Optional extensions under ext/")
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="Installed extensions").set_defaults(func=cmd_list)

    found, problems = extensions.discover()
    _warn(problems)
    for e in found:
        if e.name in reserved:
            print(f"\033[91mext: {e.name} shadows a core component; not mounted\033[0m",
                  file=sys.stderr)
            continue
        subparsers.add_parser(e.name, help=f"[ext] {e.summary}", add_help=False)
