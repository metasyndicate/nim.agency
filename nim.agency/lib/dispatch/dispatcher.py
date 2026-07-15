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

from ..data import DataStore, get_datastore
from ..data.composer import AgentComposer, get_composer

from .mission import Mission, MissionStatus, Task, TaskStatus, TaskResult
from .provider import Provider, ClaudeCliProvider, get_provider
from .ops_log import DispatchLogger, get_dispatch_logger, AgentState


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
        self.provider = provider or get_provider("claude-cli")
        self.logger = logger or get_dispatch_logger()

        # Cache agents by ID
        self._agent_cache: dict[str, dict] = {}
        self._prompt_cache: dict[str, str] = {}

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

        profile = self.composer.compose(agent, scope=scope)
        prompt = profile.render_system_prompt()
        self._prompt_cache[cache_key] = prompt
        return prompt

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
            emit("error", "Provider not available (claude CLI not found)")
            self.logger.log_mission_fail(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                error="Provider not available"
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

        # Determine scope from constraints (shared with briefing so disclosed
        # prompts match executed prompts)
        from .briefing import mission_scope
        scope = mission_scope(mission.constraints)

        # Process tasks respecting dependencies
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

                # Get agent's system prompt
                system_prompt = self._get_agent_prompt(agent, scope)
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
                    tasks_total=len(mission.tasks)
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
                result = await self.provider.execute(system_prompt, task, mission.constraints)
                task_duration = (datetime.now(timezone.utc) - task_start).total_seconds() * 1000

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
                    self.logger.log_task_fail(
                        dispatch_id=dispatch_id,
                        mission_id=mission.id,
                        task_id=task.id,
                        agent_id=task.agent_id,
                        agent_codename=agent_name,
                        error=result.error or "Unknown error",
                        duration_ms=task_duration
                    )
                    emit("task_failed", f"Task '{task.role}' failed: {result.error}", task.id, task.agent_id)

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
            # Assemble a report even on failure so partial results and
            # per-task errors are never lost.
            mission.final_report = self._assemble_report(mission)
            self.logger.log_mission_fail(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                error=f"{failed_count} tasks failed",
                duration_ms=mission_duration,
                tasks_complete=tasks_complete_count,
                tasks_total=len(mission.tasks)
            )
            emit("mission_failed", f"Mission failed - {failed_count} tasks failed")
        else:
            mission.status = MissionStatus.COLLECTING
            emit("collecting", "Assembling final report...")

            # Assemble final report
            mission.final_report = self._assemble_report(mission)
            mission.status = MissionStatus.COMPLETE

            self.logger.log_mission_complete(
                dispatch_id=dispatch_id,
                mission_id=mission.id,
                duration_ms=mission_duration,
                tasks_complete=tasks_complete_count,
                tasks_total=len(mission.tasks)
            )
            emit("mission_complete", f"Mission complete!")

        return mission

    def execute_sync(
        self,
        mission: Mission,
        on_event: Optional[EventCallback] = None,
        max_concurrent: int = 2
    ) -> Mission:
        """Synchronous wrapper for execute."""
        return asyncio.run(self.execute(mission, on_event, max_concurrent))

    def _assemble_report(self, mission: Mission) -> str:
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

        return "\n".join(lines)


def create_dispatcher() -> Dispatcher:
    """Create a dispatcher instance."""
    return Dispatcher()
