"""Build onet_work_styles.json from raw O*NET 31.0 CSV files (stdlib only)."""
import csv
import hashlib
import html
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "raw")
CSV_BASE = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/"

FILES = {
    "onet_work_styles.csv": CSV_BASE + "work_styles.csv",
    "onet_content_model_reference.csv": CSV_BASE + "content_model_reference.csv",
    "onet_scales_reference.csv": CSV_BASE + "scales_reference.csv",
    "onet_occupation_data.csv": CSV_BASE + "occupation_data.csv",
    "onet_work_styles_to_work_activities.csv": CSV_BASE + "work_styles_to_work_activities.csv",
    "onet_work_styles_to_work_context.csv": CSV_BASE + "work_styles_to_work_context.csv",
    "onet_job_titles.csv": CSV_BASE + "job_titles.csv",
    "onet_license_db.html": "https://www.onetcenter.org/license_db.html",
    "onet_dictionary_work_styles.html": "https://www.onetcenter.org/dictionary/31.0/excel/work_styles.html",
    "onet_db_page.html": "https://www.onetcenter.org/database.html",
}

CANDIDATES = [
    ("15-1244.00", "Network and Computer Systems Administrators"),
    ("15-1241.00", "Computer Network Architects"),
    ("15-1212.00", "Information Security Analysts"),
    ("15-1242.00", "Database Administrators"),
    ("15-1252.00", "Software Developers"),
    ("15-1253.00", "Software Quality Assurance Analysts and Testers"),
    ("15-1299.08", "Computer Systems Engineers/Architects"),
    ("15-1243.00", "Database Architects"),
    ("15-1211.00", "Computer Systems Analysts"),
    ("15-1299.09", "Information Technology Project Managers"),
    ("15-2051.00", "Data Scientists"),
]
KEYWORDS = ["Site Reliability", "DevOps", "Cloud", "Platform", "Data Engineer"]


def rows(name):
    with open(os.path.join(RAW, name), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sha(name):
    with open(os.path.join(RAW, name), "rb") as fh:
        b = fh.read()
    return hashlib.sha256(b).hexdigest(), len(b)


def page_text(name):
    with open(os.path.join(RAW, name), encoding="utf-8", errors="replace") as fh:
        t = fh.read()
    t = re.sub(r"(?s)<(script|style|head)[^>]*>.*?</\1>", " ", t)
    s = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", s).strip()


def main():
    cm = rows("onet_content_model_reference.csv")
    ws_cm = [r for r in cm if r["Element ID"] == "1.D" or r["Element ID"].startswith("1.D.")]
    groups = {r["Element ID"]: r for r in ws_cm if r["Element ID"].count(".") == 2}
    elements = []
    for r in ws_cm:
        eid = r["Element ID"]
        depth = eid.count(".")
        if depth == 3:
            gid = eid.rsplit(".", 1)[0]
            elements.append({
                "id": eid,
                "name": r["Element Name"],
                "description": r["Description"],
                "group_id": gid,
                "group": groups[gid]["Element Name"] if gid in groups else None,
            })
    group_list = [{"id": g["Element ID"], "name": g["Element Name"], "description": g["Description"]} for g in groups.values()]
    root = next((r for r in ws_cm if r["Element ID"] == "1.D"), None)

    scales_ref = {r["Scale ID"]: r for r in rows("onet_scales_reference.csv")}
    ws = rows("onet_work_styles.csv")
    used_scales = sorted({r["Scale ID"] for r in ws})
    rated_ids = sorted({r["Element ID"] for r in ws})
    cm_ids = sorted(e["id"] for e in elements)
    scale = {}
    for sid in used_scales:
        vals = [float(r["Data Value"]) for r in ws if r["Scale ID"] == sid]
        scale[sid] = {
            "name": scales_ref[sid]["Scale Name"],
            "minimum": scales_ref[sid]["Minimum"],
            "maximum": scales_ref[sid]["Maximum"],
            "observed_min": min(vals),
            "observed_max": max(vals),
            "rows": len(vals),
        }
    dict_text = page_text("onet_dictionary_work_styles.html")
    m = re.search(r"Work styles are personality tendencies exhibited at work.*?A DR rating of 0\.00 indicates the Work Style is not part of the ranked list\.", dict_text)
    scale_doc = m.group(0) if m else None

    occ = {r["O*NET-SOC Code"]: r for r in rows("onet_occupation_data.csv")}
    by_soc = {}
    for r in ws:
        d = by_soc.setdefault(r["O*NET-SOC Code"], {"title": r["Title"], "styles": {}, "dates": set(), "domain_sources": set()})
        d["styles"].setdefault(r["Element ID"], {})[r["Scale ID"]] = float(r["Data Value"])
        d["dates"].add(r["Date"])
        d["domain_sources"].add(r["Domain Source"])

    ratings = {}
    candidate_report = []
    for soc, expected in CANDIDATES:
        o = occ.get(soc)
        rep = {"soc": soc, "expected_title": expected, "in_occupation_data": o is not None,
               "title_31_0": o["Title"] if o else None,
               "title_matches": (o is not None and o["Title"] == expected),
               "has_work_styles": soc in by_soc}
        candidate_report.append(rep)
        if soc in by_soc:
            d = by_soc[soc]
            ratings[soc] = {
                "title": d["title"],
                "date": sorted(d["dates"]),
                "domain_source": sorted(d["domain_sources"]),
                "styles": {k: d["styles"][k] for k in sorted(d["styles"])},
            }

    keyword_matches = {}
    for kw in KEYWORDS:
        pat = re.compile(re.escape(kw), re.I)
        keyword_matches[kw] = {
            "title_matches": [{"soc": c, "title": r["Title"], "has_work_styles": c in by_soc} for c, r in sorted(occ.items()) if pat.search(r["Title"])],
            "description_only_matches": [{"soc": c, "title": r["Title"]} for c, r in sorted(occ.items()) if not pat.search(r["Title"]) and pat.search(r["Description"])],
        }

    jt = rows("onet_job_titles.csv")
    for kw in KEYWORDS:
        pat = re.compile(re.escape(kw), re.I)
        hits = {}
        for r in jt:
            if pat.search(r["Job Title"]) or pat.search(r["Short Title"] or ""):
                h = hits.setdefault(r["O*NET-SOC Code"], {"soc": r["O*NET-SOC Code"], "occupation_title": r["Title"], "has_work_styles": r["O*NET-SOC Code"] in by_soc, "job_titles": []})
                h["job_titles"].append(r["Job Title"])
        keyword_matches[kw]["job_titles_file_matches"] = sorted(hits.values(), key=lambda x: (-len(x["job_titles"]), x["soc"]))

    lic = page_text("onet_license_db.html")
    v1 = re.search(r"If you use O\*NET information verbatim: (This page includes information from the O\*NET 31\.0 Database.*?O\*NET® is a trademark of USDOL/ETA\.)", lic)
    v2 = re.search(r"If you make edits or additions to O\*NET information: (This page includes information from the O\*NET 31\.0 Database.*?USDOL/ETA has not approved, endorsed, or tested these modifications\.)", lic)
    lic_head = re.search(r"Except as noted below, the content of the O\*NET 31\.0 Database is licensed under a Creative Commons Attribution 4\.0 International License", lic)

    raws = {}
    for fn, url in FILES.items():
        h, n = sha(fn)
        raws[fn] = {"url": url, "sha256": h, "bytes": n}

    out = {
        "source": {
            "name": "O*NET 31.0 Database - Work Styles",
            "publisher": "National Center for O*NET Development, sponsored by U.S. Department of Labor, Employment and Training Administration (USDOL/ETA)",
            "database_page": "https://www.onetcenter.org/database.html",
            "format_used": "CSV (db_31_0_csv individual files; no tab-delimited 'text' format is offered for 31.0 on database.html)",
            "retrieved": "2026-10-15",
            "raw_files": raws,
            "license": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "license_page": "https://www.onetcenter.org/license_db.html",
            "license_statement": lic_head.group(0) if lic_head else None,
            "attribution_verbatim": v1.group(1) if v1 else None,
            "attribution_modified": v2.group(1) if v2 else None,
            "modification_note": "This JSON restructures (reshapes, filters to selected occupations, renames keys of) the O*NET 31.0 Work Styles, Content Model Reference, Scales Reference and Occupation Data files; values are unchanged.",
            "counts": {
                "work_styles_rows": len(ws),
                "occupations_rated": len(by_soc),
                "elements_rated": len(rated_ids),
                "element_groups": len(group_list),
                "candidate_socs_with_ratings": len(ratings),
            },
            "rated_ids_equal_content_model_leaves": rated_ids == cm_ids,
        },
        "root": {"id": root["Element ID"], "name": root["Element Name"], "description": root["Description"]} if root else None,
        "groups": group_list,
        "elements": elements,
        "scale": {"scales": scale, "data_dictionary_text": scale_doc, "data_dictionary_url": "https://www.onetcenter.org/dictionary/31.0/excel/work_styles.html"},
        "ratings_value_shape": "styles[element_id] = {scale_id: Data Value} for each scale present (WI, DR)",
        "ratings": ratings,
        "candidate_check": candidate_report,
        "keyword_search": keyword_matches,
    }
    with open(os.path.join(BASE, "onet_work_styles.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(json.dumps(out["source"]["counts"]), out["source"]["rated_ids_equal_content_model_leaves"])
    print(json.dumps(candidate_report, indent=1))
    print(json.dumps(keyword_matches, indent=1))
    print(out["source"]["attribution_verbatim"])
    print(out["source"]["attribution_modified"])
    print(scale_doc)


if __name__ == "__main__":
    main()
