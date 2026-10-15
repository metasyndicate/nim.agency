"""
Launch - choose the interpreter bin/nim.agency should run on
(plan.101426.installer.md, I5).

When the `default` instance exists, the agency runs on its venv's
interpreter, invoked by path (no activation), so its dependency groups are
present. Kept light: it reads the receipt file directly rather than loading
the datastore, because it runs before every command.

Never re-execs when:
  - NIM_NO_VENV is set (operator opt-out),
  - NIM_IN_VENV is set (already re-exec'd: loop guard),
  - the command is `install` (the installer runs on the base interpreter, so
    it can rebuild or remove a venv without running from inside it),
  - this interpreter already is the instance's.
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

from .paths import DEFAULT_INSTANCE, home


def instance_python(name: str = DEFAULT_INSTANCE) -> Optional[Path]:
    """The named instance's venv interpreter, if its receipt and
    interpreter both exist."""
    folder = home() / "installs"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        try:
            receipt = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if receipt.get("name") != name or (receipt.get("audit") or {}).get("retired_at"):
            continue
        python = Path((receipt.get("paths") or {}).get("python", ""))
        return python if python.is_file() else None
    return None


def target(argv: list) -> Optional[Path]:
    """The interpreter to re-exec into, or None to run here."""
    if os.environ.get("NIM_NO_VENV") or os.environ.get("NIM_IN_VENV"):
        return None
    if argv[1:2] == ["install"]:
        return None
    python = instance_python()
    if python is None:
        return None
    venv_dir = python.parent.parent
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return None
    except OSError:
        return None
    return python


def reexec(python: Path, script: str, argv: list) -> None:
    """Replace this process with the instance interpreter (POSIX), or run it
    and exit with its status (Windows, where exec spawns)."""
    env = dict(os.environ, NIM_IN_VENV="1")
    args = [str(python), script, *argv[1:]]
    if os.name == "nt":
        import subprocess
        sys.exit(subprocess.call(args, env=env))
    os.execve(str(python), args, env)
