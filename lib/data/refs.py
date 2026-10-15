"""
Declared references - the `x-nim-ref` schema annotation.

A property annotated

    "x-nim-ref": {"entity": "channel_capability", "key": "slug"}

holds keys into another entity: the value (or each array item, or each
object key under `propertyNames`) must equal the `key` field of some
record of `entity`. This is the agency's foreign key. It follows SHACL
`sh:class` and LinkML slot `range` in meaning; the keyword is `x-`
prefixed because JSON Schema 2020-12 reserves `$` keywords for its core
and collects unknown keywords as annotations (see the plan's D-REF).
Standard validators ignore it; this module gives it teeth:

    declarations(schema_name)   where a schema declares references
    resolve(ds, entity, key)    the key set a reference may point into
    check(ds)                   every declared reference in the datastore

Path patterns use JSON Pointer (RFC 6901) tokens plus three wildcards:
`*` an array item, `~value` any object member value
(additionalProperties), `~key` any object member name (propertyNames).

Keys into collections stored operator-locally (substation, credential)
are reported as *unbound* rather than dangling: those records live in one
operator's ~/.nim, so a key from the repo may legitimately not resolve on
this machine.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

KEYWORD = "x-nim-ref"
ITEM, VALUE, KEY = "*", "~value", "~key"

# Composite keywords whose subschemas describe the same instance
_SAME_INSTANCE = ("allOf", "anyOf", "oneOf")


@dataclass(frozen=True)
class Declaration:
    """One declared reference: values at `path` are `key`s of `entity`."""
    schema: str
    path: tuple
    entity: str
    key: str
    # Optional bound: {"via": pointer, "field": name} - only values listed
    # in `field` of the record the reference at `via` points to
    within: Optional[dict] = None
    # Optional filter: {field: [allowed values]} on qualifying target records
    where: Optional[dict] = None

    @property
    def pointer(self) -> str:
        return "/" + "/".join(str(p) for p in self.path) if self.path else ""


@dataclass
class Finding:
    """A reference value that does not resolve."""
    entity: str          # entity holding the reference
    record: str          # record id/slug
    pointer: str         # concrete JSON Pointer to the value
    value: Any
    target: str          # entity.key it should resolve into
    unbound: bool = False  # target is operator-local; may resolve elsewhere

    def __str__(self) -> str:
        kind = "unbound" if self.unbound else "dangling"
        return (f"{self.entity}[{self.record}]{self.pointer}: {kind} "
                f"{self.value!r} -> {self.target}")


@dataclass
class Report:
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def dangling(self) -> list[Finding]:
        return [f for f in self.findings if not f.unbound]

    @property
    def unbound(self) -> list[Finding]:
        return [f for f in self.findings if f.unbound]


# -- schema side --------------------------------------------------------------

def _resolve_ref(ref: str, schema_manager, base: dict) -> Optional[dict]:
    """Resolve a $ref against the loaded schemas (by $id) or `base`."""
    target, _, fragment = ref.partition("#")
    if target:
        doc = next((s for s in (schema_manager.get_schema(n)
                                for n in schema_manager.list_schemas())
                    if s and s.get("$id") == target), None)
    else:
        doc = base
    if doc is None:
        return None
    node = doc
    for token in [t for t in fragment.split("/") if t]:
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node if isinstance(node, dict) else None


def walk(schema: dict, schema_manager, base: Optional[dict] = None,
         path: tuple = (), seen: Optional[set] = None
         ) -> Iterator[tuple[tuple, dict]]:
    """Yield (path, subschema) for every subschema describing a location
    in an instance. $refs are followed once per path (cycle guard)."""
    base = base if base is not None else schema
    seen = set() if seen is None else seen
    yield path, schema

    ref = schema.get("$ref")
    if isinstance(ref, str) and (ref, path) not in seen:
        seen.add((ref, path))
        target = _resolve_ref(ref, schema_manager, base)
        if target is not None:
            next_base = base if ref.startswith("#") else (
                _resolve_ref(ref.partition("#")[0], schema_manager, base) or base)
            yield from walk(target, schema_manager, next_base, path, seen)

    for keyword in _SAME_INSTANCE:
        for sub in schema.get(keyword, []) or []:
            if isinstance(sub, dict):
                yield from walk(sub, schema_manager, base, path, seen)

    for name, sub in (schema.get("properties") or {}).items():
        if isinstance(sub, dict):
            yield from walk(sub, schema_manager, base, path + (name,), seen)
    items = schema.get("items")
    if isinstance(items, dict):
        yield from walk(items, schema_manager, base, path + (ITEM,), seen)
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        yield from walk(extra, schema_manager, base, path + (VALUE,), seen)
    names = schema.get("propertyNames")
    if isinstance(names, dict):
        yield from walk(names, schema_manager, base, path + (KEY,), seen)


def declarations(schema_name: str, schema_manager) -> list[Declaration]:
    """Every reference declared by a schema, in walk order, deduplicated."""
    schema = schema_manager.get_schema(schema_name)
    if schema is None:
        return []
    found: dict[tuple, Declaration] = {}
    for path, sub in walk(schema, schema_manager):
        spec = sub.get(KEYWORD)
        if isinstance(spec, dict) and path not in found:
            within = spec.get("within")
            where = spec.get("where")
            found[path] = Declaration(schema_name, path, spec.get("entity", ""),
                                      spec.get("key", "id"),
                                      dict(within) if within else None,
                                      dict(where) if where else None)
    return list(found.values())


# -- data side ----------------------------------------------------------------

def values_at(instance: Any, path: tuple, at: tuple = ()
              ) -> Iterator[tuple[str, Any]]:
    """Yield (concrete JSON Pointer, value) for each instance location
    matching a pattern path. Absent and null locations yield nothing."""
    if not path:
        if instance is not None:
            yield "/" + "/".join(str(p) for p in at), instance
        return
    head, rest = path[0], path[1:]
    if head == ITEM:
        if isinstance(instance, list):
            for i, item in enumerate(instance):
                yield from values_at(item, rest, at + (i,))
    elif head == VALUE:
        if isinstance(instance, dict):
            for name, item in instance.items():
                yield from values_at(item, rest, at + (name,))
    elif head == KEY:
        if isinstance(instance, dict) and not rest:
            for name in instance:
                yield "/" + "/".join(str(p) for p in at + (name,)), name
    elif isinstance(instance, dict) and head in instance:
        yield from values_at(instance[head], rest, at + (head,))


def records(ds, entity: str, include_retired: bool = False) -> list[dict]:
    """Records of an entity, reference rows and collections alike. Retired
    (soft-deleted) collection records are not live: they are neither
    reference targets nor choices unless asked for."""
    config = ds.ENTITY_CONFIG.get(entity, {})
    if config.get("type") == "reference":
        return list(ds.load_reference(entity))
    if config.get("type") == "collection":
        return list(ds.query(entity, include_retired=include_retired))
    return []


def qualifies(row: dict, where: Optional[dict]) -> bool:
    """Whether a target record satisfies a declaration's `where` filter."""
    return all(row.get(field) in allowed for field, allowed in (where or {}).items())


def resolve(ds, entity: str, key: str, where: Optional[dict] = None) -> set:
    """The set of values a reference into `entity.key` may take."""
    return {r.get(key) for r in records(ds, entity)
            if r.get(key) is not None and qualifies(r, where)}


def within_bound(within: dict, via_ref: Optional[dict], record: dict,
                 ds) -> Optional[set]:
    """The values a `within` clause allows for one record: `field` of the
    record that the reference at `within.via` points to. None (no bound)
    when the via value is unset or the via field declares no reference."""
    if not via_ref:
        return None
    tokens = tuple(t for t in within["via"].split("/") if t)
    via_values = [v for _, v in values_at(record, tokens)]
    if not via_values:
        return None
    key = via_ref.get("key", "id")
    for row in records(ds, via_ref["entity"]):
        if row.get(key) == via_values[0]:
            return set(row.get(within["field"]) or [])
    return None


def is_operator_local(ds, entity: str) -> bool:
    return bool(ds.ENTITY_CONFIG.get(entity, {}).get("local_storage"))


def schema_name(ds, entity: str) -> str:
    """The schema an entity validates against (defaults to its name)."""
    return ds.ENTITY_CONFIG.get(entity, {}).get("schema", entity)


def _record_label(record: dict) -> str:
    return str(record.get("slug") or record.get("id") or "?")


def check(ds, schema_manager, entities: Optional[list[str]] = None) -> Report:
    """Resolve every declared reference held by the given entities
    (default: every reference table and collection)."""
    report = Report()
    names = entities or [e for e, c in ds.ENTITY_CONFIG.items()
                         if c.get("type") in ("reference", "collection")]
    key_sets: dict[tuple, set] = {}
    for entity in names:
        decls = declarations(schema_name(ds, entity), schema_manager)
        if not decls:
            continue
        rows = records(ds, entity)
        by_path = {d.path: d for d in decls}
        for decl in decls:
            target = (decl.entity, decl.key, json.dumps(decl.where, sort_keys=True))
            if target not in key_sets:
                key_sets[target] = resolve(ds, decl.entity, decl.key, decl.where)
            allowed = key_sets[target]
            for row in rows:
                bound = None
                if decl.within:
                    via = by_path.get(tuple(
                        t for t in decl.within["via"].split("/") if t))
                    bound = within_bound(decl.within, via and {
                        "entity": via.entity, "key": via.key}, row, ds)
                for pointer, value in values_at(row, decl.path):
                    report.checked += 1
                    if value not in allowed:
                        report.findings.append(Finding(
                            entity, _record_label(row), pointer, value,
                            f"{decl.entity}.{decl.key}"
                            + (f" where {decl.where}" if decl.where else ""),
                            unbound=is_operator_local(ds, decl.entity)))
                    elif bound is not None and value not in bound:
                        report.findings.append(Finding(
                            entity, _record_label(row), pointer, value,
                            f"{decl.entity}.{decl.key} within "
                            f"{decl.within['via']} -> {decl.within['field']}"))
    return report
