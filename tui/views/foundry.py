"""
Type Foundry - operator-defined reference types.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from lib.data import AgentGenerator
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class FoundryMixin:
    """operator-defined reference types."""

    FOUNDRY_TABLES = [
        ("specialty", "Specialties", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("ops_domain_id", "Ops Domain", "choice:ops_domain"),
            ("category", "Category", "text"),
            ("icon", "Icon", "text"),
        ]),
        ("focus", "Foci", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("ops_domain_id", "Ops Domain", "choice:ops_domain"),
            ("abbrev", "Abbrev", "text"),
            ("icon", "Icon", "text"),
        ]),
        ("tool", "Tools", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("category_id", "Category", "choice:tool_category"),
            ("cost", "Cost (gold)", "int"),
            ("icon", "Icon", "text"),
        ]),
        ("crue_type", "Crue Types", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("mission_profile", "Mission Profile", "text"),
            ("icon", "Icon", "text"),
        ]),
        ("role_title", "Roles / Titles", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("icon", "Icon", "text"),
        ]),
        ("task_preset", "Task Presets", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("prompt", "Task Prompt", "text"),
            ("skill", "Skill Terms", "text"),
            ("count", "Crew Size", "int"),
            ("icon", "Icon", "text"),
        ]),
        ("skill_domain", "Skill Domains", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("abbrev", "Abbrev", "text"),
            ("icon", "Icon", "text"),
        ]),
        ("ops_domain", "Ops Domains", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("abbrev", "Abbrev", "text"),
            ("icon", "Icon", "text"),
        ]),
        ("qualifier", "Qualifiers", [
            ("label", "Label", "text"),
            ("description", "Description", "text"),
            ("vibe", "Vibe", "text"),
            ("min_xp", "Min XP", "int"),
            ("max_xp", "Max XP", "int"),
            ("icon", "Icon", "text"),
        ]),
    ]

    # Structural defaults injected on create so new entries satisfy what
    # the generator/economy expect from each table
    FOUNDRY_DEFAULTS = {
        "specialty": {"category": "platform"},
        "focus": {},
        "tool": {
            "is_active": True,
            "risk": {"level": "safe", "reversibility": "varies",
                     "scope": "local", "requires_confirmation": False},
            "usage": {"syntax": "", "examples": [], "whispers": []},
        },
        "crue_type": {
            "composition": {"max_size": 10},
            "primary_archetypes": [],
            "support_archetypes": [],
            "coordination": "",
        },
        "role_title": {},
        "task_preset": {"count": 1},
        "skill_domain": {"max_points": 20, "category": "technical"},
        "ops_domain": {"layer": 0, "color": "#888888"},
        "qualifier": {},
    }

    def _refresh_reference_caches(self):
        """Rebuild in-memory reference caches after a foundry write so the
        generator and every screen see the new types immediately."""
        self.focuses = {f["id"]: f for f in self.ds.load_reference("focus")}
        self.crue_types = {c["id"]: c for c in self.ds.load_reference("crue_type")}
        self.tool_refs = {t["id"]: t for t in self.ds.load_reference("tool")}
        self.gen = AgentGenerator(datastore=self.ds)

    def _enter_foundry_mode(self):
        """Open the type foundry table picker."""
        self.foundry_table_index = 0
        self.mode = "foundry"

    def _foundry_spec(self):
        return self.FOUNDRY_TABLES[self.foundry_table_index]

    def _draw_foundry(self, height: int, width: int):
        """Draw the foundry table picker."""
        self._draw_header(width)

        box_width = 56
        box_height = len(self.FOUNDRY_TABLES) + 6
        box_y = 7
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width, "TYPE FOUNDRY")

        self._safe_addstr(box_y + 1, box_x + 2,
                          "Operator-defined types for agent generation",
                          curses.color_pair(Colors.DIM))
        for i, (table, label, _) in enumerate(self.FOUNDRY_TABLES):
            count = len(self.ds.load_reference(table))
            row = f" {label:<20} {count:>3} defined "
            color = (Colors.HIGHLIGHT if i == self.foundry_table_index
                     else Colors.NORMAL)
            self._safe_addstr(box_y + 3 + i, box_x + 2, row,
                              curses.color_pair(color))

        self._draw_message(box_y + box_height + 1, width)
        self._draw_footer(height, width, "UP/DOWN: Table | ENTER: Open | Q: Back")

    def _handle_foundry_input(self, key: int):
        """Handle foundry table picker input."""
        count = len(self.FOUNDRY_TABLES)
        if key == curses.KEY_UP:
            self.foundry_table_index = (self.foundry_table_index - 1) % count
        elif key == curses.KEY_DOWN:
            self.foundry_table_index = (self.foundry_table_index + 1) % count
        elif key in (curses.KEY_ENTER, 10, 13):
            self._load_foundry_entries()
            self.mode = "foundry_list"
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "menu"

    def _load_foundry_entries(self):
        table, _, _ = self._foundry_spec()
        self.foundry_entries = sorted(
            self.ds.load_reference(table, force_reload=True),
            key=lambda e: e.get("id", 0))
        self.foundry_index = 0

    def _draw_foundry_list(self, height: int, width: int):
        """Draw the entries of the selected reference table."""
        self._draw_header(width)
        table, label, _ = self._foundry_spec()
        self._safe_addstr(5, 2, f"TYPE FOUNDRY / {label.upper()} "
                          f"({len(self.foundry_entries)} entries)",
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)

        list_y = 7
        header = f"{'ID':>4} {'SLUG':<26} {'LABEL':<26} DESCRIPTION"
        self._safe_addstr(list_y, 4, clip(header, width - 8),
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self._safe_addstr(list_y + 1, 4, "-" * min(width - 8, 90),
                          curses.color_pair(Colors.DIM))

        list_height = height - 12
        visible = list_height - 2
        start = max(0, self.foundry_index - visible + 3)
        for i, entry in enumerate(self.foundry_entries[start:start + visible]):
            row_y = list_y + 2 + i
            if row_y >= list_y + list_height:
                break
            row = (f"{entry.get('id', '?'):>4} {pad(entry.get('slug', ''), 26)} "
                   f"{pad(entry.get('label', ''), 26)} "
                   f"{entry.get('description', '')}")
            color = (Colors.HIGHLIGHT if start + i == self.foundry_index
                     else Colors.NORMAL)
            self._safe_addstr(row_y, 4, f" {clip(row, width - 10)} ",
                              curses.color_pair(color))

        self._draw_message(height - 4, width)
        self._draw_footer(height, width,
                          "N: New Type | ENTER/E: Edit | Q: Back "
                          "(no delete - agents reference these)")

    def _handle_foundry_list_input(self, key: int):
        """Handle foundry entry list input."""
        count = len(self.foundry_entries)
        if key == curses.KEY_UP and count:
            self.foundry_index = max(0, self.foundry_index - 1)
        elif key == curses.KEY_DOWN and count:
            self.foundry_index = min(count - 1, self.foundry_index + 1)
        elif key == ord('n') or key == ord('N'):
            self._open_foundry_form(None)
        elif (key in (curses.KEY_ENTER, 10, 13) or key in (ord('e'), ord('E'))) and count:
            self._open_foundry_form(self.foundry_entries[self.foundry_index])
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "foundry"

    def _open_foundry_form(self, entry):
        """Prime the add/edit form. entry=None means creating a new type."""
        table, _, fields = self._foundry_spec()
        self.foundry_entry_id = entry.get("id") if entry else None
        self.foundry_form = {}
        for field_key, _, kind in fields:
            if entry:
                self.foundry_form[field_key] = entry.get(field_key, "")
            elif kind.startswith("choice"):
                ref = kind.split(":")[1]
                options = self.ds.load_reference(ref)
                self.foundry_form[field_key] = options[0]["id"] if options else 1
            elif kind == "int":
                self.foundry_form[field_key] = 0
            else:
                self.foundry_form[field_key] = ""
        self.foundry_field_index = 0
        self.editing = False
        self.edit_buffer = ""
        self.mode = "foundry_form"

    def _foundry_choice_display(self, kind: str, value) -> str:
        ref = kind.split(":")[1]
        entry = self.ds.get_reference_by_id(ref, value)
        return entry.get("label", str(value)) if entry else str(value)

    def _foundry_cycle(self, field_key: str, kind: str, direction: int = 1):
        ref = kind.split(":")[1]
        options = [e["id"] for e in sorted(self.ds.load_reference(ref),
                                           key=lambda e: e["id"])]
        if not options:
            return
        current = self.foundry_form.get(field_key)
        idx = options.index(current) if current in options else 0
        self.foundry_form[field_key] = options[(idx + direction) % len(options)]

    def _draw_foundry_form(self, height: int, width: int):
        """Draw the add/edit form for a reference entry."""
        self._draw_header(width)
        table, label, fields = self._foundry_spec()
        verb = "EDIT" if self.foundry_entry_id else "NEW"

        box_width = min(74, width - 4)
        box_height = len(fields) * 2 + 5
        box_y = 6
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width,
                       f"{verb} {label.upper().rstrip('S')} TYPE")

        if self.foundry_entry_id:
            entry = next((e for e in self.foundry_entries
                          if e.get("id") == self.foundry_entry_id), {})
            self._safe_addstr(box_y + 1, box_x + 2,
                              f"id:{self.foundry_entry_id}  slug:{entry.get('slug', '?')} "
                              "(immutable - agents reference them)",
                              curses.color_pair(Colors.DIM))

        y = box_y + 3
        for i, (field_key, flabel, kind) in enumerate(fields):
            is_selected = (i == self.foundry_field_index)
            value = self.foundry_form.get(field_key, "")
            if kind.startswith("choice"):
                display = self._foundry_choice_display(kind, value)
                if is_selected:
                    display = f"< {display} >"
            elif is_selected and self.editing:
                display = f"[{self.edit_buffer}_]"
            else:
                display = str(value)
            color = Colors.HIGHLIGHT if is_selected else Colors.STAT_VALUE
            self._safe_addstr(y, box_x + 2, f"{flabel:<16}",
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(y, box_x + 19, clip(display, box_width - 22),
                              curses.color_pair(color))
            y += 2

        self._draw_message(box_y + box_height + 1, width)
        if self.editing:
            self._draw_footer(height, width, "Type to edit | ENTER: Apply | ESC: Cancel")
        else:
            self._draw_footer(height, width,
                              "UP/DOWN: Field | ENTER: Edit/Cycle | S: Save | Q: Cancel")

    def _handle_foundry_form_input(self, key: int):
        """Handle foundry form input."""
        table, _, fields = self._foundry_spec()
        field_key, _, kind = fields[self.foundry_field_index]

        if self.editing:
            if key == 27:
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                value = self.edit_buffer.strip()
                if kind == "int":
                    try:
                        value = int(value)
                    except ValueError:
                        value = 0
                self.foundry_form[field_key] = value
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
            return

        if key == curses.KEY_UP:
            self.foundry_field_index = (self.foundry_field_index - 1) % len(fields)
        elif key == curses.KEY_DOWN:
            self.foundry_field_index = (self.foundry_field_index + 1) % len(fields)
        elif key == curses.KEY_LEFT and kind.startswith("choice"):
            self._foundry_cycle(field_key, kind, -1)
        elif key == curses.KEY_RIGHT and kind.startswith("choice"):
            self._foundry_cycle(field_key, kind, 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            if kind.startswith("choice"):
                self._foundry_cycle(field_key, kind, 1)
            else:
                self.edit_buffer = str(self.foundry_form.get(field_key, ""))
                self.editing = True
        elif key == ord('s') or key == ord('S'):
            self._save_foundry_form()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "foundry_list"

    def _save_foundry_form(self):
        """Create or update the reference entry, then refresh caches."""
        table, _, fields = self._foundry_spec()

        if not str(self.foundry_form.get("label", "")).strip():
            self.message = "Label is required"
            self.message_color = Colors.HEALTH
            return

        try:
            if self.foundry_entry_id:
                self.ds.update_reference_entry(
                    table, self.foundry_entry_id, dict(self.foundry_form))
                self.message = f"Updated {self.foundry_form['label']}"
            else:
                entry = dict(self.FOUNDRY_DEFAULTS.get(table, {}))
                entry.update(self.foundry_form)
                saved = self.ds.add_reference_entry(table, entry)
                self.message = (f"Forged {saved['label']} "
                                f"(id:{saved['id']} slug:{saved['slug']})")
        except ValueError as e:
            self.message = str(e)
            self.message_color = Colors.HEALTH
            return

        self.message_color = Colors.GOLD
        self._refresh_reference_caches()
        self._load_foundry_entries()
        self.mode = "foundry_list"

