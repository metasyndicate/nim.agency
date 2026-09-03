"""
Setup - datastore init, operator identity, archive/reset.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from lib.data import AgentGenerator
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class SetupMixin:
    """datastore init, operator identity, archive/reset."""

    def _draw_setup(self, height: int, width: int):
        """Draw setup/configuration view."""
        self._draw_header(width)

        # Setup submenu
        setup_items = [
            ("Organization", "Set organization name"),
            ("Unit", "Set operational department"),
            ("Substation", "Set deployment codename"),
            ("Operator", "View/set operator info"),
            ("Archive & Reset", "Archive agents and reseed"),
        ]

        # Left panel: submenu
        menu_width = 28
        menu_height = len(setup_items) + 4
        menu_y = 6
        menu_x = 2

        self._draw_box(menu_y, menu_x, menu_height, menu_width, "SETUP")

        for i, (label, desc) in enumerate(setup_items):
            y = menu_y + 2 + i
            color = Colors.HIGHLIGHT if i == self.setup_index else Colors.NORMAL
            self._safe_addstr(y, menu_x + 2, f" {label:<22} ",
                              curses.color_pair(color))

        # Right panel: details
        detail_x = menu_x + menu_width + 2
        detail_width = width - detail_x - 2
        detail_height = height - 10
        detail_y = 6

        self._draw_box(detail_y, detail_x, detail_height, detail_width, setup_items[self.setup_index][0].upper())

        # Draw content based on selected item
        content_y = detail_y + 2
        content_x = detail_x + 3

        if self.setup_index == 0:  # Organization
            self._draw_setup_field(content_y, content_x, detail_width - 6,
                                   "Organization Name:",
                                   self.config.get("organization", {}).get("name") or "(not set)")
        elif self.setup_index == 1:  # Unit
            self._draw_setup_field(content_y, content_x, detail_width - 6,
                                   "Unit Name:",
                                   self.config.get("unit", {}).get("name") or "(not set)")
        elif self.setup_index == 2:  # Substation
            self._draw_setup_field(content_y, content_x, detail_width - 6,
                                   "Substation Name:",
                                   self.config.get("substation", {}).get("name") or "(not set)")
        elif self.setup_index == 3:  # Operator
            self._draw_setup_operator(content_y, content_x, detail_width - 6)
        elif self.setup_index == 4:  # Archive & Reset
            self._draw_setup_data(content_y, content_x, detail_width - 6)

        # Show status message if any
        self._draw_message(height - 4, width)

        footer_text = "UP/DOWN: Navigate | ENTER: Edit/Action | Q: Back"
        if self.editing:
            footer_text = "Type to edit | ENTER: Save | ESC: Cancel"
        self._draw_footer(height, width, footer_text)

    def _draw_setup_field(self, y: int, x: int, width: int, label: str, value: str):
        """Draw a single setup field."""
        self.stdscr.addstr(y, x, label, curses.color_pair(Colors.STAT_LABEL))
        y += 1

        if self.editing and self.setup_field_index == 0:
            # Show edit mode
            display = self.edit_buffer + "_"
            self.stdscr.addstr(y, x, f"[{display:<{width-4}}]",
                               curses.color_pair(Colors.HIGHLIGHT))
        else:
            self.stdscr.addstr(y, x, value, curses.color_pair(Colors.STAT_VALUE))

        y += 2
        self.stdscr.addstr(y, x, "Press ENTER to edit", curses.color_pair(Colors.DIM))

    def _draw_setup_operator(self, y: int, x: int, width: int):
        """Draw operator info panel."""
        self.stdscr.addstr(y, x, "System Detected:", curses.color_pair(Colors.STAT_LABEL))
        y += 1

        sys_op = self.system_operator
        self.stdscr.addstr(y, x + 2, f"Username: ", curses.color_pair(Colors.DIM))
        self.stdscr.addstr(sys_op["username"], curses.color_pair(Colors.STAT_VALUE))
        y += 1

        if sys_op.get("name"):
            self.stdscr.addstr(y, x + 2, f"Name: ", curses.color_pair(Colors.DIM))
            self.stdscr.addstr(sys_op["name"], curses.color_pair(Colors.STAT_VALUE))
            y += 1

        if sys_op.get("email"):
            self.stdscr.addstr(y, x + 2, f"Email: ", curses.color_pair(Colors.DIM))
            self.stdscr.addstr(sys_op["email"], curses.color_pair(Colors.STAT_VALUE))
            y += 1

        self.stdscr.addstr(y, x + 2, f"Timezone: ", curses.color_pair(Colors.DIM))
        self.stdscr.addstr(sys_op["timezone"], curses.color_pair(Colors.STAT_VALUE))
        y += 2

        # Config override
        self.stdscr.addstr(y, x, "Display Name Override:", curses.color_pair(Colors.STAT_LABEL))
        y += 1
        override = self.config.get("operator", {}).get("display_name")
        if self.editing:
            display = self.edit_buffer + "_"
            self.stdscr.addstr(y, x, f"[{display:<{width-4}}]",
                               curses.color_pair(Colors.HIGHLIGHT))
        else:
            self.stdscr.addstr(y, x, override or "(using system username)",
                               curses.color_pair(Colors.STAT_VALUE if override else Colors.DIM))

        y += 2
        role = self.config.get("operator", {}).get("role", "operator")
        self.stdscr.addstr(y, x, f"Role: ", curses.color_pair(Colors.STAT_LABEL))
        self.stdscr.addstr(role, curses.color_pair(Colors.STAT_VALUE))

    def _refresh_setup_stats(self):
        """Cache datastore counts shown by the setup panel - the draw loop
        runs ~10x/sec and must not hit the filesystem every frame."""
        try:
            self._setup_stats = (self.ds.count("agent"),
                                 self.ds.list_archives("agent"))
        except Exception:
            self._setup_stats = (len(self.roster), [])

    def _draw_setup_data(self, y: int, x: int, width: int):
        """Draw data management panel."""
        if not hasattr(self, "_setup_stats"):
            self._refresh_setup_stats()
        agent_count, archives = self._setup_stats

        self.stdscr.addstr(y, x, f"Current Agents: ", curses.color_pair(Colors.STAT_LABEL))
        self.stdscr.addstr(str(agent_count), curses.color_pair(Colors.STAT_VALUE))
        y += 2
        self.stdscr.addstr(y, x, f"Archives: ", curses.color_pair(Colors.STAT_LABEL))
        self.stdscr.addstr(str(len(archives)), curses.color_pair(Colors.STAT_VALUE))
        y += 1

        if archives:
            for arch in archives[:3]:  # Show last 3
                self.stdscr.addstr(y, x + 2, f"- {arch['name']} ({arch['count']} agents)",
                                   curses.color_pair(Colors.DIM))
                y += 1
            if len(archives) > 3:
                self.stdscr.addstr(y, x + 2, f"  ... and {len(archives) - 3} more",
                                   curses.color_pair(Colors.DIM))
                y += 1

        y += 1

        # Actions
        actions = [
            ("[A] Archive & Reset", "Archive current agents, then reseed"),
            ("[R] Reset Only", "Delete all agents (no archive)"),
            ("[G] Generate Agents", "Add 5 random agents to roster"),
        ]

        self.stdscr.addstr(y, x, "Actions:", curses.color_pair(Colors.STAT_LABEL))
        y += 1

        for label, desc in actions:
            self.stdscr.addstr(y, x + 2, label, curses.color_pair(Colors.TITLE))
            self.stdscr.addstr(f" - {desc}", curses.color_pair(Colors.DIM))
            y += 1

    def _handle_setup_input(self, key: int):
        """Handle setup view input."""
        setup_count = 5

        if self.editing:
            # Text input mode
            if key == 27:  # ESC
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                self._save_setup_field()
                self.editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:  # Printable ASCII
                self.edit_buffer += chr(key)
        else:
            # Navigation mode
            if key == curses.KEY_UP:
                self.setup_index = (self.setup_index - 1) % setup_count
            elif key == curses.KEY_DOWN:
                self.setup_index = (self.setup_index + 1) % setup_count
            elif key in (curses.KEY_ENTER, 10, 13):
                self._setup_action()
            elif key == ord('q') or key == ord('Q') or key == 27:
                self.mode = "menu"
            # Data management shortcuts (when on index 4)
            elif self.setup_index == 4:
                if key == ord('a') or key == ord('A'):
                    self._archive_and_reset()
                elif key == ord('r') or key == ord('R'):
                    self._reset_only()
                elif key == ord('g') or key == ord('G'):
                    self._seed_agents()

    def _setup_action(self):
        """Handle enter key on setup item."""
        if self.setup_index in (0, 1, 2):  # Org, Unit, Substation
            # Start editing
            field_map = {0: "organization", 1: "unit", 2: "substation"}
            field = field_map[self.setup_index]
            current = self.config.get(field, {}).get("name") or ""
            self.edit_buffer = current
            self.editing = True
        elif self.setup_index == 3:  # Operator
            current = self.config.get("operator", {}).get("display_name") or ""
            self.edit_buffer = current
            self.editing = True
        # Index 4 uses letter shortcuts

    def _save_setup_field(self):
        """Save the current edit to config."""
        field_map = {0: "organization", 1: "unit", 2: "substation", 3: "operator"}
        field = field_map.get(self.setup_index)

        if field:
            if field == "operator":
                if field not in self.config:
                    self.config[field] = {}
                self.config[field]["display_name"] = self.edit_buffer if self.edit_buffer else None
            else:
                if field not in self.config:
                    self.config[field] = {}
                self.config[field]["name"] = self.edit_buffer if self.edit_buffer else None

            self.ds.save_config(self.config)
            self.config = self.ds.get_config()  # Reload
            self.message = f"{field.title()} updated"
            self.message_color = Colors.STAT_VALUE

        self.edit_buffer = ""

    def _archive_and_reset(self):
        """Archive current agents and reseed."""
        archive_path, count = self.ds.reset_collection("agent", archive_first=True)
        if count > 0:
            # Reseed with new agents
            gen = AgentGenerator(datastore=self.ds)
            for _ in range(5):
                gen.create_and_save(status_id=2)
            self._load_roster()
            self.message = f"Archived {count} agents, seeded 5 new"
            self.message_color = Colors.STAT_VALUE
        else:
            self.message = "No agents to archive"
            self.message_color = Colors.DIM
        self._refresh_setup_stats()

    def _reset_only(self):
        """Delete all agents without archiving."""
        _, count = self.ds.reset_collection("agent", archive_first=False)
        self._load_roster()
        self._refresh_setup_stats()
        if count > 0:
            self.message = f"Deleted {count} agents"
            self.message_color = Colors.HEALTH
        else:
            self.message = "No agents to delete"
            self.message_color = Colors.DIM

