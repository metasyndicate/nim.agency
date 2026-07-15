"""
Provider - Interface to LLM backends for agent execution.

Currently supports:
- Claude CLI (`claude` command)

The provider takes a composed agent prompt + task and executes it,
returning the result.
"""

import asyncio
import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .mission import Task, TaskResult, TaskStatus, MissionConstraints


@dataclass
class ProviderConfig:
    """Configuration for a provider."""
    name: str
    command: str = "claude"
    model: Optional[str] = None  # Use default if not specified
    max_tokens: int = 4096
    timeout: int = 300
    working_dir: Optional[str] = None


class Provider(ABC):
    """Abstract base for LLM providers."""

    @abstractmethod
    async def execute(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        """Execute a task and return the result."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is available."""
        pass


class ClaudeCliProvider(Provider):
    """
    Provider using the `claude` CLI tool.

    Executes agent tasks by invoking claude with:
    - System prompt (agent persona/instructions)
    - Task prompt (the actual work)
    - Constraints (read-only mode, allowed tools, etc.)
    """

    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig(name="claude-cli")
        self._check_availability()

    def _check_availability(self):
        """Check if claude CLI is available."""
        try:
            result = subprocess.run(
                ["which", "claude"],
                capture_output=True,
                text=True,
                timeout=5
            )
            self._available = result.returncode == 0
            self._claude_path = result.stdout.strip() if self._available else None
        except Exception:
            self._available = False
            self._claude_path = None

    def is_available(self) -> bool:
        return self._available

    @property
    def binary_path(self) -> Optional[str]:
        """Resolved path to the claude binary (None if unavailable)."""
        return self._claude_path

    def _build_prompt(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> str:
        """Build the full prompt for claude CLI."""
        # Inject constraints into the prompt
        constraint_block = self._build_constraint_block(constraints)

        full_prompt = f"""{system_prompt}

{constraint_block}

---

## CURRENT TASK

**Role:** {task.role}

**Instructions:**
{task.prompt}

---

Proceed with the task. Provide your analysis and findings in a clear, structured format.
"""
        return full_prompt

    def _build_constraint_block(self, constraints: MissionConstraints) -> str:
        """Build constraint instructions."""
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

        return "\n".join(lines)

    async def execute(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        """Execute task via claude CLI."""
        started_at = datetime.now()

        if not self._available:
            return TaskResult(
                task_id=task.id,
                agent_id=task.agent_id,
                status=TaskStatus.FAILED,
                output="",
                error="Claude CLI not available",
                started_at=started_at,
                completed_at=datetime.now()
            )

        # Build the full prompt
        full_prompt = self._build_prompt(system_prompt, task, constraints)

        # Determine working directory
        cwd = constraints.working_directory or os.getcwd()

        try:
            # Build claude command
            cmd = ["claude", "--print"]

            # Add dangerously-skip-permissions for automation
            # This allows the agent to run without interactive prompts
            cmd.append("--dangerously-skip-permissions")

            # Run claude with the prompt piped to stdin
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

            completed_at = datetime.now()

            if process.returncode == 0:
                return TaskResult(
                    task_id=task.id,
                    agent_id=task.agent_id,
                    status=TaskStatus.COMPLETE,
                    output=stdout.decode(),
                    started_at=started_at,
                    completed_at=completed_at
                )
            else:
                return TaskResult(
                    task_id=task.id,
                    agent_id=task.agent_id,
                    status=TaskStatus.FAILED,
                    output=stdout.decode(),
                    error=stderr.decode(),
                    started_at=started_at,
                    completed_at=completed_at
                )

        except asyncio.TimeoutError:
            return TaskResult(
                task_id=task.id,
                agent_id=task.agent_id,
                status=TaskStatus.FAILED,
                output="",
                error=f"Task timed out after {constraints.timeout_seconds} seconds",
                started_at=started_at,
                completed_at=datetime.now()
            )
        except Exception as e:
            return TaskResult(
                task_id=task.id,
                agent_id=task.agent_id,
                status=TaskStatus.FAILED,
                output="",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.now()
            )

    def execute_sync(
        self,
        system_prompt: str,
        task: Task,
        constraints: MissionConstraints
    ) -> TaskResult:
        """Synchronous wrapper for execute."""
        return asyncio.run(self.execute(system_prompt, task, constraints))


def get_provider(name: str = "claude-cli") -> Provider:
    """Get a provider by name."""
    if name == "claude-cli":
        return ClaudeCliProvider()
    else:
        raise ValueError(f"Unknown provider: {name}")
