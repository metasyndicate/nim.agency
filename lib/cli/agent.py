"""
`nim.agency agent` - agent roster operations.

    agent list      tabular roster of agents
    agent show      agent details by codename, or from a JSON file (--from)
    agent generate  procedurally generate agents; --dry-run prints without saving
    agent credit    paymaster adjustment: award/deduct XP and gold
    agent train     dojo: allocate unassigned skill points to a domain
    agent equip     depot: buy or upgrade a tool with gold
    agent enroll    issue badge + primary keypair (--backfill for the roster)
"""

import json
import sys
from pathlib import Path

from ..data import AgentGenerator, get_datastore
from ..data.economy import apply_award, equip, train
from ..data.identity import backfill, enroll, is_enrolled, org_id


def _find_agent(ds, codename: str) -> dict:
    match = [a for a in ds.query("agent").data
             if a["identity"]["codename"] == codename]
    if not match:
        print(f"Agent not found: {codename}", file=sys.stderr)
        raise SystemExit(1)
    return match[0]


def cmd_list(args) -> int:
    ds = get_datastore()
    sort_key = "economy.xp" if args.by_xp else None
    agents = ds.query("agent", limit=args.limit,
                      sort_key=sort_key, sort_reverse=bool(sort_key)).data
    if not agents:
        print("No agents. Generate some: nim.agency agent generate -n 5")
        return 0

    ranks = {r["id"]: r for r in ds.load_reference("rank")}
    print(f"{'CODENAME':<24} {'NAME':<28} {'RANK':<12} {'XP':>6} {'GOLD':>6} "
          f"{'MSN':>4} {'KEY':<4} VIOL")
    print("-" * 95)
    for agent in agents:
        ident = agent["identity"]
        cls = agent.get("classification", {})
        economy = agent.get("economy", {})
        rank = ranks.get(cls.get("rank_id"), {}).get("label", "?")
        missions = agent.get("ratings", {}).get("missions_completed", 0)
        violations = (agent.get("conduct") or {}).get("violations", 0)
        key = "yes" if is_enrolled(agent) else "-"
        print(f"{ident['codename']:<24} {ident['name']:<28} {rank:<12} "
              f"{economy.get('xp', 0):>6} {economy.get('gold', 0):>6} "
              f"{missions:>4} {key:<4} {violations if violations else '-'}")
    return 0


def _load_agent_file(source: str) -> list[dict]:
    """Agents from a JSON file or stdin ('-'): an object or a list."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text()
        doc = json.loads(text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Cannot read agent JSON from {source}: {e}", file=sys.stderr)
        raise SystemExit(1)
    agents = doc if isinstance(doc, list) else [doc]
    if not all(isinstance(a, dict) and "identity" in a for a in agents):
        print(f"Not an agent document: {source}", file=sys.stderr)
        raise SystemExit(1)
    return agents


def _emit_json(agents: list[dict]) -> None:
    out = agents[0] if len(agents) == 1 else agents
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))


def cmd_show(args) -> int:
    ds = get_datastore()
    if bool(args.codename) == bool(args.from_file):
        print("Name an agent codename or pass --from FILE (not both)",
              file=sys.stderr)
        return 1
    agents = (_load_agent_file(args.from_file) if args.from_file
              else [_find_agent(ds, args.codename)])
    if args.format == "json":
        _emit_json(agents)
        return 0
    for i, agent in enumerate(agents):
        if i:
            print()
        render(ds, agent)
    return 0


def render(ds, agent: dict) -> None:
    """Human-readable profile. Works for saved and unsaved agents."""
    ident = agent["identity"]
    cls = agent.get("classification", {})
    economy = agent.get("economy", {})
    ranks = {r["id"]: r for r in ds.load_reference("rank")}
    quals = {q["id"]: q for q in ds.load_reference("qualifier")}
    rank = ranks.get(cls.get("rank_id"), {})
    qual = quals.get(cls.get("qualifier_id"), {})
    sp = economy.get("skill_points", {})
    print(f"NAME:     {ident['name']}")
    print(f"CODENAME: {ident['codename']}")
    print(f"TITLE:    {ident.get('role_title', '-')}")
    print(f"ID:       {agent.get('id')}")
    print(f"RANK:     {rank.get('label', cls.get('rank_id', '?'))} "
          f"(tier {rank.get('tier', '?')})   "
          f"QUAL: {qual.get('label', '?')}   CLASS: {cls.get('class_id', '?')}   "
          f"ARCH: {cls.get('archetype_id', '?')}")
    print(f"ECONOMY:  xp:{economy.get('xp', 0)}  gold:{economy.get('gold', 0)}  "
          f"skill points: {sp.get('assigned', 0)}/{sp.get('total', 0)} assigned  "
          f"missions: {agent.get('ratings', {}).get('missions_completed', 0)}")

    from ..data.project import agent_bookings, engagement_status
    bookings = agent_bookings(ds, agent["id"])
    if bookings:
        engaged = ", ".join(
            f"{b['project']['slug']} ({b['engagement'].get('role', '?')}, "
            f"{engagement_status(ds, b['engagement']).get('label', '?')})"
            for b in bookings)
        print(f"ENGAGED:  {engaged}")

    crypto = agent.get("crypto") or {}
    if is_enrolled(agent):
        print(f"ACCESS:   badge:{org_id(agent)}  user:{ident.get('dogtag')}  "
              f"key:{crypto.get('key_type')} {crypto.get('fingerprint')} "
              f"({crypto.get('status', 'active')})")
        print(f"VAULT:    {crypto.get('vault_path', '?')}/")
    else:
        print(f"ACCESS:   not enrolled - issue a badge: "
              f"nim.agency agent enroll {ident['codename']}")

    skills = agent.get("skills", [])
    if skills:
        domains = {d["id"]: d for d in ds.load_reference("skill_domain")}
        print("SKILLS:")
        for skill in skills:
            domain = domains.get(skill.get("domain_id"), {})
            label = domain.get("label", f"domain {skill.get('domain_id', '?')}")
            print(f"  {label:<20} {skill.get('points', 0)}")

    conduct = agent.get("conduct") or {}
    if conduct.get("violations"):
        by_provider = ", ".join(f"{p}: {n}" for p, n in
                                (conduct.get("by_provider") or {}).items())
        print(f"CONDUCT:  {conduct['violations']} policy violation(s)"
              + (f"  ({by_provider})" if by_provider else ""))
        if conduct.get("last_violation_at"):
            print(f"          last: {conduct['last_violation_at']}")

    profile = agent.get("profile", {})
    if profile.get("personality"):
        from ..data.persona import Persona
        desc = Persona(ds).describe(profile["personality"])
        if desc["work_styles"]:
            print(f"STYLE:    {', '.join(desc['work_styles'])}")
        if desc["tone"]:
            print(f"TONE:     {', '.join(desc['tone'])}")
        if desc["temperament"]:
            print(f"TEMPER:   {desc['temperament']}")
        if desc["descriptors"]:
            print(f"TRAITS:   {', '.join(desc['descriptors'])}")
    if profile.get("motto"):
        print(f"MOTTO:    {profile['motto']}")
    if profile.get("bio"):
        print(f"BIO: {profile['bio']}")


def cmd_generate(args) -> int:
    ds = get_datastore()
    gen = AgentGenerator(ds, seed=args.seed)
    if args.dry_run:
        agents = gen.generate_batch(args.n)
        if args.format == "json":
            _emit_json(agents)
        else:
            for i, agent in enumerate(agents):
                if i:
                    print()
                render(ds, agent)
            print(f"\n(dry run: {args.n} agent(s) not saved)", file=sys.stderr)
        return 0
    agents = [gen.create_and_save() for _ in range(args.n)]
    if args.format == "json":
        _emit_json(agents)
        return 0
    for agent in agents:
        ident = agent["identity"]
        print(f"  + {ident['name']} ({ident['codename']})")
    print(f"\nGenerated {args.n} agent(s).")
    return 0


def cmd_credit(args) -> int:
    """Paymaster adjustment - manual XP/gold award or (negative) penalty."""
    if not args.xp and not args.gold:
        print("Nothing to credit: pass --xp and/or --gold", file=sys.stderr)
        return 1
    ds = get_datastore()
    agent = _find_agent(ds, args.codename)
    payout = apply_award(
        ds, agent, xp=args.xp, gold=args.gold,
        tx_type="bonus" if args.gold >= 0 else "penalty",
        reference={"type": "manual", "id": "cli"},
        note=args.note or "operator adjustment")
    print(payout.summary_line())
    return 0


def cmd_train(args) -> int:
    """Dojo - spend unassigned skill points on a skill domain."""
    ds = get_datastore()
    agent = _find_agent(ds, args.codename)
    try:
        payout = train(ds, agent, args.skill, args.points)
    except ValueError as e:
        print(f"\033[91m{e}\033[0m", file=sys.stderr)
        return 1
    print(payout.summary_line())
    return 0


def cmd_equip(args) -> int:
    """Depot - buy a new tool or upgrade proficiency with gold."""
    ds = get_datastore()
    agent = _find_agent(ds, args.codename)
    try:
        payout = equip(ds, agent, args.tool)
    except ValueError as e:
        print(f"\033[91m{e}\033[0m", file=sys.stderr)
        return 1
    print(payout.summary_line())
    return 0


def cmd_enroll(args) -> int:
    """Issue badges: primary keypair + vault registration."""
    ds = get_datastore()

    if args.backfill:
        try:
            issued, existing = backfill(ds)
        except RuntimeError as e:
            print(f"\033[91mERROR: {e}\033[0m", file=sys.stderr)
            return 1
        for enrollment in issued:
            print(enrollment.render())
            print()
        print(f"Enrollment sweep: {len(issued)} badge(s) issued, "
              f"{len(existing)} already on file.")
        return 0

    if not args.codename:
        print("Name an agent or pass --backfill", file=sys.stderr)
        return 1
    agent = _find_agent(ds, args.codename)
    try:
        enrollment = enroll(ds, agent)
    except RuntimeError as e:
        print(f"\033[91mERROR: {e}\033[0m", file=sys.stderr)
        return 1
    print(enrollment.render())
    return 0


def add_parser(subparsers):
    p = subparsers.add_parser("agent", help="Agent roster operations")
    actions = p.add_subparsers(dest="action", required=True)

    lst = actions.add_parser("list", help="List agents")
    lst.add_argument("--limit", type=int, default=20, help="Max rows (default: 20)")
    lst.add_argument("--by-xp", action="store_true",
                     help="Sort by XP descending (the veterans float up)")
    lst.set_defaults(func=cmd_list)

    fmt = dict(choices=("text", "json"), default="text",
               help="Output format (default: text)")

    show = actions.add_parser("show", help="Show agent details")
    show.add_argument("codename", nargs="?", help="Agent codename")
    show.add_argument("--from", dest="from_file", metavar="FILE",
                      help="Render agent JSON from FILE ('-' for stdin) "
                           "instead of the roster")
    show.add_argument("--format", **fmt)
    show.set_defaults(func=cmd_show)

    gen = actions.add_parser("generate", help="Generate agents (saved unless --dry-run)")
    gen.add_argument("-n", type=int, default=1, help="Number of agents (default: 1)")
    gen.add_argument("--dry-run", action="store_true",
                     help="Generate and print; save nothing, issue no badge")
    gen.add_argument("--seed", type=int, help="Random seed (reproducible draws)")
    gen.add_argument("--format", **fmt)
    gen.set_defaults(func=cmd_generate)

    credit = actions.add_parser(
        "credit", help="Paymaster adjustment: award/deduct XP and gold")
    credit.add_argument("codename", help="Agent codename")
    credit.add_argument("--xp", type=int, default=0,
                        help="XP delta (negative allowed)")
    credit.add_argument("--gold", type=int, default=0,
                        help="Gold delta (negative allowed, floors at 0)")
    credit.add_argument("--note", type=str, help="Ledger note")
    credit.set_defaults(func=cmd_credit)

    trn = actions.add_parser(
        "train", help="Dojo: allocate unassigned skill points to a domain")
    trn.add_argument("codename", help="Agent codename")
    trn.add_argument("--skill", required=True, help="Skill domain slug")
    trn.add_argument("--points", type=int, default=1,
                     help="Points to allocate (default: 1)")
    trn.set_defaults(func=cmd_train)

    eqp = actions.add_parser(
        "equip", help="Depot: buy or upgrade a tool with gold")
    eqp.add_argument("codename", help="Agent codename")
    eqp.add_argument("--tool", required=True, help="Tool slug (see reference data)")
    eqp.set_defaults(func=cmd_equip)

    enr = actions.add_parser(
        "enroll", help="Issue badge + primary keypair into the depot vault")
    enr.add_argument("codename", nargs="?", help="Agent codename")
    enr.add_argument("--backfill", action="store_true",
                     help="Enroll every unenrolled agent on the roster")
    enr.set_defaults(func=cmd_enroll)
