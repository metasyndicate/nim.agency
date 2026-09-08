"""
Provider - transport implementations for dispatching agent work.

One class per transport in the syndicate inventory:

    ClaudeCliProvider     subprocess `claude --print` - full agentic harness
    OllamaProvider        HTTP /api/generate - text in, text out
    OpenAICompatProvider  HTTP /v1/chat/completions - the lingua franca

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

    def _build_constraint_block(self, constraints: MissionConstraints) -> str:
        """Build constraint instructions.

        In-band constraints are advisory: they shape the model's behavior
        but enforce nothing on their own. Transports with a real harness
        (claude CLI) back them with a sandbox; text-only transports have
        no tools to constrain in the first place, which is why the
        capability gate keeps tool-bearing missions away from them.
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

        # A text-only channel must be told it has no hands. The composed
        # persona describes a field specialist with tools, and a model that
        # believes it will narrate an inspection it never performed - the
        # exact failure the capability gate exists to prevent.
        if not self.has_harness:
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
        constraints: MissionConstraints
    ) -> str:
        """Compose the full task prompt (persona, constraints, work)."""
        constraint_block = self._build_constraint_block(constraints)

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

        # The persona goes in the system channel; the task body carries
        # constraints and instructions. Text-only channels have no tools to
        # gate, so the constraint block is context, not enforcement.
        body = self._build_prompt("", task, constraints).strip()
        timeout = min(constraints.timeout_seconds, self.config.timeout)

        try:
            payload = self._payload(system_prompt, body, constraints)
            response = await asyncio.wait_for(
                asyncio.to_thread(self._post, payload, timeout),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            return self._result(
                task, started_at,
                error=f"Task timed out after {timeout} seconds "
                      f"({self.config.url})")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            return self._result(
                task, started_at,
                error=f"syndicate channel anomaly: HTTP {e.code} from "
                      f"{self.config.url}{' - ' + detail if detail else ''}")
        except urllib.error.URLError as e:
            return self._result(
                task, started_at,
                error=f"syndicate unreachable ({self.config.url}): {e.reason}")
        except Exception as e:
            return self._result(task, started_at, error=str(e))

        try:
            text, tokens = self._extract(response)
        except (KeyError, IndexError, TypeError) as e:
            return self._result(
                task, started_at,
                error=f"unexpected response shape from {self.config.url}: {e}")

        if not text.strip():
            return self._result(
                task, started_at,
                error=f"empty response from {self.config.url}")

        refusal = self._classify_refusal(text)
        return self._result(task, started_at, output=text,
                            error=refusal, tokens=tokens)


class OllamaProvider(HttpProvider):
    """Ollama's native /api/generate: prompt in, completion out, with a
    first-class system field. No tool harness - text only."""

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
        }

    def _extract(self, response: dict) -> tuple[str, int]:
        tokens = (response.get("prompt_eval_count", 0)
                  + response.get("eval_count", 0))
        return response.get("response", ""), tokens


class OpenAICompatProvider(HttpProvider):
    """The /v1/chat/completions lingua franca - Ollama, vLLM, llama.cpp,
    LM Studio, OpenRouter, and the commercial APIs all answer here."""

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


# Transport handler registry. The transport reference table names the class;
# this maps the name to it. Adding a backend means a data row plus a class.
HANDLERS = {
    "ClaudeCliProvider": ClaudeCliProvider,
    "OllamaProvider": OllamaProvider,
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
    return provider
