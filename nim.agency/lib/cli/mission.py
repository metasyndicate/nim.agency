"""
`nim.agency mission` - mission briefing, inspection, and reports.

    mission plan    build a mission from flags and render its briefing
                    (no execution)
    mission show    render a saved mission's briefing
    mission list    list saved missions in the report directory
    mission report  print a saved mission report (markdown)
"""

import json

from ..config import report_dir
from ..data import get_datastore
from ..dispatch import Mission, build_briefing, render_briefing, get_provider

from .common import (
    build_mission_from_args, mission_json_path,
    resolve_crew, resolve_report_path,
)


def cmd_plan(args) -> int:
    """Build a mission and render its briefing without executing."""
    ds = get_datastore()
    crew = resolve_crew(ds, args)
    mission = build_mission_from_args(args, crew)
    report_path = resolve_report_path(args, mission)

    briefing = build_briefing(
        mission, ds=ds, report_path=report_path, verbosity=args.verbose,
    )
    print(render_briefing(briefing, verbosity=args.verbose))
    print("\n(plan only - nothing dispatched. Execute with: nim.agency dispatch ...)")
    return 0


def cmd_show(args) -> int:
    """Render a saved mission's briefing."""
    path = mission_json_path(args.id)
    if not path.exists():
        print(f"No saved mission '{args.id}' in {report_dir()}")
        print("List saved missions with: nim.agency mission list")
        return 1

    mission = Mission.load(path)
    briefing = build_briefing(
        mission, ds=get_datastore(),
        report_path=path.with_suffix(".md"),
        verbosity=args.verbose,
    )
    print(render_briefing(briefing, verbosity=args.verbose))
    if args.verbose >= 1:
        print("\n(note: prompt sources reflect current composition, "
              "not dispatch-time state)")
    return 0


def cmd_list(args) -> int:
    """List saved missions from the report directory."""
    paths = sorted(report_dir().glob("mission-*-report.json"))
    if not paths:
        print(f"No saved missions in {report_dir()}")
        return 0

    print(f"{'ID':<10} {'STATUS':<10} {'TASKS':<7} {'CREATED':<20} TITLE")
    print("-" * 76)
    for path in paths:
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        tasks = data.get("tasks", [])
        complete = sum(1 for t in tasks if t.get("status") == "complete")
        created = (data.get("created_at") or "")[:19].replace("T", " ")
        print(f"{data.get('id', '?'):<10} {data.get('status', '?'):<10} "
              f"{complete}/{len(tasks):<5} {created:<20} {data.get('title', '')}")
    return 0


def cmd_report(args) -> int:
    """Print a saved mission report."""
    md_path = mission_json_path(args.id).with_suffix(".md")
    if md_path.exists():
        print(md_path.read_text())
        return 0

    # Fall back to the report embedded in the mission JSON
    json_path = mission_json_path(args.id)
    if json_path.exists():
        with open(json_path) as f:
            report = json.load(f).get("final_report")
        if report:
            print(report)
            return 0

    print(f"No report for mission '{args.id}' in {report_dir()}")
    return 1


def add_parser(subparsers, mission_flags, verbosity):
    p = subparsers.add_parser("mission", help="Mission briefing, inspection, and reports")
    actions = p.add_subparsers(dest="action", required=True)

    plan = actions.add_parser(
        "plan", parents=[mission_flags, verbosity],
        help="Build a mission and render its briefing (no execution)")
    plan.set_defaults(func=cmd_plan)

    show = actions.add_parser(
        "show", parents=[verbosity],
        help="Render a saved mission's briefing")
    show.add_argument("id", help="Mission id (see: mission list)")
    show.set_defaults(func=cmd_show)

    lst = actions.add_parser("list", help="List saved missions")
    lst.set_defaults(func=cmd_list)

    report = actions.add_parser("report", help="Print a saved mission report")
    report.add_argument("id", help="Mission id (see: mission list)")
    report.set_defaults(func=cmd_report)
