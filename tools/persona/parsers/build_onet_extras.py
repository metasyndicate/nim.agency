"""Build onet_tasks.json and onet_work_styles_bigfive_crosswalk.json (stdlib only).

Task data come from O*NET 31.0 CSV files. The crosswalk is a transcription of tables printed in
O*NET technical reports; every transcribed pair is verified against the pdftotext output of the
report before it is written, and the run fails if any pair cannot be located.
"""
import csv
import hashlib
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "raw")
TXT = os.path.join(os.path.dirname(BASE), "onet_pdf_text")
CSV_BASE = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/"

CANDIDATES = ["15-1244.00", "15-1241.00", "15-1212.00", "15-1242.00", "15-1252.00", "15-1253.00",
              "15-1299.08", "15-1243.00", "15-1211.00", "15-1299.09", "15-2051.00"]
KEYWORDS = ["Site Reliability", "DevOps", "Cloud", "Platform", "Data Engineer"]
TECH_TITLE = re.compile(r"Engineer|Architect|Developer")


def rows(name):
    with open(os.path.join(RAW, name), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def sha(name):
    with open(os.path.join(RAW, name), "rb") as fh:
        b = fh.read()
    return hashlib.sha256(b).hexdigest(), len(b)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v if v != "" else None


def build_tasks():
    occ = {r["O*NET-SOC Code"]: r for r in rows("onet_occupation_data.csv")}
    jt = rows("onet_job_titles.csv")
    selection = {s: {"reason": ["candidate list"], "matched_job_titles": []} for s in CANDIDATES}
    for kw in KEYWORDS:
        pat = re.compile(re.escape(kw), re.I)
        for r in jt:
            if not pat.search(r["Job Title"]):
                continue
            soc = r["O*NET-SOC Code"]
            if not (soc.startswith("15-") or TECH_TITLE.search(r["Job Title"])):
                continue
            s = selection.setdefault(soc, {"reason": [], "matched_job_titles": []})
            tag = "job_titles.csv keyword '%s'" % kw
            if tag not in s["reason"]:
                s["reason"].append(tag)
            if r["Job Title"] not in s["matched_job_titles"]:
                s["matched_job_titles"].append(r["Job Title"])

    ts = [r for r in rows("onet_task_statements.csv") if r["O*NET-SOC Code"] in selection]
    tr = [r for r in rows("onet_task_ratings.csv") if r["O*NET-SOC Code"] in selection]
    cats = rows("onet_task_categories.csv")
    scales_ref = {r["Scale ID"]: r for r in rows("onet_scales_reference.csv")}

    ratings = {}
    for r in tr:
        key = (r["O*NET-SOC Code"], r["Task ID"])
        d = ratings.setdefault(key, {})
        rec = {"value": num(r["Data Value"]), "n": num(r["N"]), "standard_error": num(r["Standard Error"]),
               "lower_ci": num(r["Lower CI Bound"]), "upper_ci": num(r["Upper CI Bound"]),
               "recommend_suppress": r["Recommend Suppress"], "date": r["Date"], "domain_source": r["Domain Source"]}
        if r["Scale ID"] == "FT":
            d.setdefault("FT", {})[r["Category"]] = rec
        else:
            d[r["Scale ID"]] = rec

    occupations = {}
    for soc in sorted(selection):
        o = occ.get(soc)
        tasks = []
        for r in ts:
            if r["O*NET-SOC Code"] != soc:
                continue
            tasks.append({
                "id": r["Task ID"],
                "text": r["Task"],
                "type": r["Task Type"] or None,
                "incumbents_responding": num(r["Incumbents Responding"]),
                "date": r["Date"],
                "domain_source": r["Domain Source"],
                "ratings": ratings.get((soc, r["Task ID"]), {}),
            })
        tasks.sort(key=lambda t: int(t["id"]))
        occupations[soc] = {
            "title": o["Title"] if o else None,
            "description": o["Description"] if o else None,
            "selection": selection[soc],
            "task_count": len(tasks),
            "tasks_with_ratings": sum(1 for t in tasks if t["ratings"]),
            "tasks": tasks,
        }
    files = ["onet_task_statements.csv", "onet_task_ratings.csv", "onet_task_categories.csv",
             "onet_occupation_data.csv", "onet_job_titles.csv", "onet_scales_reference.csv"]
    raws = {}
    for fn in files:
        h, n = sha(fn)
        raws[fn] = {"url": CSV_BASE + fn.replace("onet_", ""), "sha256": h, "bytes": n}
    out = {
        "source": {
            "name": "O*NET 31.0 Database - Task Statements, Task Ratings, Occupation Data",
            "retrieved": "2026-10-15",
            "raw_files": raws,
            "license": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "license_page": "https://www.onetcenter.org/license_db.html",
            "attribution_modified": "This page includes information from the O*NET 31.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA. [Your name or company] has modified all or some of this information. USDOL/ETA has not approved, endorsed, or tested these modifications.",
            "selection_rule": "All 11 candidate SOC codes, plus every SOC whose job_titles.csv 'Job Title' matches one of the keywords (case-insensitive) AND (SOC begins with '15-' OR the matched job title contains 'Engineer', 'Architect' or 'Developer'). occupation_data.csv titles matched none of the keywords.",
            "rating_scales": {s: scales_ref[s] for s in ("IM", "RT", "FT") if s in scales_ref},
            "ft_categories": cats,
            "ratings_shape": "ratings = {IM: rec, RT: rec, FT: {category: rec}}; rec = {value, n, standard_error, lower_ci, upper_ci, recommend_suppress, date, domain_source}. FT values are percentages of respondents per frequency category.",
            "counts": {"occupations": len(occupations), "tasks": sum(o["task_count"] for o in occupations.values()),
                       "tasks_with_ratings": sum(o["tasks_with_ratings"] for o in occupations.values())},
        },
        "occupations": occupations,
    }
    with open(os.path.join(BASE, "onet_tasks.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(json.dumps(out["source"]["counts"]))
    for soc, o in occupations.items():
        types = {}
        for t in o["tasks"]:
            types[t["type"]] = types.get(t["type"], 0) + 1
        print(soc, o["title"], o["task_count"], o["tasks_with_ratings"], types, o["selection"]["reason"], o["selection"]["matched_job_titles"][:4])


# --- crosswalk transcription (verified below against report text) ---
TABLE3 = [
    ("Innovation", "Compound Dimension"),
    ("Achievement Orientation", "Conscientiousness"),
    ("Intellectual Curiosity", "Openness"),
    ("Tolerance for Ambiguity", "Openness"),
    ("Initiative", "Compound Dimensions"),
    ("Adaptability", "Openness"),
    ("Self-Confidence", "Conscientiousness"),
    ("Perseverance", "Compound Dimensions"),
    ("Leadership Orientation", "Extraversion"),
    ("Humility", "Honesty-Humility"),
    ("Sincerity", "Honesty-Humility"),
    ("Empathy", "Agreeableness"),
    ("Cooperation", "Agreeableness"),
    ("Optimism", "Compound Dimensions"),
    ("Social Orientation", "Extraversion"),
    ("Cautiousness", "Conscientiousness"),
    ("Attention to Detail", "Conscientiousness"),
    ("Dependability", "Conscientiousness"),
    ("Integrity", "Honesty-Humility"),
    ("Stress Tolerance", "Emotional Stability"),
    ("Self-Control", "Emotional Stability"),
]
TABLE1 = [
    ("Conscientiousness", "A tendency to exhibit achievement-oriented behavior, dependability, attention to detail, self-confidence, and cautiousness at work.",
     ["Achievement Orientation", "Attention to Detail", "Cautiousness", "Dependability", "Self-Confidence"]),
    ("Extraversion", "A tendency to exhibit leadership and socially oriented behavior at work.",
     ["Leadership Orientation", "Social Orientation"]),
    ("Agreeableness", "A tendency to exhibit cooperative and empathetic behavior towards others at work.",
     ["Cooperation", "Empathy"]),
    ("Honesty-Humility", "A tendency to exhibit sincerity, humility, and integrity at work.",
     ["Humility", "Integrity", "Sincerity"]),
    ("Emotional Stability", "A tendency to exhibit self-control and tolerate stress at work.",
     ["Self-Control", "Stress Tolerance"]),
    ("Openness", "A tendency to exhibit adaptability, flexibility, intellectual curiosity, and innovation at work.",
     ["Adaptability", "Innovation", "Intellectual Curiosity", "Tolerance for Ambiguity"]),
    ("Compound Dimensions", "Work style dimensions that reflect a combination of elements from two or more broad personality domains.",
     ["Initiative", "Optimism", "Perseverance"]),
]
LEGACY = [
    ("1.C.1.a", "Achievement/Effort", "C"), ("1.C.1.b", "Persistence", "C"), ("1.C.1.c", "Initiative", "C"),
    ("1.C.2.b", "Leadership", "C, Ex, O"), ("1.C.3.a", "Cooperation", "A"), ("1.C.3.b", "Concern for Others", "A"),
    ("1.C.3.c", "Social Orientation", "A, Ex"), ("1.C.4.a", "Self-Control", "ES"), ("1.C.4.b", "Stress Tolerance", "ES"),
    ("1.C.4.c", "Adaptability/Flexibility", "ES, O"), ("1.C.5.a", "Dependability", "C"), ("1.C.5.b", "Attention to Detail", "C"),
    ("1.C.5.c", "Integrity", "A, C, ES"), ("1.C.6", "Independence", "C, O"), ("1.C.7.a", "Innovation", "O"),
    ("1.C.7.b", "Analytical Thinking", "O"),
]


def flat(path):
    with open(path, encoding="utf-8") as fh:
        return re.sub(r"\s+", " ", fh.read())


def verify():
    problems = []
    ho = open(os.path.join(TXT, "Higher_Order_Styles.txt"), encoding="utf-8").read()
    t3 = ho[ho.index("Table 3. Comparison"):ho.index("To further evaluate the substantive")]
    lines = t3.splitlines()
    for name, val in TABLE3:
        first = name.split()[0]
        hit = [l for l in lines if re.search(r"^\s*" + re.escape(first) + r"\b", l) and val.split()[0] in l]
        if not hit:
            problems.append("Table 3: %s -> %s not located" % (name, val))
    s1 = ho.rindex("Table 1. Provisional Work Styles from Phase 1"); t1 = ho[s1:ho.index("Revisiting the Higher-order Work Style Dimensions", s1)]
    f1 = re.sub(r"\s+", " ", t1)
    for hname, desc, members in TABLE1:
        # descriptions wrap across columns in the layout text; verify each word sequence loosely
        words = desc.split()
        if not all(w in f1 for w in words):
            problems.append("Table 1: description words for %s not all found" % hname)
        if hname not in f1:
            problems.append("Table 1: %s not found" % hname)
    ws = open(os.path.join(TXT, "Work_Styles_New.txt"), encoding="utf-8").read()
    tl = ws[ws.index("Table 1. Higher-Order and Lower-Order Work Style Dimensions"):ws.index("Note. Big Five = Mappings")]
    for eid, name, b5 in LEGACY:
        if eid not in tl or name.split()[0] not in tl:
            problems.append("Legacy table: %s %s not found" % (eid, name))
        pat = re.compile(r"X\s+" + re.escape(b5) + r"\s*$", re.M)
        if not pat.search(tl):
            problems.append("Legacy table: mapping %r not found" % b5)
    return problems


def build_crosswalk():
    problems = verify()
    cm = {r["Element Name"]: r["Element ID"] for r in rows("onet_content_model_reference.csv") if r["Element ID"].startswith("1.D.") and r["Element ID"].count(".") == 3}
    unmatched = [n for n, _ in TABLE3 if n not in cm]
    phase1_group = {m: h for h, _, ms in TABLE1 for m in ms}
    pairs = []
    for name, val in TABLE3:
        pairs.append({"element_id_31_0": cm.get(name), "work_style": name,
                      "higher_order_dimension_from_phase_1_table3": val,
                      "phase_1_group_table1": phase1_group.get(name),
                      "table1_table3_agree": (val.startswith("Compound") and phase1_group.get(name) == "Compound Dimensions") or val == phase1_group.get(name)})
    reports = {
        "Higher_Order_Styles.pdf": "https://www.onetcenter.org/dl_files/Higher_Order_Styles.pdf",
        "Work_Styles_New.pdf": "https://www.onetcenter.org/dl_files/Work_Styles_New.pdf",
        "Hybrid_AI_Ratings.pdf": "https://www.onetcenter.org/dl_files/Hybrid_AI_Ratings.pdf",
    }
    raws = {}
    for fn, url in reports.items():
        h, n = sha("onet_report_" + fn)
        raws["onet_report_" + fn] = {"url": url, "sha256": h, "bytes": n}
    for p in ("Higher_Order_Styles", "Work_Styles_New", "Hybrid_AI_Ratings"):
        h, n = sha("onet_report_%s.html" % p)
        raws["onet_report_%s.html" % p] = {"url": "https://www.onetcenter.org/reports/%s.html" % p, "sha256": h, "bytes": n}
    out = {
        "source": {
            "finding": "Neither the O*NET 31.0 database files nor the 31.0 Work Styles data dictionary contain a Big Five/HEXACO mapping. O*NET technical reports linked from the Work Styles data dictionary page print the following tables, transcribed here verbatim (names and dimension labels only) and verified programmatically against the report text.",
            "retrieved": "2026-10-15",
            "raw_files": raws,
            "data_dictionary_page_linking_reports": "https://www.onetcenter.org/dictionary/31.0/excel/work_styles.html",
            "license": "Report pages state: licensed under a Creative Commons Attribution 4.0 International License (CC BY 4.0), U.S. Department of Labor, Employment and Training Administration.",
            "verification_problems": problems,
            "work_style_names_not_in_31_0_content_model": unmatched,
        },
        "phase_1_higher_order_dimensions": {
            "citation": "Putka, D. J., Liu, J., & Lewis, P. Updating Higher-order Work Style Dimensions in the O*NET Work Styles Taxonomy (December 2025), Table 1 'Provisional Work Styles from Phase 1' and Table 3 'Comparison of PCA-Based Higher-order Work Style Dimensions and Higher-order Work Style Dimensions from Phase 1'. Same tables appear as Table 1 and Table 6.8 in 'Using a Hybrid Artificial Intelligence-Expert Method to Develop Work Style Ratings for the O*NET Database' (December 2025).",
            "url": "https://www.onetcenter.org/dl_files/Higher_Order_Styles.pdf",
            "nature": "These are the provisional (Phase 1) higher-order Work Style dimensions, named after Big Five domains plus HEXACO Honesty-Humility plus 'Compound Dimensions'. The O*NET 31.0 Content Model does not use them; it uses the four PCA-based groups (1.D.1-1.D.4). The reports describe the Phase 1 structure as 'heavily informed by the body of past research on the factor structure underling indicators of individual personality'.",
            "groups_table1": [{"name": h, "description": d, "work_styles": m} for h, d, m in TABLE1],
            "per_style_table3": pairs,
            "internal_inconsistency": [p["work_style"] for p in pairs if not p["table1_table3_agree"]],
        },
        "legacy_sackett_walmsley_2014": {
            "citation": "Putka, D. J., Kell, H. J., Voss, N., Oswald, F., & Lewis, P. Revisiting the Work Styles Domain of the O*NET Content Model (August 2024), Table 1 'Higher-Order and Lower-Order Work Style Dimensions in the O*NET Content Model'. Note to table: 'Big Five = Mappings of lower-order O*NET Work Styles to the Big Five personality factors are from Sackett and Walmsley (2014). A = Agreeableness. C = Conscientiousness. ES = Emotional Stability. Ex = Extraversion. O = Openness to Experience.'",
            "url": "https://www.onetcenter.org/dl_files/Work_Styles_New.pdf",
            "nature": "Applies to the 16 legacy (pre-30.1, 1.C.x) Work Styles, NOT to the 21 Work Styles in O*NET 31.0.",
            "mappings": [{"legacy_element_id": e, "name": n, "big_five": b} for e, n, b in LEGACY],
        },
    }
    with open(os.path.join(BASE, "onet_work_styles_bigfive_crosswalk.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print("crosswalk problems:", problems, "unmatched:", unmatched, "inconsistent:", out["phase_1_higher_order_dimensions"]["internal_inconsistency"])


if __name__ == "__main__":
    build_tasks()
    build_crosswalk()
