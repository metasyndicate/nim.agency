# Persona source pipeline

Rebuilds the persona reference tables from the publishers' own files.
Plan of record: `.nim/work/plan.101426.persona.md`.

```
publisher downloads ──parsers/──> normalized JSON ──import_sources.py──> data/agency/data/reference/
  (raw/, not committed)             (ipip_neo.json, ...)                   trait, work_style,
                                                                           work_role, work_task
```

## Sources (retrieved 2026-10-15)

| Source | License | Used for |
| --- | --- | --- |
| IPIP-NEO 300 / IPIP-NEO-120 keys, https://ipip.ori.org/ | Public domain | `trait` (measurement only, never prompt text) |
| O*NET 31.0 Database (CSV), https://www.onetcenter.org/database.html | CC BY 4.0 | `work_style`, `work_role` (occupations), `work_task` |
| O*NET reports Higher_Order_Styles.pdf, Work_Styles_New.pdf | CC BY 4.0 | `work_style.higher_order` |
| NICE Framework components v2.2.0 JSON, https://csrc.nist.gov/csrc/media/Projects/cprt/documents/nice/v2-2-0_nf_components.json | NIST work (17 U.S.C. 105) | `work_role` (NICE), `work_task` |

Every raw file's URL and sha256 is recorded in the generated tables'
`$comment.raw`.

O*NET attribution (required, reproduced in each generated table):

> This page includes information from the O*NET 31.0 Database by the U.S.
> Department of Labor, Employment and Training Administration (USDOL/ETA).
> Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA.
> NIM Agency has modified all or some of this information. USDOL/ETA has
> not approved, endorsed, or tested these modifications.

## Run

1. Download the raw files listed in each parser's header into
   `tools/persona/raw/`. Then convert the two O*NET report PDFs to text
   (`pdftotext`) into `tools/onet_pdf_text/`, which
   `parsers/build_onet_extras.py` verifies the transcribed crosswalk
   against.
2. Run `python3 tools/persona/parsers/build_ipip.py` (and `build_onet.py`,
   `build_onet_extras.py`, `build_nice.py`). They are stdlib-only and
   offline, and write normalized JSON into `tools/persona/`.
3. Run `python3 tools/persona/import_sources.py tools/persona [--dry-run]`.
4. Run `bin/nim.agency data check trait work_style work_role work_task`.

The parsers were written during source acquisition (2026-10-15). Each one
cross-checks its output against a second publisher page or file. The one
editorial input is `TEMPERAMENT_MATCHES` in `import_sources.py`, which maps
generated lex words onto work styles for migration. It is marked as
editorial and is open to review.
