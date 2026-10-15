#!/usr/bin/env python3
"""
Import persona reference data from normalized source JSON.

    python3 tools/persona/import_sources.py SOURCES_DIR [--out DIR] [--dry-run]

SOURCES_DIR holds the parser outputs (tools/persona/parsers, run over the
publishers' downloads; see tools/persona/README.md):

    ipip_neo.json                           IPIP-NEO 300 / 120 keys (public domain)
    onet_work_styles.json                   O*NET 31.0 work styles + ratings (CC BY 4.0)
    onet_work_styles_bigfive_crosswalk.json O*NET Phase 1 higher-order dimensions
    onet_tasks.json                         O*NET 31.0 task statements + ratings
    nice_work_roles.json                    NICE Framework components v2.2.0 (NIST)

Writes trait.json, work_style.json, work_role.json, work_task.json into
the reference data directory (default data/agency/data/reference). Ids
are assigned in source order; slugs derive from the publishers' codes
(skos:notation), so a re-import is stable. Plan of record:
.nim/work/plan.101426.persona.md
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/agency/data/reference"

ONET_ATTRIBUTION = (
    "This page includes information from the O*NET 31.0 Database by the U.S. "
    "Department of Labor, Employment and Training Administration (USDOL/ETA). "
    "Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA. "
    "NIM Agency has modified all or some of this information. USDOL/ETA has "
    "not approved, endorsed, or tested these modifications.")

# O*NET Phase 1 higher-order dimension -> IPIP domain slug (Emotional
# Stability is the low pole of Neuroticism; Honesty-Humility is HEXACO and
# Compound Dimensions span domains, so neither names an IPIP domain)
HIGHER_ORDER_DOMAIN = {
    "Conscientiousness": "conscientiousness",
    "Extraversion": "extraversion",
    "Agreeableness": "agreeableness",
    "Openness": "openness_to_experience",
    "Emotional Stability": "neuroticism",
    "Honesty-Humility": None,
    "Compound Dimensions": None,
}

# EDITORIAL (not from any source): lex/temperaments.csv words, as
# skos:closeMatch onto work styles, to migrate generated agents. Words
# without a close match (Empirical) stay flavor only. Review freely.
TEMPERAMENT_MATCHES = {
    "intellectual_curiosity": ["Analytical", "Theoretical"],
    "achievement_orientation": ["Driven"],
    "attention_to_detail": ["Methodical", "Systematic", "Observational"],
    "innovation": ["Intuitive"],
    "adaptability": ["Pragmatic"],
    "cautiousness": ["Reflective"],
}

ONET_TYPES = {"Core", "Supplemental"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def label_of(text: str, limit: int = 128) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def load(folder: Path, name: str) -> dict:
    return json.loads((folder / name).read_text(encoding="utf-8"))


def provenance(*sources: dict) -> list[dict]:
    """Retrieval record for the data file's $comment: url + sha256 per raw file."""
    out = []
    for src in sources:
        for name, meta in src.get("raw_files", {}).items():
            out.append({"file": name, "url": meta.get("url"), "sha256": meta.get("sha256")})
    return out


def build_traits(ipip: dict) -> list[dict]:
    rows, n = [], 0
    for domain in ipip["domains"]:
        n += 1
        dslug = slugify(domain["name"])
        rows.append({"id": n, "slug": dslug, "label": domain["name"], "code": domain["code"],
                     "level": "domain", "domain": None, "neo_name": None, "items": [],
                     "sort_order": n})
        for facet in domain["facets"]:
            n += 1
            label = facet["label_title_case_in_comparison_table"]
            neo = facet.get("neo_pi_r_facet_name")
            rows.append({
                "id": n, "slug": slugify(label), "label": label, "code": facet["code"],
                "level": "facet", "domain": dslug,
                "neo_name": neo if neo and neo != label else None,
                "items": [{"text": i["text"], "key": i["key"], "in_120": bool(i["in_120"])}
                          for i in facet["items"]],
                "sort_order": n})
    return rows


def build_work_styles(styles: dict, crosswalk: dict) -> list[dict]:
    phase1 = crosswalk["phase_1_higher_order_dimensions"]
    group_of = {name: g["name"] for g in phase1["groups_table1"] for name in g["work_styles"]}
    disagree = set(phase1.get("internal_inconsistency", []))
    rows = []
    for n, e in enumerate(styles["elements"], start=1):
        slug = slugify(e["name"])
        higher = group_of[e["name"]]
        note = None
        if e["name"] in disagree:
            table3 = next(r["higher_order_dimension_from_phase_1_table3"]
                          for r in phase1["per_style_table3"] if r["work_style"] == e["name"])
            note = (f"The report's Table 1 groups it under {higher}; its Table 3 lists "
                    f"{table3}. Table 1 is used here.")
        rows.append({
            "id": n, "slug": slug, "label": e["name"], "description": e["description"],
            "element_id": e["id"], "group": e["group"], "higher_order": higher,
            "trait_domain": HIGHER_ORDER_DOMAIN[higher],
            "lex_matches": TEMPERAMENT_MATCHES.get(slug, []), "note": note,
            "sort_order": n})
    return rows


def build_roles_and_tasks(styles: dict, tasks: dict, nice: dict, style_slugs: dict):
    roles, task_rows, seen_tasks = [], [], {}

    def add_task(framework: str, code: str, text: str) -> str:
        slug = f"{framework}_{slugify(code)}" if framework == "nice" else f"onet_t{code}"
        if slug not in seen_tasks:
            seen_tasks[slug] = True
            task_rows.append({"id": len(task_rows) + 1, "slug": slug, "label": label_of(text),
                              "description": text, "framework": framework, "code": code,
                              "sort_order": len(task_rows) + 1})
        return slug

    # O*NET: the occupations with work-style ratings
    for soc in sorted(styles["ratings"]):
        rated = styles["ratings"][soc]
        occ = tasks["occupations"][soc]
        links = []
        for t in occ["tasks"]:
            im = (t.get("ratings") or {}).get("IM", {}).get("value")
            links.append({"task": add_task("onet", t["id"], t["text"]),
                          "type": t["type"] if t.get("type") in ONET_TYPES else None,
                          "importance": im})
        ws = [{"style": style_slugs[eid], "impact": v["WI"], "distinctiveness": v["DR"]}
              for eid, v in sorted(rated["styles"].items())]
        roles.append({"slug": f"onet_{slugify(soc)}", "label": occ["title"],
                      "description": occ["description"], "framework": "onet", "code": soc,
                      "category": None, "tasks": links, "work_styles": ws})

    # NICE: every work role, with its tasks
    nice_tasks = nice["tasks"]
    for r in nice["work_roles"]:
        links = [{"task": add_task("nice", tid, nice_tasks[tid]), "type": None,
                  "importance": None} for tid in r["task_ids"]]
        roles.append({"slug": f"nice_{slugify(r['id'])}", "label": r["name"],
                      "description": r["description"], "framework": "nice", "code": r["id"],
                      "category": r.get("category_name"), "tasks": links, "work_styles": []})

    for n, role in enumerate(roles, start=1):
        role["id"] = n
        role["sort_order"] = n
    return roles, task_rows


def document(schema: str, comment: str, rows: list[dict]) -> str:
    """One row per line block, matching the hand-written reference files."""
    body = ",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in rows)
    head = json.dumps({"$schema": f"../../schema/reference/{schema}.schema.json",
                       "$comment": comment}, ensure_ascii=False, indent=2)[:-2]
    return f'{head},\n  "data": [\n{body}\n  ]\n}}\n'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("sources", type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    ipip = load(args.sources, "ipip_neo.json")
    styles = load(args.sources, "onet_work_styles.json")
    crosswalk = load(args.sources, "onet_work_styles_bigfive_crosswalk.json")
    tasks = load(args.sources, "onet_tasks.json")
    nice = load(args.sources, "nice_work_roles.json")

    traits = build_traits(ipip)
    work_styles = build_work_styles(styles, crosswalk)
    style_slugs = {r["element_id"]: r["slug"] for r in work_styles}
    roles, task_rows = build_roles_and_tasks(styles, tasks, nice, style_slugs)

    ipip_note = ipip["source"]["public_domain_statement"]
    nice_src = nice["source"]
    files = {
        "trait": (traits, json.dumps({
            "source": "International Personality Item Pool, IPIP-NEO (Goldberg 1999; IPIP-NEO-120, Johnson 2014)",
            "license": ipip_note["text"], "license_url": ipip_note["url"],
            "retrieved": ipip["source"]["retrieved"], "raw": provenance(ipip["source"]),
            "generated_by": "tools/persona/import_sources.py"}, ensure_ascii=False)),
        "work_style": (work_styles, json.dumps({
            "source": "O*NET 31.0 Database, Work Styles; higher-order: Putka, Liu & Lewis (2025)",
            "attribution": ONET_ATTRIBUTION, "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "retrieved": styles["source"]["retrieved"],
            "raw": provenance(styles["source"], crosswalk["source"]),
            "editorial": "lex_matches are project editorial (tools/persona/import_sources.py TEMPERAMENT_MATCHES)",
            "generated_by": "tools/persona/import_sources.py"}, ensure_ascii=False)),
        "work_role": (roles, json.dumps({
            "source": f"O*NET 31.0 Database (occupations, task ratings, work styles); {nice_src['name']} v{nice_src['version']}",
            "attribution": ONET_ATTRIBUTION, "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "nice": "NIST work, 17 U.S.C. 105: https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-srd-data-software-and-technical-series-publications",
            "retrieved": styles["source"]["retrieved"],
            "raw": provenance(tasks["source"], nice_src),
            "generated_by": "tools/persona/import_sources.py"}, ensure_ascii=False)),
        "work_task": (task_rows, json.dumps({
            "source": "O*NET 31.0 task statements; NICE Framework v2.2.0 tasks linked to a work role",
            "attribution": ONET_ATTRIBUTION,
            "generated_by": "tools/persona/import_sources.py"}, ensure_ascii=False)),
    }
    for name, (rows, comment) in files.items():
        print(f"{name:<11} {len(rows):>5} rows -> {args.out / (name + '.json')}")
        if not args.dry_run:
            (args.out / f"{name}.json").write_text(document(name, comment, rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
