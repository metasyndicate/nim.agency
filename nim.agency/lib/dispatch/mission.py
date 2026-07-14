"""
Mission - Work order definitions for agent dispatch.

A Mission is a structured work order that defines:
- Objective: What needs to be accomplished
- Crew: Which agents are assigned
- Tasks: Individual work items distributed to agents
- Constraints: Operational limits (read-only, time limits, etc.)
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class MissionStatus(Enum):
    """Mission lifecycle states."""
    DRAFT = "draft"           # Being planned
    STAGED = "staged"         # Ready for dispatch
    DISPATCHED = "dispatched" # In progress
    COLLECTING = "collecting" # Gathering results
    COMPLETE = "complete"     # All done
    FAILED = "failed"         # Mission failed
    ABORTED = "aborted"       # Manually stopped


class TaskStatus(Enum):
    """Individual task states."""
    PENDING = "pending"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class TaskResult:
    """Result from a completed task."""
    task_id: str
    agent_id: str
    status: TaskStatus
    output: str
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tokens_used: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "tokens_used": self.tokens_used
        }


@dataclass
class Task:
    """Individual work item for an agent."""
    id: str
    agent_id: str
    prompt: str
    role: str  # What role this task plays in the mission
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[TaskResult] = None
    depends_on: list[str] = field(default_factory=list)  # Task IDs this depends on

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "prompt": self.prompt,
            "role": self.role,
            "status": self.status.value,
            "result": self.result.to_dict() if self.result else None,
            "depends_on": self.depends_on
        }


@dataclass
class MissionConstraints:
    """Operational constraints for a mission."""
    read_only: bool = True           # No write operations
    local_only: bool = True          # Local host only
    max_tokens_per_task: int = 4096  # Token limit per task
    timeout_seconds: int = 300       # 5 minute timeout per task
    allowed_tools: list[str] = field(default_factory=lambda: [
        "Read", "Glob", "Grep", "Bash"  # Default safe tools
    ])
    blocked_commands: list[str] = field(default_factory=lambda: [
        "rm", "mv", "chmod", "chown", "sudo", "su"  # Blocked for safety
    ])
    working_directory: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "read_only": self.read_only,
            "local_only": self.local_only,
            "max_tokens_per_task": self.max_tokens_per_task,
            "timeout_seconds": self.timeout_seconds,
            "allowed_tools": self.allowed_tools,
            "blocked_commands": self.blocked_commands,
            "working_directory": self.working_directory
        }


@dataclass
class Mission:
    """
    A work order for a crew of agents.

    Example:
        mission = Mission(
            title="Codebase Analysis",
            objective="Analyze the repository structure and provide a technical summary",
            crew_ids=["agent-1", "agent-2", "agent-3"],
        )
        mission.add_task(Task(...))
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    objective: str = ""
    crew_ids: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    constraints: MissionConstraints = field(default_factory=MissionConstraints)
    status: MissionStatus = MissionStatus.DRAFT

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Results
    final_report: Optional[str] = None

    def add_task(self, task: Task):
        """Add a task to the mission."""
        self.tasks.append(task)

    def get_ready_tasks(self) -> list[Task]:
        """Get tasks that are ready to run (dependencies satisfied)."""
        completed_ids = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETE}
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            if all(dep in completed_ids for dep in task.depends_on):
                ready.append(task)
        return ready

    def all_complete(self) -> bool:
        """Check if all tasks are complete."""
        return all(t.status == TaskStatus.COMPLETE for t in self.tasks)

    def any_failed(self) -> bool:
        """Check if any tasks failed."""
        return any(t.status == TaskStatus.FAILED for t in self.tasks)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "crew_ids": self.crew_ids,
            "tasks": [t.to_dict() for t in self.tasks],
            "constraints": self.constraints.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "final_report": self.final_report
        }

    def save(self, path: Path):
        """Save mission to JSON file."""
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "Mission":
        """Load mission from JSON file."""
        with open(path) as f:
            data = json.load(f)

        mission = cls(
            id=data["id"],
            title=data["title"],
            objective=data["objective"],
            crew_ids=data["crew_ids"],
            status=MissionStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
        )

        if data.get("started_at"):
            mission.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            mission.completed_at = datetime.fromisoformat(data["completed_at"])

        mission.final_report = data.get("final_report")

        # Load constraints
        c = data.get("constraints", {})
        mission.constraints = MissionConstraints(
            read_only=c.get("read_only", True),
            local_only=c.get("local_only", True),
            max_tokens_per_task=c.get("max_tokens_per_task", 4096),
            timeout_seconds=c.get("timeout_seconds", 300),
            allowed_tools=c.get("allowed_tools", []),
            blocked_commands=c.get("blocked_commands", []),
            working_directory=c.get("working_directory")
        )

        # Load tasks
        for t in data.get("tasks", []):
            task = Task(
                id=t["id"],
                agent_id=t["agent_id"],
                prompt=t["prompt"],
                role=t["role"],
                status=TaskStatus(t["status"]),
                depends_on=t.get("depends_on", [])
            )
            if t.get("result"):
                r = t["result"]
                task.result = TaskResult(
                    task_id=r["task_id"],
                    agent_id=r["agent_id"],
                    status=TaskStatus(r["status"]),
                    output=r["output"],
                    error=r.get("error"),
                    tokens_used=r.get("tokens_used", 0)
                )
            mission.tasks.append(task)

        return mission
