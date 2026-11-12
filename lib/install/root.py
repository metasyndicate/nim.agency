"""
Root - the one directory an agency instance keeps everything it generates
in, resolved and validated the same way everywhere
(plan.101526.instance.md, N0).

    explicit   --home > NIM_HOME > the checkout's coupling (data/local as a
               link, data/local.json as a pointer, or data/local itself as a
               project-scope root). Validated; a failure refuses and
               suggests what the enumeration would choose - never uses it.
    enumerate  first candidate that validates wins:
               system   linux   /var/local/nim/agency/<name>     (FHS 3.0)
                        darwin  /Library/Application Support/nim/agency/<name>
                        win32   %ProgramData%\\nim\\agency\\<name>
               user     <platform data dir>/agency/<name>       (XDG, Apple, Known Folders)
               project  <checkout>/data/local                   (must be gitignored)

Every candidate carries every check's result, so `install env` can say why
it was taken or passed over. Validation creates nothing but probe entries
it removes.

`establish` (N1) makes a resolved root this checkout's instance: the class
directories, `instance.json` (identity: a uuid that never changes, the name,
scope, and the checkout it belongs to), and the checkout's coupling. The
runtime keeps paths.home() until the consumers move over (plan N2) and the
data is migrated (N3).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .paths import (APP, COMPONENT, DEFAULT_INSTANCE, REPO_ROOT, _env_dir,
                    nearest_existing, platform_dirs, probe)

SCOPES = ("system", "user", "project")
LAYOUT_VERSION = "1"


class RootError(RuntimeError):
    """A root that cannot be established, with the reasons and the remedy."""


@dataclass
class Check:
    id: str
    status: str            # ok, fail, skip
    detail: str

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "detail": self.detail}


@dataclass
class Candidate:
    path: Path
    source: str
    scope: str
    checks: list = field(default_factory=list)
    verdict: str = ""      # chosen, refused, passed over, valid (not needed)

    @property
    def valid(self) -> bool:
        return bool(self.checks) and all(c.status != "fail" for c in self.checks)

    @property
    def failures(self) -> list:
        return [c for c in self.checks if c.status == "fail"]

    def to_dict(self) -> dict:
        return {"path": str(self.path), "source": self.source, "scope": self.scope,
                "verdict": self.verdict, "checks": [c.to_dict() for c in self.checks]}


@dataclass
class Resolution:
    root: Optional[Path]
    source: str
    scope: Optional[str]
    candidates: list
    refused: bool = False
    suggestion: Optional["Resolution"] = None

    def to_dict(self) -> dict:
        return {"root": str(self.root) if self.root else None, "source": self.source,
                "scope": self.scope, "refused": self.refused,
                "candidates": [c.to_dict() for c in self.candidates],
                "suggestion": self.suggestion.to_dict() if self.suggestion else None}


class Root:
    """Resolve and validate the instance root for one checkout and name."""

    IDENTITY = "instance.json"
    CLASSES = ("data", "vault", "state", "cache", "runtime")

    class Scopes:
        """Where each scope's instances live on a platform (name not appended)."""

        @staticmethod
        def system(platform: str) -> Path:
            if platform == "darwin":
                return Path("/Library/Application Support") / APP / COMPONENT
            if platform.startswith("win"):
                base = _env_dir("PROGRAMDATA") or Path("C:/ProgramData")
                return base / APP / COMPONENT
            return Path("/var/local") / APP / COMPONENT

        @staticmethod
        def user(platform: str) -> Path:
            return platform_dirs(platform=platform)["data"] / COMPONENT

    class Coupling:
        """The checkout's persisted choice of root."""

        def __init__(self, checkout: Path):
            self.link = checkout / "data" / "local"
            self.pointer = checkout / "data" / "local.json"

        def read(self) -> Optional[tuple]:
            if self.link.is_symlink():
                target = Path(os.readlink(self.link))
                if not target.is_absolute():
                    target = self.link.parent / target
                return target, "coupling data/local (link)"
            if self.link.is_dir():
                return self.link, "coupling data/local (project root)"
            if self.pointer.is_file():
                with open(self.pointer) as f:
                    return Path(json.load(f)["root"]), "coupling data/local.json"
            return None

        def write(self, root: Path, dry_run: bool = False) -> list:
            """Point the checkout at `root`: a symlink where the platform
            allows one, else the data/local.json pointer. A project-scope
            root is data/local itself and needs no coupling."""
            if Path(root) == self.link:
                return []
            actions = []
            if self.link.is_symlink():
                current = self.read()[0]
                if current == Path(root):
                    return []
                actions.append(f"relink  data/local -> {root}  (was {current})")
                if not dry_run:
                    self.link.unlink()
            elif self.link.exists():
                raise RootError(f"{self.link} is a project-scope root (a real directory); "
                                f"move its contents into {root} before coupling elsewhere")
            else:
                actions.append(f"link    data/local -> {root}")
            if dry_run:
                return actions
            try:
                self.link.symlink_to(root, target_is_directory=True)
            except OSError as exc:
                actions.append(f"symlink unavailable ({exc.strerror or exc}); "
                               f"wrote data/local.json instead")
                Root.Identity.dump(self.pointer, {"root": str(root)})
                return actions
            if self.pointer.exists():
                actions.append("remove  data/local.json (superseded by the link)")
                self.pointer.unlink()
            return actions

    class Identity:
        """instance.json: who an instance is and which checkout it belongs to."""

        @staticmethod
        def read(root: Path) -> Optional[dict]:
            path = Path(root) / Root.IDENTITY
            if not path.is_file():
                return None
            with open(path) as f:
                return json.load(f)

        @staticmethod
        def dump(path: Path, record: dict) -> None:
            """Atomic write: temp file in the same directory, then replace."""
            fd, temp = tempfile.mkstemp(dir=Path(path).parent, prefix=".nim-write-")
            with os.fdopen(fd, "w") as f:
                json.dump(record, f, indent=2)
                f.write("\n")
            os.replace(temp, path)

    class Validate:
        """Every check a root must pass, explicit or enumerated."""

        def __init__(self, checkout: Path):
            self.checkout = checkout

        def run(self, path: Path, adopt: bool = False) -> list:
            first = self.absolute(path)
            if first.status == "fail":
                return [first]
            return [first, self.directory(path), self.writable(path), self.mode(path),
                    self.owner(path), self.permissions(path), self.shippable(path),
                    self.claimed(path, adopt)]

        @staticmethod
        def absolute(path: Path) -> Check:
            if Path(path).is_absolute():
                return Check("absolute", "ok", "absolute path")
            return Check("absolute", "fail", f"relative path '{path}'")

        @staticmethod
        def directory(path: Path) -> Check:
            if path.exists() and not path.is_dir():
                return Check("directory", "fail", "exists and is not a directory")
            if path.is_dir():
                return Check("directory", "ok", "exists")
            parent = nearest_existing(path)
            if not parent.is_dir():
                return Check("directory", "fail", f"{parent} is not a directory")
            return Check("directory", "ok", f"creatable under {parent}")

        @staticmethod
        def writable(path: Path) -> Check:
            problem = probe(path)
            return Check("writable", "fail" if problem else "ok", problem or "probe written")

        @staticmethod
        def mode(path: Path) -> Check:
            if os.name == "nt":
                return Check("mode", "skip", "POSIX modes not applicable")
            if not path.exists():
                return Check("mode", "ok", "created 0700 when made")
            if path.stat().st_mode & 0o002:
                return Check("mode", "fail", "world-writable; instance data includes the vault")
            return Check("mode", "ok", oct(path.stat().st_mode & 0o777))

        @staticmethod
        def owner(path: Path) -> Check:
            if os.name == "nt":
                return Check("owner", "skip", "POSIX ownership not applicable")
            if not path.exists():
                return Check("owner", "ok", "created by this operator")
            st = path.stat()
            if st.st_uid == os.geteuid():
                return Check("owner", "ok", "owned by this operator")
            if st.st_gid in os.getgroups() or st.st_gid == os.getegid():
                return Check("owner", "ok", f"shared through group {st.st_gid}")
            return Check("owner", "fail", f"owned by uid {st.st_uid}, gid {st.st_gid}: "
                                          "not this operator or one of its groups")

        @staticmethod
        def permissions(path: Path) -> Check:
            if os.name == "nt":
                return Check("permissions", "skip", "POSIX modes not applicable")
            parent = nearest_existing(path)
            if probe(path):
                return Check("permissions", "skip", "not writable; see writable")
            trial = Path(tempfile.mkdtemp(dir=parent, prefix=".nim-probe-"))
            try:
                trial.chmod(0o700)
                kept = (trial.stat().st_mode & 0o777) == 0o700
            finally:
                trial.rmdir()
            if kept:
                return Check("permissions", "ok", "0700 honored")
            return Check("permissions", "fail", "filesystem ignores modes; the vault needs 0700")

        @staticmethod
        def shippable(path: Path) -> Check:
            git = shutil.which("git")
            if not git:
                return Check("shippable", "skip", "git not found; not checked")
            parent = nearest_existing(path)
            top = subprocess.run([git, "-C", str(parent), "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True)
            if top.returncode != 0:
                return Check("shippable", "ok", "not in a git work tree")
            tree = top.stdout.strip()
            ignored = subprocess.run([git, "-C", tree, "check-ignore", "-q", str(path)],
                                     capture_output=True, text=True)
            if ignored.returncode == 0:
                return Check("shippable", "ok", f"ignored by git ({tree})")
            return Check("shippable", "fail", f"inside git work tree {tree} and not ignored: "
                                              "instance data could be committed")

        def claimed(self, path: Path, adopt: bool = False) -> Check:
            record = Root.Identity.read(path)
            if record is None:
                return Check("claimed", "ok", "unclaimed")
            owner = record.get("code_root")
            if owner and Path(owner).resolve() != self.checkout.resolve():
                if adopt:
                    return Check("claimed", "ok", f"instance {record.get('id', '?')} "
                                                  f"adopted from checkout {owner}")
                return Check("claimed", "fail", f"instance {record.get('id', '?')} "
                                                f"belongs to checkout {owner} "
                                                f"(install adopt takes it over)")
            return Check("claimed", "ok", f"instance {record.get('id', '?')} of this checkout")

    def __init__(self, name: str = DEFAULT_INSTANCE, checkout: Path = REPO_ROOT,
                 platform: Optional[str] = None, bases: Optional[dict] = None):
        self.name = name
        self.checkout = Path(checkout)
        self.platform = platform or sys.platform
        bases = bases or {}
        self.bases = {
            "system": bases.get("system") or self.Scopes.system(self.platform),
            "user": bases.get("user") or self.Scopes.user(self.platform),
        }
        self.coupling = self.Coupling(self.checkout)
        self.validate = self.Validate(self.checkout)

    def candidates(self) -> list:
        """The enumeration, in order (unchecked)."""
        return [Candidate(self.bases["system"] / self.name, "system scope", "system"),
                Candidate(self.bases["user"] / self.name, "user scope", "user"),
                Candidate(self.checkout / "data" / "local", "project scope", "project")]

    def scope_of(self, path: Path) -> str:
        for candidate in self.candidates():
            if Path(path) == candidate.path:
                return candidate.scope
        return "custom"

    def explicit(self, cli: Optional[str] = None) -> Optional[tuple]:
        if cli:
            return Path(cli).expanduser(), "flag --home"
        env = _env_dir("NIM_HOME")
        if env:
            return env, "env NIM_HOME"
        return self.coupling.read()

    def enumerate(self, adopt: bool = False) -> Resolution:
        chosen = None
        listed = self.candidates()
        for candidate in listed:
            candidate.checks = self.validate.run(candidate.path, adopt)
            if chosen is None and candidate.valid:
                candidate.verdict = "chosen"
                chosen = candidate
            else:
                candidate.verdict = "valid" if candidate.valid else "passed over"
        if chosen is None:
            return Resolution(None, "none valid", None, listed)
        return Resolution(chosen.path, chosen.source, chosen.scope, listed)

    def resolve(self, cli: Optional[str] = None, adopt: bool = False) -> Resolution:
        given = self.explicit(cli)
        if given is None:
            return self.enumerate(adopt)
        path, source = given
        candidate = Candidate(path, source, self.scope_of(path),
                              self.validate.run(path, adopt))
        if candidate.valid:
            candidate.verdict = "chosen"
            return Resolution(path, source, candidate.scope, [candidate])
        candidate.verdict = "refused"
        return Resolution(None, source, None, [candidate], refused=True,
                          suggestion=self.enumerate(adopt))

    @staticmethod
    def explain(resolution: Resolution) -> str:
        """Why a resolution has no root, and what would work instead."""
        reasons = "; ".join(f"{c.path}: " + ", ".join(f"{k.id} ({k.detail})" for k in c.failures)
                            for c in resolution.candidates if c.failures)
        text = f"no valid instance root from {resolution.source}: {reasons or 'no candidates'}"
        if resolution.suggestion and resolution.suggestion.root:
            text += f". The enumeration would choose {resolution.suggestion.root} " \
                    f"({resolution.suggestion.source}); pass it with --home to accept"
        return text

    def establish(self, resolution: Resolution, dry_run: bool = False) -> tuple:
        """Make a resolved root this checkout's instance: class directories
        (0700), instance.json, and the coupling. Returns (identity record,
        one line per change made, or that would be made under dry_run).
        Adoption is decided at resolution (`resolve(adopt=True)`)."""
        if resolution.root is None:
            raise RootError(self.explain(resolution))
        root = Path(resolution.root)
        now = datetime.now(timezone.utc).isoformat()
        actions = []
        layout = self.layout(root)
        for directory in [root] + [layout[name] for name in self.CLASSES]:
            if not directory.exists():
                actions.append(f"create  {directory}  (0700)")
                if not dry_run:
                    directory.mkdir(parents=True, mode=0o700)
        record = self.Identity.read(root)
        if record is None:
            record = {"id": str(uuid.uuid4()), "name": self.name, "created": now,
                      "layout": LAYOUT_VERSION, "history": []}
            named = "a new id" if dry_run else record["id"]
            actions.append(f"write   {layout['identity']}  (new instance, {named})")
        else:
            owner = record.get("code_root")
            if owner and Path(owner).resolve() != self.checkout.resolve():
                record["history"] = record.get("history", []) + [{"code_root": owner,
                                                                  "until": now}]
                actions.append(f"adopt   instance {record['id']} from checkout {owner}")
            actions.append(f"update  {layout['identity']}  (instance {record['id']})")
        record.update({"scope": resolution.scope, "code_root": str(self.checkout),
                       "source": resolution.source, "coupled": now})
        if not dry_run:
            self.Identity.dump(layout["identity"], record)
        actions += self.coupling.write(root, dry_run)
        return record, actions

    @classmethod
    def coupled(cls, name: str = DEFAULT_INSTANCE,
                checkout: Path = REPO_ROOT) -> Optional[Path]:
        """The root this checkout runs against at run time: NIM_HOME, else the
        coupling; None when there is neither (nothing is enumerated here).
        Checked cheaply on every run: an instance.json must exist and name
        this checkout, since writing into another checkout's instance is
        never right."""
        given = cls(name, checkout).explicit()
        if given is None:
            return None
        path, source = given
        record = cls.Identity.read(path)
        if record is None:
            raise RootError(f"{path} ({source}) has no instance.json: nim.agency install couple")
        owner = record.get("code_root")
        if owner and Path(owner).resolve() != Path(checkout).resolve():
            raise RootError(f"{path} ({source}) belongs to checkout {owner}: "
                            f"nim.agency install adopt")
        return Path(path)

    @classmethod
    def layout(cls, root: Path) -> dict:
        """Where each data class lives under a root."""
        paths = {name: Path(root) / name for name in cls.CLASSES}
        paths["identity"] = Path(root) / cls.IDENTITY
        paths["venv"] = paths["runtime"] / "venv"
        return paths


def coupled_root(name: str = DEFAULT_INSTANCE) -> Optional[Path]:
    """Module-level entry for the datastore: Root.coupled for this checkout."""
    return Root.coupled(name)
