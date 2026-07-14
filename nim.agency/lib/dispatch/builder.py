"""
Mission Builder - Convenient API for creating missions.

Provides templates and helpers for common mission types.
"""

import uuid
from pathlib import Path
from typing import Optional

from ..config import get_config
from ..data import DataStore, get_datastore

from .mission import Mission, MissionConstraints, Task


class MissionBuilder:
    """
    Fluent builder for creating missions.

    Usage:
        builder = MissionBuilder()
        mission = (builder
            .title("Codebase Analysis")
            .objective("Analyze repository structure")
            .add_crew_member(agent1)
            .add_crew_member(agent2)
            .constraint_read_only()
            .working_dir("/path/to/repo")
            .add_task(agent1["id"], "Structure Analysis",
                      "Analyze the directory structure and identify key components")
            .add_task(agent2["id"], "Code Review",
                      "Review the main source files for patterns and quality")
            .build())
    """

    def __init__(self, datastore: Optional[DataStore] = None):
        self.ds = datastore or get_datastore()
        self._mission = Mission()
        self._task_counter = 0

    def title(self, title: str) -> "MissionBuilder":
        """Set mission title."""
        self._mission.title = title
        return self

    def objective(self, objective: str) -> "MissionBuilder":
        """Set mission objective."""
        self._mission.objective = objective
        return self

    def add_crew_member(self, agent: dict) -> "MissionBuilder":
        """Add an agent to the crew."""
        if agent["id"] not in self._mission.crew_ids:
            self._mission.crew_ids.append(agent["id"])
        return self

    def add_crew_by_id(self, agent_id: str) -> "MissionBuilder":
        """Add an agent by ID."""
        if agent_id not in self._mission.crew_ids:
            self._mission.crew_ids.append(agent_id)
        return self

    def constraint_read_only(self, read_only: bool = True) -> "MissionBuilder":
        """Set read-only constraint."""
        self._mission.constraints.read_only = read_only
        return self

    def constraint_local_only(self, local_only: bool = True) -> "MissionBuilder":
        """Set local-only constraint."""
        self._mission.constraints.local_only = local_only
        return self

    def working_dir(self, path: str) -> "MissionBuilder":
        """Set working directory for tasks."""
        self._mission.constraints.working_directory = path
        return self

    def timeout(self, seconds: int) -> "MissionBuilder":
        """Set timeout per task."""
        self._mission.constraints.timeout_seconds = seconds
        return self

    def add_task(
        self,
        agent_id: str,
        role: str,
        prompt: str,
        depends_on: list[str] = None
    ) -> "MissionBuilder":
        """Add a task to the mission."""
        self._task_counter += 1
        task = Task(
            id=f"task-{self._task_counter}",
            agent_id=agent_id,
            role=role,
            prompt=prompt,
            depends_on=depends_on or []
        )
        self._mission.add_task(task)
        return self

    def build(self) -> Mission:
        """Build and return the mission."""
        return self._mission

    def reset(self) -> "MissionBuilder":
        """Reset builder for a new mission."""
        self._mission = Mission()
        self._task_counter = 0
        return self


# Pre-built mission templates

def codebase_analysis_mission(
    agents: list[dict],
    target_dir: str,
    focus: str = "general"
) -> Mission:
    """
    Create a codebase analysis mission.

    Assigns roles:
    - Agent 1: Structure analysis (directories, files, patterns)
    - Agent 2: Code quality review (if available)
    - Agent 3: Documentation review (if available)
    - Agent 4: Summary synthesis (if available)
    """
    builder = MissionBuilder()

    builder.title(f"Codebase Analysis: {Path(target_dir).name}")
    builder.objective(f"Analyze the codebase at {target_dir} and provide a comprehensive technical summary")
    builder.working_dir(target_dir)
    builder.constraint_read_only()
    builder.timeout(get_config()["dispatch"]["timeout_seconds"])

    # Add all agents to crew
    for agent in agents:
        builder.add_crew_member(agent)

    # Assign roles based on available agents
    task_ids = []

    if len(agents) >= 1:
        # Structure analyst
        task_id = "task-1"
        task_ids.append(task_id)
        builder.add_task(
            agents[0]["id"],
            "Structure Analyst",
            f"""Analyze the directory structure of this codebase.

Your tasks:
1. List the top-level directories and their purposes
2. Identify the main source code locations
3. Note any configuration files (package.json, pyproject.toml, etc.)
4. Identify the primary programming language(s)
5. Look for build/deployment configurations

Provide a structured summary of the codebase layout."""
        )

    if len(agents) >= 2:
        # Code reviewer
        task_id = "task-2"
        task_ids.append(task_id)
        builder.add_task(
            agents[1]["id"],
            "Code Reviewer",
            f"""Review the main source code files in this codebase.

Your tasks:
1. Identify the main entry points
2. Look for key modules/classes and their responsibilities
3. Note any patterns used (MVC, factory, singleton, etc.)
4. Identify external dependencies
5. Rate the overall code organization (1-5)

Focus on understanding the architecture, not nitpicking style."""
        )

    if len(agents) >= 3:
        # Documentation reviewer
        task_id = "task-3"
        task_ids.append(task_id)
        builder.add_task(
            agents[2]["id"],
            "Documentation Analyst",
            f"""Review the documentation in this codebase.

Your tasks:
1. Check for README files and their completeness
2. Look for API documentation
3. Check for inline code comments quality
4. Identify any missing documentation
5. Note any setup/installation instructions

Provide a documentation quality assessment."""
        )

    if len(agents) >= 4:
        # Synthesizer - depends on all other tasks
        depends = task_ids[:-1] if len(task_ids) > 1 else []
        builder.add_task(
            agents[3]["id"],
            "Report Synthesizer",
            f"""Synthesize the findings from the analysis team.

Based on your expertise and the analysis performed:
1. Summarize the key findings
2. Identify strengths of the codebase
3. Note areas for improvement
4. Provide actionable recommendations
5. Give an overall assessment

Create an executive summary suitable for stakeholders.""",
            depends_on=depends
        )

    return builder.build()


def security_audit_mission(
    agents: list[dict],
    target_dir: str
) -> Mission:
    """Create a security-focused audit mission."""
    builder = MissionBuilder()

    builder.title(f"Security Audit: {Path(target_dir).name}")
    builder.objective("Perform a security-focused review of the codebase")
    builder.working_dir(target_dir)
    builder.constraint_read_only()
    builder.timeout(get_config()["dispatch"]["timeout_seconds"])

    for agent in agents:
        builder.add_crew_member(agent)

    if len(agents) >= 1:
        builder.add_task(
            agents[0]["id"],
            "Secrets Scanner",
            """Scan the codebase for potential security issues.

Your tasks:
1. Look for hardcoded credentials, API keys, or secrets
2. Check for .env files or similar that might be committed
3. Review .gitignore for proper exclusions
4. Look for any sensitive data in configuration files
5. Check for any exposed endpoints or debug modes

Report any findings with file locations."""
        )

    if len(agents) >= 2:
        builder.add_task(
            agents[1]["id"],
            "Dependency Auditor",
            """Audit the project dependencies for security.

Your tasks:
1. Identify all dependency files (package.json, requirements.txt, etc.)
2. Note any pinned vs unpinned versions
3. Look for any obviously outdated packages
4. Check for any known vulnerable package patterns
5. Review lock files if present

Provide a dependency security assessment."""
        )

    return builder.build()
