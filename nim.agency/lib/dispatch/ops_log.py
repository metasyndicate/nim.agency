"""
Dispatch Operations Log - Agent dispatch lifecycle tracking.

Extends the core OpsLogger to track agent work through the full lifecycle:

    IDLE → ASSIGNED → DISPATCHED → ACTIVE → COMPLETE → RETURN

Provides:
- Mission-level tracking (start, progress, complete, fail)
- Task-level tracking (assigned, dispatched, active, complete, fail)
- Agent state transitions with timing
- Correlation IDs for tracing (mission_id, task_id, dispatch_id)

Log entries are written to: ~/.nim/agency/dispatch.log
"""

import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List
import csv
import json
import os


class DispatchLogType(Enum):
    """Dispatch-specific log types."""
    # Mission lifecycle
    MISSION_CREATE = "mission_create"
    MISSION_START = "mission_start"
    MISSION_COMPLETE = "mission_complete"
    MISSION_FAIL = "mission_fail"
    MISSION_ABORT = "mission_abort"

    # Task lifecycle
    TASK_CREATE = "task_create"
    TASK_QUEUED = "task_queued"
    TASK_DISPATCHED = "task_dispatched"
    TASK_ACTIVE = "task_active"
    TASK_COMPLETE = "task_complete"
    TASK_FAIL = "task_fail"
    TASK_TIMEOUT = "task_timeout"

    # Agent lifecycle
    AGENT_IDLE = "agent_idle"
    AGENT_ASSIGNED = "agent_assigned"
    AGENT_DISPATCHED = "agent_dispatched"
    AGENT_ACTIVE = "agent_active"
    AGENT_COMPLETE = "agent_complete"
    AGENT_RETURN = "agent_return"
    AGENT_ERROR = "agent_error"

    # Provider events
    PROVIDER_INVOKE = "provider_invoke"
    PROVIDER_RESPONSE = "provider_response"
    PROVIDER_ERROR = "provider_error"


class AgentState(Enum):
    """Agent operational states."""
    IDLE = "idle"
    ASSIGNED = "assigned"
    DISPATCHED = "dispatched"
    ACTIVE = "active"
    COMPLETE = "complete"
    RETURNING = "returning"
    ERROR = "error"


@dataclass
class DispatchLogEntry:
    """
    Standard dispatch log entry.

    Captures the full context of a dispatch operation:
    - Correlation: dispatch_id, mission_id, task_id
    - Who: agent_id, agent_codename, operator
    - What: log_type, action, agent_state
    - When: timestamp, duration_ms
    - Where: provider, target_host
    - Result: status, output_summary, error
    """
    # Correlation IDs (for tracing)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    dispatch_id: str = ""  # Unique per dispatch session
    mission_id: str = ""
    task_id: str = ""

    # Timestamp
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Who
    agent_id: str = ""
    agent_tag: int = 0
    agent_codename: str = ""
    agent_role: str = ""  # Role in the mission (e.g., "Structure Analyst")
    operator: str = ""

    # What
    log_type: str = "task_dispatched"
    action: str = ""
    agent_state: str = "idle"
    previous_state: str = ""

    # Where
    provider: str = "claude-cli"
    target_host: str = "localhost"
    working_dir: str = ""

    # Timing
    duration_ms: float = 0
    queue_time_ms: float = 0  # Time spent waiting in queue

    # Result
    status: str = "ok"  # ok, fail, timeout, pending
    exit_code: Optional[int] = None
    output_lines: int = 0
    output_tokens: int = 0
    output_summary: str = ""  # First 200 chars of output
    error: str = ""

    # Context
    crew_size: int = 0
    task_index: int = 0
    tasks_total: int = 0
    tasks_complete: int = 0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "dispatch_id": self.dispatch_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "agent_tag": self.agent_tag,
            "agent_codename": self.agent_codename,
            "agent_role": self.agent_role,
            "operator": self.operator,
            "log_type": self.log_type,
            "action": self.action,
            "agent_state": self.agent_state,
            "previous_state": self.previous_state,
            "provider": self.provider,
            "target_host": self.target_host,
            "working_dir": self.working_dir,
            "duration_ms": self.duration_ms,
            "queue_time_ms": self.queue_time_ms,
            "status": self.status,
            "exit_code": self.exit_code,
            "output_lines": self.output_lines,
            "output_tokens": self.output_tokens,
            "output_summary": self.output_summary,
            "error": self.error,
            "crew_size": self.crew_size,
            "task_index": self.task_index,
            "tasks_total": self.tasks_total,
            "tasks_complete": self.tasks_complete,
            "metadata": self.metadata,
        }

    def to_csv_row(self) -> List[str]:
        """Convert to CSV row."""
        return [
            self.timestamp,
            self.dispatch_id,
            self.mission_id,
            self.task_id,
            self.agent_id,
            str(self.agent_tag),
            self.agent_codename,
            self.agent_role,
            self.log_type,
            self.agent_state,
            self.previous_state,
            self.provider,
            self.target_host,
            f"{self.duration_ms:.2f}",
            f"{self.queue_time_ms:.2f}",
            self.status,
            str(self.exit_code) if self.exit_code is not None else "",
            str(self.output_lines),
            str(self.output_tokens),
            self.output_summary[:200],
            self.error[:500],
            str(self.tasks_complete),
            str(self.tasks_total),
            json.dumps(self.metadata) if self.metadata else "",
        ]


# CSV headers for dispatch log
DISPATCH_LOG_HEADERS = [
    "timestamp",
    "dispatch_id",
    "mission_id",
    "task_id",
    "agent_id",
    "agent_tag",
    "agent_codename",
    "agent_role",
    "log_type",
    "agent_state",
    "previous_state",
    "provider",
    "target_host",
    "duration_ms",
    "queue_time_ms",
    "status",
    "exit_code",
    "output_lines",
    "output_tokens",
    "output_summary",
    "error",
    "tasks_complete",
    "tasks_total",
    "metadata",
]


class DispatchLogger:
    """
    Dispatch operations logger with agent lifecycle tracking.

    Usage:
        logger = DispatchLogger()

        # Start a dispatch session
        dispatch_id = logger.start_dispatch()

        # Log mission start
        logger.log_mission_start(
            dispatch_id=dispatch_id,
            mission_id="abc123",
            title="Codebase Analysis",
            crew_size=3,
            tasks_total=3
        )

        # Log agent state transitions
        logger.log_agent_transition(
            dispatch_id=dispatch_id,
            mission_id="abc123",
            task_id="task-1",
            agent_id="agent-xyz",
            agent_codename="ghost.one",
            from_state=AgentState.IDLE,
            to_state=AgentState.ASSIGNED,
            role="Structure Analyst"
        )

        # Log task completion
        logger.log_task_complete(
            dispatch_id=dispatch_id,
            mission_id="abc123",
            task_id="task-1",
            agent_id="agent-xyz",
            duration_ms=45000,
            output_lines=150,
            output_summary="Analysis complete..."
        )
    """

    def __init__(
        self,
        log_path: Optional[Path] = None,
        json_log_path: Optional[Path] = None,
    ):
        """
        Initialize dispatch logger.

        Args:
            log_path: Path to CSV log (default: ~/.nim/agency/dispatch.log)
            json_log_path: Path to JSON log (default: ~/.nim/agency/dispatch.json)
        """
        from ..config import get_config, log_dir
        base = log_dir()
        logging_cfg = get_config()["logging"]

        self.log_path = log_path or (base / logging_cfg["dispatch_csv"])
        self.json_log_path = json_log_path or (base / logging_cfg["dispatch_json"])

        self._ensure_log_files()

        # Track agent states for transition logging
        self._agent_states: Dict[str, AgentState] = {}
        self._agent_dispatch_times: Dict[str, datetime] = {}

    def _ensure_log_files(self) -> None:
        """Create log files with headers if they don't exist."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.log_path.exists():
            with open(self.log_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(DISPATCH_LOG_HEADERS)
            if os.name != 'nt':
                os.chmod(self.log_path, 0o600)

        if not self.json_log_path.exists():
            with open(self.json_log_path, 'w') as f:
                f.write("[]")
            if os.name != 'nt':
                os.chmod(self.json_log_path, 0o600)

    def _write_entry(self, entry: DispatchLogEntry) -> None:
        """Write entry to both CSV and JSON logs."""
        # CSV append
        with open(self.log_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(entry.to_csv_row())

        # JSON append (read, append, write - not ideal for large logs)
        # For production, consider jsonlines format instead
        try:
            with open(self.json_log_path, 'r') as f:
                entries = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            entries = []

        entries.append(entry.to_dict())

        # Keep last 10000 entries
        if len(entries) > 10000:
            entries = entries[-10000:]

        with open(self.json_log_path, 'w') as f:
            json.dump(entries, f, indent=2)

    def log(self, entry: DispatchLogEntry) -> DispatchLogEntry:
        """Log an entry and return it."""
        self._write_entry(entry)
        return entry

    # -------------------------------------------------------------------------
    # Dispatch session management
    # -------------------------------------------------------------------------

    def start_dispatch(self) -> str:
        """Start a new dispatch session, return dispatch_id."""
        dispatch_id = f"dsp-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        return dispatch_id

    # -------------------------------------------------------------------------
    # Mission lifecycle logging
    # -------------------------------------------------------------------------

    def log_mission_start(
        self,
        dispatch_id: str,
        mission_id: str,
        title: str,
        crew_size: int,
        tasks_total: int,
        working_dir: str = "",
        **metadata
    ) -> DispatchLogEntry:
        """Log mission start."""
        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            log_type=DispatchLogType.MISSION_START.value,
            action=f"start:{title}",
            status="ok",
            crew_size=crew_size,
            tasks_total=tasks_total,
            working_dir=working_dir,
            metadata={"title": title, **metadata}
        ))

    def log_mission_complete(
        self,
        dispatch_id: str,
        mission_id: str,
        duration_ms: float,
        tasks_complete: int,
        tasks_total: int,
        **metadata
    ) -> DispatchLogEntry:
        """Log mission completion."""
        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            log_type=DispatchLogType.MISSION_COMPLETE.value,
            action="complete",
            status="ok",
            duration_ms=duration_ms,
            tasks_complete=tasks_complete,
            tasks_total=tasks_total,
            metadata=metadata
        ))

    def log_mission_fail(
        self,
        dispatch_id: str,
        mission_id: str,
        error: str,
        duration_ms: float = 0,
        tasks_complete: int = 0,
        tasks_total: int = 0,
        **metadata
    ) -> DispatchLogEntry:
        """Log mission failure."""
        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            log_type=DispatchLogType.MISSION_FAIL.value,
            action="fail",
            status="fail",
            error=error,
            duration_ms=duration_ms,
            tasks_complete=tasks_complete,
            tasks_total=tasks_total,
            metadata=metadata
        ))

    # -------------------------------------------------------------------------
    # Task lifecycle logging
    # -------------------------------------------------------------------------

    def log_task_dispatched(
        self,
        dispatch_id: str,
        mission_id: str,
        task_id: str,
        agent_id: str,
        agent_codename: str,
        role: str,
        task_index: int = 0,
        tasks_total: int = 0,
        provider: str = "claude-cli",
        **metadata
    ) -> DispatchLogEntry:
        """Log task dispatch to agent."""
        # Track dispatch time for duration calculation
        self._agent_dispatch_times[f"{dispatch_id}:{task_id}"] = datetime.now(timezone.utc)

        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_codename=agent_codename,
            agent_role=role,
            log_type=DispatchLogType.TASK_DISPATCHED.value,
            action=f"dispatch:{role}",
            agent_state=AgentState.DISPATCHED.value,
            previous_state=AgentState.ASSIGNED.value,
            provider=provider,
            status="pending",
            task_index=task_index,
            tasks_total=tasks_total,
            metadata=metadata
        ))

    def log_task_active(
        self,
        dispatch_id: str,
        mission_id: str,
        task_id: str,
        agent_id: str,
        agent_codename: str,
        **metadata
    ) -> DispatchLogEntry:
        """Log task becoming active (provider invoked)."""
        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_codename=agent_codename,
            log_type=DispatchLogType.TASK_ACTIVE.value,
            action="active",
            agent_state=AgentState.ACTIVE.value,
            previous_state=AgentState.DISPATCHED.value,
            status="pending",
            metadata=metadata
        ))

    def log_task_complete(
        self,
        dispatch_id: str,
        mission_id: str,
        task_id: str,
        agent_id: str,
        agent_codename: str,
        duration_ms: float,
        output_lines: int = 0,
        output_tokens: int = 0,
        output_summary: str = "",
        tasks_complete: int = 0,
        tasks_total: int = 0,
        **metadata
    ) -> DispatchLogEntry:
        """Log task completion."""
        # Calculate queue time if we tracked dispatch
        queue_time_ms = 0
        key = f"{dispatch_id}:{task_id}"
        if key in self._agent_dispatch_times:
            dispatch_time = self._agent_dispatch_times.pop(key)
            total_time = (datetime.now(timezone.utc) - dispatch_time).total_seconds() * 1000
            queue_time_ms = max(0, total_time - duration_ms)

        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_codename=agent_codename,
            log_type=DispatchLogType.TASK_COMPLETE.value,
            action="complete",
            agent_state=AgentState.COMPLETE.value,
            previous_state=AgentState.ACTIVE.value,
            status="ok",
            duration_ms=duration_ms,
            queue_time_ms=queue_time_ms,
            output_lines=output_lines,
            output_tokens=output_tokens,
            output_summary=output_summary[:200],
            tasks_complete=tasks_complete,
            tasks_total=tasks_total,
            metadata=metadata
        ))

    def log_task_fail(
        self,
        dispatch_id: str,
        mission_id: str,
        task_id: str,
        agent_id: str,
        agent_codename: str,
        error: str,
        duration_ms: float = 0,
        **metadata
    ) -> DispatchLogEntry:
        """Log task failure."""
        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_codename=agent_codename,
            log_type=DispatchLogType.TASK_FAIL.value,
            action="fail",
            agent_state=AgentState.ERROR.value,
            previous_state=AgentState.ACTIVE.value,
            status="fail",
            error=error,
            duration_ms=duration_ms,
            metadata=metadata
        ))

    # -------------------------------------------------------------------------
    # Agent state tracking
    # -------------------------------------------------------------------------

    def log_agent_transition(
        self,
        dispatch_id: str,
        mission_id: str,
        agent_id: str,
        agent_codename: str,
        from_state: AgentState,
        to_state: AgentState,
        task_id: str = "",
        role: str = "",
        **metadata
    ) -> DispatchLogEntry:
        """Log agent state transition."""
        log_type_map = {
            AgentState.IDLE: DispatchLogType.AGENT_IDLE,
            AgentState.ASSIGNED: DispatchLogType.AGENT_ASSIGNED,
            AgentState.DISPATCHED: DispatchLogType.AGENT_DISPATCHED,
            AgentState.ACTIVE: DispatchLogType.AGENT_ACTIVE,
            AgentState.COMPLETE: DispatchLogType.AGENT_COMPLETE,
            AgentState.RETURNING: DispatchLogType.AGENT_RETURN,
            AgentState.ERROR: DispatchLogType.AGENT_ERROR,
        }

        return self.log(DispatchLogEntry(
            dispatch_id=dispatch_id,
            mission_id=mission_id,
            task_id=task_id,
            agent_id=agent_id,
            agent_codename=agent_codename,
            agent_role=role,
            log_type=log_type_map.get(to_state, DispatchLogType.AGENT_IDLE).value,
            action=f"transition:{from_state.value}->{to_state.value}",
            agent_state=to_state.value,
            previous_state=from_state.value,
            status="ok",
            metadata=metadata
        ))

    # -------------------------------------------------------------------------
    # Query methods
    # -------------------------------------------------------------------------

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent log entries."""
        try:
            with open(self.json_log_path, 'r') as f:
                entries = json.load(f)
            return entries[-limit:]
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def get_by_mission(self, mission_id: str) -> List[Dict[str, Any]]:
        """Get all entries for a mission."""
        return [e for e in self.get_recent(10000) if e.get("mission_id") == mission_id]

    def get_by_dispatch(self, dispatch_id: str) -> List[Dict[str, Any]]:
        """Get all entries for a dispatch session."""
        return [e for e in self.get_recent(10000) if e.get("dispatch_id") == dispatch_id]

    def get_by_agent(self, agent_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get entries for a specific agent."""
        return [e for e in self.get_recent(limit * 10) if e.get("agent_id") == agent_id][-limit:]

    def get_agent_timeline(self, agent_id: str, dispatch_id: str = "") -> List[Dict[str, Any]]:
        """Get agent's state timeline for a dispatch session."""
        entries = self.get_by_agent(agent_id, 1000)
        if dispatch_id:
            entries = [e for e in entries if e.get("dispatch_id") == dispatch_id]
        return sorted(entries, key=lambda e: e.get("timestamp", ""))

    def get_mission_summary(self, mission_id: str) -> Dict[str, Any]:
        """Get summary stats for a mission."""
        entries = self.get_by_mission(mission_id)
        if not entries:
            return {}

        tasks_complete = sum(1 for e in entries if e.get("log_type") == "task_complete")
        tasks_failed = sum(1 for e in entries if e.get("log_type") == "task_fail")
        total_duration = sum(e.get("duration_ms", 0) for e in entries if e.get("log_type") == "task_complete")

        agents = set(e.get("agent_id") for e in entries if e.get("agent_id"))

        return {
            "mission_id": mission_id,
            "dispatch_id": entries[0].get("dispatch_id", ""),
            "tasks_complete": tasks_complete,
            "tasks_failed": tasks_failed,
            "total_duration_ms": total_duration,
            "agents": list(agents),
            "agent_count": len(agents),
            "first_event": entries[0].get("timestamp"),
            "last_event": entries[-1].get("timestamp"),
        }


# Module-level convenience
_default_dispatch_logger: Optional[DispatchLogger] = None


def get_dispatch_logger() -> DispatchLogger:
    """Get the default dispatch logger."""
    global _default_dispatch_logger
    if _default_dispatch_logger is None:
        _default_dispatch_logger = DispatchLogger()
    return _default_dispatch_logger
