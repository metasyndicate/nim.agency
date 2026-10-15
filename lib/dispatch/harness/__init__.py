"""
Harness - the agency's own tool loop for rogue (unplatformed) syndicates.

A platformed syndicate brings its own loop; a bare model endpoint emits
tool calls and nothing runs them. This package runs them:

    executor.py   policy-enforced execution of one call (confinement,
                  offer, read-only, per-call deadline)
    loop.py       the turn loop around a chat-capable provider, and the
                  join that decides which functions a task is offered
"""

from .executor import (
    CallOutcome, CallRefused, ExecutionPolicy, Executor, glob_regex,
)
from .loop import (
    ChatTurn, ProfileMissing, ToolCall, channel_profile, harness_config,
    offered_functions, parse_arguments, run, tool_schema,
)

__all__ = [
    "CallOutcome",
    "CallRefused",
    "ChatTurn",
    "ExecutionPolicy",
    "Executor",
    "ProfileMissing",
    "ToolCall",
    "channel_profile",
    "glob_regex",
    "harness_config",
    "offered_functions",
    "parse_arguments",
    "run",
    "tool_schema",
]
