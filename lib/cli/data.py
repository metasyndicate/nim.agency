"""
`nim.agency data` - datastore initialization and schema inspection.

    data verify     verify reference data loads correctly
    data seed       generate sample agents
    data schemas    list available schemas
    data describe   describe a specific schema
"""

from ..data import SchemaManager, DataStore
from ..data.seed import verify_reference_data, seed_sample_agents, print_summary


def _datastore() -> tuple[SchemaManager, DataStore]:
    sm = SchemaManager().load_all()
    return sm, DataStore(schema_manager=sm)


def cmd_verify(args) -> int:
    _, ds = _datastore()
    results = verify_reference_data(ds)
    print_summary("Reference Data Verification", results)
    return 0 if all(r.get("status") == "ok" for r in results.values()) else 1


def cmd_seed(args) -> int:
    _, ds = _datastore()
    agents = seed_sample_agents(ds, args.agents)
    print_summary(f"Seeded {len(agents)} Agents", agents)
    return 0


def cmd_schemas(args) -> int:
    sm, _ = _datastore()
    print_summary("Available Schemas", sm.list_schemas())
    return 0


def cmd_describe(args) -> int:
    sm, _ = _datastore()
    desc = sm.describe(args.schema)
    print(f"\n{'=' * 60}")
    print(f" Schema: {desc.get('title', args.schema)}")
    print('=' * 60)
    print(f"  Description: {desc.get('description', 'N/A')}")
    print(f"  Type: {desc.get('type', 'N/A')}")
    print(f"  Required: {', '.join(desc.get('required', []))}")
    print(f"\n  Properties:")
    for name, prop in desc.get("properties", {}).items():
        req = "*" if prop.get("required") else " "
        print(f"    {req} {name}: {prop.get('type', 'any')}")
        if prop.get("description"):
            print(f"        {prop['description'][:60]}")
    return 0


def add_parser(subparsers):
    p = subparsers.add_parser("data", help="Datastore initialization and schema inspection")
    actions = p.add_subparsers(dest="action", required=True)

    verify = actions.add_parser("verify", help="Verify reference data")
    verify.set_defaults(func=cmd_verify)

    seed = actions.add_parser("seed", help="Generate sample agents")
    seed.add_argument("--agents", type=int, required=True, metavar="N",
                      help="Number of sample agents to generate")
    seed.set_defaults(func=cmd_seed)

    schemas = actions.add_parser("schemas", help="List available schemas")
    schemas.set_defaults(func=cmd_schemas)

    describe = actions.add_parser("describe", help="Describe a schema")
    describe.add_argument("schema", help="Schema name (see: data schemas)")
    describe.set_defaults(func=cmd_describe)
