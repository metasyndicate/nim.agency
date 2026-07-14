"""
NIM Dispatch - Agent task dispatch and execution system.

This module provides:
- Mission: Work order definitions
- Dispatcher: Coordinates execution across agents
- Provider: Interface to LLM backends (claude CLI)
- Builder: Convenient mission creation helpers
- Logging: Dispatch lifecycle tracking and audit
"""

from .mission import (
    Mission,
    MissionStatus,
    MissionConstraints,
    Task,
    TaskStatus,
    TaskResult,
)

from .dispatcher import (
    Dispatcher,
    DispatchEvent,
    create_dispatcher,
)

from .provider import (
    Provider,
    ProviderConfig,
    ClaudeCliProvider,
    get_provider,
)

from .builder import (
    MissionBuilder,
    codebase_analysis_mission,
    security_audit_mission,
)

from .ops_log import (
    DispatchLogger,
    DispatchLogEntry,
    DispatchLogType,
    AgentState,
    get_dispatch_logger,
    DISPATCH_LOG_HEADERS,
)

__all__ = [
    # Mission
    "Mission",
    "MissionStatus",
    "MissionConstraints",
    "Task",
    "TaskStatus",
    "TaskResult",
    # Dispatcher
    "Dispatcher",
    "DispatchEvent",
    "create_dispatcher",
    # Provider
    "Provider",
    "ProviderConfig",
    "ClaudeCliProvider",
    "get_provider",
    # Builder
    "MissionBuilder",
    "codebase_analysis_mission",
    "security_audit_mission",
    # Logging
    "DispatchLogger",
    "DispatchLogEntry",
    "DispatchLogType",
    "AgentState",
    "get_dispatch_logger",
    "DISPATCH_LOG_HEADERS",
]
