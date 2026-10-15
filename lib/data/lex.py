"""
Lex - the NIM flavor vocabulary (lex/*.csv).

The tables are described by lex/csv-metadata.json, a W3C CSV on the Web
(CSVW) table group: each file's columns, their header titles, which are
required, list separators, and the unit of a row (dc:type). Files are
parsed as RFC 4180 and read by column name, so a reader never depends on a
column position or on a header row being present.

This is the cross-platform port of lex/taxonomy.sh (retired 2026-10-15):

    groups()            the table names (taxonomy.sh `groups` also listed itself)
    phrase(groups)      one value from each named table   (taxonomy.sh `random`)
    salad(n)            one value from each of n distinct word tables
    name()              given name and surname, with an honorific or post-nominal

Randomness comes from a caller's random.Random (seedable), never `$RANDOM %
n`, so choices are uniform and every index is in range.
"""

import csv
import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

LEX = Path(__file__).resolve().parents[2] / "lex"
METADATA = "csv-metadata.json"
DATATYPES = {"string": str, "integer": int}
KINDS = ("word", "phrase", "sentence", "template", "record")


class LexError(ValueError):
    pass


@dataclass
class Column:
    name: str
    title: str
    datatype: str = "string"
    required: bool = False
    separator: Optional[str] = None


@dataclass
class Table:
    name: str                     # file stem: the group name
    path: Path
    title: str
    description: str
    kind: str
    columns: list[Column] = field(default_factory=list)

    def raw(self) -> list[list[str]]:
        with open(self.path, encoding="utf-8", newline="") as f:
            return list(csv.reader(f))

    def rows(self) -> list[dict]:
        """Rows keyed by column name: values trimmed, blank rows skipped,
        list columns split on their separator, typed per datatype."""
        out = []
        for row in self.raw()[1:]:
            if not any(c.strip() for c in row):
                continue
            record = {}
            for col, cell in zip(self.columns, row):
                cell = cell.strip()
                if col.separator is not None:
                    record[col.name] = [v.strip() for v in cell.split(col.separator)
                                        if v.strip()]
                elif cell == "":
                    record[col.name] = None
                else:
                    record[col.name] = DATATYPES[col.datatype](cell)
            out.append(record)
        return out

    def values(self, column: Optional[str] = None) -> list[str]:
        """One column's non-empty values (the first column by default)."""
        name = column or self.columns[0].name
        if name not in {c.name for c in self.columns}:
            raise LexError(f"{self.name}: no column '{name}' "
                           f"(columns: {', '.join(c.name for c in self.columns)})")
        return [r[name] for r in self.rows() if r.get(name) not in (None, "", [])]

    def problems(self) -> list[str]:
        """Conformance of the file to its description (CSVW-style checks)."""
        if not self.path.exists():
            return [f"{self.path.name}: described but missing"]
        rows = self.raw()
        found = []
        if not rows:
            return [f"{self.path.name}: empty"]
        titles = [c.title for c in self.columns]
        if [h.strip() for h in rows[0]] != titles:
            found.append(f"{self.path.name}: header {rows[0]} != titles {titles}")
        seen = {}
        for n, row in enumerate(rows[1:], start=2):
            where = f"{self.path.name}:{n}"
            if not any(c.strip() for c in row):
                found.append(f"{where}: blank row")
                continue
            if len(row) != len(self.columns):
                found.append(f"{where}: {len(row)} fields, {len(self.columns)} columns")
                continue
            for col, cell in zip(self.columns, row):
                if cell != cell.strip():
                    found.append(f"{where}: {col.name} has surrounding whitespace")
                if col.required and not cell.strip():
                    found.append(f"{where}: {col.name} is required")
                if cell.strip() and col.datatype == "integer" and col.separator is None:
                    try:
                        int(cell)
                    except ValueError:
                        found.append(f"{where}: {col.name} '{cell}' is not an integer")
            key = tuple(c.strip() for c in row)
            if key in seen:
                found.append(f"{where}: duplicate of line {seen[key]}")
            else:
                seen[key] = n
        return found


class Lexicon:
    """The described tables of one lex directory."""

    def __init__(self, folder: Path = LEX):
        self.folder = Path(folder)
        meta_path = self.folder / METADATA
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise LexError(f"{meta_path} not found") from None
        self.title = meta.get("dc:title", "")
        self.tables: dict[str, Table] = {}
        for t in meta.get("tables", []):
            path = self.folder / t["url"]
            columns = [Column(name=c["name"], title=c["titles"],
                              datatype=c.get("datatype", "string"),
                              required=c.get("required", False),
                              separator=c.get("separator"))
                       for c in t["tableSchema"]["columns"]]
            kind = t.get("dc:type", "word")
            if kind not in KINDS:
                raise LexError(f"{t['url']}: dc:type '{kind}' not one of {KINDS}")
            self.tables[path.stem] = Table(
                name=path.stem, path=path, title=t.get("dc:title", path.stem),
                description=t.get("dc:description", ""), kind=kind,
                columns=columns)

    def groups(self, kind: Optional[str] = None) -> list[str]:
        return sorted(n for n, t in self.tables.items()
                      if kind is None or t.kind == kind)

    def table(self, name: str) -> Table:
        name = name[:-4] if name.endswith(".csv") else name
        if name not in self.tables:
            raise LexError(f"no lex group '{name}' (see: nim.agency lex groups)")
        return self.tables[name]

    def check(self) -> list[str]:
        """Every described file conforms; every CSV in the folder is described."""
        found = []
        for table in self.tables.values():
            found += table.problems()
        described = {t.path.name for t in self.tables.values()}
        for path in sorted(self.folder.glob("*.csv")):
            if path.name not in described:
                found.append(f"{path.name}: not described in {METADATA}")
        return found

    # -- composition (taxonomy.sh) ---------------------------------------------

    def pick(self, group: str, rng: random.Random,
             column: Optional[str] = None) -> str:
        values = self.table(group).values(column)
        if not values:
            raise LexError(f"lex group '{group}' has no values")
        value = rng.choice(values)
        return " ".join(value) if isinstance(value, list) else str(value)

    def phrase(self, groups: list[str], rng: random.Random) -> str:
        """One value from each group, in order (taxonomy.sh random)."""
        return " ".join(self.pick(g, rng) for g in groups)

    def salad(self, words: int, rng: random.Random) -> str:
        """One value from each of `words` distinct word tables."""
        pool = self.groups(kind="word")
        if not 0 < words <= len(pool):
            raise LexError(f"salad takes 1..{len(pool)} words")
        return " ".join(self.pick(g, rng) for g in rng.sample(pool, words))

    def name(self, rng: random.Random, title: Optional[bool] = None) -> str:
        """Given name and surname. With an honorific (`Dr. Ada Beck`) or,
        when there is none, possibly post-nominal letters (`Ada Beck, Ph.D.`)
        - never both, as taxonomy.sh. title=True forces an honorific,
        False forbids one; None draws it (one chance in titles+1 of none)."""
        given, surname = self.pick("bioentity", rng), self.pick("surnames", rng)
        titles = self.table("titles").values()
        if title is None:
            honorific = rng.choice(titles + [""])
        else:
            honorific = rng.choice(titles) if title else ""
        if honorific:
            return f"{honorific} {given} {surname}"
        letters = rng.choice(self.table("postnominals").values() + [""])
        return f"{given} {surname}" + (f", {letters}" if letters else "")


@lru_cache(maxsize=None)
def default() -> Lexicon:
    """The repository's lexicon (read once per process)."""
    return Lexicon(LEX)
