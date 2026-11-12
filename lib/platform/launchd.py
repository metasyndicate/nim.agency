"""launchd: render a platform record as a user LaunchAgent and apply it.

Apply is bootout-then-bootstrap of the agency's own label; units the record
`replaces` (a package manager's generated agent) are booted out and their
plist moved aside, so they cannot come back at login with stale settings.
"""

import os
import plistlib
import time
from pathlib import Path

from . import host as hostlib

AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def unit_path(label: str, agents_dir: Path = AGENTS_DIR) -> Path:
    return Path(agents_dir) / f"{label}.plist"


def unit(record: dict) -> dict:
    """The LaunchAgent dictionary the record describes."""
    service = record["service"]
    plist = {
        "Label": service["label"],
        "ProgramArguments": list(service["program"]),
        "RunAtLoad": bool(service.get("run_at_load", True)),
        "KeepAlive": bool(service.get("keep_alive", True)),
    }
    if service.get("environment"):
        plist["EnvironmentVariables"] = dict(sorted(service["environment"].items()))
    for src, dst in (("working_directory", "WorkingDirectory"),
                     ("stdout_path", "StandardOutPath"),
                     ("stderr_path", "StandardErrorPath")):
        if service.get(src):
            plist[dst] = service[src]
    return plist


def render(record: dict, agents_dir: Path = AGENTS_DIR) -> tuple[str, str]:
    text = plistlib.dumps(unit(record), sort_keys=True).decode()
    return str(unit_path(record["service"]["label"], agents_dir)), text


def domain() -> str:
    return f"gui/{os.getuid()}"


def loaded(label: str, runner=hostlib.run) -> bool:
    return runner(["launchctl", "print", f"{domain()}/{label}"]) is not None


def plan(record: dict, agents_dir: Path = AGENTS_DIR) -> list[str]:
    """The steps apply will take, as disclosed before confirmation."""
    label = record["service"]["label"]
    path, _ = render(record, agents_dir)
    steps = []
    for old in record["service"].get("replaces") or []:
        steps.append(f"stop and retire {old} (bootout; plist moved to "
                     f"{old}.plist.retired-by-nim)")
    steps.append(f"write {path}")
    steps.append(f"bootout {domain()}/{label} (if loaded), then bootstrap it")
    return steps


def apply(record: dict, agents_dir: Path = AGENTS_DIR,
          runner=hostlib.run) -> list[str]:
    """Make launchd run the record's service. Returns what was done."""
    done = []
    agents_dir = Path(agents_dir)
    for old in record["service"].get("replaces") or []:
        if old.startswith("homebrew.mxcl."):
            # brew's own stop also forgets the service, so a later
            # `brew services` run does not resurrect it
            runner(["brew", "services", "stop", old.removeprefix("homebrew.mxcl.")])
        runner(["launchctl", "bootout", f"{domain()}/{old}"])
        old_path = unit_path(old, agents_dir)
        if old_path.exists():
            old_path.rename(old_path.with_name(old_path.name + ".retired-by-nim"))
            done.append(f"retired {old_path}")
        else:
            done.append(f"stopped {old}")
    label = record["service"]["label"]
    path, text = render(record, agents_dir)
    Path(path).write_text(text)
    done.append(f"wrote {path}")
    runner(["launchctl", "bootout", f"{domain()}/{label}"])
    time.sleep(1)
    if runner(["launchctl", "bootstrap", domain(), path]) is None:
        raise RuntimeError(f"launchctl bootstrap {domain()} {path} failed - "
                           f"check `launchctl print {domain()}/{label}`")
    done.append(f"bootstrapped {domain()}/{label}")
    return done
