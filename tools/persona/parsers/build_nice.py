"""Build nice_work_roles.json from the NICE Framework Components v2.2.0 JSON, cross-checked against the XLSX (stdlib only)."""
import hashlib
import html
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "raw")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

FILES = {
    "nice_v2-2-0_nf_components.json": "https://csrc.nist.gov/csrc/media/Projects/cprt/documents/nice/v2-2-0_nf_components.json",
    "nice_components_v220.xlsx": "https://www.nist.gov/document/nice-framework-components-v220 (redirected to https://www.nist.gov/system/files/documents/2026/06/24/NICE%20Framework%20Components%20v2.2.0.xlsx)",
    "nice_current_versions.html": "https://www.nist.gov/itl/applied-cybersecurity/nice/nice-framework-resource-center/nice-framework-current-versions",
    "nice_change_logs.html": "https://www.nist.gov/itl/applied-cybersecurity/nice/nice-framework-resource-center/current-version/change-logs",
    "nice_news_v220.html": "https://www.nist.gov/news-events/news/2026/04/nice-releases-nice-framework-components-v220",
    "nice_resource_center.html": "https://www.nist.gov/itl/applied-cybersecurity/nice/nice-framework-resource-center",
    "nice_json_faq.html": "https://www.nist.gov/itl/applied-cybersecurity/nice/nice-framework-resource-center/faq-nice-framework-data-json-format",
    "nist_copyright_fair_use_licensing.html": "https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-srd-data-software-and-technical-series-publications",
    "nist_copyrights_disclaimers.html": "https://www.nist.gov/copyrights-disclaimers (requested as https://www.nist.gov/oism/copyrights)",
}


def sha(name):
    with open(os.path.join(RAW, name), "rb") as fh:
        b = fh.read()
    return hashlib.sha256(b).hexdigest(), len(b)


def page_text(name):
    with open(os.path.join(RAW, name), encoding="utf-8", errors="replace") as fh:
        t = fh.read()
    t = re.sub(r"(?s)<(script|style|head|nav|header|footer)[^>]*>.*?</\1>", " ", t)
    s = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", s).strip()


def xlsx_sheets(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    out = {}
    for sh in wb.find("m:sheets", NS):
        rid = sh.get("{%s}id" % NS["r"])
        tgt = target[rid].lstrip("/")
        if not tgt.startswith("xl/"):
            tgt = "xl/" + tgt
        root = ET.fromstring(z.read(tgt))
        rows = []
        for row in root.iter("{%s}row" % NS["m"]):
            vals = []
            for c in row.findall("m:c", NS):
                v = c.find("m:v", NS)
                if c.get("t") == "s" and v is not None:
                    vals.append(shared[int(v.text)])
                elif c.get("t") == "inlineStr":
                    vals.append("".join(t.text or "" for t in c.iter("{%s}t" % NS["m"])))
                else:
                    vals.append(v.text if v is not None else "")
            rows.append(vals)
        out[sh.get("name")] = rows
    return out


def main():
    with open(os.path.join(RAW, "nice_v2-2-0_nf_components.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    doc = d["documents"][0]
    el = {e["element_identifier"]: e for e in d["elements"]}
    by_type = {}
    for e in d["elements"]:
        by_type.setdefault(e["element_type"], []).append(e)
    rel = d["relationships"]

    def targets(src, typ):
        return [r["dest_element_identifier"] for r in rel
                if r["source_element_identifier"] == src and el.get(r["dest_element_identifier"], {}).get("element_type") == typ]

    def id_key(x):
        m = re.match(r"([A-Z]+)(\d+)$", x)
        return (m.group(1), int(m.group(2))) if m else (x, 0)

    cat_of = {}
    categories = []
    for c in by_type["category"]:
        wrs = targets(c["element_identifier"], "work_role")
        for w in wrs:
            cat_of.setdefault(w, []).append(c["element_identifier"])
        categories.append({"id": c["element_identifier"], "name": c["title"], "description": c["text"], "work_role_ids": sorted(wrs)})
    categories.sort(key=lambda x: x["id"])

    work_roles = []
    problems = []
    for w in sorted(by_type["work_role"], key=lambda x: x["element_identifier"]):
        wid = w["element_identifier"]
        cats = cat_of.get(wid, [])
        if len(cats) != 1:
            problems.append("%s: category relationships = %r" % (wid, cats))
        sorts = targets(wid, "sort")
        if not sorts:
            problems.append("%s: no work_role->sort relationship in JSON" % wid)
        work_roles.append({
            "id": wid,
            "name": w["title"],
            "description": w["text"],
            "category": cats[0] if len(cats) == 1 else cats,
            "category_name": el[cats[0]]["title"] if len(cats) == 1 else None,
            "opm_codes": sorted(targets(wid, "opm_code")),
            "task_ids": sorted(set(targets(wid, "task")), key=id_key),
            "knowledge_ids": sorted(set(targets(wid, "knowledge")), key=id_key),
            "skill_ids": sorted(set(targets(wid, "skill")), key=id_key),
        })

    competency_areas = []
    for c in sorted(by_type["competency_area"], key=lambda x: x["element_identifier"]):
        competency_areas.append({"id": c["element_identifier"], "name": c["title"], "description": c["text"],
                                 "knowledge_ids": sorted(set(targets(c["element_identifier"], "knowledge")), key=id_key),
                                 "skill_ids": sorted(set(targets(c["element_identifier"], "skill")), key=id_key)})

    tasks = {e["element_identifier"]: e["text"] for e in sorted(by_type["task"], key=lambda x: id_key(x["element_identifier"]))}
    knowledge = {e["element_identifier"]: e["text"] for e in sorted(by_type["knowledge"], key=lambda x: id_key(x["element_identifier"]))}
    skills = {e["element_identifier"]: e["text"] for e in sorted(by_type["skill"], key=lambda x: id_key(x["element_identifier"]))}
    mapped_tasks = {t for w in work_roles for t in w["task_ids"]}
    unmapped_tasks = sorted(set(tasks) - mapped_tasks, key=id_key)

    # cross-check against the XLSX per-work-role sheets
    xcheck = {"performed": False}
    try:
        sheets = xlsx_sheets(os.path.join(RAW, "nice_components_v220.xlsx"))
        xcheck = {"performed": True, "sheet_names": list(sheets), "work_role_task_set_mismatches": {}, "work_role_sheets_missing": []}
        for w in work_roles:
            rows = sheets.get(w["id"])
            if rows is None:
                xcheck["work_role_sheets_missing"].append(w["id"])
                continue
            ids = set()
            for r in rows:
                for v in r:
                    if re.fullmatch(r"T\d{4}", (v or "").strip()):
                        ids.add(v.strip())
            js = set(w["task_ids"])
            if ids != js:
                xcheck["work_role_task_set_mismatches"][w["id"]] = {"only_in_xlsx": sorted(ids - js), "only_in_json": sorted(js - ids)}
        wr_sheet = sheets.get("v2.2.0 Work Roles + Categories", [])
        xcheck["work_roles_sheet_header_rows"] = wr_sheet[:2]
        xlsx_wr = {}
        for r in wr_sheet:
            for i, v in enumerate(r):
                if re.fullmatch(r"[A-Z]{2}-WRL-\d{3}", (v or "").strip()):
                    xlsx_wr[v.strip()] = r
        xcheck["work_role_ids_in_xlsx_summary_sheet"] = len(xlsx_wr)
        xcheck["work_role_ids_only_in_json"] = sorted(set(w["id"] for w in work_roles) - set(xlsx_wr))
        xcheck["work_role_ids_only_in_xlsx"] = sorted(set(xlsx_wr) - set(w["id"] for w in work_roles))
        tks = sheets.get("v2.2.0 TKS Statements", [])
        xt = {}
        for r in tks:
            if r and re.fullmatch(r"T\d{4}", (r[0] or "").strip()):
                xt[r[0].strip()] = r[1] if len(r) > 1 else ""
        xcheck["task_ids_in_xlsx_tks_sheet"] = len(xt)
        xcheck["task_ids_only_in_json"] = sorted(set(tasks) - set(xt), key=id_key)
        xcheck["task_ids_only_in_xlsx"] = sorted(set(xt) - set(tasks), key=id_key)
        xcheck["task_text_differences"] = {k: {"json": tasks[k], "xlsx": xt[k]} for k in sorted(set(tasks) & set(xt), key=id_key) if tasks[k].strip() != (xt[k] or "").strip()}
    except Exception as exc:  # report, do not hide
        xcheck = {"performed": False, "error": repr(exc)}

    cv = page_text("nice_current_versions.html")
    m_cv = re.search(r"CURRENT VERSION: [0-9.]+ \([^)]*\)", cv)
    lic = page_text("nist_copyright_fair_use_licensing.html")
    m_lic = re.search(r"This data/work was created by employees of the National Institute of Standards and Technology \(NIST\).*?providing appropriate acknowledgments of NIST’s creation of the data/work\.", lic)
    m_fair = re.search(r"Data/works created by NIST employees that are not covered by the Standard Reference Data Act are subject to 17 U\.S\.C\. §105 and generally are not subject to copyright protection within the United States\.", lic)
    dis = page_text("nist_copyrights_disclaimers.html")
    m_dis = re.search(r"NIST web pages are provided as a public service.*?Use of appropriate byline/photo/image credits is requested\.", dis)

    raws = {}
    for fn, url in FILES.items():
        h, n = sha(fn)
        raws[fn] = {"url": url, "sha256": h, "bytes": n}

    out = {
        "source": {
            "name": "NICE Workforce Framework for Cybersecurity (NICE Framework) Components",
            "version": doc["version"],
            "doc_identifier": doc["doc_identifier"],
            "document_name": doc["name"],
            "document_website": doc["website"],
            "publisher": "National Institute of Standards and Technology (NIST), NICE Program Office",
            "primary_download": "https://csrc.nist.gov/csrc/media/Projects/cprt/documents/nice/v2-2-0_nf_components.json",
            "retrieved": "2026-10-15",
            "raw_files": raws,
            "current_versions_page_statement": m_cv.group(0) if m_cv else None,
            "release_date_per_change_log_and_news": "28 April 2026",
            "license": "US Government work (NIST); 17 U.S.C. 105 - not subject to copyright protection within the United States; may be subject to foreign copyright (see statement).",
            "license_statement_fair_use": {"text": m_fair.group(0) if m_fair else None, "url": "https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-srd-data-software-and-technical-series-publications"},
            "license_statement_distribution": {"text": m_lic.group(0) if m_lic else None, "url": "https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-srd-data-software-and-technical-series-publications"},
            "nist_site_use_statement": {"text": m_dis.group(0) if m_dis else None, "url": "https://www.nist.gov/copyrights-disclaimers"},
            "modification_note": "Restructured from the NIST CPRT JSON (elements + projection relationships) into per-work-role lists; texts unchanged. Retrieved 2026-10-15.",
            "counts": {
                "categories": len(categories),
                "work_roles": len(work_roles),
                "tasks": len(tasks),
                "knowledge": len(knowledge),
                "skills": len(skills),
                "competency_areas": len(competency_areas),
                "work_role_task_links": sum(len(w["task_ids"]) for w in work_roles),
                "tasks_not_linked_to_any_work_role": len(unmapped_tasks),
            },
            "parse_problems": problems,
            "xlsx_cross_check": xcheck,
        },
        "categories": categories,
        "work_roles": work_roles,
        "competency_areas": competency_areas,
        "tasks": tasks,
        "knowledge": knowledge,
        "skills": skills,
        "tasks_not_linked_to_any_work_role": unmapped_tasks,
    }
    with open(os.path.join(BASE, "nice_work_roles.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    s = dict(out["source"])
    s.pop("raw_files")
    xc = dict(s.pop("xlsx_cross_check"))
    xc.pop("sheet_names", None)
    print(json.dumps(s, indent=1, ensure_ascii=False))
    print(json.dumps(xc, indent=1, ensure_ascii=False)[:4000])
    for w in work_roles:
        print(w["id"], w["category"], w["name"], len(w["task_ids"]), len(w["knowledge_ids"]), len(w["skill_ids"]), w["opm_codes"])


if __name__ == "__main__":
    main()
