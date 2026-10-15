"""
Drafts - compose, edit, and delete records through the form model.

A Draft holds one record under composition. Edits are addressed by JSON
Pointer and checked against the entity's form before they land:

    draft = Draft.new(ds, sm, "substation")        # or Draft.load(..., ref)
    draft.set("/identity/name", "bonsai")
    draft.add("/channels/0/capabilities", "tool_use")
    draft.problems()                               # [] when savable
    draft.save()

Interface-agnostic: the CLI (`nim.agency data new|edit`) and the TUI
form drive the same Draft, so a rule enforced in one is enforced in all.

Checks that need no third-party code run here on every edit and save:
readOnly fields (JSON Schema annotation: owned by the system), required
fields, primitive types, enum membership, declared references (x-nim-ref,
including `within` bounds). Full JSON Schema validation runs in the
datastore on write when jsonschema is installed (lib/data/crud.py).
"""

import copy
import json
import re
from typing import Any, Optional

from . import forms, refs


class EditError(ValueError):
    """An edit the form refuses (unknown field, read-only, bad value)."""


_TYPES = {"integer": int, "number": (int, float), "boolean": bool,
          "string": str, "array": list, "object": dict}


def find_record(ds, entity: str, ref: str) -> Optional[dict]:
    """A record by id, slug, or the entity's lookup fields (ENTITY_CONFIG
    `lookup`, dotted paths). Raises on an ambiguous match."""
    config = ds.ENTITY_CONFIG.get(entity)
    if config is None:
        raise ValueError(f"unknown entity {entity!r}")
    paths = [("id",), ("slug",)] + [tuple(p.split("."))
                                     for p in config.get("lookup", [])]
    for path in paths:
        matches = []
        for row in refs.records(ds, entity):
            value = row
            for part in path:
                value = value.get(part) if isinstance(value, dict) else None
            if value is not None and str(value) == str(ref):
                matches.append(row)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"{entity} {ref!r} is ambiguous "
                             f"({len(matches)} records match {'.'.join(path)})")
    return None


def inbound(ds, sm, entity: str, record: dict) -> list[refs.Finding]:
    """Records whose declared references point at this one (by whichever
    key each reference uses). Used to refuse deletes, as ON DELETE
    RESTRICT does in SQL."""
    found = []
    for holder in [e for e, c in ds.ENTITY_CONFIG.items()
                   if c.get("type") in ("reference", "collection")]:
        for decl in refs.declarations(refs.schema_name(ds, holder), sm):
            if decl.entity != entity or record.get(decl.key) is None:
                continue
            target = record[decl.key]
            for row in refs.records(ds, holder):
                if holder == entity and row.get("id") == record.get("id"):
                    continue
                for pointer, value in refs.values_at(row, decl.path):
                    if value == target:
                        found.append(refs.Finding(
                            holder, refs._record_label(row), pointer, value,
                            f"{entity}.{decl.key}"))
    return found


def _conditional(schema: dict, value: dict, pointer: str) -> list[str]:
    """JSON Schema 2020-12 if/then/else, for the form JSON Schema uses for
    conditional requirements: `if` tests property consts, `then`/`else`
    require properties. A const on an absent property passes (as `if`
    does), so absent means the default case."""
    test = schema.get("if")
    if not isinstance(test, dict):
        return []
    holds = all(name not in value or value[name] == rule.get("const")
                for name, rule in (test.get("properties") or {}).items()
                if "const" in rule)
    branch = schema.get("then" if holds else "else") or {}
    return [f"{pointer}/{name}: required" for name in branch.get("required", [])
            if name not in value]


def _bounds(schema: dict, value: Any, pointer: str) -> list[str]:
    """JSON Schema validation keywords for strings and numbers
    (minLength, maxLength, pattern, minimum, maximum)."""
    out = []
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{pointer}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{pointer}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{pointer}: does not match {schema['pattern']}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{pointer}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{pointer}: above maximum {schema['maximum']}")
    return out


def describe_holder(finding: refs.Finding) -> str:
    """One inbound reference, as 'entity[record]/pointer'."""
    return f"{finding.entity}[{finding.record}]{finding.pointer}"


def _leaves(doc: Any, at: str = "") -> dict:
    if isinstance(doc, dict) and doc:
        out = {}
        for key, value in doc.items():
            out.update(_leaves(value, f"{at}/{key}"))
        return out
    if isinstance(doc, list) and doc and any(isinstance(v, (dict, list))
                                             for v in doc):
        out = {}
        for i, value in enumerate(doc):
            out.update(_leaves(value, f"{at}/{i}"))
        return out
    return {at or "/": doc}


class Draft:

    def __init__(self, ds, sm, entity: str, record: dict,
                 original: Optional[dict] = None):
        self.ds, self.sm, self.entity = ds, sm, entity
        self.config = ds.ENTITY_CONFIG[entity]
        self.root = forms.form(entity, ds, sm)
        self.record = record
        self.original = original

    # -- construction ---------------------------------------------------------

    @classmethod
    def new(cls, ds, sm, entity: str, start: Optional[dict] = None) -> "Draft":
        if entity not in ds.ENTITY_CONFIG:
            raise ValueError(f"unknown entity {entity!r}")
        draft = cls(ds, sm, entity, {})
        record = forms.skeleton(draft.root)
        for f in draft.root.children:
            if f.read_only:
                record.pop(f.name, None)
        if start:
            record.update(copy.deepcopy(start))
        draft.record = record
        return draft

    @classmethod
    def load(cls, ds, sm, entity: str, ref: str) -> "Draft":
        found = find_record(ds, entity, ref)
        if found is None:
            raise ValueError(f"no {entity} matches {ref!r}")
        return cls(ds, sm, entity, copy.deepcopy(found), copy.deepcopy(found))

    @property
    def is_new(self) -> bool:
        return self.original is None

    # -- edits ----------------------------------------------------------------

    def field(self, pointer: str) -> forms.Field:
        tokens = tuple(forms.parse_pointer(pointer))
        f = self.root.find(tokens)
        if f is None:
            raise EditError(f"{self.entity} has no field at {pointer} "
                            f"(see: nim.agency data form {self.entity})")
        for depth in range(1, len(tokens) + 1):
            ancestor = self.root.find(tokens[:depth])
            if ancestor is not None and ancestor.read_only:
                raise EditError(f"{ancestor.pointer} is read-only: the system "
                                f"maintains it")
        if (not self.is_new and self.config["type"] == "reference"
                and tokens in (("id",), ("slug",))):
            raise EditError(f"{pointer} is immutable once a reference row "
                            f"exists: records point at it")
        return f

    def _guard(self, f: forms.Field, value: Any, pointer: str) -> None:
        problems = forms.check_choice(f, value, self.ds, self.record, self.root)
        if problems:
            raise EditError(problems[0].replace(f.pointer, pointer, 1))

    def _ref_value(self, f: forms.Field, value: Any) -> Any:
        """A reference given by any lookup value (id, slug, codename...)
        is stored as the key the declaration names."""
        if not f.ref or value is None or isinstance(value, list):
            return value
        key = f.ref.get("key", "id")
        if any(row.get(key) == value
               for row in refs.records(self.ds, f.ref["entity"])):
            return value
        try:
            found = find_record(self.ds, f.ref["entity"], str(value))
        except ValueError:
            return value
        return found.get(key, value) if found else value

    def _apply(self, pointer: str, change) -> None:
        """Run one pointer mutation; on a bad address, refuse and leave the
        record exactly as it was."""
        before = copy.deepcopy(self.record)
        try:
            change()
        except (IndexError, KeyError, TypeError) as exc:
            self.record = before
            raise EditError(f"{pointer}: no such location in this record "
                            f"({type(exc).__name__}: {exc})")

    def set(self, pointer: str, raw: Any) -> None:
        f = self.field(pointer)
        value = forms.coerce(f, raw) if isinstance(raw, str) else raw
        value = self._ref_value(f, value)
        self._guard(f, value, pointer)
        self._apply(pointer, lambda: forms.pointer_set(self.record, pointer, value))

    def add(self, pointer: str, raw: Any) -> None:
        f = self.field(pointer)
        if f.kind not in ("multiselect", "tags", "list"):
            raise EditError(f"{pointer} is not a list ({f.kind})")
        item = forms.coerce_item(f, raw) if isinstance(raw, str) else raw
        item = self._ref_value(f, item)
        if f.kind == "multiselect":
            self._guard(f, [item], pointer)

        def append():
            current = forms.pointer_get(self.record, pointer, None)
            if current is None:
                current = []
                forms.pointer_set(self.record, pointer, current)
            if item not in current:
                current.append(item)
        self._apply(pointer, append)

    def remove(self, pointer: str, raw: Any) -> None:
        f = self.field(pointer)
        current = forms.pointer_get(self.record, pointer, None)
        if not isinstance(current, list):
            raise EditError(f"{pointer} holds no list")
        item = forms.coerce_item(f, raw) if isinstance(raw, str) else raw
        if item not in current:
            raise EditError(f"{pointer} does not contain {item!r}")
        current.remove(item)

    def append_item(self, pointer: str) -> str:
        """Append a new element to a list-of-objects field, seeded with its
        required fields' defaults. Returns the new element's pointer."""
        f = self.field(pointer)
        if f.kind != "list":
            raise EditError(f"{pointer} is not a list of records ({f.kind})")
        item = {c.name: forms.skeleton(c) for c in f.children
                if c.required or c.default is not None}
        current = forms.pointer_get(self.record, pointer, None)
        if current is None:
            current = []
            forms.pointer_set(self.record, pointer, current)
        current.append(item)
        return f"{pointer}/{len(current) - 1}"

    def remove_at(self, pointer: str) -> None:
        """Remove one element of a list field by its index pointer."""
        tokens = forms.parse_pointer(pointer)
        if not tokens or not tokens[-1].isdigit():
            raise EditError(f"{pointer} is not a list element")
        parent = "/" + "/".join(t.replace("~", "~0").replace("/", "~1")
                                for t in tokens[:-1])
        self.field(parent)          # refuses read-only and unknown fields
        if not forms.pointer_unset(self.record, pointer):
            raise EditError(f"{pointer} does not exist")

    def unset(self, pointer: str) -> None:
        f = self.field(pointer)
        if f.required and len(forms.parse_pointer(pointer)) == len(f.path):
            raise EditError(f"{pointer} is required")
        if not forms.pointer_unset(self.record, pointer):
            raise EditError(f"{pointer} is not set")

    # -- checks ---------------------------------------------------------------

    def problems(self) -> list[str]:
        """Everything that would stop a save, without jsonschema."""
        if (self.is_new and self.config["type"] == "reference"
                and not self.record.get("slug") and self.record.get("label")):
            from .crud import slugify
            self.record["slug"] = slugify(self.record["label"])
        out = _conditional(self.root.schema, self.record, "")
        for f in self.root.walk():
            if f is self.root or f.read_only:
                continue
            parent_path = f.path[:-1]
            parents = ([("", self.record)] if not parent_path else
                       list(refs.values_at(self.record, parent_path)))
            for at, parent in parents:
                if not isinstance(parent, dict):
                    continue
                pointer = f"{at}/{f.name}"
                if f.kind == "group" and isinstance(parent.get(f.name), dict):
                    out += _conditional(f.schema, parent[f.name], pointer)
                if f.name not in parent:
                    if f.required and not self._parent_read_only(f):
                        out.append(f"{pointer}: required")
                    continue
                value = parent[f.name]
                if value is None:
                    if not f.nullable and f.required:
                        out.append(f"{pointer}: may not be null")
                    continue
                expected = f.schema.get("type")
                if isinstance(expected, str) and expected in _TYPES:
                    ok = isinstance(value, _TYPES[expected]) and not (
                        expected in ("integer", "number")
                        and isinstance(value, bool))
                    if not ok:
                        out.append(f"{pointer}: expected {expected}, got "
                                   f"{type(value).__name__}")
                        continue
                out += _bounds(f.schema, value, pointer)
                if f.choice and f.ref and refs.is_operator_local(
                        self.ds, f.ref["entity"]):
                    continue    # may resolve in another operator's inventory
                if f.choice:
                    out += [p.replace(f.pointer, pointer, 1) for p in
                            forms.check_choice(f, value, self.ds, self.record,
                                               self.root)]
        return out

    def _parent_read_only(self, f: forms.Field) -> bool:
        return any(a is not None and a.read_only
                   for a in (self.root.find(f.path[:d])
                             for d in range(1, len(f.path))))

    def diff(self) -> list[tuple[str, Any, Any]]:
        before = _leaves(self.original) if self.original else {}
        after = _leaves(self.record)
        changed = []
        for pointer in sorted(set(before) | set(after)):
            if pointer.startswith("/audit"):
                continue
            old, new = before.get(pointer), after.get(pointer)
            if old != new:
                changed.append((pointer, old, new))
        return changed

    # -- persistence ----------------------------------------------------------

    def save(self) -> dict:
        problems = self.problems()
        if problems:
            raise EditError("; ".join(problems))
        kind = self.config["type"]
        if kind == "reference":
            if self.is_new:
                return self.ds.add_reference_entry(self.entity, self.record)
            return self.ds.replace_reference_entry(
                self.entity, self.original["id"], self.record)
        if kind == "collection":
            if self.is_new:
                return self.ds.create(self.entity, self.record)
            # System-maintained fields (status, deployments) may have changed
            # since the draft was loaded - a probe ran meanwhile - so they are
            # taken from the store, never from the draft's copy
            id_field = self.config.get("id_field", "id")
            fresh = self.ds.read(self.entity, self.original[id_field]) or {}
            for f in self.root.children:
                if f.read_only and f.name != id_field:
                    if f.name in fresh:
                        self.record[f.name] = fresh[f.name]
                    else:
                        self.record.pop(f.name, None)
            return self.ds.replace(self.entity,
                                   self.original[self.config.get("id_field", "id")],
                                   self.record)
        raise EditError(f"{self.entity} is a {kind}; it is not composed")

    def delete(self, hard: bool = False) -> None:
        if self.is_new:
            raise EditError("nothing to delete: the draft was never saved")
        holders = inbound(self.ds, self.sm, self.entity, self.original)
        if holders:
            listed = "; ".join(describe_holder(h) for h in holders[:6])
            more = f" (+{len(holders) - 6} more)" if len(holders) > 6 else ""
            raise EditError(f"{len(holders)} reference(s) point here, so it "
                            f"cannot be deleted: {listed}{more}")
        if self.config["type"] == "reference":
            self.ds.delete_reference_entry(self.entity, self.original["id"])
        else:
            self.ds.delete(self.entity,
                           self.original[self.config.get("id_field", "id")],
                           hard=hard)

    def render(self) -> str:
        return json.dumps(self.record, indent=2, ensure_ascii=False)
