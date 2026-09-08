"""
`nim.agency dispatch` - gated mission execution.

Renders the mission briefing (condensed by default; -v/-vv for detail),
requires operator confirmation (-y/--yes to bypass), then executes.
"""

import asyncio

from ..config import get_config, modality_violation
from ..data import get_datastore
from ..dispatch import (
    Dispatcher, build_briefing, render_briefing,
)

from .common import (
    apply_preset, build_mission_from_args, confirm_dispatch, print_event,
    resolve_crew, resolve_provider, resolve_report_path, save_report,
)


def cmd_dispatch(args) -> int:
    ds = get_datastore()

    provider = resolve_provider(ds, args)
    if provider is None:
        return 1

    apply_preset(ds, args)
    crew = resolve_crew(ds, args)
    mission = build_mission_from_args(args, crew)

    # Fail the briefing, not the mission: the global posture rules first
    refusal = modality_violation(mission.constraints.read_only,
                                 mission.constraints.local_only)
    if refusal:
        print(f"\033[91mERROR: {refusal}\033[0m")
        return 1

    report_path = resolve_report_path(args, mission)

    # Full disclosure before any agent work is spawned
    briefing = build_briefing(
        mission, ds=ds, report_path=report_path,
        provider=provider, verbosity=args.verbose,
    )
    print(render_briefing(briefing, verbosity=args.verbose))

    # Fail the briefing, not the mission: a channel that cannot serve these
    # requirements would answer from imagination rather than refuse.
    if briefing.capability_refusal:
        print(f"\033[91mERROR: {briefing.capability_refusal}\033[0m")
        return 1

    if not confirm_dispatch(args.yes):
        return 2

    print("\nDISPATCHING...\n")
    dispatcher = Dispatcher(datastore=ds, provider=provider)
    result = asyncio.run(dispatcher.execute(
        mission,
        on_event=print_event,
        max_concurrent=get_config()["dispatch"]["max_concurrent"],
    ))

    # Summary
    print("\n" + "=" * 60)
    print("MISSION SUMMARY")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    completed = sum(1 for t in result.tasks if t.status.value == "complete")
    print(f"Tasks completed: {completed}/{len(result.tasks)}")
    if result.started_at and result.completed_at:
        duration = (result.completed_at - result.started_at).total_seconds()
        print(f"Duration: {duration:.1f}s")

    # Written on success and failure - failed missions retain partial
    # results and per-task errors
    save_report(result, report_path)

    if result.final_report:
        print("\n" + "-" * 60)
        print("REPORT PREVIEW (first 50 lines):")
        print("-" * 60)
        lines = result.final_report.split("\n")
        for line in lines[:50]:
            print(line)
        if len(lines) > 50:
            print("... (truncated)")

    return 0 if result.status.value == "complete" else 1


def add_parser(subparsers, mission_flags, verbosity):
    p = subparsers.add_parser(
        "dispatch",
        parents=[mission_flags, verbosity],
        help="Execute a mission (briefing + confirmation gate, then dispatch)",
    )
    p.add_argument("-y", "--yes", action="store_true",
                   help="Bypass the confirmation gate")
    p.set_defaults(func=cmd_dispatch)
