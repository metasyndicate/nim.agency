"""
Briefing - Centralized mission disclosure renderer.

Single source of truth for presenting a mission's scope, crew, tasks,
provider, permissions, prompt provenance, and output artifacts to the
operator BEFORE any agent work is spawned. Used by:

- `nim.agency dispatch`     (pre-dispatch confirmation gate)
- `nim.agency mission plan` (preview without execution)
- `nim.agency mission show` (render a saved mission)

Verbosity levels:
    0  condensed summary (default): scope, crew, tasks w/ READ/WRITE tags,
       provider + permission mode, timeout/concurrency, output paths
    1  + full constraints, instruction/prompt source file paths, config path
    2  + full composed system prompts and full task prompts
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import CONFIG_PATH, get_config, log_dir
from ..data import DataStore, get_datastore
from ..data.composer import AgentComposer, get_composer

from .mission import Mission, MissionConstraints
from .provider import ClaudeCliProvider


def mission_scope(constraints: MissionConstraints) -> str:
    """Map mission constraints to a composer scope.

    Single authority for this mapping - the dispatcher uses it too, so the
    prompts disclosed in a briefing are exactly the prompts executed.
    """
    return "read_only" if constraints.read_only else "supervised"


@dataclass
class Briefing:
    """Assembled disclosure data for a mission."""
    mission: Mission
    agents: dict[str, Optional[dict]]        # agent_id -> entity (None if missing)
    report_path: Path
    provider_name: str
    provider_binary: Optional[str]
    permission_mode: str
    max_concurrent: int
    prompt_sources: dict[str, list[str]] = field(default_factory=dict)
    system_prompts: dict[str, str] = field(default_factory=dict)


def build_briefing(
    mission: Mission,
    ds: Optional[DataStore] = None,
    report_path: Optional[Path] = None,
    composer: Optional[AgentComposer] = None,
    provider: Optional[ClaudeCliProvider] = None,
    verbosity: int = 0,
) -> Briefing:
    """Assemble a Briefing for a mission (built or loaded)."""
    ds = ds or get_datastore()
    cfg = get_config()["dispatch"]

    if report_path is None:
        from ..config import report_dir
        report_path = report_dir() / f"mission-{mission.id}-report.md"

    agents: dict[str, Optional[dict]] = {
        agent_id: ds.read("agent", agent_id) for agent_id in mission.crew_ids
    }

    provider = provider or ClaudeCliProvider()

    briefing = Briefing(
        mission=mission,
        agents=agents,
        report_path=report_path,
        provider_name=cfg["provider"],
        provider_binary=provider.binary_path,
        permission_mode=(
            "claude --print --dangerously-skip-permissions "
            "(unattended; constraints enforced via prompt injection)"
        ),
        max_concurrent=cfg["max_concurrent"],
    )

    # Prompt provenance and full prompts require composition
    if verbosity >= 1:
        composer = composer or get_composer()
        scope = mission_scope(mission.constraints)
        for agent_id, agent in agents.items():
            if agent is None:
                continue
            profile = composer.compose(agent, scope=scope)
            briefing.prompt_sources[agent_id] = profile.sources
            if verbosity >= 2:
                briefing.system_prompts[agent_id] = profile.render_system_prompt()

    return briefing


def render_briefing(briefing: Briefing, verbosity: int = 0) -> str:
    """Render a Briefing as operator-facing text at the given verbosity."""
    m = briefing.mission
    c = m.constraints
    op_tag = "READ" if c.read_only else "READ/WRITE"
    cfg_logging = get_config()["logging"]

    def codename(agent_id: str) -> str:
        agent = briefing.agents.get(agent_id)
        if agent is None:
            return f"{agent_id} (not found in datastore)"
        return agent["identity"]["codename"]

    lines = [
        "=" * 60,
        "MISSION BRIEFING",
        "=" * 60,
        f"MISSION:   {m.title}  [{m.id}]",
        f"OBJECTIVE: {m.objective}",
        f"STATUS:    {m.status.value}",
        f"TARGET:    {c.working_directory or '(current directory)'}",
        f"SCOPE:     {op_tag}"
        + ("  (read-only: no write operations)" if c.read_only else "  (WRITE-CAPABLE)"),
        "",
        f"CREW ({len(m.crew_ids)}):",
    ]

    for agent_id in m.crew_ids:
        agent = briefing.agents.get(agent_id)
        if agent is None:
            lines.append(f"  - {agent_id} (not found in datastore)")
        else:
            ident = agent["identity"]
            arch = agent.get("classification", {}).get("archetype_id", "?")
            lines.append(f"  - {ident['name']} ({ident['codename']})  arch:{arch}")

    lines.extend(["", f"TASKS ({len(m.tasks)}):"])
    for task in m.tasks:
        dep = f"  depends on: {','.join(task.depends_on)}" if task.depends_on else ""
        lines.append(f"  [{task.id}] {task.role} -> {codename(task.agent_id)} [{op_tag}]{dep}")

    lines.extend([
        "",
        "PROVIDER:",
        f"  name:        {briefing.provider_name}",
        f"  binary:      {briefing.provider_binary or '(not found)'}",
        f"  permissions: {briefing.permission_mode}",
        "",
        "LIMITS:",
        f"  timeout:        {c.timeout_seconds}s per task",
        f"  max_concurrent: {briefing.max_concurrent}",
        "",
        "OUTPUTS:",
        f"  report:       {briefing.report_path}",
        f"  mission json: {briefing.report_path.with_suffix('.json')}",
        f"  dispatch log: {log_dir() / cfg_logging['dispatch_csv']} (csv)",
        f"                {log_dir() / cfg_logging['dispatch_json']} (json)",
    ])

    if verbosity >= 1:
        lines.extend([
            "",
            "CONSTRAINTS:",
            f"  read_only:       {c.read_only}",
            f"  local_only:      {c.local_only}",
            f"  max_tokens/task: {c.max_tokens_per_task}",
            f"  allowed_tools:   {', '.join(c.allowed_tools) or '(none)'}",
            f"  blocked_cmds:    {', '.join(c.blocked_commands) or '(none)'}",
            f"  config:          {CONFIG_PATH}",
            "",
            "PROMPT SOURCES (per agent):",
        ])
        if briefing.prompt_sources:
            for agent_id, sources in briefing.prompt_sources.items():
                lines.append(f"  {codename(agent_id)}:")
                for src in sources:
                    lines.append(f"    - {src}")
        else:
            lines.append("  (unavailable - agents missing from datastore)")

    if verbosity >= 2:
        lines.extend(["", "-" * 60, "SYSTEM PROMPTS (full, as executed):", "-" * 60])
        for agent_id, prompt in briefing.system_prompts.items():
            lines.extend([f"", f"--- {codename(agent_id)} ---", prompt])
        lines.extend(["", "-" * 60, "TASK PROMPTS (full):", "-" * 60])
        for task in m.tasks:
            lines.extend([f"", f"--- [{task.id}] {task.role} ---", task.prompt])

    lines.append("=" * 60)
    return "\n".join(lines)
