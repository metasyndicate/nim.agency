"""
Forms - the composition kernel: schema in, form model out.

Every interface that composes a record (CLI today, TUI, a later GUI)
asks this module the same three questions, so none of them hardcodes a
field list:

    form(entity)             the field tree: what can be set, of what kind
    options(field, ds, rec)  the values a choice field may take now
    set/add/remove/unset     edits addressed by JSON Pointer (RFC 6901)

Field kinds are scored from constraints, after SHACL DASH's editor
selection (InstancesSelectEditor for a class-typed value, EnumSelectEditor
for `sh:in`): a value carrying `x-nim-ref` is a `select` over the target's
records, an `enum` is a `select` over its values, an array of either is a
`multiselect`, and so on. Nothing UI-specific lives in the data schemas
(plan D-REF).

Dependent options are declared, not coded: an `x-nim-ref` may carry

    "within": {"via": "/transport_slug", "field": "capability_ceiling"}

meaning "only the values listed in `field` of the record that the
reference at `via` points to" - the transport ceiling on a syndicate
channel's capabilities. `refs.check` enforces the same rule on stored data.
"""

import copy
import json
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterator, Optional

from . import refs

# Kinds, in the order a form presents them
SCALAR_KINDS = ("text", "integer", "number", "toggle", "select")
KINDS = SCALAR_KINDS + ("multiselect", "tags", "group", "list", "json")


@dataclass
class Option:
    value: Any
    label: str
    description: str = ""


@dataclass
class Field:
    """One node of a form. `path` is the pattern from the record root
    (refs.ITEM for 'each element'); `pointer` renders it."""
    path: tuple
    name: str
    kind: str
    label: str = ""
    description: str = ""
    required: bool = False
    nullable: bool = False
    read_only: bool = False
    default: Any = None
    schema: dict = dc_field(default_factory=dict)       # resolved subschema
    ref: Optional[dict] = None                          # x-nim-ref (scalar or items)
    enum: Optional[list] = None                         # [(value, title)]
    children: list["Field"] = dc_field(default_factory=list)

    @property
    def pointer(self) -> str:
        return "/" + "/".join(str(p) for p in self.path) if self.path else ""

    @property
    def choice(self) -> bool:
        return self.kind in ("select", "multiselect")

    def walk(self) -> Iterator["Field"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, path: tuple) -> Optional["Field"]:
        """The field describing a concrete path (array indexes match ITEM)."""
        pattern = tuple(refs.ITEM if isinstance(p, int) or str(p).isdigit()
                        else p for p in path)
        return next((f for f in self.walk() if f.path == pattern), None)


# -- schema normalization -------------------------------------------------------

def _resolve(node: dict, sm, base: dict, depth: int = 0) -> dict:
    """Flatten a subschema for presentation: follow $ref (the referencing
    node's own keywords win), merge allOf, and fold `X or null` unions
    into X with `nullable`."""
    return _resolve_in(node, sm, base, depth)[0]


def _resolve_in(node: dict, sm, base: dict, depth: int = 0) -> tuple[dict, dict]:
    """_resolve, also returning the document the result's own subschemas
    (properties, items) resolve in - the target document when the node
    was an external $ref."""
    if depth > 12 or not isinstance(node, dict):
        return (dict(node) if isinstance(node, dict) else {}), base
    child_base = base
    out = {}
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = refs._resolve_ref(ref, sm, base)
        if target is not None and target is not node:
            # An external $ref moves resolution into that document, so its
            # own "#/$defs/..." refs resolve there, not in the referrer
            doc, _, _ = ref.partition("#")
            next_base = (refs._resolve_ref(doc, sm, base) or base) if doc else base
            resolved, child_base = _resolve_in(target, sm, next_base, depth + 1)
            out.update(resolved)
    for part in node.get("allOf", []) or []:
        resolved = _resolve(part, sm, base, depth + 1)
        props = {**out.get("properties", {}), **resolved.get("properties", {})}
        required = list(dict.fromkeys(out.get("required", []) +
                                      resolved.get("required", [])))
        out.update(resolved)
        if props:
            out["properties"] = props
        if required:
            out["required"] = required
    for key, value in node.items():
        if key in ("$ref", "allOf"):
            continue
        if key == "properties":
            out["properties"] = {**out.get("properties", {}), **value}
        elif key == "required":
            out["required"] = list(dict.fromkeys(out.get("required", []) + value))
        else:
            out[key] = value

    for union in ("oneOf", "anyOf"):
        branches = out.get(union)
        if isinstance(branches, list):
            non_null = [b for b in branches
                        if not (isinstance(b, dict) and b.get("type") == "null")]
            if len(non_null) < len(branches) and len(non_null) == 1:
                merged = _resolve(non_null[0], sm, base, depth + 1)
                out.pop(union)
                out = {**merged, **out, "nullable": True}
            elif all(isinstance(b, dict) and "const" in b for b in branches):
                out.pop(union)
                out["enum"] = [b["const"] for b in branches]
                out["enumTitles"] = [b.get("title", str(b["const"]))
                                     for b in branches]
    types = out.get("type")
    if isinstance(types, list) and "null" in types:
        rest = [t for t in types if t != "null"]
        out["type"] = rest[0] if len(rest) == 1 else rest
        out["nullable"] = True
    return out, child_base


def _kind(node: dict) -> str:
    """Score the editor for a resolved subschema (DASH-style)."""
    if node.get(refs.KEYWORD):
        return "select"
    if "enum" in node or "const" in node:
        return "select"
    kind = node.get("type")
    if isinstance(kind, list) or any(k in node for k in ("oneOf", "anyOf")):
        return "json"
    if kind == "boolean":
        return "toggle"
    if kind in ("integer", "number"):
        return kind
    if kind == "string":
        return "text"
    if kind == "array":
        items = node.get("items") or {}
        if items.get(refs.KEYWORD) or "enum" in items:
            return "multiselect"
        if items.get("type") == "object" or "properties" in items:
            return "list"
        if items.get("type") in ("string", "integer", "number"):
            return "tags"
        return "json"
    if kind == "object" or "properties" in node:
        return "group" if node.get("properties") else "json"
    return "json"


def _humanize(name: str) -> str:
    return str(name).replace("_", " ").strip().capitalize()


def _build(name: str, node: dict, path: tuple, required: bool, sm,
           base: dict) -> Field:
    node, base = _resolve_in(node, sm, base)
    kind = _kind(node)
    items, item_base = _resolve_in(node.get("items") or {}, sm, base) if kind in (
        "multiselect", "list", "tags") else ({}, base)
    ref = node.get(refs.KEYWORD) or items.get(refs.KEYWORD)
    enum_source = node if "enum" in node else items if "enum" in items else None
    enum = None
    if enum_source is not None:
        titles = enum_source.get("enumTitles") or [str(v) for v in enum_source["enum"]]
        enum = list(zip(enum_source["enum"], titles))
    elif "const" in node:
        enum = [(node["const"], str(node["const"]))]

    f = Field(path=path, name=name, kind=kind,
              label=node.get("title") or _humanize(name),
              description=node.get("description", ""),
              required=required, nullable=bool(node.get("nullable")),
              read_only=bool(node.get("readOnly")),
              default=node.get("default"), schema=node, ref=ref, enum=enum)
    if kind == "group":
        req = set(node.get("required", []))
        f.children = [_build(k, v, path + (k,), k in req, sm, base)
                      for k, v in node.get("properties", {}).items()]
    elif kind == "list":
        req = set(items.get("required", []))
        f.children = [_build(k, v, path + (refs.ITEM, k), k in req, sm, item_base)
                      for k, v in items.get("properties", {}).items()]
    return f


def form(entity: str, ds, sm) -> Field:
    """The form for an entity: a group field whose children are its
    top-level properties (inherited ones included)."""
    name = refs.schema_name(ds, entity)
    schema = sm.get_schema(name)
    if schema is None:
        raise ValueError(f"no schema for entity {entity!r}")
    root = _build(entity, schema, (), True, sm, schema)
    root.label = schema.get("title") or _humanize(entity)
    return root


# -- options ----------------------------------------------------------------------

def _row_label(row: dict) -> str:
    for path in (("label",), ("identity", "label"), ("identity", "name"),
                 ("title",), ("name",)):
        value = row
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            return value
    return str(row.get("slug") or row.get("id"))


def options(f: Field, ds, record: Optional[dict] = None,
            root: Optional[Field] = None) -> list[Option]:
    """Values a choice field may take, given the record being composed
    (and its form, which resolves `within` clauses)."""
    if f.enum is not None:
        return [Option(v, t) for v, t in f.enum]
    if not f.ref:
        return []
    key = f.ref.get("key", "id")
    bound = None
    if f.ref.get("within") and record is not None and root is not None:
        via = root.find(tuple(parse_pointer(f.ref["within"]["via"])))
        bound = refs.within_bound(f.ref["within"], via.ref if via else None,
                                  record, ds)
    rows = refs.records(ds, f.ref["entity"])
    rows.sort(key=lambda r: (r.get("sort_order", 0), str(r.get(key))))
    return [Option(r.get(key), _row_label(r), r.get("description", "") or "")
            for r in rows if r.get(key) is not None
            and refs.qualifies(r, f.ref.get("where"))
            and (bound is None or r.get(key) in bound)]


# -- JSON Pointer (RFC 6901) ------------------------------------------------------

def parse_pointer(pointer: str) -> list[str]:
    if pointer in ("", "/"):
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


_MISSING = object()


def pointer_get(doc: Any, pointer: str, default: Any = _MISSING) -> Any:
    node = doc
    for token in parse_pointer(pointer):
        if isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        elif isinstance(node, dict) and token in node:
            node = node[token]
        elif default is not _MISSING:
            return default
        else:
            raise KeyError(pointer)
    return node


def pointer_set(doc: dict, pointer: str, value: Any) -> None:
    """Set a value, creating intermediate objects. `-` as the last array
    token appends (as in JSON Patch, RFC 6902)."""
    tokens = parse_pointer(pointer)
    if not tokens:
        raise ValueError("cannot replace the whole record")
    node = doc
    for i, token in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if isinstance(node, list):
            node = node[int(token)]
            continue
        if token not in node or node[token] is None:
            node[token] = [] if (nxt.isdigit() or nxt == "-") else {}
        node = node[token]
    last = tokens[-1]
    if isinstance(node, list):
        if last == "-":
            node.append(value)
        else:
            node[int(last)] = value
    else:
        node[last] = value


def pointer_unset(doc: dict, pointer: str) -> bool:
    tokens = parse_pointer(pointer)
    try:
        parent = pointer_get(doc, "/" + "/".join(
            t.replace("~", "~0").replace("/", "~1") for t in tokens[:-1]))
    except KeyError:
        return False
    last = tokens[-1]
    if isinstance(parent, list) and last.isdigit() and int(last) < len(parent):
        parent.pop(int(last))
        return True
    if isinstance(parent, dict) and last in parent:
        del parent[last]
        return True
    return False


# -- values -----------------------------------------------------------------------

def coerce(f: Field, raw: str) -> Any:
    """Parse operator text into the field's type. JSON is accepted
    everywhere; choice lists also accept comma-separated values."""
    text = raw.strip()
    if text == "null" and f.nullable:
        return None
    if f.kind in ("group", "list", "json"):
        return json.loads(text)
    if f.kind == "toggle":
        lowered = text.lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"{f.pointer}: expected true/false, got {raw!r}")
    if f.kind in ("multiselect", "tags"):
        if text.startswith("["):
            return json.loads(text)
        item_type = (f.schema.get("items") or {}).get("type")
        parts = [p.strip() for p in text.split(",") if p.strip()]
        return [int(p) if item_type == "integer" else p for p in parts]
    return _scalar(f.schema.get("type"), text, f)


def coerce_item(f: Field, raw: str) -> Any:
    """Parse one element of an array field (for add/remove)."""
    if f.kind == "list":
        return json.loads(raw)
    item_type = (f.schema.get("items") or {}).get("type")
    return _scalar(item_type, raw.strip(), f)


def _scalar(kind, text: str, f: Field) -> Any:
    if kind == "integer" or (f.ref and f.ref.get("key") == "id"
                             and kind != "string" and text.lstrip("-").isdigit()):
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{f.pointer}: expected an integer, got {text!r}")
    if kind == "number":
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"{f.pointer}: expected a number, got {text!r}")
    return text


def check_choice(f: Field, value: Any, ds, record: Optional[dict] = None,
                 root: Optional[Field] = None) -> list[str]:
    """Problems with a value for a choice field (empty when acceptable)."""
    if not f.choice or value is None:
        return []
    choices = options(f, ds, record, root)
    allowed = {o.value for o in choices}
    values = value if f.kind == "multiselect" else [value]
    if not isinstance(values, list):
        return [f"{f.pointer}: expected a list"]
    bad = [v for v in values if v not in allowed]
    if not bad:
        return []
    source = (f"{f.ref['entity']}.{f.ref.get('key', 'id')}" if f.ref
              else "the schema enum")
    where = (f.ref or {}).get("where")
    if where:
        source += " where " + ", ".join(f"{k} in {v}" for k, v in where.items())
    within = (f.ref or {}).get("within")
    if within and record is not None:
        via = pointer_get(record, within["via"], None)
        if via is not None:
            source += f" allowed by {within['field']} of {via!r}"
    hint = ", ".join(str(o.value) for o in choices[:12])
    return [f"{f.pointer}: {', '.join(map(repr, bad))} not in {source}"
            + (f" (choose from: {hint})" if hint else "")]


def skeleton(f: Field) -> Any:
    """A minimal new record: required fields with defaults or empties."""
    if f.kind == "group":
        out = {}
        for child in f.children:
            if child.required or child.default is not None:
                out[child.name] = skeleton(child)
        return out
    if f.default is not None:
        return copy.deepcopy(f.default)
    return {"list": [], "multiselect": [], "tags": [], "toggle": False,
            "json": {}}.get(f.kind, None if f.nullable else "")
