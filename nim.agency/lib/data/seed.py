#!/usr/bin/env python3
"""
Data Seeding Script

Initializes the datastore with reference data and optionally
generates sample agents for testing/demonstration.
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.data import SchemaManager, DataStore, AgentGenerator


def verify_reference_data(ds: DataStore) -> dict:
    """Verify all reference data is loaded correctly."""
    results = {}

    reference_types = [
        "status", "rank", "class", "skill_domain",
        "tool_category", "achievement", "mission_status", "tool"
    ]

    for ref_type in reference_types:
        try:
            data = ds.load_reference(ref_type)
            results[ref_type] = {
                "status": "ok",
                "count": len(data)
            }
        except Exception as e:
            results[ref_type] = {
                "status": "error",
                "error": str(e)
            }

    return results


def seed_sample_agents(ds: DataStore, count: int = 5) -> list[dict]:
    """Generate and save sample agents."""
    gen = AgentGenerator(datastore=ds)
    agents = []

    for i in range(count):
        # Vary parameters for diversity
        rank_id = (i % 4) + 1  # Ranks 1-4
        class_id = (i % 3) + 1  # Classes 1-3

        agent = gen.create_and_save(
            rank_id=rank_id,
            class_id=class_id,
            status_id=2,  # Operational
            skill_points=50 + (i * 10)
        )
        agents.append(agent)

    return agents


def print_summary(title: str, data: dict | list) -> None:
    """Print a formatted summary."""
    print(f"\n{'='*60}")
    print(f" {title}")
    print('='*60)

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, dict):
                status = value.get("status", "?")
                count = value.get("count", "?")
                icon = "+" if status == "ok" else "x"
                print(f"  [{icon}] {key}: {count} records")
            else:
                print(f"  {key}: {value}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                name = item.get("identity", {}).get("name", "Unknown")
                codename = item.get("identity", {}).get("codename", "???")
                rank_id = item.get("classification", {}).get("rank_id", 0)
                print(f"  [+] {name} ({codename}) - Rank {rank_id}")
            else:
                print(f"  - {item}")


def main():
    parser = argparse.ArgumentParser(
        description="Initialize and seed the NIM Agency datastore"
    )
    parser.add_argument(
        "--verify", "-v",
        action="store_true",
        help="Verify reference data is loaded correctly"
    )
    parser.add_argument(
        "--seed-agents", "-a",
        type=int,
        default=0,
        metavar="N",
        help="Generate N sample agents"
    )
    parser.add_argument(
        "--list-schemas", "-s",
        action="store_true",
        help="List all available schemas"
    )
    parser.add_argument(
        "--describe",
        type=str,
        metavar="SCHEMA",
        help="Describe a specific schema"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format"
    )

    args = parser.parse_args()

    # Initialize
    sm = SchemaManager().load_all()
    ds = DataStore(schema_manager=sm)

    output = {}

    # List schemas
    if args.list_schemas:
        schemas = sm.list_schemas()
        if args.json:
            output["schemas"] = schemas
        else:
            print_summary("Available Schemas", schemas)

    # Describe schema
    if args.describe:
        desc = sm.describe(args.describe)
        if args.json:
            output["schema_description"] = desc
        else:
            print(f"\n{'='*60}")
            print(f" Schema: {desc.get('title', args.describe)}")
            print('='*60)
            print(f"  Description: {desc.get('description', 'N/A')}")
            print(f"  Type: {desc.get('type', 'N/A')}")
            print(f"  Required: {', '.join(desc.get('required', []))}")
            print(f"\n  Properties:")
            for name, prop in desc.get("properties", {}).items():
                req = "*" if prop.get("required") else " "
                print(f"    {req} {name}: {prop.get('type', 'any')}")
                if prop.get("description"):
                    print(f"        {prop['description'][:60]}")

    # Verify reference data
    if args.verify:
        results = verify_reference_data(ds)
        if args.json:
            output["reference_data"] = results
        else:
            print_summary("Reference Data Verification", results)

    # Seed agents
    if args.seed_agents > 0:
        agents = seed_sample_agents(ds, args.seed_agents)
        if args.json:
            output["seeded_agents"] = [
                {
                    "id": a["id"],
                    "name": a["identity"]["name"],
                    "codename": a["identity"]["codename"]
                }
                for a in agents
            ]
        else:
            print_summary(f"Seeded {len(agents)} Agents", agents)

    # JSON output
    if args.json and output:
        print(json.dumps(output, indent=2))

    # Default: show help if no args
    if not any([args.verify, args.seed_agents, args.list_schemas, args.describe]):
        parser.print_help()
        print("\nQuick start:")
        print("  python seed.py --verify           # Check reference data")
        print("  python seed.py --list-schemas     # List all schemas")
        print("  python seed.py --describe agent   # Describe agent schema")
        print("  python seed.py --seed-agents 5    # Create 5 sample agents")


if __name__ == "__main__":
    main()
