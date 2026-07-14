#!/usr/bin/env python3
"""
NIM Dispatch CLI - Execute agent missions from the command line.

Usage:
    python bin/dispatch.py --mission codebase --target /path/to/repo
    python bin/dispatch.py --mission security --target /path/to/repo
    python bin/dispatch.py --crew agent1,agent2 --prompt "Analyze this code"
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.config import get_config, log_dir, report_dir
from lib.data import DataStore, get_datastore
from lib.dispatch import (
    Mission, MissionBuilder, Dispatcher, DispatchEvent,
    codebase_analysis_mission, security_audit_mission,
    get_provider
)


class DispatchCLI:
    """CLI interface for dispatch operations."""

    def __init__(self):
        self.ds = get_datastore()
        self.dispatcher = Dispatcher(datastore=self.ds)

    def list_agents(self, limit: int = 10) -> list[dict]:
        """List available agents."""
        result = self.ds.query("agent", limit=limit)
        return result.data

    def get_agents_by_codenames(self, codenames: list[str]) -> list[dict]:
        """Get agents by their codenames."""
        result = self.ds.query("agent")
        all_agents = result.data
        agents = []
        for codename in codenames:
            for agent in all_agents:
                if agent["identity"]["codename"] == codename:
                    agents.append(agent)
                    break
        return agents

    def select_crew(self, count: int = 4) -> list[dict]:
        """Select a crew of agents."""
        agents = self.list_agents(count * 2)  # Get extras to choose from

        # Try to get diverse archetypes
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

    def print_event(self, event: DispatchEvent):
        """Print dispatch events."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {
            "mission_started": "\033[94m[MISSION]\033[0m",
            "mission_complete": "\033[92m[COMPLETE]\033[0m",
            "mission_failed": "\033[91m[FAILED]\033[0m",
            "task_started": "\033[93m[TASK]\033[0m",
            "task_complete": "\033[92m[DONE]\033[0m",
            "task_failed": "\033[91m[FAIL]\033[0m",
            "collecting": "\033[96m[COLLECT]\033[0m",
            "error": "\033[91m[ERROR]\033[0m",
        }.get(event.event_type, f"[{event.event_type}]")

        print(f"{timestamp} {prefix} {event.message}")

    async def run_mission(self, mission: Mission, report_path: Path) -> Mission:
        """Execute a mission with progress output."""
        cfg = get_config()["logging"]
        print("\n" + "=" * 60)
        print(f"MISSION: {mission.title}")
        print(f"OBJECTIVE: {mission.objective}")
        print(f"CREW: {len(mission.crew_ids)} agents")
        print(f"TASKS: {len(mission.tasks)}")
        print(f"TIMEOUT: {mission.constraints.timeout_seconds}s per task")
        print("-" * 60)
        print("OUTPUTS:")
        print(f"  report:       {report_path}")
        print(f"  mission json: {report_path.with_suffix('.json')}")
        print(f"  dispatch log: {log_dir() / cfg['dispatch_csv']} (csv)")
        print(f"                {log_dir() / cfg['dispatch_json']} (json)")
        print("=" * 60 + "\n")

        # List crew
        print("CREW ROSTER:")
        for agent_id in mission.crew_ids:
            agent = self.ds.read("agent", agent_id)
            if agent:
                name = agent["identity"]["name"]
                codename = agent["identity"]["codename"]
                arch_id = agent.get("classification", {}).get("archetype_id", 0)
                print(f"  - {name} ({codename})")

        print("\n" + "-" * 60 + "\n")
        print("DISPATCHING...\n")

        # Execute
        result = await self.dispatcher.execute(
            mission,
            on_event=self.print_event,
            max_concurrent=get_config()["dispatch"]["max_concurrent"]
        )

        return result

    def save_report(self, mission: Mission, output_path: Path):
        """Save the mission report and full mission record (prompts, results)."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if mission.final_report:
            with open(output_path, "w") as f:
                f.write(mission.final_report)
            print(f"\nReport saved to:  {output_path}")
        mission.save(output_path.with_suffix(".json"))
        print(f"Mission saved to: {output_path.with_suffix('.json')}")


async def main():
    parser = argparse.ArgumentParser(description="NIM Dispatch CLI")
    parser.add_argument("--mission", choices=["codebase", "security", "custom"],
                        default="codebase", help="Mission type")
    parser.add_argument("--target", type=str, default=".",
                        help="Target directory for analysis")
    parser.add_argument("--crew", type=str,
                        help="Comma-separated agent codenames")
    parser.add_argument("--count", type=int, default=3,
                        help="Number of agents to use (if not specifying crew)")
    parser.add_argument("--output", type=str,
                        help="Output file for report (default: <report_dir>/mission-<id>-report.md)")
    parser.add_argument("--timeout", type=int,
                        help="Per-task timeout in seconds (default: from etc/agency.json)")
    parser.add_argument("--list-agents", action="store_true",
                        help="List available agents")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show mission plan without executing")

    args = parser.parse_args()

    cli = DispatchCLI()

    # List agents mode
    if args.list_agents:
        print("\nAvailable Agents:")
        print("-" * 60)
        agents = cli.list_agents(20)
        for agent in agents:
            ident = agent["identity"]
            cls = agent.get("classification", {})
            print(f"  {ident['codename']:<20} {ident['name']:<25} arch:{cls.get('archetype_id', '?')}")
        return

    # Check provider
    provider = get_provider("claude-cli")
    if not provider.is_available():
        print("\033[91mERROR: claude CLI not found. Install it first.\033[0m")
        print("  brew install claude  (or equivalent)")
        return

    # Select crew
    if args.crew:
        codenames = [c.strip() for c in args.crew.split(",")]
        crew = cli.get_agents_by_codenames(codenames)
        if len(crew) != len(codenames):
            found = [a["identity"]["codename"] for a in crew]
            missing = [c for c in codenames if c not in found]
            print(f"\033[91mERROR: Agents not found: {missing}\033[0m")
            return
    else:
        crew = cli.select_crew(args.count)
        if not crew:
            print("\033[91mERROR: No agents available. Generate some first.\033[0m")
            return

    # Create mission
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"\033[91mERROR: Target directory not found: {target}\033[0m")
        return

    if args.mission == "codebase":
        mission = codebase_analysis_mission(crew, str(target))
    elif args.mission == "security":
        mission = security_audit_mission(crew, str(target))
    else:
        print("Custom missions not yet supported via CLI")
        return

    if args.timeout:
        mission.constraints.timeout_seconds = args.timeout

    # Resolve report path up front so it can be announced before dispatch
    if args.output:
        report_path = Path(args.output)
    else:
        report_path = report_dir() / f"mission-{mission.id}-report.md"

    # Dry run mode
    if args.dry_run:
        print("\n" + "=" * 60)
        print("MISSION PLAN (dry-run)")
        print("=" * 60)
        print(f"Title: {mission.title}")
        print(f"Objective: {mission.objective}")
        print(f"Target: {target}")
        print(f"\nCrew ({len(mission.crew_ids)}):")
        for agent_id in mission.crew_ids:
            agent = cli.ds.read("agent", agent_id)
            if agent:
                print(f"  - {agent['identity']['codename']}")

        print(f"\nTasks ({len(mission.tasks)}):")
        for task in mission.tasks:
            agent = cli.ds.read("agent", task.agent_id)
            agent_name = agent["identity"]["codename"] if agent else task.agent_id
            print(f"  [{task.id}] {task.role} -> {agent_name}")
            if task.depends_on:
                print(f"       depends on: {task.depends_on}")

        print("\nConstraints:")
        print(f"  read_only: {mission.constraints.read_only}")
        print(f"  timeout: {mission.constraints.timeout_seconds}s")
        return

    # Execute mission
    result = await cli.run_mission(mission, report_path)

    # Print summary
    print("\n" + "=" * 60)
    print("MISSION SUMMARY")
    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Tasks completed: {sum(1 for t in result.tasks if t.status.value == 'complete')}/{len(result.tasks)}")

    if result.started_at and result.completed_at:
        duration = (result.completed_at - result.started_at).total_seconds()
        print(f"Duration: {duration:.1f}s")

    # Save report (written on success and failure - failed missions retain
    # partial results and per-task errors)
    cli.save_report(result, report_path)

    # Print report preview
    if result.final_report:
        print("\n" + "-" * 60)
        print("REPORT PREVIEW (first 50 lines):")
        print("-" * 60)
        lines = result.final_report.split("\n")[:50]
        for line in lines:
            print(line)
        if len(result.final_report.split("\n")) > 50:
            print("... (truncated)")


if __name__ == "__main__":
    asyncio.run(main())
