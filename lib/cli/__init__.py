"""
NIM Agency CLI - unified command interface.

Grammar: nim.agency <component> <action> [--flags]

Components mirror the library nomenclature:
    dispatch    execute missions (lib.dispatch, gated by briefing + confirm)
    mission     briefing, inspection, and reports (lib.dispatch.mission)
    project     stateful scoped work units (lib.data.project)
    syndicate   model provider inventory (lib.dispatch.syndicate)
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
from . import project as project_cmd
from . import install as install_cmd
from . import substation as substation_cmd
from . import syndicate as syndicate_cmd


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
    mission_flags.add_argument("--mission", choices=["codebase", "security", "task"],
                               default="codebase", help="Mission template (default: codebase)")
    mission_flags.add_argument("--task", type=str,
                               help="Ad-hoc task prompt (implies --mission task)")
    mission_flags.add_argument("--preset", type=str,
                               help="Dispatch a predefined task_preset by slug "
                                    "(fills --task/--skill/--count; explicit "
                                    "flags win. Define presets in the Type "
                                    "Foundry)")
    mission_flags.add_argument("--target", type=str, default=".",
                               help="Target directory (default: .)")
    mission_flags.add_argument("--crew", type=str,
                               help="Comma-separated agent codenames")
    mission_flags.add_argument("--skill", type=str,
                               help="Comma-separated competency terms for crew "
                                    "selection (matches skills, tools, specialties; "
                                    "drafts a new hire when nobody qualifies)")
    mission_flags.add_argument("--count", type=int,
                               help="Crew size when auto-selecting "
                                    "(default: 1 for task missions, 3 otherwise)")
    mission_flags.add_argument("--timeout", type=int,
                               help="Per-task timeout in seconds (default: etc/agency.json)")
    mission_flags.add_argument("--text-only", action="store_true",
                               help="Declare the task needs no tools: clears "
                                    "allowed_tools so text-only syndicates "
                                    "(local models) can serve it. The crew "
                                    "reasons from the prompt alone - it "
                                    "cannot read the target.")
    mission_flags.add_argument("--provider", type=str,
                               help="Syndicate to dispatch to, as 'syndicate' "
                                    "or 'syndicate:channel' (default: "
                                    "dispatch.provider in etc/agency.json). "
                                    "List them: nim.agency syndicate list")
    mission_flags.add_argument("--tools", type=str,
                               help="Comma-separated harness functions the "
                                    "mission may use, narrowing the default "
                                    "Read,Glob,Grep,Bash (e.g. Read,Glob,Grep,LS "
                                    "for a read-only harness without a shell)")
    mission_flags.add_argument("--expect", type=str,
                               help="Known-answer check: each task's output "
                                    "must contain this text, or the task fails "
                                    "before payroll")
    mission_flags.add_argument("--expect-json", type=str,
                               help="Known-answer check: each task's output "
                                    "must carry a JSON object containing these "
                                    "keys and values (arrays compare order-free)")
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
    project_cmd.add_parser(sub)
    syndicate_cmd.add_parser(sub)
    substation_cmd.add_parser(sub)
    install_cmd.add_parser(sub)
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
