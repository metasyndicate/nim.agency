"""
`nim.agency agent` - agent roster operations.

    agent list      tabular roster of agents
    agent show      agent details by codename
    agent generate  procedurally generate and save agents
"""

from ..data import AgentGenerator, get_datastore


def cmd_list(args) -> int:
    ds = get_datastore()
    agents = ds.query("agent", limit=args.limit).data
    if not agents:
        print("No agents. Generate some: nim.agency agent generate -n 5")
        return 0

    print(f"{'CODENAME':<24} {'NAME':<28} {'RANK':<6} {'ARCH':<6} VIOL")
    print("-" * 72)
    for agent in agents:
        ident = agent["identity"]
        cls = agent.get("classification", {})
        violations = (agent.get("conduct") or {}).get("violations", 0)
        print(f"{ident['codename']:<24} {ident['name']:<28} "
              f"{cls.get('rank_id', '?'):<6} {cls.get('archetype_id', '?'):<6} "
              f"{violations if violations else '-'}")
    return 0


def cmd_show(args) -> int:
    ds = get_datastore()
    match = [a for a in ds.query("agent").data
             if a["identity"]["codename"] == args.codename]
    if not match:
        print(f"Agent not found: {args.codename}")
        return 1

    agent = match[0]
    ident = agent["identity"]
    cls = agent.get("classification", {})
    print(f"NAME:     {ident['name']}")
    print(f"CODENAME: {ident['codename']}")
    print(f"TITLE:    {ident.get('role_title', '-')}")
    print(f"ID:       {agent.get('id')}")
    print(f"RANK:     {cls.get('rank_id', '?')}   CLASS: {cls.get('class_id', '?')}   "
          f"ARCH: {cls.get('archetype_id', '?')}   FOCUS: {cls.get('focus_id', '?')}")

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
    if profile.get("bio"):
        print(f"BIO: {profile['bio']}")
    return 0


def cmd_generate(args) -> int:
    ds = get_datastore()
    gen = AgentGenerator(ds)
    for _ in range(args.n):
        agent = gen.create_and_save()
        ident = agent["identity"]
        print(f"  + {ident['name']} ({ident['codename']})")
    print(f"\nGenerated {args.n} agent(s).")
    return 0


def add_parser(subparsers):
    p = subparsers.add_parser("agent", help="Agent roster operations")
    actions = p.add_subparsers(dest="action", required=True)

    lst = actions.add_parser("list", help="List agents")
    lst.add_argument("--limit", type=int, default=20, help="Max rows (default: 20)")
    lst.set_defaults(func=cmd_list)

    show = actions.add_parser("show", help="Show agent details")
    show.add_argument("codename", help="Agent codename")
    show.set_defaults(func=cmd_show)

    gen = actions.add_parser("generate", help="Generate and save agents")
    gen.add_argument("-n", type=int, default=1, help="Number of agents (default: 1)")
    gen.set_defaults(func=cmd_generate)
