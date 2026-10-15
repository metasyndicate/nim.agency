"""
Doctor - read-only checks of this machine and an agency instance
(plan.101426.installer.md, I-DOCTOR). After `brew doctor` / `flutter
doctor`: each check is {id, status ok|warn|fail, detail, remedy}; any fail
makes the run fail. Versions inside the venv are read by the venv's own
interpreter.
"""

import json
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from . import instance
from .paths import DEFAULT_INSTANCE, probe, resolve_home

MIN_FREE_MB = (500, 100)       # warn below the first, fail below the second


@dataclass
class Check:
    id: str
    status: str                # ok | warn | fail
    detail: str
    remedy: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Context:
    name: str
    home: Optional[Path] = None
    receipt: Optional[dict] = None


CHECKS: list = []


def check(fn: Callable) -> Callable:
    CHECKS.append(fn)
    return fn


@check
def python_version(ctx):
    """Interpreter running the agency"""
    v = sys.version_info
    text = platform.python_version()
    if v >= instance.MIN_PYTHON:
        return Check("python_version", "ok", f"Python {text}")
    if v >= (3, 9):
        return Check("python_version", "warn", f"Python {text}: the core runs, the "
                     "installer needs 3.11+", "install Python 3.11 or newer")
    return Check("python_version", "fail", f"Python {text} is too old",
                 "install Python 3.11 or newer")


@check
def platform_info(ctx):
    """Operating system and architecture"""
    return Check("platform", "ok", f"{platform.system()} {platform.release()} "
                 f"{platform.machine()} ({platform.python_implementation()})")


@check
def externally_managed(ctx):
    """PEP 668 marker on the base interpreter"""
    if instance.externally_managed():
        return Check("externally_managed", "ok", "base interpreter is externally "
                     "managed (PEP 668): dependencies go into a venv, never into it")
    return Check("externally_managed", "ok", "base interpreter is not externally managed")


@check
def ensurepip(ctx):
    """venv can bootstrap pip"""
    try:
        import ensurepip as _  # noqa: F401
    except ImportError:
        return Check("ensurepip", "fail", "ensurepip is missing; venvs cannot get pip",
                     "on Debian/Ubuntu: apt install python3-venv")
    return Check("ensurepip", "ok", "available")


@check
def home(ctx):
    """Instance home resolves to a writable location"""
    resolved = resolve_home()
    ctx.home = resolved.path
    if resolved.path is None:
        return Check("home", "fail", "no candidate is usable", "set NIM_HOME to a "
                     "writable directory")
    return Check("home", "ok", f"{resolved.path}  ({resolved.source})")


@check
def disk_space(ctx):
    """Free space where the instance lives"""
    if ctx.home is None:
        return Check("disk_space", "warn", "skipped: no home")
    free_mb = shutil.disk_usage(_existing(ctx.home)).free // (1024 * 1024)
    warn, fail = MIN_FREE_MB
    status = "ok" if free_mb >= warn else "warn" if free_mb >= fail else "fail"
    return Check("disk_space", status, f"{free_mb} MB free",
                 "" if status == "ok" else "free disk space")


@check
def receipt(ctx):
    """An instance receipt exists"""
    ctx.receipt = instance.find(ctx.name)
    if ctx.receipt is None:
        return Check("receipt", "warn", f"no instance named {ctx.name!r}",
                     f"nim.agency install create --name {ctx.name} --group validation")
    return Check("receipt", "ok", f"{ctx.name}: created "
                 f"{ctx.receipt.get('audit', {}).get('created_at', '?')[:19]}")


@check
def venv_health(ctx):
    """The instance venv exists and its interpreter runs"""
    if ctx.receipt is None:
        return Check("venv", "warn", "skipped: no receipt")
    python = Path(ctx.receipt["paths"]["python"])
    if not python.exists():
        return Check("venv", "fail", f"{python} is missing",
                     f"nim.agency install create --name {ctx.name} --recreate")
    version = instance.run_in(python, "import platform; print(platform.python_version())")
    if version is None:
        return Check("venv", "fail", f"{python} does not run (base interpreter moved "
                     "or upgraded?)", f"nim.agency install create --name {ctx.name} --recreate")
    mirror = Path(ctx.receipt["paths"]["venv"]) / instance.RECEIPT_NAME
    if not mirror.exists() or json.loads(mirror.read_text()).get("id") != ctx.receipt["id"]:
        return Check("venv", "warn", f"Python {version}; venv-side receipt missing or "
                     "from another instance", f"nim.agency install create --name "
                     f"{ctx.name} --recreate")
    return Check("venv", "ok", f"Python {version} at {python}")


@check
def packages(ctx):
    """Dependency groups are installed in the venv"""
    if ctx.receipt is None or not ctx.receipt.get("requirements"):
        return Check("packages", "ok", "no dependency groups recorded")
    names = [instance.requirement_name(s) for s in ctx.receipt["requirements"]]
    found = instance.installed_versions(Path(ctx.receipt["paths"]["python"]), names)
    missing = [n for n, v in found.items() if v is None]
    if missing:
        return Check("packages", "fail", f"missing in venv: {', '.join(missing)}",
                     f"nim.agency install create --name {ctx.name} --recreate")
    return Check("packages", "ok", ", ".join(f"{n} {v}" for n, v in found.items()))


@check
def launcher(ctx):
    """Which interpreter bin/nim.agency runs agency commands on"""
    # The installer itself always runs on the base interpreter (launch.py),
    # so ask what the launcher would do for an ordinary command
    import os
    from . import launch
    if os.environ.get("NIM_NO_VENV"):
        return Check("launcher", "warn", "NIM_NO_VENV is set: commands run on "
                     f"{sys.executable} without the instance venv",
                     "unset NIM_NO_VENV")
    if ctx.receipt is None:
        validation = "active" if _has("jsonschema") else "off (jsonschema absent)"
        return Check("launcher", "warn" if validation != "active" else "ok",
                     f"no instance: commands run on {sys.executable}; schema "
                     f"validation {validation}",
                     f"nim.agency install create --name {ctx.name} --group all")
    target = launch.instance_python(ctx.name)
    if target is None:
        return Check("launcher", "fail", "receipt exists but its interpreter is missing",
                     f"nim.agency install create --name {ctx.name} --recreate")
    return Check("launcher", "ok", f"agency commands run on {target}")


def _has(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _existing(path: Path) -> Path:
    from .paths import nearest_existing
    return nearest_existing(path)


def run(name: str = DEFAULT_INSTANCE) -> list:
    ctx = Context(name=name)
    return [fn(ctx) for fn in CHECKS]


def listing() -> list:
    return [(fn.__name__, (fn.__doc__ or "").strip()) for fn in CHECKS]
