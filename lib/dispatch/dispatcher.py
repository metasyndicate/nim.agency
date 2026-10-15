"""
Dispatcher - Coordinates mission execution across agent crews.

The dispatcher:
1. Takes a mission with assigned tasks
2. Composes agent prompts using the composer
3. Dispatches tasks to the provider
4. Collects results and assembles the final report
5. Logs all operations for audit and tracking
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ..config import modality_violation
from ..data import DataStore, get_datastore
from ..data.attribution import banner, mission_scope_label, spawn_context
from ..data.composer import AgentComposer, get_composer
from ..data.economy import Payout, award_mission
from ..data.project import (
    ENGAGEMENT_SWARMING, apply_mission_outcome, transition_engagements,
)

from .mission import Mission, MissionStatus, Task, TaskStatus, TaskResult
from .provider import Provider, get_provider, POLICY_VIOLATION_PREFIX
from .syndicate import capability_violation
from .verification import VERIFICATION_FAILED_PREFIX, Expectation, verify
from .ops_log import DispatchLogger, get_dispatch_logger, AgentState
from . import bird


@dataclass
class DispatchEvent:
    """Event emitted during dispatch."""
    event_type: str  # task_started, task_complete, task_failed, mission_complete, etc.
    mission_id: str
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    message: str = ""
    data: Optional[dict] = None


# Event callback type
EventCallback = Callable[[DispatchEvent], None]


class Dispatcher:
    """
    Coordinates mission execution.

    Usage:
        dispatcher = Dispatcher()

        # Create and configure mission
        mission = Mission(title="Code Analysis", objective="...")
        mission.crew_ids = ["agent-1", "agent-2"]
        # ... add tasks ...

        # Execute with progress callbacks
        def on_event(event):
            print(f"[{event.event_type}] {event.message}")

        await dispatcher.execute(mission, on_event=on_event)

        # Get results
        print(mission.final_report)
    """

    def __init__(
        self,
        datastore: Optional[DataStore] = None,
        composer: Optional[AgentComposer] = None,
        provider: Optional[Provider] = None,
        logger: Optional[DispatchLogger] = None
    ):
        self.ds = datastore or get_datastore()
        self.composer = composer or get_composer()
        self.provider = provider or get_provider(ds=self.ds)
        self.logger = logger or get_dispatch_logger()

        # Cache agents by ID
        self._agent_cache: dict[str, dict] = {}
        self._prompt_cache: dict[str, str] = {}
        self._profile_cache: dict[str, object] = {}

    def _load_agent(self, agent_id: str) -> Optional[dict]:
        """Load an agent by ID."""
        if agent_id in self._agent_cache:
            return self._agent_cache[agent_id]

        agent = self.ds.read("agent", agent_id)
        if agent:
            self._agent_cache[agent_id] = agent
        return agent

    def _get_agent_prompt(self, agent: dict, scope: str = "read_only") -> str:
        """Get the composed system prompt for an agent."""
        cache_key = f"{agent['id']}:{scope}"
        if cache_key in self._prompt_cache:
            return self._prompt_cache[cache_key]

        prompt = self._get_profile(agent, scope).render_system_prompt()
        self._prompt_cache[cache_key] = prompt
        return prompt

    def _get_profile(self, agent: dict, scope: str = "read_only"):
        """The composed OperationalProfile for an agent (cached)."""
        cache_key = f"{agent['id']}:{scope}"
        if cache_key not in self._profile_cache:
            self._profile_cache[cache_key] = self.composer.compose(
                agent, scope=scope)
        return self._profile_cache[cache_key]

    def _verify_result(self, task: Task, result: TaskResult) -> None:
        """Settle a task's known-answer check before payroll sees it.

        A wrong answer to a task with an expectation is a failed task: the
        paymaster only pays completed work, and the conduct ledger is not
        charged - an honest miss is not misconduct.
        """
        expectation = Expectation.from_dict(task.expect)
        if expectation is None or result.status != TaskStatus.COMPLETE:
            return
        verdict = verify(expectation, result.output or "")
        result.verdict = verdict.to_dict()
        if not verdict.passed:
            result.status = TaskStatus.FAILED
            result.error = f"{VERIFICATION_FAILED_PREFIX}: {verdict.detail}"

    async def execute(
        self,
        mission: Mission,
        on_event: Optional[EventCallback] = None,
        max_concurrent: int = 2
    ) -> Mission:
        """
        Execute a mission.

        Args:
            mission: The mission to execute
            on_event: Optional callback for progress events
            max_concurrent: Max concurrent task executions

        Returns:
            The mission with results populated
        """
        # Start dispatch session for logging
        dispatch_id = self.logger.start_dispatch()
        mission_start_time = datetime.now(timezone.utc)
        tasks_complete_count = 0

        def emit(event_type: str, message: str, task_id: str = None, agent_id: str = None, data: dict = None):
            if on_event:
                on_event(DispatchEvent(
                    event_type=event_type,
                    mission_id=mission.id,
                    task_id=task_id,
                    agent_id=agent_id,
                    message=message,
                    data=data
                ))

        # Global modality backstop - the CLI fails the briefing first, but
        # nothing dispatches past the posture regardless of entry point
        refusal = modality_violation(mission.constraints.read_only,
                                     mission.constraints.local_only)
        if not refusal:
            # Capability backstop: a channel that cannot serve this mission's
            # requirements would answer from imagination rather than fail.
            # Providers built without inventory context (test stubs) skip it.
            syndicate = getattr(self.provider, "syndicate", None)
            channel = getattr(self.provider, "channel", None)
            if syndicate and channel:
                refusal = capability_violation(
                    mission.constraints, syndicate, channel, self.ds)
        if refusal:
            emit("error", f"Dispatch refused: {refusal}")
            self.logger.log_mission_fail(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                error=f"modality refusal: {refusal}"
            )
            mission.status = MissionStatus.FAILED
            return mission

        # Stamp the spawn context (attribution: the operator/host/session
        # identity this crew piggybacks on) and the syndicate that served it
        if mission.dispatched_by is None:
            mission.dispatched_by = spawn_context()
        mission.provider_address = getattr(
            getattr(self.provider, "config", None), "name", None)

        # Validate mission
        if not mission.tasks:
            emit("error", "Mission has no tasks")
            self.logger.log_mission_fail(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                error="Mission has no tasks"
            )
            mission.status = MissionStatus.FAILED
            return mission

        if not self.provider.is_available():
            reason = getattr(self.provider, "unavailable_reason",
                             "provider not available")
            emit("error", f"Provider not available: {reason}")
            self.logger.log_mission_fail(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                error=f"Provider not available: {reason}"
            )
            mission.status = MissionStatus.FAILED
            return mission

        # Load all agents
        for agent_id in mission.crew_ids:
            agent = self._load_agent(agent_id)
            if not agent:
                emit("error", f"Agent not found: {agent_id}")
                self.logger.log_mission_fail(
                    dispatch_id=dispatch_id,
                    mission_id=mission.id,
                    error=f"Agent not found: {agent_id}"
                )
                mission.status = MissionStatus.FAILED
                return mission

        # Log mission start
        self.logger.log_mission_start(
            dispatch_id=dispatch_id,
            mission_id=mission.id,
            title=mission.title,
            crew_size=len(mission.crew_ids),
            tasks_total=len(mission.tasks),
            working_dir=mission.constraints.working_directory or ""
        )

        emit("mission_started", f"Mission '{mission.title}' started with {len(mission.tasks)} tasks")
        mission.status = MissionStatus.DISPATCHED
        mission.started_at = datetime.now()

        # Project-tagged missions drive engagement state on the project
        project = self._mission_project(mission)
        if project:
            from ..data.worklog import project_record_path, queue_banner
            emit("queue", queue_banner(
                project, project_record_path(self.ds, project)))
            moved = transition_engagements(
                self.ds, project, ENGAGEMENT_SWARMING,
                agent_ids=mission.crew_ids)
            emit("engagement",
                 f"{moved} engagement(s) -> swarming on {project['slug']}")

        # Determine scope from constraints (shared with briefing so disclosed
        # prompts match executed prompts)
        from .briefing import mission_scope
        scope = mission_scope(mission.constraints)

        # Process tasks respecting dependencies. The channel's own ceiling
        # wins: one workstation GPU serving a 30B model is not a fleet, and
        # oversubscribing it only queues requests behind each other.
        channel_limit = getattr(
            getattr(self.provider, "config", None), "max_concurrent", 0)
        if channel_limit and channel_limit < max_concurrent:
            emit("provider", f"concurrency clamped {max_concurrent} -> "
                             f"{channel_limit} by channel limit")
            max_concurrent = channel_limit
        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_task(task: Task, task_index: int):
            nonlocal tasks_complete_count

            async with semaphore:
                agent = self._load_agent(task.agent_id)
                if not agent:
                    task.status = TaskStatus.FAILED
                    task.result = TaskResult(
                        task_id=task.id,
                        agent_id=task.agent_id,
                        status=TaskStatus.FAILED,
                        output="",
                        error=f"Agent not found: {task.agent_id}"
                    )
                    self.logger.log_task_fail(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename="unknown",
                        error=f"Agent not found: {task.agent_id}"
                    )
                    emit("task_failed", f"Agent not found: {task.agent_id}", task.id, task.agent_id)
                    return

                # Get agent's system prompt, and stamp the depot tools the
                # composed profile grants: a harness offers functions only
                # within them
                system_prompt = self._get_agent_prompt(agent, scope)
                task.tool_grants = [
                    t.slug for t in self._get_profile(agent, scope).tools]
                agent_name = agent["identity"]["codename"]
                agent_tag = agent["identity"].get("tag", 0)

                # Log task dispatch
                self.logger.log_task_dispatched(
                    dispatch_id=dispatch_id,
                    mission_id=mission.id,
                    task_id=task.id,
                    agent_id=task.agent_id,
                    agent_codename=agent_name,
                    role=task.role,
                    task_index=task_index,
                    tasks_total=len(mission.tasks),
                    provider=mission.provider_address or "",
                )

                emit("task_started", f"Task '{task.role}' assigned to {agent_name}", task.id, task.agent_id)
                task.status = TaskStatus.RUNNING

                # Log task active
                self.logger.log_task_active(
                    dispatch_id=dispatch_id,
                    mission_id=mission.id,
                    task_id=task.id,
                    agent_id=task.agent_id,
                    agent_codename=agent_name
                )

                # Execute via provider
                task_start = datetime.now(timezone.utc)
                # BIRD: one ledger per task; every exchange with the
                # syndicate records its anomalies there
                ledger_token = bird.begin(task.id, self.ds)
                try:
                    result = await self.provider.execute(system_prompt, task, mission.constraints)
                    self._verify_result(task, result)
                    bird.note_result(result)
                finally:
                    anomalies = bird.end(ledger_token)
                result.anomalies = anomalies + list(result.anomalies or [])
                task_duration = (datetime.now(timezone.utc) - task_start).total_seconds() * 1000

                for problem in result.anomalies:
                    self.logger.log_anomaly(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename=agent_name,
                        problem=problem,
                        provider=mission.provider_address or "",
                    )
                    emit("bird", bird.summary_line(problem), task.id, task.agent_id)

                for call in result.tool_calls or []:
                    self.logger.log_tool_call(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename=agent_name,
                        call=call,
                        provider=mission.provider_address or "",
                        working_dir=mission.constraints.working_directory or "",
                    )
                    emit("tool", _tool_call_line(call), task.id, task.agent_id)

                if result.verdict is not None:
                    self.logger.log_task_verified(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename=agent_name,
                        passed=result.verdict["passed"],
                        detail=result.verdict["detail"],
                    )
                    emit("verify",
                         ("PASS" if result.verdict["passed"] else "FAIL")
                         + f" known-answer check: {result.verdict['detail']}",
                         task.id, task.agent_id)

                task.result = result
                task.status = result.status

                if result.status == TaskStatus.COMPLETE:
                    tasks_complete_count += 1
                    output_lines = len(result.output.split('\n')) if result.output else 0
                    output_summary = result.output[:200] if result.output else ""

                    self.logger.log_task_complete(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename=agent_name,
                        duration_ms=task_duration,
                        output_lines=output_lines,
                        output_tokens=result.tokens_used,
                        output_summary=output_summary,
                        tasks_complete=tasks_complete_count,
                        tasks_total=len(mission.tasks)
                    )
                    emit("task_complete", f"Task '{task.role}' completed by {agent_name}", task.id, task.agent_id)
                else:
                    violation_note = ""
                    if result.error and result.error.startswith(POLICY_VIOLATION_PREFIX):
                        total = self._record_violation(agent)
                        violation_note = f" [conduct ledger: {total} violation(s) for {agent_name}]"

                    self.logger.log_task_fail(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename=agent_name,
                        error=result.error or "Unknown error",
                        duration_ms=task_duration
                    )
                    emit("task_failed", f"Task '{task.role}' failed: {result.error}{violation_note}", task.id, task.agent_id)

        # Execute tasks in dependency order
        task_index = 0
        while not mission.all_complete() and not mission.any_failed():
            ready_tasks = mission.get_ready_tasks()
            if not ready_tasks:
                # No tasks ready but not all complete - might be stuck
                if not any(t.status == TaskStatus.RUNNING for t in mission.tasks):
                    emit("error", "No tasks ready and none running - possible dependency cycle")
                    self.logger.log_mission_fail(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        error="Dependency cycle detected",
                        tasks_complete=tasks_complete_count,
                        tasks_total=len(mission.tasks)
                    )
                    mission.status = MissionStatus.FAILED
                    break
                # Wait for running tasks
                await asyncio.sleep(0.5)
                continue

            # Execute ready tasks
            tasks_with_index = [(t, task_index + i) for i, t in enumerate(ready_tasks)]
            task_index += len(ready_tasks)
            await asyncio.gather(*[run_task(t, idx) for t, idx in tasks_with_index])

        # Mission complete
        mission.completed_at = datetime.now()
        mission_duration = (datetime.now(timezone.utc) - mission_start_time).total_seconds() * 1000

        if mission.any_failed():
            mission.status = MissionStatus.FAILED
            failed_count = sum(1 for t in mission.tasks if t.status == TaskStatus.FAILED)
            # Completed tasks still pay out on a failed mission - only the
            # completion bonus is forfeit
            payouts = self._run_payroll(mission, emit)
            # Assemble a report even on failure so partial results and
            # per-task errors are never lost.
            mission.final_report = self._assemble_report(mission, payouts)
            self.logger.log_mission_fail(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                error=f"{failed_count} tasks failed",
                duration_ms=mission_duration,
                tasks_complete=tasks_complete_count,
                tasks_total=len(mission.tasks)
            )
            emit("mission_failed", f"Mission failed - {failed_count} tasks failed")
            self._settle_engagements(project, mission, emit)
        else:
            mission.status = MissionStatus.COLLECTING
            emit("collecting", "Assembling final report...")

            # Flip to COMPLETE before assembly so the report snapshot
            # records the terminal status, not the transient collecting state
            mission.status = MissionStatus.COMPLETE
            payouts = self._run_payroll(mission, emit)
            mission.final_report = self._assemble_report(mission, payouts)

            self.logger.log_mission_complete(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                duration_ms=mission_duration,
                tasks_complete=tasks_complete_count,
                tasks_total=len(mission.tasks)
            )
            emit("mission_complete", f"Mission complete!")
            self._settle_engagements(project, mission, emit)

        return mission

    def _mission_project(self, mission: Mission) -> Optional[dict]:
        """Resolve a mission's tagged project, if any (audit-logged)."""
        project_id = getattr(mission, "project_id", None)
        if not project_id:
            return None
        project = self.ds.read("project", project_id)
        if project:
            from ..data.worklog import log_queue_access, project_record_path
            log_queue_access("dispatch", project=project,
                             path=project_record_path(self.ds, project),
                             detail=f"mission {mission.id} engagement sync")
        return project

    def _settle_engagements(self, project: Optional[dict],
                            mission: Mission, emit) -> None:
        """Drive engagement status from the mission outcome: complete ->
        review, failed -> smoked (suspended pending operator)."""
        if not project:
            return
        moved, status_id = apply_mission_outcome(
            self.ds, project, mission.status.value,
            agent_ids=mission.crew_ids)
        status = self.ds.get_reference_by_id("assignment_status", status_id)
        label = (status or {}).get("slug", status_id)
        emit("engagement",
             f"{moved} engagement(s) -> {label} on {project['slug']}")

    def _run_payroll(self, mission: Mission, emit) -> list[Payout]:
        """Settle XP/gold for the mission and refresh the agent cache so
        report rendering sees post-payroll state."""
        payouts = award_mission(self.ds, mission)
        for payout in payouts:
            self._agent_cache[payout.agent["id"]] = payout.agent
            emit("payroll", payout.summary_line(),
                 agent_id=payout.agent["id"])
        return payouts

    def _record_violation(self, agent: dict) -> int:
        """Increment the agent's conduct ledger after a provider policy refusal.

        Returns the agent's new total violation count.
        """
        provider_name = getattr(getattr(self.provider, "config", None), "name", "unknown")
        prior = agent.get("conduct") or {}
        by_provider = dict(prior.get("by_provider") or {})
        by_provider[provider_name] = by_provider.get(provider_name, 0) + 1

        conduct = {
            "violations": prior.get("violations", 0) + 1,
            "by_provider": by_provider,
            "last_violation_at": datetime.now(timezone.utc).isoformat(),
        }
        updated = self.ds.update("agent", agent["id"], {"conduct": conduct})
        if updated:
            self._agent_cache[agent["id"]] = updated
        return conduct["violations"]

    def execute_sync(
        self,
        mission: Mission,
        on_event: Optional[EventCallback] = None,
        max_concurrent: int = 2
    ) -> Mission:
        """Synchronous wrapper for execute."""
        return asyncio.run(self.execute(mission, on_event, max_concurrent))

    def _assemble_report(
        self, mission: Mission, payouts: Optional[list[Payout]] = None
    ) -> str:
        """Assemble the final report from task results."""
        lines = [
            f"# Mission Report: {mission.title}",
            "",
            f"**Mission ID:** {mission.id}",
            f"**Objective:** {mission.objective}",
            f"**Status:** {mission.status.value}",
            f"**Started:** {mission.started_at.strftime('%Y-%m-%d %H:%M:%S') if mission.started_at else 'N/A'}",
            f"**Completed:** {mission.completed_at.strftime('%Y-%m-%d %H:%M:%S') if mission.completed_at else 'N/A'}",
            "",
            f"## Crew ({len(mission.crew_ids)} agents)",
            ""
        ]

        # List crew members
        for agent_id in mission.crew_ids:
            agent = self._load_agent(agent_id)
            if agent:
                name = agent["identity"]["name"]
                codename = agent["identity"]["codename"]
                lines.append(f"- **{name}** ({codename})")
            else:
                lines.append(f"- {agent_id} (not found)")

        lines.extend(["", "---", "", "## Task Results", ""])

        # Include each task result
        for task in mission.tasks:
            agent = self._load_agent(task.agent_id)
            agent_name = agent["identity"]["codename"] if agent else task.agent_id

            lines.extend([
                f"### {task.role}",
                f"**Agent:** {agent_name}",
                f"**Status:** {task.status.value}",
                ""
            ])

            if task.result:
                if task.result.verdict is not None:
                    mark = "PASS" if task.result.verdict["passed"] else "FAIL"
                    lines.extend([
                        f"**Known-answer check:** {mark} - "
                        f"{task.result.verdict['detail']}",
                        "",
                    ])
                if task.result.tool_calls:
                    lines.extend(["#### Tool Calls", ""])
                    for call in task.result.tool_calls:
                        lines.append(f"- {_tool_call_line(call)}")
                    lines.append("")
                if task.result.anomalies:
                    lines.extend(["#### Relay Anomalies (BIRD)", ""])
                    for problem in task.result.anomalies:
                        lines.append(f"- {bird.summary_line(problem)}")
                    lines.append("")
                if task.result.output:
                    lines.extend([
                        "#### Output",
                        "",
                        task.result.output,
                        ""
                    ])
                if task.result.error:
                    lines.extend([
                        "#### Error",
                        "",
                        f"```\n{task.result.error}\n```",
                        ""
                    ])

            lines.append("---")
            lines.append("")

        if payouts:
            lines.extend(["## Payroll", ""])
            for payout in payouts:
                lines.append(f"- {payout.summary_line()}")
            lines.extend(["", "---", ""])

        # Attribution footer (lib.data.attribution banner convention)
        pay_by_agent = {p.agent["id"]: p.gold for p in (payouts or [])}
        scope = mission_scope_label(mission.constraints.read_only)
        lines.extend(["## Attribution", ""])
        for agent_id in mission.crew_ids:
            agent = self._load_agent(agent_id)
            if agent:
                lines.append(f"- {banner(agent, mission=mission, pay_gold=pay_by_agent.get(agent_id), ctx=mission.dispatched_by, ds=self.ds, scope=scope)}")
        lines.extend(["", "---", ""])

        return "\n".join(lines)


def _tool_call_line(call: dict) -> str:
    """One-line rendering of a harness tool call for events and reports."""
    args = ", ".join(f"{k}={v!r}" for k, v in
                     (call.get("arguments") or {}).items())
    outcome = call.get("outcome", "ok")
    tail = call.get("summary", "") if outcome == "ok" else call.get("error", "")
    return (f"[{call.get('iteration', '?')}] {call.get('function', '?')}"
            f"({args}) -> {outcome}: {tail}")[:240]


def create_dispatcher() -> Dispatcher:
    """Create a dispatcher instance."""
    return Dispatcher()
