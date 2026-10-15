"""
Composer - compose any record from its schema.

No field code: the screen draws whatever lib/data/forms.py derives from
the entity's schema, and every edit goes through a lib/data/drafts.Draft,
so the TUI refuses exactly what `nim.agency data edit` refuses (read-only
fields, values outside a reference table or enum, capabilities beyond the
transport ceiling) and saves through the validating datastore.

Screens: compose_entities -> compose_records -> compose_form, with
compose_pick as the single/multi-select picker over a choice field's
options (each option's description shown as it is highlighted).

Other screens open a form directly with `_open_compose(entity, ref,
return_mode)` - the substation and crue screens do, for new and edit.
"""

import curses
import json
from dataclasses import dataclass
from typing import Any, Optional

from lib.data import SchemaManager, forms, refs
from lib.data.drafts import Draft, EditError
from tui.chrome import Colors
from tui.textkit import clip

# Entity groups on the first screen; anything composable not listed here
# lands under "Reference"
ENTITY_GROUPS = (
    ("Inventory", ("syndicate", "substation")),
    ("Harness", ("transport", "channel_capability", "harness_function")),
    ("Agency", ("agent", "group", "project", "instruction_block")),
)

# Screens that open the form with _open_compose and keep their own copy of
# the record: entity -> (reload method, attribute holding the current record)
COMPOSE_VIEWS = {
    "substation": ("_load_substations", "current_substation"),
    "group": ("_load_crues", "current_crue"),
}


@dataclass
class Row:
    """One line of a form: a field at a concrete pointer."""
    pointer: str
    field: forms.Field
    depth: int
    kind: str              # header | value | item | add
    value: Any = None
    present: bool = False
    locked: bool = False   # inside a read-only field


def form_rows(root: forms.Field, record: dict) -> list[Row]:
    """Flatten a form over a record: groups and lists become headers with
    their members indented; list items get a header each and an add row."""
    rows = []

    def visit(parent: forms.Field, value: Any, pointer: str, depth: int,
              locked: bool):
        for child in parent.children:
            present = isinstance(value, dict) and child.name in value
            v = value.get(child.name) if isinstance(value, dict) else None
            p = f"{pointer}/{child.name}"
            lock = locked or child.read_only
            if child.kind == "group":
                rows.append(Row(p, child, depth, "header", v, present, lock))
                visit(child, v if isinstance(v, dict) else {}, p, depth + 1, lock)
            elif child.kind == "list":
                items = v if isinstance(v, list) else []
                rows.append(Row(p, child, depth, "header", items, present, lock))
                for i, item in enumerate(items):
                    rows.append(Row(f"{p}/{i}", child, depth + 1, "item",
                                    item, True, lock))
                    visit(child, item, f"{p}/{i}", depth + 2, lock)
                if not lock:
                    rows.append(Row(p, child, depth + 1, "add", None, False, lock))
            else:
                rows.append(Row(p, child, depth, "value", v, present, lock))

    visit(root, record, "", 0, False)
    return rows


def display(row: Row) -> str:
    """The value column for a row."""
    f = row.field
    if row.kind == "header":
        if f.kind == "list":
            return f"[{len(row.value or [])}]"
        return "" if row.present else "(not set)"
    if row.kind == "item":
        item = row.value if isinstance(row.value, dict) else {}
        return str(item.get("slug") or item.get("name") or item.get("label") or "")
    if row.kind == "add":
        return "+ add"
    if not row.present:
        return "-" if f.default is None else f"({f.default})"
    value = row.value
    if value is None:
        return "null"
    if f.kind == "toggle":
        return "[x]" if value else "[ ]"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(empty)"
    if isinstance(value, dict):
        return json.dumps(value)
    return str(value)


class ComposerMixin:
    """Schema-driven composition for every registered entity."""

    # -- entry points ---------------------------------------------------------

    def _compose_sm(self):
        if getattr(self, "compose_sm", None) is None:
            self.compose_sm = SchemaManager().load_all()
            self.ds.schema_manager = self.compose_sm
        return self.compose_sm

    def _enter_compose_mode(self):
        self._compose_sm()
        listed = {e for _, names in ENTITY_GROUPS for e in names}
        reference = tuple(sorted(e for e, c in self.ds.ENTITY_CONFIG.items()
                                 if c.get("type") == "reference" and e not in listed))
        self.compose_entity_rows = []
        for group, names in ENTITY_GROUPS + (("Reference", reference),):
            for name in names:
                if name in self.ds.ENTITY_CONFIG:
                    self.compose_entity_rows.append((group, name))
        self.compose_entity_index = 0
        self.mode = "compose_entities"

    def _open_compose(self, entity: str, ref: Optional[str] = None,
                      return_mode: str = "compose_records"):
        """Open the form on a record (or a new one when ref is None)."""
        sm = self._compose_sm()
        try:
            self.compose_draft = (Draft.load(self.ds, sm, entity, ref) if ref
                                  else Draft.new(self.ds, sm, entity))
        except (EditError, ValueError) as exc:
            self.message = f"Cannot open {entity}: {exc}"[:70]
            self.message_color = Colors.HEALTH
            return
        self.compose_entity = entity
        self.compose_return = return_mode
        self.compose_row_index = 0
        self.compose_scroll = 0
        self.compose_discard_armed = False
        self.editing = False
        self.edit_buffer = ""
        self.mode = "compose_form"

    def _compose_records_load(self):
        self.compose_records = refs.records(self.ds, self.compose_entity)
        self.compose_records.sort(key=lambda r: forms._row_label(r).lower())
        self.compose_record_index = min(getattr(self, "compose_record_index", 0),
                                        max(0, len(self.compose_records)))
        self.compose_delete_armed = None

    # -- entity list ----------------------------------------------------------

    def _draw_compose_entities(self, height: int, width: int):
        self._draw_header(width)
        box_w = min(70, width - 4)
        rows = self.compose_entity_rows
        visible = max(1, height - 12)
        top = max(0, self.compose_entity_index - visible + 1)
        self._draw_box(5, (width - box_w) // 2, min(len(rows), visible) + 4,
                       box_w, "COMPOSER - choose what to compose")
        x = (width - box_w) // 2 + 2
        last_group = None
        for i, (group, name) in enumerate(rows[top:top + visible], start=top):
            y = 7 + i - top
            label = group if group != last_group else ""
            last_group = group
            count = len(refs.records(self.ds, name))
            text = f"{label:<10} {name:<22} {count:>4} record(s)"
            color = Colors.HIGHLIGHT if i == self.compose_entity_index else Colors.NORMAL
            self._safe_addstr(y, x, clip(text, box_w - 4), curses.color_pair(color))
        self._draw_message(height - 4, width)
        self._draw_footer(height, width, "UP/DOWN: Entity | ENTER: Records | Q: Back")

    def _handle_compose_entities_input(self, key: int):
        rows = self.compose_entity_rows
        if key == curses.KEY_UP:
            self.compose_entity_index = (self.compose_entity_index - 1) % len(rows)
        elif key == curses.KEY_DOWN:
            self.compose_entity_index = (self.compose_entity_index + 1) % len(rows)
        elif key in (curses.KEY_ENTER, 10, 13):
            self.compose_entity = rows[self.compose_entity_index][1]
            self.compose_record_index = 0
            self._compose_records_load()
            self.mode = "compose_records"
        elif key in (ord('q'), ord('Q'), 27):
            self.mode = "menu"

    # -- record list ----------------------------------------------------------

    def _draw_compose_records(self, height: int, width: int):
        self._draw_header(width)
        box_w = min(90, width - 4)
        x = (width - box_w) // 2
        records = self.compose_records
        visible = max(1, height - 12)
        top = max(0, self.compose_record_index - visible + 1)
        self._draw_box(5, x, min(len(records) + 1, visible) + 4, box_w,
                       f"{self.compose_entity.upper()} - {len(records)} record(s)")
        entries = [("+ new record", None)] + [
            (f"{str(r.get('slug') or r.get('id')):<28} {forms._row_label(r)}", r)
            for r in records]
        for i, (text, _) in enumerate(entries[top:top + visible], start=top):
            color = Colors.HIGHLIGHT if i == self.compose_record_index else (
                Colors.STAT_VALUE if i == 0 else Colors.NORMAL)
            self._safe_addstr(7 + i - top, x + 2, clip(text, box_w - 4),
                              curses.color_pair(color))
        self._draw_message(height - 4, width)
        self._draw_footer(height, width,
                          "ENTER: Open | N: New | X: Delete (refused while referenced) | Q: Back")

    def _handle_compose_records_input(self, key: int):
        count = len(self.compose_records) + 1
        if key == curses.KEY_UP:
            self.compose_record_index = (self.compose_record_index - 1) % count
        elif key == curses.KEY_DOWN:
            self.compose_record_index = (self.compose_record_index + 1) % count
        elif key in (ord('n'), ord('N')) or (
                key in (curses.KEY_ENTER, 10, 13) and self.compose_record_index == 0):
            self._open_compose(self.compose_entity, None, "compose_records")
        elif key in (curses.KEY_ENTER, 10, 13):
            record = self.compose_records[self.compose_record_index - 1]
            self._open_compose(self.compose_entity,
                               str(record.get("id")), "compose_records")
        elif key in (ord('x'), ord('X')) and self.compose_record_index > 0:
            record = self.compose_records[self.compose_record_index - 1]
            if self.compose_delete_armed != record.get("id"):
                self.compose_delete_armed = record.get("id")
                self.message = "Press X again to delete"
                self.message_color = Colors.HEALTH
                return
            try:
                Draft.load(self.ds, self._compose_sm(), self.compose_entity,
                           str(record.get("id"))).delete()
                self.message = "Deleted"
                self.message_color = Colors.STAT_VALUE
            except (EditError, ValueError) as exc:
                self.message = str(exc)[:76]
                self.message_color = Colors.HEALTH
            self._compose_records_load()
        elif key in (ord('q'), ord('Q'), 27):
            self.mode = "compose_entities"

    # -- form -----------------------------------------------------------------

    def _compose_rows(self) -> list[Row]:
        draft = self.compose_draft
        return form_rows(draft.root, draft.record)

    def _draw_compose_form(self, height: int, width: int):
        self._draw_header(width)
        draft = self.compose_draft
        rows = self._compose_rows()
        self.compose_row_index = min(self.compose_row_index, max(0, len(rows) - 1))
        changed = len(draft.diff())
        title = (f"{'NEW' if draft.is_new else 'EDIT'} {self.compose_entity.upper()}"
                 f"{'  *' + str(changed) + ' change(s)' if changed else ''}")
        box_w = width - 4
        top_y, visible = 5, max(3, height - 13)
        if self.compose_row_index < self.compose_scroll:
            self.compose_scroll = self.compose_row_index
        if self.compose_row_index >= self.compose_scroll + visible:
            self.compose_scroll = self.compose_row_index - visible + 1
        self._draw_box(top_y, 2, visible + 2, box_w, title)
        label_w = min(40, box_w // 2)
        for i, row in enumerate(rows[self.compose_scroll:self.compose_scroll + visible],
                                start=self.compose_scroll):
            y = top_y + 1 + i - self.compose_scroll
            selected = i == self.compose_row_index
            name = (f"{row.pointer.rsplit('/', 1)[-1]}" if row.kind != "add"
                    else "")
            flag = "*" if row.field.required and row.kind == "value" else " "
            left = clip(f"{'  ' * row.depth}{name}{flag}", label_w)
            if selected and self.editing:
                right = f"[{self.edit_buffer}_]"
            else:
                right = display(row)
            if selected:
                color = Colors.HIGHLIGHT
            elif row.locked:
                color = Colors.DIM
            elif row.kind in ("header", "item"):
                color = Colors.TITLE
            elif row.kind == "add":
                color = Colors.STAT_VALUE
            else:
                color = Colors.NORMAL
            self._safe_addstr(y, 4, f"{left:<{label_w}} ", curses.color_pair(
                Colors.STAT_LABEL if not selected else color))
            self._safe_addstr(y, 5 + label_w, clip(right, box_w - label_w - 6),
                              curses.color_pair(color))
        # Field help: kind, value source, description
        if rows:
            f = rows[self.compose_row_index].field
            source = ""
            if f.ref:
                source = f" <- {f.ref['entity']}.{f.ref.get('key', 'id')}"
                if f.ref.get("within"):
                    source += f" within {f.ref['within']['via']}"
            elif f.enum:
                source = " <- enum"
            lock = "  read-only (system)" if rows[self.compose_row_index].locked else ""
            self._safe_addstr(top_y + visible + 2, 4,
                              clip(f"{f.kind}{source}{lock}", box_w - 4),
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(top_y + visible + 3, 4,
                              clip(f.description, box_w - 4),
                              curses.color_pair(Colors.DIM))
        self._draw_message(height - 4, width)
        if self.editing:
            self._draw_footer(height, width, "Type | ENTER: Apply | ESC: Cancel")
        else:
            self._draw_footer(height, width,
                              "ENTER: Edit/Pick | SPACE: Toggle | D: Unset/Remove | "
                              "S: Save | V: JSON | ?: Problems | Q: Back")

    def _compose_fail(self, exc: Exception):
        self.message = str(exc)[:78]
        self.message_color = Colors.HEALTH

    def _handle_compose_form_input(self, key: int):
        draft = self.compose_draft
        rows = self._compose_rows()
        if not rows:
            if key in (ord('q'), ord('Q'), 27):
                self.mode = self.compose_return
            return
        row = rows[min(self.compose_row_index, len(rows) - 1)]

        if self.editing:
            if key == 27:
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                self.editing = False
                try:
                    if self.edit_buffer.strip() == "" and not row.field.required:
                        if row.present:
                            draft.unset(row.pointer)
                    else:
                        draft.set(row.pointer, self.edit_buffer)
                except (EditError, ValueError) as exc:
                    self._compose_fail(exc)
                self.edit_buffer = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
            return

        self.compose_discard_armed = self.compose_discard_armed and key in (
            ord('q'), ord('Q'), 27)
        if key == curses.KEY_UP:
            self.compose_row_index = (self.compose_row_index - 1) % len(rows)
        elif key == curses.KEY_DOWN:
            self.compose_row_index = (self.compose_row_index + 1) % len(rows)
        elif key == curses.KEY_PPAGE:
            self.compose_row_index = max(0, self.compose_row_index - 10)
        elif key == curses.KEY_NPAGE:
            self.compose_row_index = min(len(rows) - 1, self.compose_row_index + 10)
        elif key in (curses.KEY_ENTER, 10, 13, ord(' ')):
            self._compose_activate(row, toggle_only=key == ord(' '))
        elif key in (ord('d'), ord('D'), curses.KEY_DC):
            try:
                if row.kind == "item":
                    draft.remove_at(row.pointer)
                elif row.kind in ("value", "header") and row.present:
                    draft.unset(row.pointer)
            except (EditError, ValueError) as exc:
                self._compose_fail(exc)
        elif key in (ord('s'), ord('S')):
            self._compose_save()
        elif key in (ord('v'), ord('V')):
            self._compose_view(f"{self.compose_entity} (draft)",
                               draft.render().splitlines())
        elif key == ord('?'):
            problems = draft.problems()
            self._compose_view("PROBLEMS", problems or ["none - ready to save"])
        elif key in (ord('q'), ord('Q'), 27):
            if draft.diff() and not self.compose_discard_armed:
                self.compose_discard_armed = True
                self.message = "Unsaved changes - S to save, Q again to discard"
                self.message_color = Colors.HEALTH
                return
            self.mode = self.compose_return
            if self.compose_return == "compose_records":
                self._compose_records_load()

    def _compose_view(self, title: str, lines: list[str]):
        self.viewer_title = title
        self.viewer_lines = lines
        self.viewer_scroll = 0
        self.viewer_return_mode = "compose_form"
        self.mode = "viewer"

    def _compose_activate(self, row: Row, toggle_only: bool = False):
        draft, f = self.compose_draft, row.field
        if row.locked:
            self._compose_fail(EditError(f"{row.pointer} is read-only: the "
                                         f"system maintains it"))
            return
        try:
            if row.kind == "add":
                draft.append_item(row.pointer)
            elif row.kind != "value":
                return
            elif f.kind == "toggle":
                draft.set(row.pointer, not bool(row.value))
            elif toggle_only:
                return
            elif f.choice:
                self._open_compose_pick(row)
            else:
                current = row.value
                if isinstance(current, list):
                    self.edit_buffer = ", ".join(str(v) for v in current)
                elif isinstance(current, dict):
                    self.edit_buffer = json.dumps(current)
                else:
                    self.edit_buffer = "" if current is None else str(current)
                self.editing = True
        except (EditError, ValueError) as exc:
            self._compose_fail(exc)

    def _compose_save(self):
        draft = self.compose_draft
        problems = draft.problems()
        if problems:
            self.message = (f"{len(problems)} problem(s): {problems[0]}"
                            f" (? lists all)")[:78]
            self.message_color = Colors.HEALTH
            return
        try:
            saved = draft.save()
        except (EditError, ValueError) as exc:
            self._compose_fail(exc)
            return
        ref = str(saved.get("id"))
        self.compose_draft = Draft.load(self.ds, self._compose_sm(),
                                        self.compose_entity, ref)
        self.message = f"Saved {saved.get('slug') or saved.get('id')}"
        self.message_color = Colors.STAT_VALUE
        # Views that hold their own copy of the entity refresh it after a save
        loader, holder = COMPOSE_VIEWS.get(self.compose_entity, (None, None))
        if loader and hasattr(self, loader):
            getattr(self, loader)()
            current = getattr(self, holder, None)
            if current is None or current.get("id") == saved.get("id"):
                setattr(self, holder, saved)

    # -- picker ---------------------------------------------------------------

    def _open_compose_pick(self, row: Row):
        draft, f = self.compose_draft, row.field
        choices = forms.options(f, self.ds, draft.record, draft.root)
        multi = f.kind == "multiselect"
        if not multi and f.nullable:
            choices = [forms.Option(None, "(none)", "Clear the reference")] + choices
        current = row.value if row.present else ([] if multi else None)
        self.compose_pick = {
            "pointer": row.pointer, "field": f, "options": choices,
            "multi": multi, "index": 0, "scroll": 0,
            "selected": set(current or []) if multi else None,
        }
        if not multi:
            values = [o.value for o in choices]
            if current in values:
                self.compose_pick["index"] = values.index(current)
        if not choices:
            self._compose_fail(EditError(f"{row.pointer}: no options available"))
            return
        self.mode = "compose_pick"

    def _draw_compose_pick(self, height: int, width: int):
        self._draw_compose_form(height, width)
        pick = self.compose_pick
        choices = pick["options"]
        box_w = min(76, width - 6)
        visible = max(3, min(len(choices), height - 14))
        x, y0 = (width - box_w) // 2, 7
        if pick["index"] < pick["scroll"]:
            pick["scroll"] = pick["index"]
        if pick["index"] >= pick["scroll"] + visible:
            pick["scroll"] = pick["index"] - visible + 1
        kind = "choose any" if pick["multi"] else "choose one"
        self._draw_box(y0, x, visible + 4, box_w,
                       f"{pick['pointer']} - {kind}")
        for i in range(visible + 2):
            self._safe_addstr(y0 + 1 + i, x + 1, " " * (box_w - 2))
        for i, option in enumerate(choices[pick["scroll"]:pick["scroll"] + visible],
                                   start=pick["scroll"]):
            mark = ""
            if pick["multi"]:
                mark = "[x] " if option.value in pick["selected"] else "[ ] "
            text = f"{mark}{str(option.value):<22} {option.label}"
            color = Colors.HIGHLIGHT if i == pick["index"] else Colors.NORMAL
            self._safe_addstr(y0 + 1 + i - pick["scroll"], x + 2,
                              clip(text, box_w - 4), curses.color_pair(color))
        description = choices[pick["index"]].description if choices else ""
        self._safe_addstr(y0 + visible + 2, x + 2, clip(description, box_w - 4),
                          curses.color_pair(Colors.DIM))
        hint = ("SPACE: Toggle | ENTER: Apply | ESC: Cancel" if pick["multi"]
                else "ENTER: Choose | ESC: Cancel")
        self._draw_footer(height, width, hint)

    def _handle_compose_pick_input(self, key: int):
        pick = self.compose_pick
        count = len(pick["options"])
        if key == curses.KEY_UP:
            pick["index"] = (pick["index"] - 1) % count
        elif key == curses.KEY_DOWN:
            pick["index"] = (pick["index"] + 1) % count
        elif key == ord(' ') and pick["multi"]:
            value = pick["options"][pick["index"]].value
            pick["selected"] ^= {value}
        elif key in (curses.KEY_ENTER, 10, 13):
            try:
                if pick["multi"]:
                    ordered = [o.value for o in pick["options"]
                               if o.value in pick["selected"]]
                    self.compose_draft.set(pick["pointer"], ordered)
                else:
                    self.compose_draft.set(pick["pointer"],
                                           pick["options"][pick["index"]].value)
            except (EditError, ValueError) as exc:
                self._compose_fail(exc)
            self.mode = "compose_form"
        elif key in (27, ord('q'), ord('Q')):
            self.mode = "compose_form"
