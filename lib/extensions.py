"""
Optional extensions under ext/<name>/, each described by ext/<name>/extension.json.

An extension is a self-contained subsystem (its own bin/, lib/, etc/, tests/)
merged in with `git subtree`, so it can also be split back out with its
history. Agency core never imports extension code: the CLI mounts each one as
`nim.agency <name> ...` and runs its own entry point as a child process.
Deleting ext/<name> removes the extension and nothing else.

extension.json: {"name", "version", "summary", "command", "tests"?}
    command  entry point, relative to the extension directory
    tests    unittest start directory, relative to the extension directory
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import PROJECT_ROOT

EXT_DIR = PROJECT_ROOT / "ext"
REQUIRED = ("name", "version", "summary", "command")


class ExtensionError(Exception):
    pass


@dataclass(frozen=True)
class Extension:
    name: str
    version: str
    summary: str
    root: Path
    command: Path
    tests: Optional[Path]


def load(manifest: Path) -> Extension:
    """One extension from its manifest; raises ExtensionError naming the problem."""
    try:
        doc = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise ExtensionError(f"{manifest}: {exc}") from exc
    missing = [k for k in REQUIRED if not doc.get(k)]
    if missing:
        raise ExtensionError(f"{manifest}: missing {', '.join(missing)}")
    root = manifest.parent
    if doc["name"] != root.name:
        raise ExtensionError(f"{manifest}: name {doc['name']!r} does not match directory {root.name!r}")
    command = root / doc["command"]
    if not os.access(command, os.X_OK):
        raise ExtensionError(f"{manifest}: command {command} is not executable")
    return Extension(doc["name"], doc["version"], doc["summary"], root, command,
                     root / doc["tests"] if doc.get("tests") else None)


def discover(base: Optional[Path] = None) -> tuple[list[Extension], list[str]]:
    """Every extension under base (default EXT_DIR), plus a problem line for each one that failed to load."""
    found, problems = [], []
    for manifest in sorted((base or EXT_DIR).glob("*/extension.json")):
        try:
            found.append(load(manifest))
        except ExtensionError as exc:
            problems.append(str(exc))
    return found, problems
