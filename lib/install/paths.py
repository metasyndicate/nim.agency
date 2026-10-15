"""
Paths - where an agency instance lives, resolved the same way everywhere.

One resolution replaces the `~/.nim/agency` that was hard-coded in eight
modules. Candidates are tried in order and the first usable one wins; every
answer carries its source, so `nim.agency install env` can say *why* a path
was chosen (plan.101426.installer.md, I-PATHS / I-ENV):

    home   --home > NIM_HOME > config install.home > legacy ~/.nim/agency
           (if it exists) > platform data dir  user_data_dir("nim")/agency
    venv   --venv > NIM_VENV > <repo>/.venv (if it exists) > <home>/venvs/<name>

Platform directories reimplement the platformdirs mapping (XDG Base
Directory v0.8 on Linux, Apple's File System Programming Guide on macOS,
Known Folders on Windows - LocalAppData, never Roaming, since venvs hold
absolute paths and binaries) without depending on platformdirs.

Writability is probed by creating a temporary file in the nearest existing
directory, never with os.access (the Python docs warn it can disagree with
real I/O). Probing never creates the candidate itself: reporting has no
side effects.
"""

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

APP = "nim"
COMPONENT = "agency"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEGACY_HOME = Path.home() / ".nim" / "agency"
DEFAULT_INSTANCE = "default"


# -- platform directories -----------------------------------------------------

def _env_dir(name: str) -> Optional[Path]:
    """An absolute directory from the environment; relative values are
    ignored (XDG: 'considered invalid')."""
    value = os.environ.get(name, "").strip()
    if value and os.path.isabs(os.path.expanduser(value)):
        return Path(os.path.expanduser(value))
    return None


def platform_dirs(app: str = APP, platform: Optional[str] = None) -> dict:
    """User directories for an application, per platform convention."""
    platform = platform or sys.platform
    home = Path.home()
    if platform == "darwin":
        support = home / "Library" / "Application Support" / app
        return {"data": support, "config": support, "state": support,
                "cache": home / "Library" / "Caches" / app,
                "log": home / "Library" / "Logs" / app}
    if platform.startswith("win"):
        local = _env_dir("LOCALAPPDATA") or home / "AppData" / "Local"
        base = local / app
        return {"data": base, "config": base, "state": base,
                "cache": base / "Cache", "log": base / "Logs"}
    data = (_env_dir("XDG_DATA_HOME") or home / ".local" / "share") / app
    state = (_env_dir("XDG_STATE_HOME") or home / ".local" / "state") / app
    return {"data": data,
            "config": (_env_dir("XDG_CONFIG_HOME") or home / ".config") / app,
            "state": state,
            "cache": (_env_dir("XDG_CACHE_HOME") or home / ".cache") / app,
            "log": state / "log"}


# -- probing ------------------------------------------------------------------

def nearest_existing(path: Path) -> Path:
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def probe(path: Path) -> Optional[str]:
    """None when `path` exists as a writable directory or could be created
    as one; otherwise why not. Creates nothing but a temp file it removes."""
    path = Path(path)
    if path.exists() and not path.is_dir():
        return "exists and is not a directory"
    target = nearest_existing(path)
    if not target.is_dir():
        return f"{target} is not a directory"
    try:
        with tempfile.NamedTemporaryFile(dir=target, prefix=".nim-probe-"):
            pass
    except OSError as exc:
        return f"not writable ({exc.strerror or exc})"
    return None


# -- resolution ---------------------------------------------------------------

@dataclass
class Candidate:
    path: Path
    source: str
    verdict: str = ""          # "chosen", "skipped: <why>", or "" (not tried)


@dataclass
class Resolved:
    what: str
    path: Optional[Path]
    source: str
    candidates: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"what": self.what, "path": str(self.path) if self.path else None,
                "source": self.source,
                "candidates": [{"path": str(c.path), "source": c.source,
                                "verdict": c.verdict} for c in self.candidates]}


def _first_usable(what: str, candidates: list[Candidate]) -> Resolved:
    chosen = None
    for candidate in candidates:
        if chosen is not None:
            continue
        problem = probe(candidate.path)
        if problem:
            candidate.verdict = f"skipped: {problem}"
        else:
            candidate.verdict = "chosen"
            chosen = candidate
    if chosen is None:
        return Resolved(what, None, "none usable", candidates)
    return Resolved(what, chosen.path, chosen.source, candidates)


def _config_home() -> Optional[Path]:
    from ..config import get_config
    value = (get_config().get("install") or {}).get("home")
    if value and os.path.isabs(os.path.expanduser(value)):
        return Path(os.path.expanduser(value))
    return None


def resolve_home(cli: Optional[str] = None) -> Resolved:
    """Where this operator's instance state lives (records, vault, logs,
    venvs)."""
    candidates = []
    if cli:
        candidates.append(Candidate(Path(cli).expanduser(), "flag --home"))
    env = _env_dir("NIM_HOME")
    if env:
        candidates.append(Candidate(env, "env NIM_HOME"))
    configured = _config_home()
    if configured:
        candidates.append(Candidate(configured, "config install.home"))
    if LEGACY_HOME.exists():
        candidates.append(Candidate(LEGACY_HOME, "legacy ~/.nim/agency (exists)"))
    candidates.append(Candidate(platform_dirs()["data"] / COMPONENT,
                                f"platform default ({sys.platform})"))
    return _first_usable("home", candidates)


def resolve_venv(home: Path, name: str = DEFAULT_INSTANCE,
                 cli: Optional[str] = None) -> Resolved:
    """Where an instance's virtual environment lives."""
    candidates = []
    if cli:
        candidates.append(Candidate(Path(cli).expanduser(), "flag --venv"))
    env = _env_dir("NIM_VENV")
    if env:
        candidates.append(Candidate(env, "env NIM_VENV"))
    project = REPO_ROOT / ".venv"
    if project.exists():
        candidates.append(Candidate(project, "project .venv (exists)"))
    candidates.append(Candidate(Path(home) / "venvs" / name, "home venvs/<name>"))
    return _first_usable("venv", candidates)


_HOME: Optional[Path] = None


def home() -> Path:
    """The resolved instance home, for the rest of the agency. Falls to the
    legacy location only if nothing is usable, so callers always get a path."""
    global _HOME
    if _HOME is None:
        resolved = resolve_home()
        _HOME = resolved.path or LEGACY_HOME
    return _HOME


def reset() -> None:
    """Forget the cached home (tests; after NIM_HOME changes)."""
    global _HOME
    _HOME = None


def venv_python(venv: Path) -> Path:
    """The interpreter inside a venv, located the way the venv module lays
    it out on this platform (sysconfig 'venv' scheme, 3.11+)."""
    import sysconfig
    venv = Path(venv)
    try:
        scripts = sysconfig.get_paths(scheme="venv",
                                      vars={"base": str(venv), "platbase": str(venv)})["scripts"]
    except KeyError:
        scripts = str(venv / ("Scripts" if os.name == "nt" else "bin"))
    return Path(scripts) / ("python.exe" if os.name == "nt" else "python")
