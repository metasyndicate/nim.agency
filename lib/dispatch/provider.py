"""
Provider - transport implementations for dispatching agent work.

One class per transport in the syndicate inventory:

    ClaudeCliProvider     subprocess `claude --print` - full agentic harness
    OllamaProvider        HTTP /api/generate - text in, text out
    OllamaChatProvider    HTTP /api/chat - tool calls, run by the agency harness
    OpenAICompatProvider  HTTP /v1/chat/completions - the lingua franca,
                          tool calls run by the agency harness

`get_provider()` resolves a dispatch address through the inventory and
instantiates the handler the transport record names, so adding a backend
is a data edit plus one class. Nothing here decides whether a provider is
*allowed* to serve a mission; that is `syndicate.capability_violation`.

Core stays stdlib-only: HTTP goes through urllib on a worker thread
rather than pulling in an async client.
"""

import asyncio
import json
import os
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .mission import Task, TaskResult, TaskStatus, MissionConstraints


# Error taxonomy: task errors beginning with this prefix are provider policy
# refusals (the model declined the composed prompt). The dispatcher uses the
# prefix to attribute conduct violations to the assigned agent.
POLICY_VIOLATION_PREFIX = "policy_violation"

# Signatures of a policy refusal in provider output. Refusals arrive on
# stdout with a nonzero exit code and an empty stderr (claude CLI), or as
# a 200 body that declines (hosted and local chat endpoints alike).
POLICY_REFUSAL_MARKERS = (
    "usage policy",
    "unable to respond to this request",
    "i cannot assist with",
    "i can't assist with",
)

# Finish reasons meaning the model stopped because it hit the output limit,
# not because it was done. The value arrives with HTTP 200 and a partial
# answer, so without this check a cut-off reply reads as a complete one.
# Vocabularies: Ollama `done_reason`, OpenAI `finish_reason` ("length");
# Anthropic `stop_reason` ("max_tokens"). Raw values are kept as reported,
# per OpenTelemetry GenAI `gen_ai.response.finish_reasons`.
TRUNCATION_FINISH_REASONS = frozenset({"length", "max_tokens"})


def truncation_error(finish_reason: Optional[str], limit: int) -> Optional[str]:
    """Task error for an answer cut off at the output limit, else None."""
    if finish_reason not in TRUNCATION_FINISH_REASONS:
        return None
    return (f"output truncated: the model stopped at the {limit}-token "
            f"output limit (finish reason {finish_reason!r}) - raise "
            f"max_tokens_per_task or narrow the task")


@dataclass
class ProviderConfig:
    """Everything a provider needs to reach one channel.

    Built from a syndicate + channel record by `provider_config()`; the
    dataclass never carries a secret, only the vault id of one.
    """
    name: str                                    # syndicate:channel address
    transport: str = "claude_cli"
    command: str = "claude"
    args: list[str] = field(default_factory=list)
    base_uri: Optional[str] = None
    path: str = ""
    model: Optional[str] = None
    credential_id: Optional[str] = None
    auth_mode: str = "none"
    verify_tls: bool = True
    egress: str = "public"
    parameters: dict = field(default_factory=dict)
    max_tokens: int = 4096
    max_concurrent: int = 1
    timeout: int = 300
    working_dir: Optional[str] = None

    @property
    def url(self) -> str:
        """Fully qualified endpoint URL for HTTP transports."""
        return f"{(self.base_uri or '').rstrip('/')}{self.path}"


class Provider(ABC):
    """Abstract base for dispatch transports."""

    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig(name="claude_cli:default")

    @abstractmethod
    async def execute(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        """Execute a task and return the result."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is available."""

    @property
    def descriptor(self) -> str:
        """Operator-facing one-liner naming where work goes. Rendered in
        briefings; every transport answers, unlike a binary path."""
        return self.config.name

    @property
    def unavailable_reason(self) -> str:
        """Why `is_available()` said no - shown at the briefing gate."""
        return f"{self.config.name} is not reachable"

    def _build_constraint_block(self, constraints: MissionConstraints,
                                offered: Optional[list[str]] = None) -> str:
        """Build constraint instructions.

        In-band constraints are advisory: they shape the model's behavior
        but enforce nothing on their own. Transports with a real harness
        (claude CLI) back them with a sandbox; the agency harness backs
        them with its executor; text-only transports have no tools to
        constrain in the first place, which is why the capability gate
        keeps tool-bearing missions away from them.

        `offered` names the agency-harness functions this task may call:
        None when the transport owns its own harness (or none), an empty
        list when this task runs without tools.
        """
        lines = ["## OPERATIONAL CONSTRAINTS", ""]

        if constraints.read_only:
            lines.append("**MODE: READ-ONLY** - You MUST NOT modify any files. Observation and analysis only.")
            lines.append("")

        if constraints.local_only:
            lines.append("**SCOPE: LOCAL HOST ONLY** - Do not attempt remote connections.")
            lines.append("")

        if constraints.blocked_commands:
            lines.append(f"**BLOCKED COMMANDS:** {', '.join(constraints.blocked_commands)}")
            lines.append("")

        if constraints.working_directory:
            lines.append(f"**WORKING DIRECTORY:** {constraints.working_directory}")
            lines.append("")

        lines.append("**OUTPUT FORMAT:** Provide findings in clear markdown format.")
        lines.append("**FOCUS:** Stay focused on the assigned task. Be thorough but concise.")

        if offered:
            # The agency harness: the model gets real hands, and is told
            # exactly which, where they reach, and to use their counts.
            lines.extend([
                "",
                f"**TOOLS:** You can call these functions: "
                f"{', '.join(offered)}. They run on the target host, "
                f"confined to the WORKING DIRECTORY; give paths relative to "
                f"it.",
                "**GROUNDING:** Call a function whenever the answer depends "
                "on file contents, names, or counts - never guess. Use the "
                "narrowest function that answers the question. Function "
                "results state exact counts; report those rather than "
                "counting a listing yourself.",
                "**FINISH:** When you have what you need, reply with your "
                "final answer and no function call.",
            ])
            return "\n".join(lines)

        # A text-only channel must be told it has no hands. The composed
        # persona describes a field specialist with tools, and a model that
        # believes it will narrate an inspection it never performed - the
        # exact failure the capability gate exists to prevent.
        if offered is not None or not self.has_harness:
            lines.extend([
                "",
                "**NO TOOLS AVAILABLE** - You have no filesystem access, no "
                "shell, and no network. You cannot read, list, or inspect "
                "anything. Answer from your own knowledge only.",
                "**DO NOT** claim to have examined, read, scanned, or "
                "inspected any file, directory, or system. State plainly "
                "when something would require access you do not have.",
            ])

        return "\n".join(lines)

    @property
    def has_harness(self) -> bool:
        """Whether a runtime stands behind this provider to execute tools.

        Read from the channel record when the provider came from the
        inventory; providers built by hand assume a harness, preserving
        prior behavior for test stubs and direct construction.
        """
        channel = getattr(self, "channel", None)
        if channel is None:
            return True
        return "tool_use" in (channel.get("capabilities") or [])

    def _build_prompt(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints,
        offered: Optional[list[str]] = None,
    ) -> str:
        """Compose the full task prompt (persona, constraints, work)."""
        constraint_block = self._build_constraint_block(constraints, offered)

        return f"""{system_prompt}

{constraint_block}

---

## CURRENT TASK

**Role:** {task.role}

**Instructions:**
{task.prompt}

---

Proceed with the task. Provide your analysis and findings in a clear, structured format.
"""

    def _classify_refusal(self, text: str) -> Optional[str]:
        """Tag provider policy refusals so the dispatcher can attribute a
        conduct violation to the agent whose prompt drew it."""
        if any(marker in text.lower() for marker in POLICY_REFUSAL_MARKERS):
            return (f"{POLICY_VIOLATION_PREFIX}: provider refused the composed "
                    "prompt (usage policy); full response retained in task output")
        return None

    def _result(self, task: Task, started_at: datetime, output: str = "",
                error: Optional[str] = None, tokens: int = 0) -> TaskResult:
        """Build a TaskResult, deriving status from the presence of error."""
        return TaskResult(
            task_id=task.id,
            agent_id=task.agent_id,
            status=TaskStatus.FAILED if error else TaskStatus.COMPLETE,
            output=output,
            error=error,
            started_at=started_at,
            completed_at=datetime.now(),
            tokens_used=tokens,
        )

    def execute_sync(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        """Synchronous wrapper for execute."""
        return asyncio.run(self.execute(system_prompt, task, constraints))


class ClaudeCliProvider(Provider):
    """
    Provider using the `claude` CLI tool.

    The only transport with a real agentic harness: the CLI executes tools,
    reaches the filesystem, and enforces its own permission sandbox. The
    composed prompt is piped to stdin.
    """

    def __init__(self, config: Optional[ProviderConfig] = None):
        super().__init__(config)
        self._check_availability()

    def _check_availability(self):
        """Resolve the configured command on PATH."""
        try:
            result = subprocess.run(
                ["which", self.config.command],
                capture_output=True,
                text=True,
                timeout=5
            )
            self._available = result.returncode == 0
            self._binary_path = result.stdout.strip() if self._available else None
        except Exception:
            self._available = False
            self._binary_path = None

    def is_available(self) -> bool:
        return self._available

    @property
    def binary_path(self) -> Optional[str]:
        """Resolved path to the CLI binary (None if unavailable)."""
        return self._binary_path

    @property
    def descriptor(self) -> str:
        binary = self._binary_path or f"{self.config.command} (not found)"
        return f"{binary} {' '.join(self.config.args)}".strip()

    @property
    def unavailable_reason(self) -> str:
        return (f"'{self.config.command}' not found on PATH - install it, or "
                f"dispatch to another syndicate")

    async def execute(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        """Execute task via the CLI."""
        started_at = datetime.now()

        if not self._available:
            return self._result(task, started_at,
                                error=self.unavailable_reason)

        full_prompt = self._build_prompt(system_prompt, task, constraints)
        cwd = constraints.working_directory or os.getcwd()
        cmd = [self.config.command, *self.config.args]
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=full_prompt.encode()),
                timeout=constraints.timeout_seconds
            )

            out, err = stdout.decode(), stderr.decode()
            if process.returncode == 0:
                return self._result(task, started_at, output=out)
            return self._result(
                task, started_at, output=out,
                error=self._classify_error(out, err, process.returncode))

        except asyncio.TimeoutError:
            return self._result(
                task, started_at,
                error=f"Task timed out after {constraints.timeout_seconds} seconds")
        except Exception as e:
            return self._result(task, started_at, error=str(e))

    def _classify_error(self, stdout: str, stderr: str, returncode: int) -> str:
        """Derive a meaningful task error from provider output.

        The claude CLI reports usage-policy refusals on stdout with a nonzero
        exit and empty stderr, which previously produced blank task errors.
        """
        refusal = self._classify_refusal(f"{stdout}\n{stderr}")
        if refusal:
            return refusal
        first_line = stdout.strip().split("\n")[0][:200] if stdout.strip() else ""
        return (stderr.strip() or first_line
                or f"provider exited with code {returncode}")


class HttpProvider(Provider):
    """
    Shared machinery for HTTP transports.

    Subclasses supply the request payload and the response extraction;
    everything else - auth, TLS posture, threading, error surfacing - is
    identical across the OpenAI-compatible world and Ollama's native API.
    """

    def is_available(self) -> bool:
        """A configured endpoint is considered available; reachability is
        proven by `probe()` (or by the dispatch itself failing loudly).

        Deliberately not a live network call: `is_available` is called at
        every briefing, and a cold workstation waking from sleep should not
        turn into a silent refusal.
        """
        return bool(self.config.base_uri)

    @property
    def descriptor(self) -> str:
        model = self.config.model or "(endpoint default)"
        return f"{self.config.url}  model={model}  egress={self.config.egress}"

    @property
    def unavailable_reason(self) -> str:
        return (f"syndicate '{self.config.name}' has no base_uri - set "
                f"endpoint.base_uri on its record")

    def _headers(self) -> dict:
        """Request headers, including auth material fetched from the vault
        at call time. The token is never held on the config object."""
        headers = {"Content-Type": "application/json"}
        if self.config.auth_mode in ("none", "inherited"):
            return headers
        if not self.config.credential_id:
            return headers

        from ..remote.vault import CredentialVault, VaultError
        try:
            secret = CredentialVault().get_credential(
                self.config.credential_id).decode().strip()
        except (VaultError, ImportError, Exception):
            # Vault locked or unavailable - proceed unauthenticated and let
            # the endpoint's 401 be the honest error, rather than fizzling.
            return headers

        if self.config.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif self.config.auth_mode == "api_key_header":
            headers["X-API-Key"] = secret
        return headers

    def _post(self, payload: dict, timeout: int) -> dict:
        """Blocking JSON POST. Runs on a worker thread via execute()."""
        request = urllib.request.Request(
            self.config.url,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )

        context = None
        if not self.config.verify_tls:
            import ssl
            context = ssl._create_unverified_context()

        with urllib.request.urlopen(request, timeout=timeout,
                                    context=context) as response:
            return json.loads(response.read().decode())

    def _payload(self, system_prompt: str, prompt: str,
                 constraints: MissionConstraints) -> dict:
        raise NotImplementedError

    def _extract(self, response: dict) -> tuple[str, int]:
        """Return (text, tokens_used) from a decoded response body."""
        raise NotImplementedError

    def _finish_reason(self, response: dict) -> Optional[str]:
        """The endpoint's raw finish reason, when it reports one."""
        return None

    # Whether this transport can carry function declarations and return
    # structured tool calls - the precondition for the agency harness.
    supports_tool_calls = False

    @staticmethod
    def http_body(exc: urllib.error.HTTPError) -> str:
        """An HTTP error's body, read once and kept on the exception (the
        relay classifies it, then describe_error quotes it)."""
        if not hasattr(exc, "nim_body"):
            try:
                exc.nim_body = exc.read().decode(errors="replace")
            except Exception:
                exc.nim_body = ""
            try:
                exc.close()      # release the connection now, not at GC
            except Exception:
                pass
        return exc.nim_body

    def reply_parts(self, response: dict) -> tuple:
        """(text, tool_call_count, finish_reason, prompt_tokens) of a reply,
        for the relay's frame and context checks."""
        text, _ = self._extract(response)
        return text, 0, self._finish_reason(response), self._prompt_tokens(response)

    def _prompt_tokens(self, response: dict) -> Optional[int]:
        return None

    def describe_error(self, exc: Exception) -> str:
        """Operator-facing task error for a failed request."""
        if isinstance(exc, asyncio.TimeoutError):
            return f"request timed out ({self.config.url})"
        if isinstance(exc, urllib.error.HTTPError):
            detail = self.http_body(exc)[:300]
            # Ollama with truncate=false refuses an over-long prompt rather
            # than dropping its oldest messages (OllamaProvider.LOSSLESS)
            if exc.code == 400 and ("exceed_context_size" in detail
                                    or "exceeds the available context" in detail):
                return (f"context exceeded: the prompt is larger than the "
                        f"channel's context window and was refused, not "
                        f"truncated ({self.config.url}) - {detail}")
            return (f"syndicate channel anomaly: HTTP {exc.code} from "
                    f"{self.config.url}{' - ' + detail if detail else ''}")
        if isinstance(exc, urllib.error.URLError):
            return f"syndicate unreachable ({self.config.url}): {exc.reason}"
        if isinstance(exc, (KeyError, IndexError, TypeError)):
            return f"unexpected response shape from {self.config.url}: {exc}"
        return str(exc)

    def offered_functions(self, task: Task,
                          constraints: MissionConstraints) -> list[dict]:
        """Harness functions this task would be offered on this provider.
        Empty means the single-shot text path."""
        if not self.supports_tool_calls:
            return []
        from .harness import offered_functions
        return offered_functions(constraints, getattr(self, "channel", None),
                                 task.tool_grants, getattr(self, "ds", None))

    async def execute(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        started_at = datetime.now()

        if not self.is_available():
            return self._result(task, started_at,
                                error=self.unavailable_reason)

        # Harnessed path: the channel grants tool_use, the transport can
        # carry tool calls, and the task is offered at least one function.
        offered = self.offered_functions(task, constraints)
        if offered:
            from .harness import run
            try:
                return await run(self, system_prompt, task, constraints,
                                 offered, getattr(self, "ds", None))
            except Exception as exc:
                # The loop returns results on every anticipated path; this
                # keeps an unanticipated one from aborting sibling tasks
                return self._result(task, started_at, error=(
                    f"harness fault: {type(exc).__name__}: {exc}"))

        # Single-shot path. The persona goes in the system channel; the task
        # body carries constraints and instructions. No function is offered
        # on this path, so the model is told it has no hands.
        body = self._build_prompt("", task, constraints, offered=[]).strip()
        timeout = min(constraints.timeout_seconds, self.config.timeout)

        try:
            from . import bird
            payload = self._payload(system_prompt, body, constraints)
            response = await bird.transmit(self, payload, timeout)
        except asyncio.TimeoutError:
            return self._result(
                task, started_at,
                error=f"Task timed out after {timeout} seconds "
                      f"({self.config.url})")
        except Exception as e:
            return self._result(task, started_at, error=self.describe_error(e))

        try:
            text, tokens = self._extract(response)
        except (KeyError, IndexError, TypeError) as e:
            return self._result(task, started_at,
                                error=self.describe_error(e))

        if not text.strip():
            return self._result(
                task, started_at,
                error=f"empty response from {self.config.url}")

        truncated = truncation_error(self._finish_reason(response),
                                     constraints.max_tokens_per_task)
        if truncated:
            return self._result(task, started_at, output=text,
                                error=truncated, tokens=tokens)

        refusal = self._classify_refusal(text)
        return self._result(task, started_at, output=text,
                            error=refusal, tokens=tokens)

    # -- chat interface (agency harness) ------------------------------------
    # Transports that set supports_tool_calls implement the three hooks
    # below; `chat()` is shared.

    def _chat_payload(self, messages: list[dict], tools: list[dict],
                      constraints: MissionConstraints) -> dict:
        raise NotImplementedError

    def _parse_chat(self, response: dict):
        """Decode a response body into a harness ChatTurn."""
        raise NotImplementedError

    def tool_result_message(self, call, content: str) -> dict:
        """The history message carrying one tool result back to the model."""
        raise NotImplementedError

    async def chat(self, messages: list[dict], tools: list[dict],
                   constraints: MissionConstraints, timeout: float):
        """One request/response turn with function declarations attached.
        Raises on transport failure; the loop turns that into a task error."""
        from . import bird
        timeout = max(1.0, min(float(timeout), float(self.config.timeout)))
        payload = self._chat_payload(messages, tools, constraints)
        response = await bird.transmit(self, payload, timeout)
        return self._parse_chat(response)


class OllamaProvider(HttpProvider):
    """Ollama's native /api/generate: prompt in, completion out, with a
    first-class system field. No tool harness - text only."""

    # Ollama defaults to truncate=true: an over-long prompt loses its oldest
    # messages and the request still answers HTTP 200 (observed on bonsai,
    # 0.33.2: 3040 tokens sent, 26 evaluated, the question's facts gone).
    # false/false makes it refuse with HTTP 400 exceed_context_size_error,
    # and refuse to shift context mid-generation, instead.
    LOSSLESS = {"truncate": False, "shift": False}

    def _payload(self, system_prompt: str, prompt: str,
                 constraints: MissionConstraints) -> dict:
        options = dict(self.config.parameters or {})
        options.setdefault("num_predict", constraints.max_tokens_per_task)
        return {
            "model": self.config.model,
            "system": system_prompt,
            "prompt": prompt,
            "stream": False,
            "options": options,
            **self.LOSSLESS,
        }

    def _extract(self, response: dict) -> tuple[str, int]:
        tokens = (response.get("prompt_eval_count", 0)
                  + response.get("eval_count", 0))
        return response.get("response", ""), tokens

    def _finish_reason(self, response: dict) -> Optional[str]:
        return response.get("done_reason")

    def _prompt_tokens(self, response: dict) -> Optional[int]:
        return response.get("prompt_eval_count")


class OllamaChatProvider(HttpProvider):
    """Ollama's native /api/chat: a message history with function
    declarations. The model emits structured tool calls; the agency
    harness executes them. Text-only tasks take a single turn with no
    tools attached."""

    supports_tool_calls = True
    LOSSLESS = OllamaProvider.LOSSLESS

    def _options(self, constraints: MissionConstraints) -> dict:
        options = dict(self.config.parameters or {})
        options.setdefault("num_predict", constraints.max_tokens_per_task)
        return options

    def _payload(self, system_prompt: str, prompt: str,
                 constraints: MissionConstraints) -> dict:
        return {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": self._options(constraints),
            **self.LOSSLESS,
        }

    def _extract(self, response: dict) -> tuple[str, int]:
        tokens = (response.get("prompt_eval_count", 0)
                  + response.get("eval_count", 0))
        return (response.get("message") or {}).get("content", ""), tokens

    def _finish_reason(self, response: dict) -> Optional[str]:
        return response.get("done_reason")

    def _prompt_tokens(self, response: dict) -> Optional[int]:
        return response.get("prompt_eval_count")

    def reply_parts(self, response: dict) -> tuple:
        message = response["message"]
        return (message.get("content") or "", len(message.get("tool_calls") or []),
                response.get("done_reason"), response.get("prompt_eval_count"))

    def _chat_payload(self, messages: list[dict], tools: list[dict],
                      constraints: MissionConstraints) -> dict:
        return {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": self._options(constraints),
            **self.LOSSLESS,
        }

    def _parse_chat(self, response: dict):
        from .harness import ChatTurn, ToolCall, parse_arguments
        message = response["message"]
        calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments, error = parse_arguments(function.get("arguments"))
            calls.append(ToolCall(id=raw.get("id"),
                                  name=function.get("name", ""),
                                  arguments=arguments, parse_error=error))
        replay = {"role": "assistant", "content": message.get("content", "")}
        if message.get("tool_calls"):
            replay["tool_calls"] = message["tool_calls"]
        return ChatTurn(
            content=message.get("content", "") or "",
            tool_calls=calls,
            message=replay,
            prompt_tokens=response.get("prompt_eval_count", 0),
            completion_tokens=response.get("eval_count", 0),
            finish_reason=response.get("done_reason"),
        )

    def tool_result_message(self, call, content: str) -> dict:
        message = {"role": "tool", "content": content, "tool_name": call.name}
        if call.id:
            message["tool_call_id"] = call.id
        return message


class OpenAICompatProvider(HttpProvider):
    """The /v1/chat/completions lingua franca - Ollama, vLLM, llama.cpp,
    LM Studio, OpenRouter, and the commercial APIs all answer here. Tool
    calls are in the protocol; the agency harness executes them."""

    supports_tool_calls = True

    def _chat_payload(self, messages: list[dict], tools: list[dict],
                      constraints: MissionConstraints) -> dict:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "max_tokens": constraints.max_tokens_per_task,
        }
        payload.update(self.config.parameters or {})
        return payload

    def _parse_chat(self, response: dict):
        from .harness import ChatTurn, ToolCall, parse_arguments
        message = response["choices"][0]["message"]
        calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments, error = parse_arguments(function.get("arguments"))
            calls.append(ToolCall(id=raw.get("id"),
                                  name=function.get("name", ""),
                                  arguments=arguments, parse_error=error))
        replay = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            replay["tool_calls"] = message["tool_calls"]
        usage = response.get("usage") or {}
        return ChatTurn(
            content=message.get("content") or "",
            tool_calls=calls,
            message=replay,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=response["choices"][0].get("finish_reason"),
        )

    def tool_result_message(self, call, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id or call.name,
                "content": content}

    def _payload(self, system_prompt: str, prompt: str,
                 constraints: MissionConstraints) -> dict:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": constraints.max_tokens_per_task,
        }
        payload.update(self.config.parameters or {})
        return payload

    def _extract(self, response: dict) -> tuple[str, int]:
        text = response["choices"][0]["message"]["content"]
        tokens = (response.get("usage") or {}).get("total_tokens", 0)
        return text or "", tokens

    def _finish_reason(self, response: dict) -> Optional[str]:
        return (response.get("choices") or [{}])[0].get("finish_reason")

    def _prompt_tokens(self, response: dict) -> Optional[int]:
        return (response.get("usage") or {}).get("prompt_tokens")

    def reply_parts(self, response: dict) -> tuple:
        choice = response["choices"][0]
        message = choice["message"]
        return (message.get("content") or "", len(message.get("tool_calls") or []),
                choice.get("finish_reason"), self._prompt_tokens(response))


# Transport handler registry. The transport reference table names the class;
# this maps the name to it. Adding a backend means a data row plus a class.
HANDLERS = {
    "ClaudeCliProvider": ClaudeCliProvider,
    "OllamaProvider": OllamaProvider,
    "OllamaChatProvider": OllamaChatProvider,
    "OpenAICompatProvider": OpenAICompatProvider,
}


def provider_config(syndicate: dict, channel: dict,
                    transport: dict) -> ProviderConfig:
    """Flatten an inventory record into the config a provider needs."""
    from .syndicate import address_of

    endpoint = syndicate.get("endpoint") or {}
    limits = channel.get("limits") or {}

    return ProviderConfig(
        name=address_of(syndicate, channel),
        transport=transport["slug"],
        command=endpoint.get("command", "claude"),
        args=list(endpoint.get("args") or []),
        base_uri=endpoint.get("base_uri"),
        path=endpoint.get("path") or transport.get("default_path", ""),
        model=channel.get("model") or None,
        credential_id=endpoint.get("credential_id"),
        auth_mode=transport.get("auth_mode", "none"),
        verify_tls=endpoint.get("verify_tls", True),
        egress=endpoint.get("egress", "public"),
        parameters=dict(channel.get("parameters") or {}),
        max_tokens=limits.get("max_output_tokens") or 4096,
        max_concurrent=limits.get("max_concurrent", 1),
        timeout=limits.get("timeout_seconds", 600),
    )


def get_provider(address: Optional[str] = None, ds=None) -> Provider:
    """Resolve a dispatch address through the syndicate inventory and
    return its provider.

    Address forms: `syndicate`, `syndicate:channel`, or None for the
    configured default. Raises SyndicateError when the inventory cannot
    answer - callers turn that into a briefing refusal.
    """
    from .syndicate import SyndicateError, resolve, transport_of

    syndicate, channel = resolve(address, ds)
    transport = transport_of(syndicate, ds)

    handler = HANDLERS.get(transport.get("handler", ""))
    if handler is None:
        raise SyndicateError(
            f"transport '{transport['slug']}' names handler "
            f"'{transport.get('handler')}' which is not implemented in "
            f"lib/dispatch/provider.py (have: {', '.join(sorted(HANDLERS))})")

    provider = handler(provider_config(syndicate, channel, transport))
    # Carry the inventory records the provider was built from: the briefing
    # discloses them and the dispatcher gates against them. Providers built
    # by hand (test stubs) simply have neither, and the gate stands down.
    provider.syndicate = syndicate
    provider.channel = channel
    # The datastore the records came from: the harness reads its function
    # table from the same place the gate read the channel
    provider.ds = ds
    return provider
