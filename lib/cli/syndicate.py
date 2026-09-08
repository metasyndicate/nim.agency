"""
`nim.agency syndicate` - the model provider inventory.

    syndicate list    inventory with transport, egress, cost, and channels
    syndicate show    full disclosure for one syndicate
    syndicate probe   reach the endpoint, record state and advertised models

The inventory is what makes dispatch multivalued: `dispatch.provider` in
etc/agency.json names a syndicate here, and `--provider` overrides it per
mission.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ..config import egress_policy, get_config
from ..data import get_datastore
from ..dispatch import (
    SyndicateError, capability_index, load_syndicates, resolve, transport_of,
    validate_channel,
)

# Endpoints that advertise their served models, by transport. Probing is
# read-only: it lists, it never loads or unloads a model.
MODEL_LIST_PATHS = {
    "ollama_native": "/api/tags",
    "openai_compat": "/v1/models",
}


def _cost_label(channel: dict) -> str:
    cost = channel.get("cost") or {}
    return cost.get("class", "?")


def cmd_list(args) -> int:
    ds = get_datastore()
    syndicates = load_syndicates(ds)
    if not syndicates:
        print("No syndicates in inventory.")
        print("Seed one in data/agency/data/syndicates/ "
              "(see syndicate.schema.json)")
        return 0

    configured = get_config()["dispatch"]["provider"]
    print(f"SYNDICATE INVENTORY  (default: {configured}; "
          f"egress policy: {egress_policy()})")
    print("-" * 78)

    for syndicate in sorted(syndicates, key=lambda s: s.get("slug", "")):
        endpoint = syndicate.get("endpoint") or {}
        identity = syndicate.get("identity") or {}
        state = (syndicate.get("status") or {}).get("state", "unknown")
        flags = []
        if not syndicate.get("enabled", True):
            flags.append("DISABLED")
        if syndicate.get("slug") == configured.partition(":")[0]:
            flags.append("DEFAULT")
        flag = f"  [{', '.join(flags)}]" if flags else ""

        print(f"\n{syndicate.get('slug', '?')}  -  {identity.get('label', '?')}"
              f"{flag}")
        print(f"  transport: {syndicate.get('transport_slug', '?')}"
              f"   egress: {endpoint.get('egress', '?')}"
              f"   state: {state}")
        print(f"  endpoint:  {endpoint.get('base_uri') or endpoint.get('command', '?')}")

        for channel in syndicate.get("channels") or []:
            mark = "*" if channel.get("default") else " "
            caps = channel.get("capabilities") or []
            harness = "harness" if "tool_use" in caps else "text-only"
            limits = channel.get("limits") or {}
            print(f"   {mark} {channel.get('slug', '?'):<16} "
                  f"{channel.get('model') or '(default)':<22} "
                  f"{_cost_label(channel):<10} {harness:<10} "
                  f"conc:{limits.get('max_concurrent', 1)}")

    print("\n  * = default channel. Dispatch with: "
          "nim.agency dispatch --provider <syndicate>[:<channel>] ...")
    return 0


def cmd_show(args) -> int:
    ds = get_datastore()
    try:
        syndicate, channel = resolve(args.ref, ds)
        transport = transport_of(syndicate, ds)
    except SyndicateError as exc:
        print(f"\033[91mERROR: {exc}\033[0m", file=sys.stderr)
        return 1

    identity = syndicate.get("identity") or {}
    endpoint = syndicate.get("endpoint") or {}
    index = capability_index(ds)

    print("=" * 60)
    print(f"SYNDICATE: {identity.get('label', '?')}  [{syndicate.get('slug')}]")
    print("=" * 60)
    print(f"  {identity.get('description', '')}")
    print(f"\n  vendor:    {identity.get('vendor', '?')}")
    print(f"  enabled:   {syndicate.get('enabled', True)}")
    print(f"  transport: {transport['slug']}  ({transport.get('kind')} -> "
          f"{transport.get('handler')})")
    print(f"  endpoint:  {endpoint.get('base_uri') or endpoint.get('command', '?')}"
          f"{endpoint.get('path') or transport.get('default_path', '')}")
    print(f"  auth:      {transport.get('auth_mode', 'none')}"
          f"  credential: {endpoint.get('credential_id') or '(none held)'}")
    print(f"  egress:    {endpoint.get('egress', '?')}  "
          f"(policy: {egress_policy()})")

    status = syndicate.get("status") or {}
    print(f"  state:     {status.get('state', 'unknown')}"
          + (f"  checked {status['last_check']}" if status.get("last_check") else "")
          + (f"  {status['latency_ms']:.0f}ms" if status.get("latency_ms") else ""))
    if status.get("error_message"):
        print(f"  error:     {status['error_message']}")

    print(f"\nCHANNELS ({len(syndicate.get('channels') or [])}):")
    for entry in syndicate.get("channels") or []:
        mark = " (default)" if entry.get("default") else ""
        limits = entry.get("limits") or {}
        cost = entry.get("cost") or {}
        print(f"\n  {entry.get('slug')}{mark}  -  {entry.get('label', '')}")
        print(f"    model:      {entry.get('model') or '(endpoint default)'}")
        print(f"    limits:     context={limits.get('context_window', '?')}  "
              f"max_concurrent={limits.get('max_concurrent', 1)}  "
              f"timeout={limits.get('timeout_seconds', '?')}s")
        print(f"    cost:       {cost.get('class', '?')}")
        if entry.get("parameters"):
            print(f"    parameters: {json.dumps(entry['parameters'])}")
        print(f"    grants:")
        for slug in entry.get("capabilities") or []:
            meta = index.get(slug) or {}
            hazard = meta.get("hazard", "none")
            tag = f"  [{hazard}]" if hazard != "none" else ""
            print(f"      + {slug:<20} {meta.get('label', '?')}{tag}")

        problems = validate_channel(syndicate, entry, ds)
        for problem in problems:
            print(f"    \033[91mINCONSISTENT: {problem}\033[0m")
        if entry.get("notes"):
            print(f"    notes:      {entry['notes']}")

    print("=" * 60)
    return 0


def _probe_endpoint(url: str, timeout: int, verify_tls: bool) -> tuple:
    """GET a model-listing URL. Returns (latency_ms, payload) or raises."""
    context = None
    if not verify_tls:
        import ssl
        context = ssl._create_unverified_context()

    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode())
    return (time.monotonic() - started) * 1000, payload


def cmd_probe(args) -> int:
    """Reach the endpoint and record what it reports. Read-only."""
    ds = get_datastore()
    try:
        syndicate, _ = resolve(args.ref, ds)
        transport = transport_of(syndicate, ds)
    except SyndicateError as exc:
        print(f"\033[91mERROR: {exc}\033[0m", file=sys.stderr)
        return 1

    endpoint = syndicate.get("endpoint") or {}
    slug = syndicate.get("slug")

    if transport.get("kind") != "http":
        from ..dispatch import get_provider
        provider = get_provider(slug, ds=ds)
        ok = provider.is_available()
        print(f"PROBE {slug}: {'online' if ok else 'offline'} - "
              f"{provider.descriptor}")
        if not ok:
            print(f"  {provider.unavailable_reason}")
        _record_status(ds, syndicate, "online" if ok else "offline")
        return 0 if ok else 1

    path = MODEL_LIST_PATHS.get(transport["slug"])
    if not path:
        print(f"PROBE {slug}: transport '{transport['slug']}' advertises no "
              f"model listing - nothing to probe")
        return 0

    url = f"{(endpoint.get('base_uri') or '').rstrip('/')}{path}"
    print(f"PROBE {slug}: {url}")

    try:
        latency, payload = _probe_endpoint(
            url, args.timeout, endpoint.get("verify_tls", True))
    except urllib.error.HTTPError as e:
        print(f"\033[91m  unreachable: HTTP {e.code}\033[0m", file=sys.stderr)
        _record_status(ds, syndicate, "error", error=f"HTTP {e.code}")
        return 1
    except Exception as e:
        print(f"\033[91m  unreachable: {e}\033[0m", file=sys.stderr)
        _record_status(ds, syndicate, "unreachable", error=str(e))
        return 1

    if transport["slug"] == "ollama_native":
        models = [m.get("name", "?") for m in payload.get("models", [])]
    else:
        models = [m.get("id", "?") for m in payload.get("data", [])]

    print(f"  online  {latency:.0f}ms  {len(models)} model(s) advertised")
    for name in models:
        print(f"    - {name}")

    # Inventory drift: a channel pinned to a model the endpoint no longer
    # serves fails at dispatch time, so surface it now.
    declared = {c.get("model") for c in syndicate.get("channels") or []
                if c.get("model")}
    missing = sorted(declared - set(models))
    if missing:
        print(f"\033[91m  DRIFT: channel model(s) not served here: "
              f"{', '.join(missing)}\033[0m")
    unlisted = sorted(set(models) - declared)
    if unlisted:
        print(f"  unregistered: {', '.join(unlisted)}  "
              f"(add a channel to serve them)")

    _record_status(ds, syndicate, "online", latency=latency, models=models)
    return 0


def _record_status(ds, syndicate: dict, state: str, latency: float = 0,
                   models: list = None, error: str = "") -> None:
    """Persist probe results onto the syndicate record."""
    status = {
        "state": state,
        "last_check": datetime.now(timezone.utc).isoformat(),
        "error_message": error,
    }
    if latency:
        status["latency_ms"] = round(latency, 1)
    if models is not None:
        status["models_reported"] = models
    ds.update("syndicate", syndicate["id"], {"status": status})


def add_parser(subparsers):
    p = subparsers.add_parser(
        "syndicate", help="Model provider inventory (list, show, probe)")
    actions = p.add_subparsers(dest="action", required=True)

    lst = actions.add_parser("list", help="List the syndicate inventory")
    lst.set_defaults(func=cmd_list)

    show = actions.add_parser("show", help="Full disclosure for one syndicate")
    show.add_argument("ref", help="Syndicate slug, or 'syndicate:channel'")
    show.set_defaults(func=cmd_show)

    probe = actions.add_parser(
        "probe", help="Reach the endpoint and record state (read-only)")
    probe.add_argument("ref", help="Syndicate slug")
    probe.add_argument("--timeout", type=int, default=10,
                       help="Probe timeout in seconds (default: 10)")
    probe.set_defaults(func=cmd_probe)
