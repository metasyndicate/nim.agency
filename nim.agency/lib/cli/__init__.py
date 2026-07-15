"""
NIM Agency CLI - unified command interface.

Grammar: nim.agency <component> <action> [--flags]

Components mirror the library nomenclature:
    dispatch    execute missions (lib.dispatch, gated by briefing + confirm)
    mission     briefing, inspection, and reports (lib.dispatch.mission)
    agent       roster operations (lib.data.generator)
    data        datastore init and schema inspection (lib.data)
    tui         interactive agent builder (tui.builder)
"""

import argparse
import shutil
import sys
from typing import Optional

from . import agent as agent_cmd
from . import data as data_cmd
from . import dispatch as dispatch_cmd
from . import mission as mission_cmd


def cmd_tui(args) -> int:
    """Launch the TUI interface."""
    from tui.builder import MIN_TERMINAL_WIDTH, MIN_TERMINAL_HEIGHT

    size = shutil.get_terminal_size((80, 24))
    if size.columns < MIN_TERMINAL_WIDTH or size.lines < MIN_TERMINAL_HEIGHT:
        print(f"\033[91mFATAL: Terminal too small for TUI\033[0m")
        print(f"  Current size:  {size.columns}x{size.lines}")
        print(f"  Required size: {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}")
        print(f"\nResize your terminal window and try again.")
        return 1

    from tui.builder import AgentBuilder
    AgentBuilder().run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Shared parent parsers
    mission_flags = argparse.ArgumentParser(add_help=False)
    mission_flags.add_argument("--mission", choices=["codebase", "security"],
                               default="codebase", help="Mission template (default: codebase)")
    mission_flags.add_argument("--target", type=str, default=".",
                               help="Target directory (default: .)")
    mission_flags.add_argument("--crew", type=str,
                               help="Comma-separated agent codenames")
    mission_flags.add_argument("--count", type=int, default=3,
                               help="Crew size when auto-selecting (default: 3)")
    mission_flags.add_argument("--timeout", type=int,
                               help="Per-task timeout in seconds (default: etc/agency.json)")
    mission_flags.add_argument("--output", type=str,
                               help="Report path (default: <report_dir>/mission-<id>-report.md)")

    verbosity = argparse.ArgumentParser(add_help=False)
    verbosity.add_argument("-v", "--verbose", action="count", default=0,
                           help="Briefing verbosity (-v: constraints + prompt sources, "
                                "-vv: full prompts)")

    parser = argparse.ArgumentParser(
        prog="nim.agency",
        description="NIM Agency - dispatch desk for delegated agentic identities",
    )
    sub = parser.add_subparsers(dest="component", required=True)

    dispatch_cmd.add_parser(sub, mission_flags, verbosity)
    mission_cmd.add_parser(sub, mission_flags, verbosity)
    agent_cmd.add_parser(sub)
    data_cmd.add_parser(sub)

    tui = sub.add_parser("tui", help="Launch the interactive TUI agent builder")
    tui.set_defaults(func=cmd_tui)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
