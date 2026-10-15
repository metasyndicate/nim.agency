"""
Generic composition verbs, driven by the form model (lib/data/forms.py).

Every registered entity gets the same verbs - no per-entity field code:

    form     ENTITY                 the field tree: pointer, kind, source
    options  ENTITY POINTER         values a choice field accepts
    list     ENTITY
    show     ENTITY REF [POINTER]
    new      ENTITY  [--from FILE] [--set P=V] [--add P=V] ...
    edit     ENTITY REF [--set P=V] [--add P=V] [--remove P=V] [--unset P]
    delete   ENTITY REF [--hard]

Fields are addressed by JSON Pointer (RFC 6901): `/identity/name`,
`/channels/0/capabilities`. List values take `--add`/`--remove` one item
at a time, or `--set` with a comma list or JSON.

`nim.agency data VERB ENTITY ...` reaches every entity; components such
as `syndicate` and `substation` mount the same verbs with the entity
fixed (`nim.agency substation new --set ...`).
"""

import json
import sys

from ..data import DataStore, SchemaManager
from ..data import forms, refs
from ..data.drafts import Draft, EditError


def _store():
    sm = SchemaManager().load_all()
    return sm, DataStore(schema_manager=sm)


def _entity(args, ds) -> str:
    entity = args.entity
    if entity not in ds.ENTITY_CONFIG:
        names = ", ".join(e for e, c in ds.ENTITY_CONFIG.items()
                          if c.get("type") in ("reference", "collection"))
        raise SystemExit(f"unknown entity {entity!r}; composable: {names}")
    return entity


def _split(assignment: str) -> tuple[str, str]:
    pointer, sep, value = assignment.partition("=")
    if not sep or not pointer.startswith("/"):
        raise EditError(f"expected /pointer=value, got {assignment!r}")
    return pointer, value


def _source(f: forms.Field) -> str:
    if f.ref:
        text = f"{f.ref['entity']}.{f.ref.get('key', 'id')}"
        where = f.ref.get("where")
        if where:
            text += " where " + ", ".join(f"{k} in {v}" for k, v in where.items())
        within = f.ref.get("within")
        if within:
            text += f" within {within['via']}->{within['field']}"
        return text
    if f.enum is not None:
        values = ", ".join(str(v) for v, _ in f.enum[:6])
        return f"enum [{values}{', ...' if len(f.enum) > 6 else ''}]"
    return ""


# -- read verbs ---------------------------------------------------------------

def cmd_form(args) -> int:
    sm, ds = _store()
    root = forms.form(_entity(args, ds), ds, sm)
    print(f"{root.label}  ({args.entity})   * required  ? nullable  "
          f"ro read-only (system)")
    for f in root.walk():
        if f is root:
            continue
        flags = ("*" if f.required else " ") + ("?" if f.nullable else " ") \
            + ("ro" if f.read_only else "  ")
        print(f"  {f.pointer:<42} {f.kind:<11} {flags} {_source(f)}")
    return 0


def cmd_options(args) -> int:
    sm, ds = _store()
    entity = _entity(args, ds)
    root = forms.form(entity, ds, sm)
    f = root.find(tuple(forms.parse_pointer(args.pointer)))
    if f is None or not f.choice:
        print(f"{args.pointer} is not a choice field of {entity}")
        return 2
    record = None
    if args.record:
        record = Draft.load(ds, sm, entity, args.record).record
    choices = forms.options(f, ds, record, root)
    bound = f" (bounded by {args.record})" if record and f.ref and \
        f.ref.get("within") else ""
    print(f"{f.pointer}: {_source(f)}{bound}")
    for o in choices:
        desc = f"  {o.description[:70]}" if o.description else ""
        print(f"  {str(o.value):<24} {o.label[:36]:<36}{desc}")
    return 0


def cmd_list(args) -> int:
    sm, ds = _store()
    entity = _entity(args, ds)
    rows = refs.records(ds, entity)
    for row in rows:
        label = forms._row_label(row)
        print(f"  {str(row.get('id')):<38} {str(row.get('slug') or ''):<24} "
              f"{label[:50]}")
    print(f"{len(rows)} {entity} record(s)")
    return 0


def cmd_show(args) -> int:
    sm, ds = _store()
    try:
        draft = Draft.load(ds, sm, _entity(args, ds), args.ref)
        value = draft.record if not args.pointer else \
            forms.pointer_get(draft.record, args.pointer)
    except (KeyError, ValueError) as exc:
        print(f"not found: {exc}")
        return 1
    print(json.dumps(value, indent=2, ensure_ascii=False))
    return 0


# -- write verbs --------------------------------------------------------------

def _apply(draft: Draft, args) -> None:
    for pointer in args.unset or []:
        draft.unset(pointer)
    for assignment in args.set or []:
        draft.set(*_split(assignment))
    for assignment in args.add or []:
        draft.add(*_split(assignment))
    for assignment in args.remove or []:
        draft.remove(*_split(assignment))


def _report(draft: Draft, verb: str, dry_run: bool) -> int:
    problems = draft.problems()
    changes = draft.diff()
    for pointer, old, new in changes:
        if draft.is_new:
            print(f"  {pointer}: {json.dumps(new, ensure_ascii=False)}")
        else:
            print(f"  {pointer}: {json.dumps(old, ensure_ascii=False)} -> "
                  f"{json.dumps(new, ensure_ascii=False)}")
    if problems:
        print(f"refused ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        return 1
    if not changes and not draft.is_new:
        print("no changes")
        return 0
    if dry_run:
        print(f"dry run: {draft.entity} not {verb}")
        return 0
    try:
        saved = draft.save()
    except (EditError, ValueError) as exc:
        print(f"refused: {exc}")
        return 1
    print(f"{draft.entity} {verb}: {saved.get('slug') or saved.get('id')}")
    if not draft.sm.can_validate():
        print("note: full schema validation skipped (jsonschema not "
              "installed); form checks passed")
    return 0


def cmd_new(args) -> int:
    sm, ds = _store()
    entity = _entity(args, ds)
    start = None
    if args.from_file:
        with open(args.from_file) as handle:
            start = json.load(handle)
    try:
        draft = Draft.new(ds, sm, entity, start)
        _apply(draft, args)
    except (EditError, ValueError) as exc:
        print(f"refused: {exc}")
        return 1
    return _report(draft, "created", args.dry_run)


def cmd_edit(args) -> int:
    sm, ds = _store()
    try:
        draft = Draft.load(ds, sm, _entity(args, ds), args.ref)
        _apply(draft, args)
    except (EditError, ValueError) as exc:
        print(f"refused: {exc}")
        return 1
    return _report(draft, "updated", args.dry_run)


def cmd_delete(args) -> int:
    sm, ds = _store()
    try:
        draft = Draft.load(ds, sm, _entity(args, ds), args.ref)
        if args.dry_run:
            from ..data.drafts import describe_holder, inbound
            holders = inbound(ds, sm, draft.entity, draft.original)
            print(f"dry run: {len(holders)} inbound reference(s)")
            for h in holders[:10]:
                print(f"  {describe_holder(h)}")
            return 0
        draft.delete(hard=args.hard)
    except (EditError, ValueError) as exc:
        print(f"refused: {exc}")
        return 1
    print(f"{draft.entity} deleted{' (hard)' if args.hard else ''}: {args.ref}")
    return 0


# -- parser wiring ------------------------------------------------------------

def _edit_flags(p) -> None:
    p.add_argument("--set", action="append", metavar="/POINTER=VALUE",
                   help="Set a field (lists: comma list or JSON)")
    p.add_argument("--add", action="append", metavar="/POINTER=VALUE",
                   help="Append one item to a list field")
    p.add_argument("--remove", action="append", metavar="/POINTER=VALUE",
                   help="Remove one item from a list field")
    p.add_argument("--unset", action="append", metavar="/POINTER",
                   help="Remove a field")
    p.add_argument("--dry-run", action="store_true",
                   help="Show the result and any problems; write nothing")


def add_verbs(actions, entity: str = None, read_verbs: bool = None) -> None:
    """Mount the verbs on an argparse subparsers object. With `entity`,
    the entity is fixed (component aliases); without, it is positional.
    `read_verbs` mounts list/show too (default: only when generic, since
    components such as syndicate have their own)."""
    if read_verbs is None:
        read_verbs = entity is None

    def parser(name, help_text, func):
        p = actions.add_parser(name, help=help_text)
        if entity is None:
            p.add_argument("entity", help="Entity (see: nim.agency data schemas)")
        else:
            p.set_defaults(entity=entity)
        p.set_defaults(func=func)
        return p

    parser("form", "Show the field tree (pointers, kinds, value sources)",
           cmd_form)
    p = parser("options", "List the values a choice field accepts", cmd_options)
    p.add_argument("pointer", help="Field JSON Pointer, e.g. /transport_slug")
    p.add_argument("--record", metavar="REF",
                   help="Bound dependent choices by this record")
    if read_verbs:
        parser("list", "List records", cmd_list)
        p = parser("show", "Show a record", cmd_show)
        p.add_argument("ref", help="id, slug, or lookup field value")
        p.add_argument("pointer", nargs="?", help="Show only this field")
    p = parser("new", "Compose a new record", cmd_new)
    p.add_argument("--from", dest="from_file", metavar="FILE",
                   help="Start from a JSON document")
    _edit_flags(p)
    p = parser("edit", "Edit a record", cmd_edit)
    p.add_argument("ref", help="id, slug, or lookup field value")
    _edit_flags(p)
    p = parser("delete", "Delete a record (refused while referenced)",
               cmd_delete)
    p.add_argument("ref", help="id, slug, or lookup field value")
    p.add_argument("--hard", action="store_true",
                   help="Remove the file rather than retire the record")
    p.add_argument("--dry-run", action="store_true",
                   help="List inbound references; delete nothing")
