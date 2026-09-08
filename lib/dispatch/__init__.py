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
    HttpProvider,
    OllamaProvider,
    OpenAICompatProvider,
    get_provider,
)

from .syndicate import (
    SyndicateError,
    address_of,
    capability_index,
    capability_violation,
    channel_capabilities,
    endpoint_descriptor,
    load_syndicates,
    required_capabilities,
    resolve,
    transport_of,
    validate_channel,
)

from .builder import (
    MissionBuilder,
    adhoc_task_mission,
    codebase_analysis_mission,
    project_cycle_mission,
    security_audit_mission,
)

from .briefing import (
    Briefing,
    build_briefing,
    render_briefing,
    mission_scope,
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
    "HttpProvider",
    "OllamaProvider",
    "OpenAICompatProvider",
    "get_provider",
    # Syndicate inventory
    "SyndicateError",
    "address_of",
    "capability_index",
    "capability_violation",
    "channel_capabilities",
    "endpoint_descriptor",
    "load_syndicates",
    "required_capabilities",
    "resolve",
    "transport_of",
    "validate_channel",
    # Builder
    "MissionBuilder",
    "adhoc_task_mission",
    "codebase_analysis_mission",
    "project_cycle_mission",
    "security_audit_mission",
    # Briefing
    "Briefing",
    "build_briefing",
    "render_briefing",
    "mission_scope",
    # Logging
    "DispatchLogger",
    "DispatchLogEntry",
    "DispatchLogType",
    "AgentState",
    "get_dispatch_logger",
    "DISPATCH_LOG_HEADERS",
]
