"""
`nim.agency syndicate` - the model provider inventory.

    syndicate list    inventory with transport, egress, cost, and channels
    syndicate show    full disclosure for one syndicate
    syndicate probe   reach the endpoint, record state and advertised models
    syndicate conform run the known-answer suite, record the pass rate

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
    "ollama_chat": "/api/tags",
    "openai_compat": "/v1/models",
}


def _conformance_label(syndicate: dict, channel: dict) -> str:
    """'14/18 (78%) kat-1 harness' from the last recorded conform run."""
    record = (((syndicate.get("status") or {}).get("conformance") or {})
              .get(channel.get("slug")))
    if not record:
        return "not measured"
    rate = record.get("rate", 0)
    return (f"{record.get('passed')}/{record.get('total')} ({rate:.0%}) "
            f"{record.get('suite')} {record.get('mode', '')}  "
            f"at {str(record.get('at', '?'))[:16]}").rstrip()


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
            print(f"     {'':<16} conform: {_conformance_label(syndicate, channel)}")

    print("\n  * = default channel. Dispatch with: "
          "nim.agency dispatch --provider <syndicate>[:<channel>] ...")
    return 0


def _substation_label(ds, substation_id) -> str:
    """The hosting substation, or why there is none. Substations are
    operator-local, so a binding made elsewhere may not resolve here."""
    if not substation_id:
        return "(not inventoried - bind with: syndicate edit REF --set /endpoint/substation_id=CODENAME)"
    host = ds.read("substation", substation_id)
    if host is None:
        return f"{substation_id} (unbound: not in this operator's inventory)"
    ident, conn = host.get("identity") or {}, host.get("connection") or {}
    return (f"{ident.get('name', '?')} [{ident.get('codename', '?')}]  "
            f"{conn.get('kind', 'ssh')}://{conn.get('hostname', '?')}")


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
    print(f"  host:      {_substation_label(ds, endpoint.get('substation_id'))}")

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
        print(f"    conform:    {_conformance_label(syndicate, entry)}")
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

    model_caps: dict[str, list] = {}
    if transport["slug"] in ("ollama_native", "ollama_chat"):
        models = [m.get("name", "?") for m in payload.get("models", [])]
        model_caps = {m.get("name", "?"): m.get("capabilities")
                      for m in payload.get("models", [])}
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

    # A channel granting tool_use on a model the endpoint says cannot emit
    # tool calls would offer functions nobody will call
    for channel in syndicate.get("channels") or []:
        caps = model_caps.get(channel.get("model"))
        if (caps is not None and "tool_use" in (channel.get("capabilities") or [])
                and "tools" not in caps):
            print(f"\033[91m  DRIFT: channel '{channel.get('slug')}' grants "
                  f"tool_use but {channel.get('model')} reports no 'tools' "
                  f"capability\033[0m")

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
    # Each probe replaces the last observation (a merge would let a failed
    # probe inherit the previous latency and model list); conformance is a
    # separate observation, made by conform, and is kept
    record = ds.read("syndicate", syndicate["id"]) or dict(syndicate)
    previous = record.get("status") or {}
    if "conformance" in previous:
        status["conformance"] = previous["conformance"]
    record["status"] = status
    ds.replace("syndicate", syndicate["id"], record)


def cmd_conform(args) -> int:
    """Measure a channel against the fixed known-answer suite."""
    from ..config import report_dir
    from ..dispatch import address_of, get_provider
    from ..dispatch.conformance import run_suite

    ds = get_datastore()
    try:
        provider = get_provider(args.ref, ds=ds)
    except SyndicateError as exc:
        print(f"\033[91mERROR: {exc}\033[0m", file=sys.stderr)
        return 1
    syndicate, channel = provider.syndicate, provider.channel
    address = address_of(syndicate, channel)

    print(f"CONFORM {address}: {provider.descriptor}")
    print(f"  runs: {args.runs}   timeout: {args.timeout}s per case")
    print("-" * 78)

    def show(result):
        mark = "\033[92mPASS\033[0m" if result.passed else "\033[91mFAIL\033[0m"
        calls = f"  calls: {','.join(result.calls)}" if result.calls else ""
        print(f"  [{result.run}] {result.slug:<16} {mark}  {result.seconds:>6.1f}s  "
              f"{result.tokens:>6} tok{calls}")
        if result.anomalies:
            print(f"      bird: {', '.join(result.anomalies)}")
        if not result.passed:
            print(f"      {result.detail[:200]}")

    card = run_suite(provider, runs=args.runs, timeout=args.timeout,
                     on_result=show)
    print("-" * 78)
    print(f"  {card['suite']} ({card['mode']}): {card['passed']}/{card['total']} "
          f"passed  rate {card['rate']:.0%}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir() / f"conformance-{syndicate['slug']}-{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"address": address, **card}, indent=2))
    print(f"  scorecard: {path}")

    if not args.no_record:
        summary = {k: card[k] for k in
                   ("suite", "mode", "runs", "passed", "total", "rate", "at")}
        ds.update("syndicate", syndicate["id"], {"status": {
            "conformance": {channel["slug"]: summary}}})
    return 0 if card["passed"] == card["total"] else 1


def add_parser(subparsers):
    p = subparsers.add_parser(
        "syndicate",
        help="Model provider inventory (list, show, probe, conform; "
             "form, options, new, edit, delete)")
    actions = p.add_subparsers(dest="action", required=True)
    from . import compose
    compose.add_verbs(actions, "syndicate")

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

    conform = actions.add_parser(
        "conform",
        help="Run the known-answer suite against a channel and record its "
             "pass rate (dispatches real requests)")
    conform.add_argument("ref", help="Syndicate slug, or 'syndicate:channel'")
    conform.add_argument("--runs", type=int, default=1,
                         help="Repetitions of the suite (default: 1)")
    conform.add_argument("--timeout", type=int, default=300,
                         help="Per-case timeout in seconds (default: 300)")
    conform.add_argument("--no-record", action="store_true",
                         help="Do not write the result onto the syndicate record")
    conform.set_defaults(func=cmd_conform)
