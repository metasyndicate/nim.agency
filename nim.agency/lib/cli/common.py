"""
CLI common helpers - crew resolution, mission construction, gating, events.

Shared by the dispatch and mission subcommands.
"""

import sys
from datetime import datetime
from pathlib import Path

from ..config import get_config, report_dir
from ..data import DataStore, match_agents, draft_hire
from ..dispatch import (
    DispatchEvent, Mission,
    adhoc_task_mission, codebase_analysis_mission, security_audit_mission,
)


def find_agents_by_codenames(ds: DataStore, codenames: list[str]) -> tuple[list[dict], list[str]]:
    """Resolve agents by codename. Returns (found, missing)."""
    all_agents = ds.query("agent").data
    found = []
    for codename in codenames:
        for agent in all_agents:
            if agent["identity"]["codename"] == codename:
                found.append(agent)
                break
    found_names = [a["identity"]["codename"] for a in found]
    missing = [c for c in codenames if c not in found_names]
    return found, missing


def select_crew(ds: DataStore, count: int) -> list[dict]:
    """Select a crew of agents, preferring diverse archetypes."""
    agents = ds.query("agent", limit=count * 2).data

    selected = []
    seen_archetypes = set()
    for agent in agents:
        arch_id = agent.get("classification", {}).get("archetype_id", 0)
        if arch_id not in seen_archetypes or len(selected) < count:
            selected.append(agent)
            seen_archetypes.add(arch_id)
            if len(selected) >= count:
                break
    return selected


def crew_count(args) -> int:
    """Crew size: --count override, else 1 for ad-hoc tasks, 3 for templates."""
    if args.count:
        return args.count
    return 1 if getattr(args, "task", None) or args.mission == "task" else 3


def resolve_crew(ds: DataStore, args) -> list[dict]:
    """Resolve crew from --crew codenames, --skill competency terms, or
    auto-select --count agents.

    Exits with an error message when resolution fails.
    """
    if args.crew:
        codenames = [c.strip() for c in args.crew.split(",")]
        crew, missing = find_agents_by_codenames(ds, codenames)
        if missing:
            print(f"\033[91mERROR: Agents not found: {missing}\033[0m", file=sys.stderr)
            raise SystemExit(1)
        return crew

    if getattr(args, "skill", None):
        terms = [t.strip() for t in args.skill.split(",") if t.strip()]
        matches = match_agents(terms, count=crew_count(args), ds=ds)
        if matches:
            print(f"CREW SELECTION ({', '.join(terms)}):")
            for m in matches:
                print(f"  ~ {m.codename}  score:{m.score}  "
                      f"[{'; '.join(m.reasons)}]")
            return [m.agent for m in matches]

        # Nobody on the roster qualifies - draft a general-purpose hire
        hire = draft_hire(ds)
        ident = hire["identity"]
        print(f"CREW SELECTION ({', '.join(terms)}): no qualified agent on "
              f"roster - drafted new hire "
              f"{ident['name']} ({ident['codename']})")
        crypto = hire.get("crypto") or {}
        if crypto.get("fingerprint"):
            print(f"  badge issued: {ident['dogtag']}  "
                  f"{crypto.get('key_type')} {crypto['fingerprint']}")
        return [hire]

    crew = select_crew(ds, crew_count(args))
    if not crew:
        print("\033[91mERROR: No agents available. "
              "Generate some first: nim.agency agent generate -n 5\033[0m", file=sys.stderr)
        raise SystemExit(1)
    return crew


def build_mission_from_args(args, crew: list[dict]) -> Mission:
    """Build a mission from template flags; apply timeout override."""
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"\033[91mERROR: Target directory not found: {target}\033[0m", file=sys.stderr)
        raise SystemExit(1)

    if getattr(args, "task", None) or args.mission == "task":
        if not getattr(args, "task", None):
            print("\033[91mERROR: --mission task requires --task PROMPT\033[0m",
                  file=sys.stderr)
            raise SystemExit(1)
        mission = adhoc_task_mission(crew, str(target), args.task)
    elif args.mission == "codebase":
        mission = codebase_analysis_mission(crew, str(target))
    elif args.mission == "security":
        mission = security_audit_mission(crew, str(target))
    else:
        print("Custom missions not yet supported via CLI", file=sys.stderr)
        raise SystemExit(1)

    if args.timeout:
        mission.constraints.timeout_seconds = args.timeout
    return mission


def resolve_report_path(args, mission: Mission) -> Path:
    """Report path: --output override or <report_dir>/mission-<id>-report.md."""
    if getattr(args, "output", None):
        return Path(args.output)
    return report_dir() / f"mission-{mission.id}-report.md"


def mission_json_path(mission_id: str) -> Path:
    """Conventional saved-mission JSON path for an id."""
    return report_dir() / f"mission-{mission_id}-report.json"


def confirm_dispatch(assume_yes: bool) -> bool:
    """Interactive dispatch gate. --yes bypasses; non-TTY refuses."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing to dispatch: stdin is not a TTY and --yes was not given.",
              file=sys.stderr)
        return False
    try:
        reply = input("Dispatch this mission? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        print("\nDispatch aborted.")
        return False
    return reply.strip().lower() in ("y", "yes")


def print_event(event: DispatchEvent):
    """Print dispatch lifecycle events."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "mission_started": "\033[94m[MISSION]\033[0m",
        "mission_complete": "\033[92m[COMPLETE]\033[0m",
        "mission_failed": "\033[91m[FAILED]\033[0m",
        "task_started": "\033[93m[TASK]\033[0m",
        "task_complete": "\033[92m[DONE]\033[0m",
        "task_failed": "\033[91m[FAIL]\033[0m",
        "payroll": "\033[93m[PAYROLL]\033[0m",
        "collecting": "\033[96m[COLLECT]\033[0m",
        "error": "\033[91m[ERROR]\033[0m",
    }.get(event.event_type, f"[{event.event_type}]")

    print(f"{timestamp} {prefix} {event.message}")


def save_report(mission: Mission, output_path: Path):
    """Save the mission report and full mission record (prompts, results)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if mission.final_report:
        with open(output_path, "w") as f:
            f.write(mission.final_report)
        print(f"\nReport saved to:  {output_path}")
    mission.save(output_path.with_suffix(".json"))
    print(f"Mission saved to: {output_path.with_suffix('.json')}")
