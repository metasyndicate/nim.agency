"""
Syndicate - the model provider inventory and its capability arithmetic.

A syndicate is one endpoint speaking one transport, serving one or more
channels. A channel grants capabilities; a mission requires them. This
module is the single authority for both halves and for the refusal that
sits between them:

    resolve()               address -> (syndicate, channel)
    required_capabilities() mission constraints -> required slugs
    capability_violation()  why a channel refuses a mission, or None

The requirement set is derived from `MissionConstraints`, never hand
maintained: the capability table's `harness_tools` says which tool names
imply which capability, so adding a tool to a mission automatically
raises its requirements.
"""

from typing import Optional

from ..config import egress_policy, get_config
from ..data import DataStore, get_datastore

from .mission import MissionConstraints


# Ordering for egress disclosure, least to most exposed. A syndicate whose
# egress exceeds the configured policy is refused at the briefing.
EGRESS_RANK = {"local": 0, "private": 1, "public": 2}

# Every dispatch needs the model to emit text at minimum.
BASELINE_CAPABILITY = "text_generation"


class SyndicateError(Exception):
    """Inventory resolution failed - unknown address, or a malformed record."""


# -----------------------------------------------------------------------------
# Inventory resolution
# -----------------------------------------------------------------------------

def load_syndicates(ds: Optional[DataStore] = None) -> list[dict]:
    """All non-retired syndicates in the inventory."""
    ds = ds or get_datastore()
    return ds.query("syndicate").data


def find_syndicate(slug: str, ds: Optional[DataStore] = None) -> Optional[dict]:
    """Look up one syndicate by slug."""
    for syndicate in load_syndicates(ds):
        if syndicate.get("slug") == slug:
            return syndicate
    return None


def default_channel(syndicate: dict) -> Optional[dict]:
    """The channel used when a syndicate is addressed without one: the
    flagged default, else the only channel, else nothing."""
    channels = syndicate.get("channels") or []
    for channel in channels:
        if channel.get("default"):
            return channel
    return channels[0] if len(channels) == 1 else None


def resolve(address: Optional[str] = None,
            ds: Optional[DataStore] = None) -> tuple[dict, dict]:
    """Resolve a dispatch address to (syndicate, channel).

    Address forms: `syndicate_slug`, `syndicate_slug:channel_slug`, or
    None to take `dispatch.provider` from etc/agency.json.

    Raises SyndicateError with an actionable message - the caller turns it
    into a briefing refusal rather than a traceback.
    """
    ds = ds or get_datastore()
    address = address or get_config()["dispatch"]["provider"]

    syndicate_slug, _, channel_slug = address.partition(":")
    syndicate = find_syndicate(syndicate_slug, ds)
    if syndicate is None:
        known = ", ".join(sorted(s["slug"] for s in load_syndicates(ds)))
        raise SyndicateError(
            f"unknown syndicate '{syndicate_slug}' (inventory holds: "
            f"{known or 'nothing - seed data/agency/data/syndicates/'})")

    if not syndicate.get("enabled", True):
        raise SyndicateError(
            f"syndicate '{syndicate_slug}' is disabled - set enabled:true "
            f"on its record to dispatch to it")

    if channel_slug:
        for channel in syndicate.get("channels") or []:
            if channel.get("slug") == channel_slug:
                return syndicate, channel
        known = ", ".join(c["slug"] for c in syndicate.get("channels") or [])
        raise SyndicateError(
            f"syndicate '{syndicate_slug}' serves no channel "
            f"'{channel_slug}' (has: {known or 'none'})")

    channel = default_channel(syndicate)
    if channel is None:
        raise SyndicateError(
            f"syndicate '{syndicate_slug}' has no default channel - address "
            f"one explicitly as '{syndicate_slug}:<channel>' or flag one "
            f"default:true")
    return syndicate, channel


def transport_of(syndicate: dict, ds: Optional[DataStore] = None) -> dict:
    """The transport record backing a syndicate."""
    ds = ds or get_datastore()
    slug = syndicate.get("transport_slug", "")
    transport = ds.get_reference_by_slug("transport", slug)
    if transport is None:
        raise SyndicateError(
            f"syndicate '{syndicate.get('slug')}' names unknown transport "
            f"'{slug}' - see data/agency/data/reference/transport.json")
    return transport


def address_of(syndicate: dict, channel: dict) -> str:
    """Canonical `syndicate:channel` dispatch address."""
    return f"{syndicate.get('slug', '?')}:{channel.get('slug', '?')}"


def endpoint_descriptor(syndicate: dict, channel: dict,
                        ds: Optional[DataStore] = None) -> str:
    """One-line operator-facing description of where work actually goes.

    Provider-agnostic: the briefing prints this instead of reaching for a
    CLI-specific binary path.
    """
    endpoint = syndicate.get("endpoint") or {}
    try:
        transport = transport_of(syndicate, ds)
        default_path = transport.get("default_path", "")
    except SyndicateError:
        default_path = ""

    if endpoint.get("base_uri"):
        path = endpoint.get("path") or default_path
        where = f"{endpoint['base_uri']}{path}"
    elif endpoint.get("command"):
        where = " ".join([endpoint["command"], *(endpoint.get("args") or [])])
    else:
        where = "(endpoint undefined)"

    model = channel.get("model") or "(provider default)"
    return f"{where}  model={model}"


# -----------------------------------------------------------------------------
# Capability arithmetic
# -----------------------------------------------------------------------------

def capability_index(ds: Optional[DataStore] = None) -> dict[str, dict]:
    """The capability vocabulary keyed by slug."""
    ds = ds or get_datastore()
    return {c["slug"]: c for c in ds.load_reference("channel_capability")}


def harness_tool_names(ds: Optional[DataStore] = None) -> set[str]:
    """Every tool name a mission may list in allowed_tools: the union of
    the capability table's `harness_tools`. Derived, never hand kept."""
    names: set[str] = set()
    for capability in capability_index(ds).values():
        names.update(capability.get("harness_tools") or [])
    return names


def capability_of_tool(name: str,
                       ds: Optional[DataStore] = None) -> Optional[str]:
    """The capability slug whose `harness_tools` names this tool."""
    for slug, capability in capability_index(ds).items():
        if name in (capability.get("harness_tools") or []):
            return slug
    return None


def expand_dependencies(slugs, ds: Optional[DataStore] = None) -> set[str]:
    """Close a capability set over its `requires` edges.

    Declaring filesystem_write pulls in filesystem_read and tool_use - the
    dependency is functional, not decorative.
    """
    index = capability_index(ds)
    resolved: set[str] = set()
    pending = list(slugs)
    while pending:
        slug = pending.pop()
        if slug in resolved:
            continue
        resolved.add(slug)
        pending.extend((index.get(slug) or {}).get("requires") or [])
    return resolved


def required_capabilities(constraints: MissionConstraints,
                          ds: Optional[DataStore] = None) -> set[str]:
    """What a mission needs from a channel, derived from its constraints.

    Tool names map to capabilities through the capability table's
    `harness_tools`; a write-capable mission requires filesystem_write
    whether or not it named an editing tool.
    """
    ds = ds or get_datastore()
    required = {BASELINE_CAPABILITY}

    tools = set(constraints.allowed_tools or [])
    if tools:
        for capability in ds.load_reference("channel_capability"):
            if tools & set(capability.get("harness_tools") or []):
                required.add(capability["slug"])

    if not constraints.read_only:
        required.add("filesystem_write")

    return expand_dependencies(required, ds)


def channel_capabilities(channel: dict,
                         ds: Optional[DataStore] = None) -> set[str]:
    """What a channel grants, closed over dependencies."""
    return expand_dependencies(channel.get("capabilities") or [], ds)


def validate_channel(syndicate: dict, channel: dict,
                     ds: Optional[DataStore] = None) -> list[str]:
    """Structural problems with a channel declaration.

    Two ways an operator can write something the world cannot honor:
    granting a capability whose dependencies are absent, or granting one
    the transport physically cannot carry.
    """
    ds = ds or get_datastore()
    problems: list[str] = []
    declared = set(channel.get("capabilities") or [])
    index = capability_index(ds)

    unknown = sorted(declared - set(index))
    if unknown:
        problems.append(
            f"unknown capability slug(s): {', '.join(unknown)}")

    for slug in sorted(declared & set(index)):
        missing = sorted(set(index[slug].get("requires") or []) - declared)
        if missing:
            problems.append(
                f"'{slug}' requires {', '.join(missing)} - declare the "
                f"dependency or drop the capability")

    try:
        transport = transport_of(syndicate, ds)
    except SyndicateError as exc:
        problems.append(str(exc))
        return problems

    ceiling = set(transport.get("capability_ceiling") or [])
    if ceiling:
        over = sorted(declared - ceiling)
        if over:
            problems.append(
                f"transport '{transport['slug']}' cannot carry "
                f"{', '.join(over)} - the protocol has no mechanism for it")

    return problems


def capability_violation(constraints: MissionConstraints, syndicate: dict,
                         channel: dict,
                         ds: Optional[DataStore] = None) -> Optional[str]:
    """Why this channel refuses this mission, or None.

    Checked at briefing time and again by the dispatcher as a backstop -
    the same two-gate shape as `modality_violation`. The message names the
    knob that would change the answer.
    """
    ds = ds or get_datastore()

    structural = validate_channel(syndicate, channel, ds)
    if structural:
        return (f"channel '{address_of(syndicate, channel)}' is "
                f"inconsistently declared: {structural[0]}")

    policy = egress_policy()
    egress = (syndicate.get("endpoint") or {}).get("egress", "public")
    if EGRESS_RANK.get(egress, 2) > EGRESS_RANK.get(policy, 2):
        return (f"syndicate '{syndicate['slug']}' sends prompts to "
                f"'{egress}' egress but dispatch.egress_policy is "
                f"'{policy}' - raise the policy in etc/agency.json or "
                f"route to a closer syndicate")

    required = required_capabilities(constraints, ds)
    granted = channel_capabilities(channel, ds)
    missing = sorted(required - granted)
    if missing:
        index = capability_index(ds)
        labels = ", ".join(
            f"{slug} ({(index.get(slug) or {}).get('label', slug)})"
            for slug in missing)
        if "tool_use" in granted:
            # What the agency harness actually serves within the grant
            harnessed = ",".join(sorted(
                row["function_name"]
                for row in ds.load_reference("harness_function")
                if capability_of_tool(row["function_name"], ds) in granted))
            return (f"channel '{address_of(syndicate, channel)}' cannot serve "
                    f"this mission: missing {labels}. Its harness provides "
                    f"only {harnessed or 'no functions'}. Narrow the "
                    f"mission's allowed_tools (--tools {harnessed or '...'}), "
                    f"or dispatch to a syndicate whose harness covers the "
                    f"rest (--provider claude_cli)")
        return (f"channel '{address_of(syndicate, channel)}' cannot serve "
                f"this mission: missing {labels}. This channel generates "
                f"text but runs no tools, so the work would be answered "
                f"from imagination. Narrow the mission's allowed_tools for "
                f"a text-only task, or dispatch to a syndicate with a "
                f"harness (--provider claude_cli)")

    return None
