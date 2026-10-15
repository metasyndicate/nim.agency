"""Build ipip_neo.json from raw IPIP pages (stdlib only)."""
import hashlib
import html
import json
import os
import re
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, "raw")

URLS = {
    "ipip_newNEOFacetsKey.htm": "https://ipip.ori.org/newNEOFacetsKey.htm",
    "ipip_30FacetNEO-PI-RItems.htm": "https://ipip.ori.org/30FacetNEO-PI-RItems.htm",
    "ipip_newNEO_FacetsTable.htm": "https://ipip.ori.org/newNEO_FacetsTable.htm",
    "ipip_newPermission.htm": "https://ipip.ori.org/newPermission.htm",
    "ipip_home.html": "https://ipip.ori.org/",
    "ipip_newCitation.htm": "https://ipip.ori.org/newCitation.htm",
    "ipip_newMultipleconstructs.htm": "https://ipip.ori.org/newMultipleconstructs.htm",
}


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag in ("table", "tr", "td", "th") and self.cell is not None:
            # nested table inside an open cell: emit the cell's text so far as its own row
            pending = norm("".join(self.cell))
            if pending:
                self.rows.append([pending])
            self.cell = None
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if self.row is None:
                self.row = []
            self.row.append(norm("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def norm(s):
    s = s.replace("\u0096", "–").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def read(name):
    with open(os.path.join(RAW, name), "rb") as fh:
        b = fh.read()
    return b, b.decode("cp1252", errors="replace")


HEADER = re.compile(r"^([NEOAC][1-6]):\s*(.+?)\s*\((?:Alpha\s*=\s*)?(\.\d+)\)\s*$")


def parse_key(name):
    _, text = read(name)
    p = Rows()
    p.feed(text)
    facets = {}
    order = []
    cur = None
    key = None
    for row in p.rows:
        for cell in row:
            if not cell:
                continue
            m = HEADER.match(cell)
            if m:
                cur = m.group(1)
                facets[cur] = {"code": cur, "label": m.group(2).strip(), "alpha": m.group(3), "items": []}
                order.append(cur)
                key = None
                continue
            if re.match(r"^(Return to |Johnson, J\. A\. \(2014\))", cell):
                # page footer (navigation link / reference note) ends the item lists
                cur = None
                key = None
                continue
            if re.fullmatch(r"\+\s*keyed", cell):
                key = "+"
                continue
            if re.fullmatch(r"[–\-]\s*keyed", cell):
                key = "-"
                continue
            if cur is None or key is None:
                continue
            facets[cur]["items"].append({"text": cell, "key": key})
    return order, facets


def neo_names():
    _, text = read("ipip_newNEO_FacetsTable.htm")
    p = Rows()
    p.feed(text)
    out = {}
    domains = []
    for row in p.rows:
        cells = [c for c in row if c]
        for i, c in enumerate(cells):
            m = re.fullmatch(r"(.+?)\s*\(([NEOAC][1-6])\)", c)
            if m:
                out[m.group(2)] = {"neo_name": m.group(1), "ipip_label_table": cells[i - 1] if i > 0 else None}
        if cells and cells[0] in ("Neuroticism", "Extraversion", "Openness To Experience", "Agreeableness", "Conscientiousness"):
            domains.append(cells[0])
    return out, domains


def visible_text(name):
    _, t = read(name)
    s = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", s).strip()


def main():
    order300, f300 = parse_key("ipip_newNEOFacetsKey.htm")
    order120, f120 = parse_key("ipip_30FacetNEO-PI-RItems.htm")
    names, table_domains = neo_names()
    problems = []
    if len(order300) != 30:
        problems.append("300-item key: %d facets parsed" % len(order300))
    if len(order120) != 30:
        problems.append("120-item key: %d facets parsed" % len(order120))
    total300 = 0
    total120 = 0
    matched120 = 0
    for code in order300:
        f = f300[code]
        total300 += len(f["items"])
        if len(f["items"]) != 10:
            problems.append("%s: %d items in 300-item key" % (code, len(f["items"])))
        s120 = f120.get(code)
        texts120 = {(i["text"], i["key"]) for i in s120["items"]} if s120 else set()
        if s120:
            total120 += len(s120["items"])
            if s120["label"] != f["label"]:
                problems.append("%s: label differs 300=%r 120=%r" % (code, f["label"], s120["label"]))
        for it in f["items"]:
            if s120 is None:
                it["in_120"] = None
            else:
                it["in_120"] = (it["text"], it["key"]) in texts120
                matched120 += it["in_120"]
        if s120:
            texts300 = {(i["text"], i["key"]) for i in f["items"]}
            for it in s120["items"]:
                if (it["text"], it["key"]) not in texts300:
                    problems.append("%s: 120-item %r (%s) not found verbatim among 300 items" % (code, it["text"], it["key"]))
    dom_names = {"N": "Neuroticism", "E": "Extraversion", "O": "Openness to Experience", "A": "Agreeableness", "C": "Conscientiousness"}
    domains = []
    for d in "NEOAC":
        facets = []
        for code in order300:
            if code[0] != d:
                continue
            f = f300[code]
            facets.append({
                "code": code,
                "label": f["label"],
                "label_title_case_in_comparison_table": names.get(code, {}).get("ipip_label_table"),
                "neo_pi_r_facet_name": names.get(code, {}).get("neo_name"),
                "alpha_300": f["alpha"],
                "alpha_120": f120[code]["alpha"] if code in f120 else None,
                "items": [{"text": i["text"], "key": i["key"], "in_120": i["in_120"]} for i in f["items"]],
            })
        domains.append({"code": d, "name": dom_names[d], "facets": facets})
    raws = {}
    for fn, url in URLS.items():
        b, _ = read(fn)
        raws[fn] = {"url": url, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}
    home = visible_text("ipip_home.html")
    m = re.search(r"The items and scales are in the public domain[^.]*\.", home)
    perm = visible_text("ipip_newPermission.htm")
    pm = re.search(r"Because the IPIP has been placed in the public domain.*?for any purpose, commercial or non-commercial\.\.?", perm)
    out = {
        "source": {
            "name": "International Personality Item Pool (IPIP) - IPIP-NEO 300-item facet keys (Goldberg, 1999) and IPIP-NEO-120 keys (Johnson, 2014)",
            "publisher_site": "https://ipip.ori.org/",
            "retrieved": "2026-10-15",
            "raw_files": raws,
            "license": "Public domain",
            "public_domain_statement": {"text": m.group(0) if m else None, "url": "https://ipip.ori.org/"},
            "permission_statement": {"text": pm.group(0) if pm else None, "url": "https://ipip.ori.org/newPermission.htm"},
            "citations": [
                "Goldberg, L. R. (1999). A broad-bandwidth, public domain, personality inventory measuring the lower-level facets of several five-factor models. In I. Mervielde, I. Deary, F. De Fruyt, & F. Ostendorf (Eds.), Personality Psychology in Europe, Vol. 7 (pp. 7-28). Tilburg, The Netherlands: Tilburg University Press.",
                "Johnson, J. A. (2014). Measuring thirty facets of the Five Factor Model with a 120-item public domain inventory: Development of the IPIP-NEO-120. Journal of Research in Personality, 51, 78-89.",
            ],
            "citation_url": "https://ipip.ori.org/newCitation.htm",
            "notes": [
                "Facet 'label' is the IPIP facet scale label exactly as printed (upper case) on newNEOFacetsKey.htm.",
                "Domain names follow the task specification; the comparison table (newNEO_FacetsTable.htm) prints them as: " + ", ".join(table_domains) + ".",
                "neo_pi_r_facet_name is the corresponding NEO-PI-R facet name from newNEO_FacetsTable.htm.",
                "in_120 is true when the identical item text and key appear in the same facet on 30FacetNEO-PI-RItems.htm (Johnson 2014 IPIP-NEO-120).",
                "Item key '-' denotes items printed under the en-dash 'keyed' heading (reverse keyed).",
            ],
            "counts": {"facets_300": len(order300), "items_300": total300, "facets_120": len(order120), "items_120": total120, "items_300_flagged_in_120": matched120},
            "parse_problems": problems,
        },
        "domains": domains,
    }
    with open(os.path.join(BASE, "ipip_neo.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(json.dumps(out["source"]["counts"]), problems, out["source"]["public_domain_statement"], out["source"]["permission_statement"], table_domains)


if __name__ == "__main__":
    main()
