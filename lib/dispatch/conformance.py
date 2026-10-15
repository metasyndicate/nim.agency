"""
Conformance - a fixed known-answer suite that measures a channel.

A probe proves a syndicate is reachable; conformance measures whether its
answers are right. The suite builds a deterministic fixture tree in a temp
directory, puts the same questions to the channel every time, and checks
each answer against the value established by construction:

    harness cases   need functions: counts, names, recursive search,
                    content location, value lookup
    text cases      need none: the data is in the prompt (the original
                    in-prompt miscount, kept as a standing regression)

A channel the capability gate admits for read-only tool missions runs
both (whether the agency harness or a platform supplies the loop); any
other channel runs the text cases.
Tasks go straight to the provider with a neutral persona - this measures
the channel, not an agent, so no crew, payroll or ledger is involved.

Stdlib only.
"""

import asyncio
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .mission import MissionConstraints, Task, TaskStatus
from .verification import Expectation, verify


SUITE = "kat-1"

PERSONA = ("You are a careful field specialist. Answer exactly the question "
           "asked, in exactly the format requested.")

# path -> content. Counts and answers below are derived from this table,
# so editing it cannot silently desynchronize a case.
FIXTURE = {
    "README.md": "# Relay fixture\nA small tree for conformance checks.\n",
    "alpha.py": "def alpha():\n    return 1\n",
    "beta.py": "def beta():\n    return 2\n",
    "gamma.txt": "plain text, no code\n",
    "config/service.json": '{"name": "relay", "port": 8443, "tls": true}\n',
    "config/limits.json": '{"max_clients": 64, "burst": 8}\n',
    "config/logging.json": '{"level": "info"}\n',
    "docs/notes.md": "Field notes.\nThe quartz oscillator drifts in the cold.\n",
    "docs/archive/old.md": "Superseded notes.\n",
    "src/core.py": "import os\n\nPORT = 8443\n",
    "src/util.py": "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
    "src/vendor/shim.py": "# vendored\n",
}

TEXT_LISTING = [
    "achievement.json", "agent_relation.json", "archetype.json", "build.sh",
    "capability.json", "notes.md", "rank.json", "README.md", "seed.py",
    "skill_domain.json", "specialty.json", "status.json", "task_preset.json",
    "tools.json", "transport.json",
]


def _top_entries() -> int:
    return len({path.split("/")[0] for path in FIXTURE})


@dataclass
class Case:
    slug: str
    prompt: str
    expected: dict
    needs_tools: bool


def cases() -> list[Case]:
    """The suite. Expected values are computed from FIXTURE / TEXT_LISTING."""
    py_files = sorted(p for p in FIXTURE if p.endswith(".py"))
    config_l = sorted(p.split("/")[1] for p in FIXTURE
                      if p.startswith("config/") and p.split("/")[1][0] == "l")
    quartz = [p for p, body in FIXTURE.items() if "quartz" in body]
    listing = "\n".join(TEXT_LISTING)
    return [
        Case("count_top",
             "How many entries (files and directories) are directly in the "
             "working directory? Answer ONLY with JSON: {\"entries\": <int>}",
             {"entries": _top_entries()}, True),
        Case("names_prefix",
             "Which files in the config directory have names starting with "
             "the letter l? Answer ONLY with JSON: {\"files\": [<names>]}",
             {"files": config_l}, True),
        Case("count_recursive",
             "How many .py files are in the whole tree, including all "
             "subdirectories? Answer ONLY with JSON: {\"py_files\": <int>}",
             {"py_files": len(py_files)}, True),
        Case("locate_content",
             "Which file contains the word 'quartz'? Give its path relative "
             "to the working directory. Answer ONLY with JSON: "
             "{\"path\": \"<path>\"}",
             {"path": quartz[0]}, True),
        Case("read_value",
             "What is the value of \"port\" in config/service.json? Answer "
             "ONLY with JSON: {\"port\": <int>}",
             {"port": 8443}, True),
        Case("text_count",
             "Below is a list of file names, one per line, between the "
             "markers. How many end in .json, and which of those start with "
             "the letter t? Answer ONLY with JSON: {\"json_files\": <int>, "
             "\"json_starting_with_t\": [<names>]}\n---8<---\n"
             f"{listing}\n--->8---",
             {"json_files": sum(1 for n in TEXT_LISTING if n.endswith(".json")),
              "json_starting_with_t": sorted(
                  n for n in TEXT_LISTING
                  if n.endswith(".json") and n.startswith("t"))},
             False),
    ]


async def _execute(provider, task, constraints):
    """One case under its own BIRD ledger: (result, problems)."""
    from . import bird
    token = bird.begin(task.id)
    try:
        result = await provider.execute(PERSONA, task, constraints)
    finally:
        problems = bird.end(token)
    return result, problems


def build_fixture(root: Path) -> None:
    for relative, body in FIXTURE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


@dataclass
class CaseResult:
    slug: str
    run: int
    passed: bool
    detail: str
    seconds: float
    tokens: int = 0
    calls: list[str] = field(default_factory=list)
    output: str = ""
    anomalies: list[str] = field(default_factory=list)   # BIRD slugs

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def run_suite(provider, runs: int = 1, timeout: int = 300,
              on_result: Optional[Callable[[CaseResult], None]] = None
              ) -> dict:
    """Run the suite against one provider and return a scorecard."""
    from .syndicate import capability_violation
    ds = getattr(provider, "ds", None)

    root = Path(tempfile.mkdtemp(prefix="nim-conform-")).resolve()
    try:
        build_fixture(root)
        tools = MissionConstraints(
            allowed_tools=["Read", "LS", "Glob", "Grep"],
            working_directory=str(root), timeout_seconds=timeout)
        # Measure the channel, not an agent: hold every depot tool a
        # harness function asks for
        grants = sorted({row["depot_tool"] for row in
                         (ds.load_reference("harness_function") if ds else [])
                         if row.get("depot_tool")})
        # The gate is the authority on whether this channel may take
        # tool missions at all
        syndicate = getattr(provider, "syndicate", None)
        channel = getattr(provider, "channel", None)
        harnessed = bool(syndicate and channel and capability_violation(
            tools, syndicate, channel, ds) is None)

        suite = [c for c in cases() if harnessed or not c.needs_tools]
        results = []
        for run in range(1, runs + 1):
            for case in suite:
                constraints = tools if case.needs_tools else MissionConstraints(
                    allowed_tools=[], blocked_commands=[],
                    working_directory=str(root), timeout_seconds=timeout)
                task = Task(id=f"{case.slug}-{run}", agent_id="conformance",
                            prompt=case.prompt, role="Conformance",
                            tool_grants=grants)
                started = time.monotonic()
                result, problems = asyncio.run(_execute(provider, task,
                                                        constraints))
                seconds = time.monotonic() - started
                if result.status == TaskStatus.COMPLETE:
                    verdict = verify(Expectation("json_subset", case.expected),
                                     result.output or "")
                    passed, detail = verdict.passed, verdict.detail
                else:
                    passed, detail = False, result.error or "failed"
                outcome = CaseResult(
                    slug=case.slug, run=run, passed=passed, detail=detail,
                    seconds=round(seconds, 1), tokens=result.tokens_used,
                    calls=[c.get("function", "?") for c in result.tool_calls],
                    output=(result.output or "")[:500],
                    anomalies=[p["anomaly"] for p in problems])
                results.append(outcome)
                if on_result:
                    on_result(outcome)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = sum(1 for r in results if r.passed)
    return {
        "suite": SUITE,
        "mode": "harness" if harnessed else "text",
        "runs": runs,
        "passed": passed,
        "total": len(results),
        "rate": round(passed / len(results), 3) if results else 0.0,
        "at": datetime.now(timezone.utc).isoformat(),
        "results": [r.to_dict() for r in results],
    }
