"""
Loop - the client-side tool loop that gives a rogue syndicate hands.

A model is a pure function: tokens in, tokens out. A platformed syndicate
(claude_cli) runs its own loop; a bare endpoint runs none, so the agency
supplies it here:

    tools    = schemas(harness functions offered to this task)
    messages = [system: composed persona, user: task]
    for i in 1..max_iterations:
        turn = provider.chat(messages, tools)
        if no tool calls: return turn.content
        for call in turn.tool_calls:
            outcome = executor.run(call)        # policy + real execution
            messages.append(tool result)
            transcript.append(call, outcome)    # audit-logged by dispatcher
    fail: iterations exhausted

Bounded by max_iterations, calls per turn, the mission's wall-clock
timeout, a cumulative token budget, and the channel's context window.
Transport-agnostic: any provider exposing `chat()` /
`tool_result_message()` can be wrapped.

The context window is budgeted, not just checked. Ollama silently drops
the oldest messages when a prompt overflows num_ctx - which drops the task
itself, and the model then answers a question nobody asked. So each turn's
tool results are clipped to the room left in the window, estimated from
the tokens-per-character ratio the endpoint actually reported.

What gets offered is a declarative join over existing data:

    harness_function rows
      ∩ mission allowed_tools            (MissionConstraints)
      ∩ channel-granted capabilities     (via channel_capability.harness_tools)
      ∩ agent depot grants               (OperationalProfile.tools, when the
                                          function names a depot_tool)

The harness guarantees grounding, not correctness: a model reading a real
listing can still miscount it. Known-answer checks (verification.py) are
the measure of correctness.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...config import get_config
from ...data import DataStore, get_datastore
from ..mission import MissionConstraints, Task, TaskResult, TaskStatus
from .. import bird
from ..provider import truncation_error
from ..syndicate import (
    capability_index, capability_of_tool, channel_capabilities,
)
from .executor import CallOutcome, ExecutionPolicy, Executor


HARNESS_DEFAULTS = {
    "max_iterations": 12,
    "max_calls_per_turn": 8,
    "call_timeout_seconds": 30,
    "max_result_chars": 16000,
    "token_budget": 200000,
}


class ProfileMissing(LookupError):
    """A channel names a harness profile that is not in the table."""


def channel_profile(channel: Optional[dict],
                    ds: Optional[DataStore] = None) -> Optional[dict]:
    """The harness profile a channel names (channels[].harness_profile),
    or None when it names none. A name that does not resolve raises: the
    harness fails closed rather than run unprofiled."""
    slug = (channel or {}).get("harness_profile")
    if not slug:
        return None
    profile = (ds or get_datastore()).get_reference_by_slug("harness_profile", slug)
    if profile is None:
        raise ProfileMissing(f"channel {channel.get('slug')!r} names harness "
                             f"profile {slug!r}, which does not exist")
    return profile


def harness_config(channel: Optional[dict] = None,
                   ds: Optional[DataStore] = None) -> dict:
    """Loop limits: built-in defaults, then etc/agency.json's harness
    block, then the channel's harness profile."""
    cfg = dict(HARNESS_DEFAULTS)
    cfg.update({k: v for k, v in (get_config().get("harness") or {}).items()
                if k in HARNESS_DEFAULTS})
    profile = channel_profile(channel, ds) if channel else None
    if profile:
        cfg.update({k: v for k, v in (profile.get("limits") or {}).items()
                    if k in HARNESS_DEFAULTS})
    return cfg


@dataclass
class ToolCall:
    """One call a model emitted, normalized across transports."""
    id: Optional[str]
    name: str
    arguments: Optional[dict]
    # Set when the transport delivered arguments that are not a JSON object
    parse_error: Optional[str] = None


@dataclass
class ChatTurn:
    """One assistant turn, normalized across transports."""
    content: str
    tool_calls: list[ToolCall]
    # The assistant message exactly as it must be replayed into history
    message: dict
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Raw finish reason as the endpoint reported it (None when it does not)
    finish_reason: Optional[str] = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LoopState:
    transcript: list[dict] = field(default_factory=list)
    tokens: int = 0
    last_content: str = ""
    # Set once the window runs low: the model has been told to answer
    wrap_up: bool = False


# Before any turn is measured, assume 3 characters per token: denser than
# English prose, which is the safe direction for JSON and code.
INITIAL_TOKENS_PER_CHAR = 1 / 3

# Calibration never trusts a ratio below 1 token per 5 characters. Ollama
# 0.33 reports the full prompt even on a KV-cache hit (verified
# 2026-10-14), but an endpoint that counted only freshly evaluated tokens
# would otherwise talk the budget into overfilling the window.
MIN_TOKENS_PER_CHAR = 1 / 5

# Smallest useful room for one turn of tool results (tokens). Below this
# the conversation cannot advance without overflowing.
MIN_RESULT_ROOM = 512

# Fraction of the estimated room actually spent: the ratio is calibrated on
# the previous turn, and code, prose and JSON tokenize differently.
ROOM_SAFETY = 0.9

WRAP_UP = ("context budget nearly exhausted - stop calling functions and "
           "give your final answer now from what you already have")


class ContextBudget:
    """Tracks how much of the channel's context window a conversation
    uses, so tool results can be sized to fit before they are sent.

    Generated tokens share the window with the prompt (num_ctx covers
    both), so the prompt ceiling reserves the completion allowance.
    """

    def __init__(self, context_window: int, completion_reserve: int):
        self.window = context_window
        reserve = min(completion_reserve, context_window // 4)
        self.prompt_ceiling = max(0, context_window - reserve - 256)
        self.ratio = INITIAL_TOKENS_PER_CHAR

    @property
    def enabled(self) -> bool:
        return self.window > 0

    @staticmethod
    def size(messages: list[dict], tools: list[dict]) -> int:
        return len(json.dumps(messages)) + len(json.dumps(tools))

    def observe(self, chars_sent: int, prompt_tokens: int) -> None:
        """Calibrate tokens-per-char from what the endpoint reported."""
        if chars_sent > 0 and prompt_tokens > 0:
            self.ratio = max(prompt_tokens / chars_sent, MIN_TOKENS_PER_CHAR)

    def overflowed(self, prompt_tokens: int) -> bool:
        return self.enabled and prompt_tokens > self.prompt_ceiling

    @property
    def low_water(self) -> float:
        """Room (tokens) below which the model is told to wrap up."""
        return max(4 * MIN_RESULT_ROOM, 0.12 * self.prompt_ceiling)

    def room_tokens(self, messages: list[dict],
                    tools: list[dict]) -> Optional[float]:
        """Prompt tokens still spendable, or None when the window is not
        tracked."""
        if not self.enabled:
            return None
        used = self.size(messages, tools) * self.ratio
        return (self.prompt_ceiling - used) * ROOM_SAFETY

    def room_chars(self, messages: list[dict], tools: list[dict]) -> Optional[int]:
        """Characters of tool output that still fit, or None when the
        window is not tracked."""
        room = self.room_tokens(messages, tools)
        if room is None:
            return None
        if room < MIN_RESULT_ROOM:
            return 0
        return int(room / self.ratio)


def clip_to(content: str, limit: int) -> str:
    """Clip one tool result to its share of the context budget."""
    if len(content) <= limit:
        return content
    note = ("\n[clipped: the conversation is near the context limit - "
            "request narrower ranges or fewer files]")
    return content[:max(0, limit - len(note))] + note


def offered_functions(constraints: MissionConstraints, channel: Optional[dict],
                      grants: Optional[list[str]],
                      ds: Optional[DataStore] = None) -> list[dict]:
    """Harness functions this task may call. Empty means single-shot.

    `grants` are the agent's depot tool slugs; None (a task not stamped by
    the dispatcher) is treated as holding no depot tools.
    """
    if channel is None:
        return []
    ds = ds or get_datastore()
    granted = channel_capabilities(channel, ds)
    if "tool_use" not in granted:
        return []
    allowed = set(constraints.allowed_tools or [])
    held = set(grants or [])
    try:
        profile = channel_profile(channel, ds)
    except ProfileMissing:
        return []          # fail closed; data check reports the dangling name
    if profile is not None:
        allowed &= set(profile.get("functions") or [])

    offered = []
    for row in ds.load_reference("harness_function"):
        name = row.get("function_name")
        if name not in allowed:
            continue
        capability = capability_of_tool(name, ds)
        if capability is None or capability not in granted:
            continue
        if row.get("depot_tool") and row["depot_tool"] not in held:
            continue
        offered.append(row)
    return offered


def tool_schema(row: dict) -> dict:
    """The function-calling declaration for one harness function - the
    shape shared by Ollama /api/chat and OpenAI /v1/chat/completions."""
    return {
        "type": "function",
        "function": {
            "name": row["function_name"],
            "description": row.get("description", ""),
            "parameters": row.get("parameters")
            or {"type": "object", "properties": {}},
        },
    }


def _hazards(offered: list[dict], ds: DataStore) -> dict[str, str]:
    index = capability_index(ds)
    hazards = {}
    for row in offered:
        capability = capability_of_tool(row["function_name"], ds)
        hazards[row["function_name"]] = (index.get(capability) or {}).get(
            "hazard", "none")
    return hazards


def _summarize_arguments(arguments: Optional[dict]) -> Optional[dict]:
    """Arguments as logged: values clipped so a pathological call cannot
    flood the ops log."""
    if not isinstance(arguments, dict):
        return None
    return {k: (v[:200] if isinstance(v, str) else v)
            for k, v in arguments.items()}


async def run(provider, system_prompt: str, task: Task,
              constraints: MissionConstraints, offered: list[dict],
              ds: Optional[DataStore] = None) -> TaskResult:
    """Drive one task through the tool loop and return its result.

    Every exit path returns a TaskResult carrying the full transcript, so a
    failed loop still shows the operator what the model tried.
    """
    ds = ds or get_datastore()
    cfg = harness_config(getattr(provider, "channel", None), ds)
    started_at = datetime.now()
    state = LoopState()

    root = Path(constraints.working_directory or os.getcwd())
    names = [row["function_name"] for row in offered]
    executor = Executor(ExecutionPolicy(
        root=root,
        read_only=constraints.read_only,
        call_timeout=float(cfg["call_timeout_seconds"]),
        max_result_chars=int(cfg["max_result_chars"]),
        offered={row["function_name"]: row for row in offered},
        hazards=_hazards(offered, ds),
    ))
    tools = [tool_schema(row) for row in offered]

    body = provider._build_prompt("", task, constraints, offered=names).strip()
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": body}]

    channel = getattr(provider, "channel", None) or {}
    budget = ContextBudget(
        (channel.get("limits") or {}).get("context_window") or 0,
        constraints.max_tokens_per_task)
    deadline = time.monotonic() + constraints.timeout_seconds
    max_calls = int(cfg["max_calls_per_turn"])

    def finish(output: str = "", error: Optional[str] = None) -> TaskResult:
        result = provider._result(task, started_at, output=output,
                                  error=error, tokens=state.tokens)
        result.tool_calls = state.transcript
        return result

    for iteration in range(1, int(cfg["max_iterations"]) + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return finish(state.last_content, error=(
                f"harness timed out after {constraints.timeout_seconds}s "
                f"({len(state.transcript)} tool call(s) made)"))

        chars_sent = budget.size(messages, tools)
        try:
            turn = await provider.chat(messages, tools, constraints,
                                       timeout=remaining)
        except Exception as exc:
            return finish(state.last_content,
                          error=provider.describe_error(exc))

        state.tokens += turn.tokens
        state.last_content = turn.content or state.last_content
        budget.observe(chars_sent, turn.prompt_tokens)

        # Backstop: an answer built on a conversation the endpoint may have
        # truncated is not trustworthy, whatever it says.
        if budget.overflowed(turn.prompt_tokens):
            return finish(state.last_content, error=(
                f"harness context exhausted: prompt reached "
                f"{turn.prompt_tokens} of {budget.window} tokens - narrow "
                f"the task"))
        if not turn.tool_calls:
            text = turn.content or ""
            if not text.strip():
                return finish(error="empty final response from "
                                    f"{provider.config.url}")
            # A final answer cut off at the output limit is not an answer.
            # A turn that also carries tool calls continues: each call's
            # arguments are parsed and checked on their own.
            truncated = truncation_error(turn.finish_reason,
                                         constraints.max_tokens_per_task)
            if truncated:
                return finish(text, error=truncated)
            return finish(text, error=provider._classify_refusal(text))

        # The budget bounds further work; it never discards a final answer
        if state.tokens > cfg["token_budget"]:
            return finish(state.last_content, error=(
                f"harness token budget exceeded: {state.tokens} > "
                f"{cfg['token_budget']}"))

        if state.wrap_up and any(
                entry["iteration"] == iteration - 1 and
                entry["error"] == WRAP_UP for entry in state.transcript):
            # Told to answer, refused once, and still calling
            return finish(state.last_content, error=(
                f"harness context exhausted: the model kept calling "
                f"functions after the {budget.window}-token window ran low "
                f"- narrow the task"))

        messages.append(turn.message)

        # Size this turn's results to the room left in the window, before
        # anything runs. With no room at all, nothing runs: the model is
        # told to answer with what it has.
        room = budget.room_chars(messages, tools)
        if room == 0:
            if state.wrap_up:
                return finish(state.last_content, error=(
                    f"harness context exhausted: no room left in the "
                    f"{budget.window}-token window - narrow the task"))
            state.wrap_up = True

        outcomes = []
        for index, call in enumerate(turn.tool_calls):
            call_started = time.monotonic()
            if state.wrap_up:
                outcome = CallOutcome("refused", f"REFUSED: {WRAP_UP}",
                                      WRAP_UP)
            elif index >= max_calls:
                outcome = CallOutcome(
                    "refused",
                    f"REFUSED: at most {max_calls} calls per turn - this "
                    f"call was not run; request it again if still needed",
                    f"over the {max_calls}-calls-per-turn limit")
            elif call.parse_error:
                bird.record("frame_malformed_arguments",
                            f"{call.name or '?'}: {call.parse_error}")
                outcome = CallOutcome("refused",
                                      f"REFUSED: {call.parse_error}",
                                      call.parse_error)
            else:
                try:
                    outcome = await asyncio.wait_for(
                        asyncio.to_thread(executor.run, call.name,
                                          call.arguments),
                        timeout=float(cfg["call_timeout_seconds"]) + 5)
                except asyncio.TimeoutError:
                    outcome = CallOutcome(
                        "error", "ERROR: call exceeded its time budget",
                        "call exceeded its time budget (abandoned; its "
                        "worker may still be finishing)")
                except Exception as exc:
                    detail = f"{type(exc).__name__}: {exc}"[:300]
                    outcome = CallOutcome("error", f"ERROR: {detail}",
                                          detail)
            state.transcript.append({
                "iteration": iteration,
                "function": call.name,
                "arguments": _summarize_arguments(call.arguments),
                "outcome": outcome.outcome,
                "summary": outcome.summary if outcome.ok else "",
                "error": "" if outcome.ok else outcome.summary,
                "duration_ms": round(
                    (time.monotonic() - call_started) * 1000, 1),
            })
            outcomes.append((call, outcome))

        share = None if not room else room // max(1, len(outcomes))
        for call, outcome in outcomes:
            content = outcome.content if share is None \
                else clip_to(outcome.content, share)
            messages.append(provider.tool_result_message(call, content))

        # Running low: tell the model, once, to answer with what it has
        remaining = budget.room_tokens(messages, tools)
        if (not state.wrap_up and remaining is not None
                and remaining < budget.low_water):
            state.wrap_up = True
            messages[-1]["content"] += f"\n[{WRAP_UP}]"
            bird.record("context_low_water",
                        f"~{int(remaining)} tokens of room left in a "
                        f"{budget.window}-token window; the model was told "
                        f"to answer")

    return finish(state.last_content, error=(
        f"harness exhausted: {cfg['max_iterations']} turns without a final "
        f"answer ({len(state.transcript)} tool call(s) made)"))


def parse_arguments(raw) -> tuple[Optional[dict], Optional[str]]:
    """Normalize call arguments: Ollama sends an object, OpenAI a JSON
    string. Returns (arguments, parse_error)."""
    if isinstance(raw, dict):
        return raw, None
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"arguments are not valid JSON: {exc.msg}"
        if isinstance(value, dict):
            return value, None
    return None, "arguments must be a JSON object"
