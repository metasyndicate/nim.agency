"""
Agent edit - form over identity/classification/status fields.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class AgentEditMixin:
    """form over identity/classification/status fields."""

    AGENT_EDIT_FIELDS = [
        ("name", "Name", "text"),
        ("codename", "Codename", "text"),
        ("role_title", "Role/Title", "choice:role_title"),
        ("specialty_id", "Specialty", "choice:specialty"),
        ("focus_id", "Focus", "choice:focus"),
        ("class_id", "Class", "choice:class"),
        ("status_id", "Status", "choice:status"),
        ("motto", "Motto", "text"),
    ]

    def _enter_agent_edit(self):
        """Open the edit form primed with the current agent's fields."""
        if not self.current_agent:
            return
        agent = self.current_agent
        self.agent_edit_form = {
            "name": agent["identity"].get("name", ""),
            "codename": agent["identity"].get("codename", ""),
            "role_title": agent["identity"].get("role_title", ""),
            "specialty_id": agent["classification"].get("specialty_id"),
            "focus_id": agent["classification"].get("focus_id"),
            "class_id": agent["classification"].get("class_id"),
            "status_id": agent["classification"].get("status_id", 1),
            "motto": agent.get("profile", {}).get("motto", ""),
        }
        # role_title cycles through the operator-defined role_title table,
        # anchored on the agent's current (computed) title
        self._role_title_anchor = agent["identity"].get("role_title", "")
        self.agent_edit_index = 0
        self.editing = False
        self.edit_buffer = ""
        self.mode = "agent_edit"

    def _agent_edit_choice_display(self, field_key) -> str:
        """Label for a choice field's current value."""
        value = self.agent_edit_form.get(field_key)
        if field_key == "specialty_id":
            if value is None:
                return "(none - not everyone gets to be special)"
            entry = self.ds.get_reference_by_id("specialty", value)
            return entry.get("label", "?") if entry else "?"
        if field_key == "role_title":
            return value or "(untitled)"
        if field_key in ("status_id", "focus_id", "class_id"):
            table = {"status_id": "status", "focus_id": "focus",
                     "class_id": "class"}[field_key]
            if value is None:
                return "(none)"
            entry = self.ds.get_reference_by_id(table, value)
            return entry.get("label", "?") if entry else "?"
        return str(value)

    def _agent_edit_cycle(self, field_key, direction=1):
        """Cycle a choice field through its reference table."""
        if field_key == "specialty_id":
            options = [None] + [s["id"] for s in sorted(
                self.ds.load_reference("specialty"), key=lambda s: s["id"])]
        elif field_key == "role_title":
            # Anchor (current/computed title) first, then foundry entries
            labels = [e.get("label", "") for e in sorted(
                self.ds.load_reference("role_title"), key=lambda e: e["id"])]
            options = [self._role_title_anchor] + [
                l for l in labels if l and l != self._role_title_anchor]
        elif field_key in ("status_id", "focus_id", "class_id"):
            table = {"status_id": "status", "focus_id": "focus",
                     "class_id": "class"}[field_key]
            options = [s["id"] for s in sorted(
                self.ds.load_reference(table), key=lambda s: s["id"])]
        else:
            return
        current = self.agent_edit_form.get(field_key)
        idx = options.index(current) if current in options else 0
        self.agent_edit_form[field_key] = options[(idx + direction) % len(options)]

    def _draw_agent_edit(self, height: int, width: int):
        """Draw the agent edit form."""
        if not self.current_agent:
            self.mode = "roster"
            return
        self._draw_header(width)
        ident = self.current_agent["identity"]

        box_width = min(72, width - 4)
        box_height = len(self.AGENT_EDIT_FIELDS) * 2 + 4
        box_y = 6
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width,
                       f"EDIT AGENT - {ident['codename']}")

        y = box_y + 2
        for i, (field_key, label, kind) in enumerate(self.AGENT_EDIT_FIELDS):
            is_selected = (i == self.agent_edit_index)
            if kind.startswith("choice"):
                display = self._agent_edit_choice_display(field_key)
                if is_selected:
                    display = f"< {display} >"
            elif is_selected and self.editing:
                display = f"[{self.edit_buffer}_]"
            else:
                display = str(self.agent_edit_form.get(field_key, ""))

            color = Colors.HIGHLIGHT if is_selected else Colors.STAT_VALUE
            self._safe_addstr(y, box_x + 2, f"{label:<12}",
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(y, box_x + 15, clip(display, box_width - 18),
                              curses.color_pair(color))
            y += 2

        self._draw_message(box_y + box_height + 1, width)
        if self.editing:
            self._draw_footer(height, width, "Type to edit | ENTER: Apply | ESC: Cancel")
        else:
            self._draw_footer(height, width,
                              "UP/DOWN: Field | ENTER: Edit/Cycle | "
                              "LEFT/RIGHT: Cycle | S: Save | Q: Cancel")

    def _handle_agent_edit_input(self, key: int):
        """Handle agent edit form input."""
        fields = self.AGENT_EDIT_FIELDS
        field_key, _, kind = fields[self.agent_edit_index]

        if self.editing:
            if key == 27:
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                self.agent_edit_form[field_key] = self.edit_buffer.strip()
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
            return

        if key == curses.KEY_UP:
            self.agent_edit_index = (self.agent_edit_index - 1) % len(fields)
        elif key == curses.KEY_DOWN:
            self.agent_edit_index = (self.agent_edit_index + 1) % len(fields)
        elif key == curses.KEY_LEFT and kind.startswith("choice"):
            self._agent_edit_cycle(field_key, -1)
        elif key == curses.KEY_RIGHT and kind.startswith("choice"):
            self._agent_edit_cycle(field_key, 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            if kind.startswith("choice"):
                self._agent_edit_cycle(field_key, 1)
            else:
                self.edit_buffer = str(self.agent_edit_form.get(field_key, ""))
                self.editing = True
        elif key == ord('s') or key == ord('S'):
            self._save_agent_edit()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "view"

    def _save_agent_edit(self):
        """Persist the edit form to the agent record, keeping derived
        fields (dogtag, specialty label) consistent."""
        agent = self.current_agent
        form = self.agent_edit_form

        name = form.get("name", "").strip() or agent["identity"]["name"]
        parts = name.split(None, 2)
        if len(parts) >= 3:
            title, first, last = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            title, first, last = agent["identity"].get("title", "Agt."), parts[0], parts[1]
        else:
            title, first, last = agent["identity"].get("title", "Agt."), parts[0], "Unknown"

        specialty_id = form.get("specialty_id")
        specialty = (self.ds.get_reference_by_id("specialty", specialty_id)
                     if specialty_id else None)

        updates = {
            "identity": {
                "name": f"{title} {first} {last}",
                "title": title,
                "dogtag": self.gen.compute_dogtag(title, first, last),
                "codename": (form.get("codename", "").strip().lower()
                             or agent["identity"]["codename"]),
                "specialty": specialty.get("label") if specialty else None,
                "role_title": (form.get("role_title", "").strip()
                               or agent["identity"].get("role_title", "")),
            },
            "classification": {
                "specialty_id": specialty_id,
                "focus_id": form.get("focus_id"),
                "class_id": form.get("class_id"),
                "status_id": form.get("status_id", 1),
            },
            "profile": {"motto": form.get("motto", "")},
        }
        updated = self.ds.update("agent", agent["id"], updates)
        self._load_roster()
        self.current_agent = next(
            (a for a in self.roster if a["id"] == agent["id"]), updated or agent)
        self.message = f"Saved {self.current_agent['identity']['codename']}"
        self.message_color = Colors.STAT_VALUE
        self.mode = "view"

