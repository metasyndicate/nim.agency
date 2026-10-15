"""
`nim.agency data` - datastore initialization and schema inspection.

    data verify     verify reference data loads correctly
    data check      stored data vs schemas and declared references
    data seed       generate sample agents
    data schemas    list available schemas
    data describe   describe a specific schema
    data form|options|list|show|new|edit|delete ENTITY ...
                    compose any entity through its schema (lib/cli/compose.py)
"""

from ..data import SchemaManager, DataStore
from ..data.seed import verify_reference_data, seed_sample_agents, print_summary
from . import compose


def _datastore() -> tuple[SchemaManager, DataStore]:
    sm = SchemaManager().load_all()
    return sm, DataStore(schema_manager=sm)


def cmd_verify(args) -> int:
    _, ds = _datastore()
    results = verify_reference_data(ds)
    print_summary("Reference Data Verification", results)
    return 0 if all(r.get("status") == "ok" for r in results.values()) else 1


def cmd_check(args) -> int:
    """Conformance of the stored data to the model: each record against its
    schema (when jsonschema is installed) and every declared reference
    (x-nim-ref) against its target. Exit 1 on any invalid or dangling."""
    from collections import Counter
    from ..data import refs

    sm, ds = _datastore()
    entities = args.entity or [e for e, c in ds.ENTITY_CONFIG.items()
                               if c.get("type") in ("reference", "collection")]
    unknown = [e for e in entities if e not in ds.ENTITY_CONFIG]
    if unknown:
        print(f"unknown entity: {', '.join(unknown)}")
        return 2

    validating = sm.can_validate()
    report = refs.check(ds, sm, entities)
    by_entity: dict[str, list] = {}
    for finding in report.findings:
        by_entity.setdefault(finding.entity, []).append(finding)

    failed = False
    print(f"{'entity':<20} {'records':>7} {'invalid':>8} {'dangling':>9} "
          f"{'unbound':>8}")
    for entity in entities:
        rows = refs.records(ds, entity)
        invalid, messages = 0, Counter()
        if validating:
            for row in rows:
                errors = sm.validate(refs.schema_name(ds, entity), row)
                if errors:
                    invalid += 1
                    messages.update(errors)
        found = by_entity.get(entity, [])
        dangling = [f for f in found if not f.unbound]
        unbound = [f for f in found if f.unbound]
        failed |= bool(invalid or dangling)
        print(f"{entity:<20} {len(rows):>7} "
              f"{(invalid if validating else '-'):>8} {len(dangling):>9} "
              f"{len(unbound):>8}")
        if args.verbose:
            for message, count in messages.most_common(args.verbose_limit):
                print(f"    schema  x{count:<4} {message[:110]}")
            for finding in (dangling + unbound)[:args.verbose_limit]:
                print(f"    ref     {finding}")

    print(f"\n{report.checked} reference value(s) checked, "
          f"{len(report.dangling)} dangling, {len(report.unbound)} unbound "
          f"(operator-local targets)")
    if not validating:
        print("schema conformance skipped: jsonschema not installed "
              "(pyproject optional extra `validation`)")
    return 1 if failed else 0


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
    p = subparsers.add_parser("data", help="Datastore inspection, conformance, and composition of any entity")
    actions = p.add_subparsers(dest="action", required=True)
    compose.add_verbs(actions)

    verify = actions.add_parser("verify", help="Verify reference data")
    verify.set_defaults(func=cmd_verify)

    check = actions.add_parser(
        "check", help="Check stored data against its schemas and declared "
                      "references (x-nim-ref)")
    check.add_argument("entity", nargs="*",
                       help="Entities to check (default: all)")
    check.add_argument("-v", "--verbose", action="store_true",
                       help="List schema errors and unresolved references")
    check.add_argument("--limit", dest="verbose_limit", type=int, default=8,
                       metavar="N", help="Lines per entity with -v (default 8)")
    check.set_defaults(func=cmd_check)

    seed = actions.add_parser("seed", help="Generate sample agents")
    seed.add_argument("--agents", type=int, required=True, metavar="N",
                      help="Number of sample agents to generate")
    seed.set_defaults(func=cmd_seed)

    schemas = actions.add_parser("schemas", help="List available schemas")
    schemas.set_defaults(func=cmd_schemas)

    describe = actions.add_parser("describe", help="Describe a schema")
    describe.add_argument("schema", help="Schema name (see: data schemas)")
    describe.set_defaults(func=cmd_describe)
