"""
Platform - inference software on a host, kept as data the agency can
redeploy, reconfigure and operate (schema: core/platform.schema.json).

    capture   read the live host into a record (vendor capturer)
    render    the service unit the record describes (service manager)
    apply     install that unit on this host, replacing what it supersedes
    verify    capture again and report drift from the record

The record is the source of truth; the host is made to match it, never the
other way round except by an explicit capture. Vendors plug in through
CAPTURERS (one module per vendor slug); service managers through MANAGERS.
"""

import socket
from typing import Callable, Optional

from . import launchd, ollama

CAPTURERS: dict[str, Callable[..., dict]] = {
    "ollama": ollama.capture,
}

MANAGERS = {
    "launchd": launchd,
}

UNIT_PREFIX = "org.metasyndicate.nim"


class PlatformError(Exception):
    """A platform operation cannot proceed; the message says why."""


def this_host() -> str:
    return socket.gethostname()


def is_local(record: dict) -> bool:
    """Whether this process runs on the record's host."""
    return (record.get("host") or {}).get("hostname") == this_host()


def unit_label(slug: str) -> str:
    """Agency-owned unit name: never a package manager's generated one, so
    `brew services` (or apt) cannot rewrite the settings behind our back."""
    return f"{UNIT_PREFIX}.{slug}"


def capture(vendor_slug: str, slug: str, host_name: Optional[str] = None,
            **kw) -> dict:
    """A platform record (without id/audit) read from the live host."""
    capturer = CAPTURERS.get(vendor_slug)
    if capturer is None:
        raise PlatformError(
            f"no capturer for vendor '{vendor_slug}' "
            f"(have: {', '.join(sorted(CAPTURERS))}) - add lib/platform/"
            f"{vendor_slug}.py and register it in CAPTURERS")
    record = capturer(slug=slug, host_name=host_name, **kw)
    record["vendor_slug"] = vendor_slug
    return record


def manager_of(record: dict):
    name = (record.get("service") or {}).get("manager")
    module = MANAGERS.get(name)
    if module is None:
        raise PlatformError(f"service manager '{name}' is not supported "
                            f"(have: {', '.join(sorted(MANAGERS))})")
    return module


def render(record: dict) -> tuple[str, str]:
    """(unit path, unit text) for the record's service."""
    return manager_of(record).render(record)


# Fields whose difference between record and host is drift, with the
# reason it matters. Artifacts compare by name -> digest.
DRIFT_FIELDS = (
    (("software", "version"), "software version"),
    (("service", "label"), "service unit"),
    (("service", "program"), "service program"),
    (("service", "environment"), "settings (environment)"),
    (("exposure", "bind"), "bind address"),
)


def _get(record: dict, path: tuple):
    value = record
    for key in path:
        value = (value or {}).get(key)
    return value


def drift(record: dict, live: dict) -> list[str]:
    """Human-readable differences: what the host does that the record
    does not say (or vice versa)."""
    problems = []
    for path, label in DRIFT_FIELDS:
        want, have = _get(record, path), _get(live, path)
        if want != have:
            problems.append(f"{label}: record {want!r}, host {have!r}")
    want = {a["name"]: a.get("digest") for a in record.get("artifacts") or []}
    have = {a["name"]: a.get("digest") for a in live.get("artifacts") or []}
    for name in sorted(set(want) | set(have)):
        if name not in have:
            problems.append(f"artifact {name}: in record, not on host")
        elif name not in want:
            problems.append(f"artifact {name}: on host, not in record")
        elif want[name] != have[name]:
            problems.append(f"artifact {name}: digest {want[name]} recorded, "
                            f"{have[name]} on host")
    want_px = {(p["kind"], p["url"], p["target"])
               for p in (record.get("exposure") or {}).get("proxies") or []}
    have_px = {(p["kind"], p["url"], p["target"])
               for p in (live.get("exposure") or {}).get("proxies") or []}
    for kind, url, target in sorted(want_px - have_px):
        problems.append(f"proxy {kind} {url} -> {target}: in record, not on host")
    for kind, url, target in sorted(have_px - want_px):
        problems.append(f"proxy {kind} {url} -> {target}: on host, not in record")
    want_g = {(g["kind"], g["target"])
              for g in (record.get("exposure") or {}).get("guards") or []}
    have_g = {(g["kind"], g["target"])
              for g in (live.get("exposure") or {}).get("guards") or []}
    for kind, target in sorted(want_g - have_g):
        problems.append(f"guard {kind} {target}: in record, not enforced on host")
    for kind, target in sorted(have_g - want_g):
        problems.append(f"guard {kind} {target}: enforced on host, not in record")
    return problems


def guard_commands(record: dict) -> list[str]:
    """Root commands that enforce the record's guards (the operator runs
    them: the agency never escalates)."""
    from . import host as hostlib
    commands = []
    for guard in (record.get("exposure") or {}).get("guards") or []:
        if guard["kind"] == "alf_block_incoming":
            commands += hostlib.alf_block_commands(guard["target"])
    return commands
