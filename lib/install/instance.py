"""
Instances - create, inspect, and remove an agency instance: a virtual
environment carrying the dependency groups it needs, and a receipt that
records it (plan.101426.installer.md, I-VENV / I-DEPS / I-RECEIPT).

The venv's interpreter is always invoked by path; nothing is activated, and
pip never touches the base interpreter (PEP 668).
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import uuid
import venv
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import (
    DEFAULT_INSTANCE, REPO_ROOT, resolve_home, resolve_venv, venv_python,
)

PYPROJECT = REPO_ROOT / "pyproject.toml"
MIN_PYTHON = (3, 11)          # tomllib, sysconfig "venv" scheme
RECEIPT_NAME = "nim_install.json"


class InstallError(RuntimeError):
    """An instance operation that cannot proceed; the message says why."""


# -- facts about this machine -------------------------------------------------

def externally_managed() -> bool:
    """PEP 668: the base interpreter carries an EXTERNALLY-MANAGED marker."""
    stdlib = sysconfig.get_path("stdlib", sysconfig.get_default_scheme())
    return bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").exists()


def base_python() -> dict:
    base = getattr(sys, "_base_executable", None) or sys.executable
    return {"base_executable": base,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": f"{sys.platform}-{platform.machine()}",
            "externally_managed": externally_managed()}


def git_source() -> dict:
    source = {"path": str(REPO_ROOT), "vcs": None, "commit": None, "dirty": None}
    if not (REPO_ROOT / ".git").exists():
        return source
    try:
        commit = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10)
        status = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return source
    if commit.returncode == 0:
        source.update(vcs="git", commit=commit.stdout.strip(),
                      dirty=bool(status.stdout.strip()))
    return source


# -- dependency groups (PEP 735) -----------------------------------------------

def dependency_groups() -> dict:
    """pyproject [dependency-groups], with include-group expanded."""
    import tomllib
    with open(PYPROJECT, "rb") as handle:
        raw = tomllib.load(handle).get("dependency-groups", {})

    def expand(name: str, seen: tuple = ()) -> list:
        if name in seen:
            raise InstallError(f"dependency group cycle: {' -> '.join(seen + (name,))}")
        if name not in raw:
            raise InstallError(f"no dependency group {name!r} in pyproject.toml "
                               f"(have: {', '.join(raw) or 'none'})")
        out = []
        for item in raw[name]:
            if isinstance(item, dict) and "include-group" in item:
                out += expand(item["include-group"], seen + (name,))
            elif isinstance(item, str):
                out.append(item)
        return list(dict.fromkeys(out))

    return {name: expand(name) for name in raw}


def requirement_name(spec: str) -> str:
    """The distribution name at the head of a PEP 508 specifier."""
    for i, ch in enumerate(spec):
        if not (ch.isalnum() or ch in "-_."):
            return spec[:i]
    return spec


# -- venv interrogation --------------------------------------------------------

def run_in(python: Path, code: str, timeout: int = 60) -> Optional[str]:
    """Run code in another interpreter; its stdout, or None if it failed."""
    try:
        done = subprocess.run([str(python), "-I", "-c", code], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def installed_versions(python: Path, names: list) -> dict:
    """Each distribution's version as the venv sees it (None if absent)."""
    code = ("import json, importlib.metadata as m\n"
            "out = {}\n"
            f"for n in {json.dumps(names)}:\n"
            "    try: out[n] = m.version(n)\n"
            "    except m.PackageNotFoundError: out[n] = None\n"
            "print(json.dumps(out))")
    raw = run_in(python, code)
    return json.loads(raw) if raw else {n: None for n in names}


# -- receipts ------------------------------------------------------------------

def _datastore():
    from ..data import DataStore, SchemaManager
    return DataStore(schema_manager=SchemaManager().load_all())


def find(name: str = DEFAULT_INSTANCE, ds=None) -> Optional[dict]:
    ds = ds or _datastore()
    return ds.query("install", lambda r: r.get("name") == name).first()


def receipts(ds=None) -> list:
    return list((ds or _datastore()).query("install"))


def _write_mirror(receipt: dict) -> None:
    """The venv-side copy, written atomically (temp file, then replace)."""
    target = Path(receipt["paths"]["venv"]) / RECEIPT_NAME
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(receipt, indent=2) + "\n")
    os.replace(temp, target)


# -- operations ----------------------------------------------------------------

def plan_create(name: str = DEFAULT_INSTANCE, groups: tuple = (),
                home: Optional[str] = None, venv_path: Optional[str] = None) -> dict:
    """Everything `create` would do, decided up front (also the dry run)."""
    if sys.version_info < MIN_PYTHON:
        raise InstallError(f"Python {'.'.join(map(str, MIN_PYTHON))}+ is needed to "
                           f"install (this is {platform.python_version()})")
    resolved_home = resolve_home(home)
    if resolved_home.path is None:
        raise InstallError("no usable home: " + "; ".join(
            f"{c.path}: {c.verdict}" for c in resolved_home.candidates))
    resolved_venv = resolve_venv(resolved_home.path, name, venv_path)
    if resolved_venv.path is None:
        raise InstallError("no usable venv location")
    available = dependency_groups()
    requirements = []
    for group in groups:
        if group not in available:
            raise InstallError(f"no dependency group {group!r} "
                               f"(have: {', '.join(available)})")
        requirements += available[group]
    return {"name": name, "home": resolved_home, "venv": resolved_venv,
            "python": venv_python(resolved_venv.path),
            "groups": list(groups),
            "requirements": list(dict.fromkeys(requirements))}


def create(plan: dict, recreate: bool = False, echo=print) -> dict:
    """Create the venv, install the groups, and write the receipt."""
    venv_dir = plan["venv"].path
    python = plan["python"]
    ds = _datastore()
    existing = find(plan["name"], ds)
    if existing and not recreate:
        raise InstallError(f"instance {plan['name']!r} exists ({existing['paths']['venv']});"
                           f" use --recreate, or `install remove {plan['name']}` first")
    if venv_dir.exists() and not recreate and not existing:
        if not (venv_dir / "pyvenv.cfg").exists():
            raise InstallError(f"{venv_dir} exists and is not a virtual environment")

    symlinks = os.name != "nt"
    try:
        import ensurepip  # noqa: F401
    except ImportError:
        raise InstallError("ensurepip is missing from this Python (on Debian/Ubuntu: "
                           "apt install python3-venv)")
    echo(f"creating venv   {venv_dir}  (symlinks={symlinks})")
    venv.EnvBuilder(with_pip=True, symlinks=symlinks, clear=recreate,
                    prompt=f"nim-{plan['name']}").create(venv_dir)
    if not python.exists():
        raise InstallError(f"venv created but {python} is missing")

    if plan["requirements"]:
        echo(f"installing      {' '.join(plan['requirements'])}")
        done = subprocess.run([str(python), "-m", "pip", "install",
                               "--disable-pip-version-check", "--quiet",
                               *plan["requirements"]])
        if done.returncode != 0:
            raise InstallError(f"pip failed (exit {done.returncode}); the venv is "
                               f"left in place for inspection: {venv_dir}")

    names = [requirement_name(s) for s in plan["requirements"]]
    pip_version = run_in(python, "import importlib.metadata as m; print(m.version('pip'))")
    receipt = {
        "name": plan["name"],
        "receipt_version": "1",
        "installer": "nim.agency installer 1",
        "source": git_source(),
        "paths": {"home": str(plan["home"].path), "venv": str(venv_dir),
                  "python": str(python)},
        "resolution": {"home": plan["home"].source, "venv": plan["venv"].source},
        "python": base_python(),
        "venv": {"symlinks": symlinks, "pip_version": pip_version},
        "groups": plan["groups"],
        "requirements": plan["requirements"],
        "packages": installed_versions(python, names) if names else {},
        "status": {"state": "unknown", "detail": "not yet checked"},
    }
    if existing:
        saved = ds.replace("install", existing["id"], {**existing, **receipt})
    else:
        receipt["id"] = str(uuid.uuid4())
        saved = ds.create("install", receipt)
    _write_mirror(saved)
    return saved


def remove(name: str, echo=print) -> dict:
    """Delete the instance's venv (only the path its receipt records, and
    only if it is a venv) and its receipt."""
    ds = _datastore()
    receipt = find(name, ds)
    if receipt is None:
        raise InstallError(f"no instance named {name!r}")
    venv_dir = Path(receipt["paths"]["venv"])
    if venv_dir.exists():
        if not (venv_dir / "pyvenv.cfg").exists():
            raise InstallError(f"{venv_dir} is not a virtual environment; not deleting it")
        echo(f"removing venv   {venv_dir}")
        shutil.rmtree(venv_dir)
    ds.delete("install", receipt["id"], hard=True)
    return receipt


def record_status(name: str, state: str, detail: str) -> None:
    ds = _datastore()
    receipt = find(name, ds)
    if receipt is None:
        return
    receipt["status"] = {"state": state, "detail": detail,
                         "last_check": datetime.now(timezone.utc).isoformat()}
    saved = ds.replace("install", receipt["id"], receipt)
    if Path(saved["paths"]["venv"]).exists():
        _write_mirror(saved)
