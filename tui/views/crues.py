"""
Crues - crew formations: list, view, create from a crue_type, member picker.
Editing opens the schema-driven Composer (tui/views/composer.py).

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class CruesMixin:
    """crew formations: list, edit, member picker."""

    def _enter_crues_mode(self):
        """Enter crues management mode."""
        self._load_crues()
        self.crue_index = 0
        self.current_crue = None
        self.mode = "crues"

    def _load_crues(self):
        """Load groups from datastore."""
        try:
            results = self.ds.query("group")
            self.crues = results.data
        except Exception as e:
            self.crues = []
            self.message = f"Load error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    def _crue_type_of(self, crue: dict) -> dict:
        """The crue_type row a group is classified by (empty if unbound)."""
        slug = crue.get("crue_type_slug")
        for ct in self.crue_types.values():
            if ct.get("slug") == slug:
                return ct
        return {}

    def _draw_crues(self, height: int, width: int):
        """Draw crues list view."""
        self._draw_header(width)

        # Title
        try:
            self.stdscr.addstr(5, 2, f"CRUES ({len(self.crues)} teams)",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        if not self.crues:
            try:
                self.stdscr.addstr(10, (width - 25) // 2, "No crues registered.",
                                   curses.color_pair(Colors.DIM))
                self.stdscr.addstr(12, (width - 35) // 2, "Press N to create a new crue",
                                   curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass
        else:
            # List header
            list_y = 7
            header = f"{'NAME':<25} {'TYPE':<18} {'MEMBERS':>8} {'STATUS':<10}"
            try:
                self.stdscr.addstr(list_y, 4, header[:width-8],
                                   curses.color_pair(Colors.TITLE) | curses.A_BOLD)
                self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

            # List crues
            list_height = height - 12
            visible_count = list_height - 2
            start_idx = max(0, self.crue_index - visible_count + 3)

            for i, crue in enumerate(self.crues[start_idx:start_idx + visible_count]):
                row_y = list_y + 2 + i
                if row_y >= list_y + list_height:
                    break

                name = crue.get("name", "?")[:25]
                crue_type_data = self._crue_type_of(crue)
                crue_type = crue_type_data.get(
                    "label", crue.get("crue_type_slug") or "?")[:18]
                member_count = crue.get("capacity", {}).get("current_members", 0)
                is_active = crue.get("is_active", True)
                status = "Active" if is_active else "Inactive"
                icon = crue_type_data.get("icon", "👥")

                row = f"{pad(icon, 2)} {pad(name, 23)} {crue_type:<18} {member_count:>8} {status:<10}"
                row = f" {clip(row, width - 10)} "

                if start_idx + i == self.crue_index:
                    self._safe_addstr(row_y, 4, row,
                                      curses.color_pair(Colors.HIGHLIGHT))
                else:
                    self._safe_addstr(row_y, 4, row,
                                      curses.color_pair(Colors.NORMAL))

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "ENTER: View | N: New | E: Edit | X: Delete | Q: Back")

    def _draw_crue_view(self, height: int, width: int):
        """Draw crue detail view."""
        self._draw_header(width)

        if not self.current_crue:
            return

        crue = self.current_crue

        # Title
        try:
            icon = self._crue_type_of(crue).get("icon", "👥")
            self.stdscr.addstr(5, 2, f"{icon} CRUE: {crue.get('name', '?')}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Two-column layout
        col1_x = 4
        col2_x = width // 2 + 2
        col_width = width // 2 - 6

        # Column 1: Info
        y = 7
        try:
            self.stdscr.addstr(y, col1_x, "DETAILS", curses.color_pair(Colors.TITLE))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Name: {crue.get('name', '?')}",
                               curses.color_pair(Colors.STAT_VALUE))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Slug: {crue.get('slug', '?')}",
                               curses.color_pair(Colors.DIM))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Type: {self._crue_type_of(crue).get('label', crue.get('crue_type_slug') or '?')}",
                               curses.color_pair(Colors.STAT_VALUE))
            y += 1
            desc = crue.get('description', '')[:col_width - 2]
            self.stdscr.addstr(y, col1_x + 2, f"Desc: {desc}",
                               curses.color_pair(Colors.DIM))
            y += 2

            # Capacity
            capacity = crue.get("capacity", {})
            curr = capacity.get("current_members", 0)
            max_mem = capacity.get("max_members", "∞")
            self.stdscr.addstr(y, col1_x, "CAPACITY", curses.color_pair(Colors.TITLE))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Members: {curr}/{max_mem}",
                               curses.color_pair(Colors.STAT_VALUE))
        except curses.error:
            pass

        # Column 2: Members
        y = 7
        try:
            self.stdscr.addstr(y, col2_x, "MEMBERS", curses.color_pair(Colors.TITLE))
            y += 1

            # Find agents in this group
            members = self._get_crue_members(crue.get("id"))
            if members:
                for i, agent in enumerate(members[:10]):
                    ident = agent.get("identity", {})
                    cls = agent.get("classification", {})
                    arch = self.archetypes.get(cls.get("archetype_id", 1), {})
                    tag = ident.get("tag", 0)
                    name = ident.get("name", "?")[:20]
                    arch_label = arch.get("label", "?")[:15]
                    self.stdscr.addstr(y + i, col2_x + 2, f"#{tag:04d} {name} ({arch_label})",
                                       curses.color_pair(Colors.STAT_VALUE))
            else:
                self.stdscr.addstr(y, col2_x + 2, "No members assigned",
                                   curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width,
                          "E: Edit | A: Add Member | R: Remove Member | "
                          "D: Dispatch Crue | X: Delete | Q: Back")

    def _draw_crue_create(self, height: int, width: int):
        """Draw crue creation view - select crue type template."""
        self._draw_header(width)

        # Modal for crue type selection
        box_height = 16
        box_width = 65
        box_y = (height - box_height) // 2
        box_x = (width - box_width) // 2

        self._draw_box(box_y, box_x, box_height, box_width, "CREATE NEW CRUE - Select Type")

        # List crue types
        crue_type_list = sorted(self.crue_types.values(), key=lambda c: c.get("sort_order", 99))

        y = box_y + 2
        visible = min(len(crue_type_list), box_height - 4)

        for i, ct in enumerate(crue_type_list[:visible]):
            is_selected = (i == self.crue_edit_index)

            icon = ct.get("icon", "👥")
            label = ct.get("label", "?")
            desc = clip(ct.get("description", ""), box_width - 30)
            lead = f" {pad(icon, 2)} {pad(label, 20)}"

            if is_selected:
                self._safe_addstr(y + i, box_x + 2, clip(f"{lead} {desc}", box_width - 4),
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(y + i, box_x + 2, lead,
                                  curses.color_pair(Colors.STAT_VALUE))
                self._safe_addstr(y + i, box_x + 2 + display_width(lead), f" {desc}",
                                  curses.color_pair(Colors.DIM))

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Create | Q: Cancel")

    def _get_crue_members(self, crue_id: str) -> list:
        """Get agents that are members of a crue."""
        if not crue_id:
            return []

        members = []
        for agent in self.roster:
            groups = agent.get("groups", [])
            for g in groups:
                if g.get("group_id") == crue_id:
                    members.append(agent)
                    break
        return members

    def _handle_crues_input(self, key: int):
        """Handle crues list input."""
        if key == curses.KEY_UP and self.crues:
            self.crue_index = max(0, self.crue_index - 1)
        elif key == curses.KEY_DOWN and self.crues:
            self.crue_index = min(len(self.crues) - 1, self.crue_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13) and self.crues:
            self.current_crue = self.crues[self.crue_index]
            self.mode = "crue_view"
        elif key == ord('n') or key == ord('N'):
            self.crue_edit_index = 0
            self.mode = "crue_create"
        elif key == ord('e') or key == ord('E'):
            if self.crues:
                # Composed from the schema (tui/views/composer.py)
                self.current_crue = self.crues[self.crue_index]
                self._open_compose("group", str(self.current_crue["id"]), "crues")
        elif key == ord('x') or key == ord('X'):
            if self.crues:
                self._delete_crue(self.crues[self.crue_index])
        elif key == ord('q') or key == ord('Q'):
            self.mode = "menu"

    def _handle_crue_view_input(self, key: int):
        """Handle crue detail view input."""
        if key == ord('e') or key == ord('E'):
            self._open_compose("group", str(self.current_crue["id"]), "crue_view")
        elif key == ord('a') or key == ord('A'):
            # Add member - open the selection picker
            self._enter_crue_member_select("add")
        elif key == ord('r') or key == ord('R'):
            # Remove member - open the selection picker
            self._enter_crue_member_select("remove")
        elif key == ord('d') or key == ord('D'):
            # Direct dispatch - crew is this crue's members
            members = self._get_crue_members((self.current_crue or {}).get("id"))
            if not members:
                self.message = "No members to dispatch"
                self.message_color = Colors.DIM
            else:
                self.dispatch_crew_override = members
                self.dispatch_crew_label = (
                    f"crue: {self.current_crue.get('name', '?')} "
                    f"({len(members)} member(s))")
                self._enter_dispatch_mode()
        elif key == ord('x') or key == ord('X'):
            self._delete_crue(self.current_crue)
            self.mode = "crues"
        elif key == ord('q') or key == ord('Q'):
            self.mode = "crues"

    def _handle_crue_create_input(self, key: int):
        """Handle crue creation input."""
        crue_type_list = sorted(self.crue_types.values(), key=lambda c: c.get("sort_order", 99))
        type_count = len(crue_type_list)

        if key == curses.KEY_UP:
            self.crue_edit_index = (self.crue_edit_index - 1) % type_count
        elif key == curses.KEY_DOWN:
            self.crue_edit_index = (self.crue_edit_index + 1) % type_count
        elif key in (curses.KEY_ENTER, 10, 13):
            self._create_crue_from_type(crue_type_list[self.crue_edit_index])
        elif key == ord('q') or key == ord('Q'):
            self.mode = "crues"

    def _create_crue_from_type(self, crue_type: dict):
        """Create a new crue from a crue_type template."""
        import uuid
        from datetime import datetime, timezone

        crue_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Slug from the type and the id's first characters (slug pattern: no dots)
        type_slug = crue_type["slug"]
        slug = f"{type_slug}_{crue_id[:4]}"

        crue = {
            "id": crue_id,
            "slug": slug,
            "name": f"New {crue_type.get('label', 'Crue')}",
            "crue_type_slug": type_slug,
            "description": crue_type.get("mission_profile", ""),
            "parent_id": None,
            "specialization": {
                "domains": [],
                "tools": [],
                "mission_types": []
            },
            "capacity": {
                "max_members": crue_type.get("composition", {}).get("max_size", 10),
                "current_members": 0
            },
            "icon": crue_type.get("icon", "👥"),
            "is_active": True,
            "audit": {
                "created_at": now,
                "updated_at": now,
                "retired_at": None
            }
        }

        try:
            self.ds.create("group", crue)
            self._load_crues()
            self.current_crue = crue
            self._open_compose("group", crue_id, "crue_view")
            self.message = "Crue created - configure details"
            self.message_color = Colors.STAT_VALUE
        except Exception as e:
            self.message = f"Create error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    def _delete_crue(self, crue: dict):
        """Delete a crue."""
        if not crue:
            return

        try:
            self.ds.delete("group", crue["id"], hard=True)
            self._load_crues()
            self.crue_index = max(0, self.crue_index - 1)
            self.current_crue = None
            self.message = "Crue deleted"
            self.message_color = Colors.HEALTH
        except Exception as e:
            self.message = f"Delete error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    def _enter_crue_member_select(self, intent: str):
        """Open the member picker for add/remove on the current crue."""
        if not self.current_crue:
            return
        crue_id = self.current_crue["id"]

        if intent == "add":
            candidates = [
                a for a in self.roster
                if not any(g.get("group_id") == crue_id for g in a.get("groups", []))
            ]
            empty_msg = "All agents already assigned"
        else:
            candidates = self._get_crue_members(crue_id)
            empty_msg = "No members to remove"

        if not candidates:
            self.message = empty_msg
            self.message_color = Colors.DIM
            return

        self.crue_member_intent = intent
        self.crue_member_candidates = candidates
        self.crue_member_index = 0
        self.mode = "crue_member_select"

    def _draw_crue_member_select(self, height: int, width: int):
        """Draw the crue member picker modal."""
        self._draw_header(width)

        crue_name = (self.current_crue or {}).get("name", "?")
        verb = "ADD TO" if self.crue_member_intent == "add" else "REMOVE FROM"

        box_width = min(64, width - 4)
        box_height = min(len(self.crue_member_candidates) + 4, height - 10)
        box_y = 6
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width,
                       f"{verb}: {crue_name}")

        visible = box_height - 4
        start = max(0, self.crue_member_index - visible + 1)
        for i, agent in enumerate(
                self.crue_member_candidates[start:start + visible]):
            ident = agent.get("identity", {})
            arch = self.archetypes.get(
                agent.get("classification", {}).get("archetype_id"), {})
            tag = ident.get("tag", 0)
            row = (f" #{tag:04d} {pad(ident.get('name', '?'), 24)} "
                   f"{pad(arch.get('label', '?'), 18)}")
            color = (Colors.HIGHLIGHT if start + i == self.crue_member_index
                     else Colors.NORMAL)
            self._safe_addstr(box_y + 2 + i, box_x + 2,
                              clip(row, box_width - 4), curses.color_pair(color))

        self._draw_message(box_y + box_height + 1, width)
        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Confirm | Q: Cancel")

    def _handle_crue_member_select_input(self, key: int):
        """Handle member picker input."""
        count = len(self.crue_member_candidates)
        if not count:
            self.mode = "crue_view"
            return

        if key == curses.KEY_UP:
            self.crue_member_index = (self.crue_member_index - 1) % count
        elif key == curses.KEY_DOWN:
            self.crue_member_index = (self.crue_member_index + 1) % count
        elif key in (curses.KEY_ENTER, 10, 13):
            agent = self.crue_member_candidates[self.crue_member_index]
            self._commit_crue_member(agent, self.crue_member_intent)
            self.mode = "crue_view"
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "crue_view"

    def _commit_crue_member(self, agent: dict, intent: str):
        """Apply an add/remove membership change: dual write (agent.groups
        + crue capacity counter), then reload the roster."""
        if not self.current_crue:
            return
        crue_id = self.current_crue["id"]
        from datetime import datetime, timezone

        groups = agent.get("groups", [])
        if intent == "add":
            groups.append({
                "group_id": crue_id,
                "role": "member",
                "joined_at": datetime.now(timezone.utc).isoformat()
            })
            agent["groups"] = groups
            delta, verb = 1, "Added"
        else:
            agent["groups"] = [g for g in groups if g.get("group_id") != crue_id]
            delta, verb = -1, "Removed"

        try:
            self.ds.update("agent", agent["id"], agent)
            capacity = self.current_crue.get("capacity", {})
            capacity["current_members"] = max(
                0, capacity.get("current_members", 0) + delta)
            self.current_crue["capacity"] = capacity
            self.ds.update("group", crue_id, self.current_crue)
            self._load_roster()
            self.message = f"{verb} {agent['identity']['name']}"
            self.message_color = Colors.STAT_VALUE
        except Exception as e:
            self.message = f"Error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

