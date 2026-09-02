"""
Attribution - codified conventions for crediting biomimetic agent work.

Three attribution scenarios, applied in perpetuity across the agency.
Every format leads with WHO (the personified agent), then WHAT (mission
context), then the UNDERLYING IDENTITY the agent piggybacks on (the
operator/host/session that spawned it - op/sys/net/auth context that is
otherwise implicit).

1. AUTHORSHIP - git author string + structured commit trailers:

       Biomimetic Dr. Julia Pembroke <@synodic.miner>

       Attributed-To: Biomimetic Dr. Julia Pembroke <@synodic.miner>
       Agent-Badge: NIM-0042 ed25519 SHA256:...
       Mission: 23e2f097 (complete)
       Dispatched-By: bradley@host.example.edu (uid=501)
       Agency: NIM Biomimetic Agency [26.9.2]

2. BANNER - reports, journals, ceremonies (personified, whimsical):

       🐝 [26.9.2] @synodic.miner >> Dr. Julia Pembroke, Field
       Specialist, NIM Biomimetic Agency | 💰 450 | ⚡ 1645 |
       [23e2f097] COMPLETE pay:+15g | dispatched by bradley@host |
       scope: read-only

3. BYLINE - code/module/script and commentary headers (caller adds the
   comment marker for the target language):

       Authored by Biomimetic Dr. Julia Pembroke (@synodic.miner),
       NIM Biomimetic Agency - mission 23e2f097, 2026-09-02.
       Dispatched by bradley@host.

A requisition slot (`NIM.REQ.NNNNN`) is reserved in the banner grammar
for when external work orders exist; today the mission id is the
canonical reference.
"""

import getpass
import os
import platform
import socket
import sys
from datetime import datetime, timezone
from typing import Optional

AGENCY_NAME = "NIM Biomimetic Agency"
DEFAULT_EMOJI = "🐝"


def nim_stamp(when: Optional[datetime] = None) -> str:
    """Nim-style date stamp: yy.m.d, no zero padding (e.g. 26.9.2)."""
    when = when or datetime.now(timezone.utc)
    return f"{when.year % 100}.{when.month}.{when.day}"


def spawn_context() -> dict:
    """Capture the operator/session identity an agent piggybacks on:
    account, host, tty, cwd, and network origin. Stamped onto missions at
    dispatch so the underlying op/sys/net/auth context is explicit."""
    try:
        tty = os.ttyname(sys.stdin.fileno())
    except (OSError, ValueError):
        tty = None
    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    # getfqdn() can return loopback reverse-DNS artifacts; the plain
    # hostname is more honest than 1.0.0.127.in-addr.arpa
    if (not fqdn or fqdn.endswith(".in-addr.arpa")
            or fqdn.startswith("localhost")):
        fqdn = hostname
    return {
        "operator": getpass.getuser(),
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "host": hostname,
        "fqdn": fqdn,
        "platform": f"{sys.platform}/{platform.release()}",
        "shell": os.environ.get("SHELL"),
        "tty": tty,
        "cwd": os.getcwd(),
        "ssh_connection": os.environ.get("SSH_CONNECTION"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


def mission_scope_label(read_only: bool) -> str:
    """Human scope tag for banners: read-only vs read/write."""
    return "read-only" if read_only else "read/write"


def dispatched_by_line(ctx: Optional[dict]) -> str:
    """`operator@fqdn` - the piggybacked identity, shortest honest form."""
    if not ctx:
        return "unknown@unknown"
    return f"{ctx.get('operator', 'unknown')}@{ctx.get('fqdn') or ctx.get('host', 'unknown')}"


def agent_emoji(agent: dict, ds=None) -> str:
    """The agent's mark: archetype icon when one exists, else the bee."""
    if ds is not None:
        archetype_id = (agent.get("classification") or {}).get("archetype_id")
        if archetype_id:
            entry = ds.get_reference_by_id("archetype", archetype_id) or {}
            if entry.get("icon"):
                return entry["icon"]
    return DEFAULT_EMOJI


def git_author(agent: dict) -> str:
    """Scenario 1 (simple): git author/committer string.

    identity.name already carries the honorific ("Dr. Julia Pembroke").
    """
    ident = agent["identity"]
    return f"Biomimetic {ident['name']} <@{ident['codename']}>"


def git_trailers(agent: dict, mission=None, ctx: Optional[dict] = None) -> list[str]:
    """Scenario 1 (detailed): structured trailers for a commit body."""
    crypto = agent.get("crypto") or {}
    lines = [f"Attributed-To: {git_author(agent)}"]
    if crypto.get("fingerprint"):
        from .identity import org_id
        lines.append(f"Agent-Badge: {org_id(agent)} "
                     f"{crypto.get('key_type', 'ed25519')} "
                     f"{crypto['fingerprint']}")
    if mission is not None:
        status = getattr(getattr(mission, "status", None), "value", None) \
            or getattr(mission, "status", "?")
        lines.append(f"Mission: {mission.id} ({status})")
    if ctx:
        uid = f" (uid={ctx['uid']})" if ctx.get("uid") is not None else ""
        lines.append(f"Dispatched-By: {dispatched_by_line(ctx)}{uid}")
    lines.append(f"Agency: {AGENCY_NAME} [{nim_stamp()}]")
    return lines


def banner(
    agent: dict,
    mission=None,
    pay_gold: Optional[int] = None,
    ctx: Optional[dict] = None,
    ds=None,
    scope: Optional[str] = None,
) -> str:
    """Scenario 2: the personified one-liner for reports and journals."""
    ident = agent["identity"]
    economy = agent.get("economy") or {}
    role = ident.get("role_title") or "Field Specialist"

    parts = [
        f"{agent_emoji(agent, ds)} [{nim_stamp()}] @{ident['codename']} "
        f">> {ident['name']}, {role}, {AGENCY_NAME}",
        f"💰 {economy.get('gold', 0)}",
        f"⚡ {economy.get('xp', 0)}",
    ]
    if mission is not None:
        status = getattr(getattr(mission, "status", None), "value", None) \
            or getattr(mission, "status", "?")
        pay = f" pay:+{pay_gold}g" if pay_gold else ""
        parts.append(f"[{mission.id}] {str(status).upper()}{pay}")
    if ctx:
        parts.append(f"dispatched by {dispatched_by_line(ctx)}")
    if scope:
        parts.append(f"scope: {scope}")
    return " | ".join(parts)


def code_byline(agent: dict, mission=None, ctx: Optional[dict] = None) -> str:
    """Scenario 3: one-sentence header for code/scripts/commentary.
    Caller prepends the comment marker for the target language."""
    ident = agent["identity"]
    line = (f"Authored by Biomimetic {ident['name']} "
            f"(@{ident['codename']}), {AGENCY_NAME}")
    if mission is not None:
        line += f" - mission {mission.id}"
    line += f", {datetime.now(timezone.utc).date().isoformat()}."
    if ctx:
        line += f" Dispatched by {dispatched_by_line(ctx)}."
    return line
