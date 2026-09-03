"""
Roster - agent list, filter, selection, seeding.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from lib.data import AgentGenerator
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class RosterMixin:
    """agent list, filter, selection, seeding."""

    def _draw_roster(self, height: int, width: int):
        """Draw agent roster view."""
        self._draw_header(width)

        # Apply filter if set
        display_roster = self._get_filtered_roster()

        if not display_roster:
            msg = "No agents match filter." if (self.roster_filter_archetype or self.roster_filter_focus) else "No agents registered."
            self.stdscr.addstr(10, (width - len(msg)) // 2, msg,
                               curses.color_pair(Colors.DIM))
            self._draw_footer(height, width, "N: New Agent | G: Generate | F: Filter | Q: Back")
            return

        # Roster list
        list_height = height - 12
        list_width = width - 4
        list_y = 6
        list_x = 2

        # Title with filter indicator
        filter_str = ""
        if self.roster_filter_archetype:
            arch = self.archetypes.get(self.roster_filter_archetype, {})
            filter_str = f" [Filter: {arch.get('label', '?')}]"
        elif self.roster_filter_focus:
            focus = self.focuses.get(self.roster_filter_focus, {})
            filter_str = f" [Filter: {focus.get('label', '?')}]"

        # Show shortlist count if any selected
        shortlist_str = f" | {len(self.selected_agents)} in shortlist" if self.selected_agents else ""
        title = f"AGENT ROSTER ({len(display_roster)}/{len(self.roster)} agents){filter_str}{shortlist_str}"
        self._draw_box(list_y, list_x, list_height, list_width, title)

        # Header row - now with ROLE (archetype) and FOCUS instead of DOGTAG
        header = f"{'TAG':>5} {'NAME':<22} {'ROLE':<18} {'FOCUS':<12} {'RANK':<10} {'ST':<4}"
        try:
            self.stdscr.addstr(list_y + 1, list_x + 2, header[:list_width-4],
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(list_y + 2, list_x + 2, "-" * (list_width - 4),
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Agent rows
        visible_count = list_height - 5
        start_idx = max(0, self.roster_index - visible_count + 3)

        for i, agent in enumerate(display_roster[start_idx:start_idx + visible_count]):
            row_y = list_y + 3 + i
            if row_y >= list_y + list_height - 1:
                break

            ident = agent["identity"]
            cls = agent["classification"]

            rank = self.ranks.get(cls["rank_id"], {"label": "?", "icon": ""})
            status = self.statuses.get(cls["status_id"], {"label": "?", "icon": "?"})
            archetype = self.archetypes.get(cls.get("archetype_id", 1), {"label": "Unknown", "icon": "?"})
            focus = self.focuses.get(cls.get("focus_id", 1), {"label": "?", "abbrev": "?"})

            tag = ident.get('tag', 0)
            tag_str = f"{tag:04d}" if isinstance(tag, int) else "????"

            # Build row with archetype icon + label, focus abbrev, rank icon
            # (pad() is cell-accurate - wide icons no longer drift columns)
            arch_str = pad(f"{archetype.get('icon', '')} {archetype.get('label', '?')}", 18)
            focus_str = pad(focus.get('abbrev', focus.get('label', '?')[:3]), 12)
            rank_str = pad(f"{rank.get('icon', '')} {rank.get('label', '')}", 10)
            status_icon = pad(status.get('icon', '?'), 4)

            # Mark selected agents with asterisk
            selected_marker = "*" if agent["id"] in self.selected_agents else " "
            row = f"{selected_marker}{tag_str:>4} {pad(ident['name'], 22)} {arch_str} {focus_str} {rank_str} {status_icon}"
            row = f" {clip(row, list_width - 6)} "

            is_selected = start_idx + i == self.roster_index
            is_in_shortlist = agent["id"] in self.selected_agents
            if is_selected:
                self._safe_addstr(row_y, list_x + 2, row,
                                  curses.color_pair(Colors.HIGHLIGHT))
            elif is_in_shortlist:
                # Gold tint for shortlisted agents
                self._safe_addstr(row_y, list_x + 2, row,
                                  curses.color_pair(Colors.GOLD))
            else:
                self._safe_addstr(row_y, list_x + 2, row)

        # Dynamic footer based on shortlist state
        if self.selected_agents:
            footer = f"ENTER: View | SPACE: +/- Select | C: Clear ({len(self.selected_agents)}) | B: Bulk | N/G/F | Q: Back"
        else:
            footer = "ENTER: View | SPACE: Select | N: New | G: Generate | F: Filter | Q: Back"
        self._draw_footer(height, width, footer)

    def _draw_roster_filter(self, height: int, width: int):
        """Draw roster filter selection overlay."""
        self._draw_header(width)

        # Filter modal
        box_height = 16
        box_width = 60
        box_y = (height - box_height) // 2
        box_x = (width - box_width) // 2

        self._draw_box(box_y, box_x, box_height, box_width, "FILTER ROSTER BY ROLE")

        # Build list of archetypes for selection
        archetype_list = sorted(self.archetypes.values(), key=lambda a: a.get("sort_order", 99))

        # Add "All" option at the top
        options = [{"id": None, "label": "All Roles (clear filter)", "icon": "✱"}]
        options.extend(archetype_list)

        y = box_y + 2
        visible = min(len(options), box_height - 4)

        for i, opt in enumerate(options[:visible]):
            is_selected = (i == self.filter_index)
            is_current = (opt.get("id") == self.roster_filter_archetype)

            icon = opt.get("icon", "?")
            label = opt.get("label", "?")

            # Mark current filter
            marker = " *" if is_current else "  "
            row = f" {pad(icon, 2)}{pad(label, 45)}{marker}"

            if is_selected:
                self._safe_addstr(y + i, box_x + 2, row,
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                color = Colors.STAT_VALUE if is_current else Colors.NORMAL
                self._safe_addstr(y + i, box_x + 2, row, curses.color_pair(color))

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Apply | Q: Cancel")

    def _handle_roster_input(self, key: int):
        """Handle roster input."""
        display_roster = self._get_filtered_roster()

        if key == curses.KEY_UP and display_roster:
            self.roster_index = max(0, self.roster_index - 1)
        elif key == curses.KEY_DOWN and display_roster:
            self.roster_index = min(len(display_roster) - 1, self.roster_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13) and display_roster:
            self.current_agent = display_roster[self.roster_index]
            self.mode = "view"
        elif key == ord('n') or key == ord('N'):
            self.mode = "create"
        elif key == ord('g') or key == ord('G'):
            self._generate_agent()
        elif key == ord('f') or key == ord('F'):
            # Enter filter mode
            self.filter_mode = True
            self.filter_index = 0
            self.mode = "roster_filter"
        elif key == ord('/'):
            # Clear filter
            self.roster_filter_archetype = None
            self.roster_filter_focus = None
            self.roster_index = 0
            self.message = "Filter cleared"
            self.message_color = Colors.DIM
        elif key == ord('c') or key == ord('C'):
            # Clear shortlist
            if self.selected_agents:
                count = len(self.selected_agents)
                self.selected_agents = []
                self.message = f"Cleared {count} agents from shortlist"
                self.message_color = Colors.DIM
        elif key == ord('b') or key == ord('B'):
            # Bulk operations on shortlist
            if self.selected_agents:
                self._show_bulk_ops_menu()
            else:
                self.message = "No agents in shortlist. Select agents with 'C' in dossier view."
                self.message_color = Colors.DIM
        elif key == ord(' '):
            # Space to toggle current agent in shortlist (quick select)
            if display_roster:
                agent = display_roster[self.roster_index]
                if agent["id"] in self.selected_agents:
                    self.selected_agents.remove(agent["id"])
                else:
                    self.selected_agents.append(agent["id"])
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "menu"

    def _show_bulk_ops_menu(self):
        """Show bulk operations menu for selected agents."""
        count = len(self.selected_agents)
        # For now, just show what's available
        self.message = f"Bulk ops for {count} agents: Coming soon (assign to crue, deploy, export)"
        self.message_color = Colors.XP

    def _handle_roster_filter_input(self, key: int):
        """Handle roster filter selection input."""
        # Options: "All" + archetypes
        archetype_list = sorted(self.archetypes.values(), key=lambda a: a.get("sort_order", 99))
        option_count = len(archetype_list) + 1  # +1 for "All"

        if key == curses.KEY_UP:
            self.filter_index = (self.filter_index - 1) % option_count
        elif key == curses.KEY_DOWN:
            self.filter_index = (self.filter_index + 1) % option_count
        elif key in (curses.KEY_ENTER, 10, 13):
            # Apply filter
            if self.filter_index == 0:
                # "All" - clear filter
                self.roster_filter_archetype = None
                self.message = "Filter cleared"
            else:
                # Select archetype
                arch = archetype_list[self.filter_index - 1]
                self.roster_filter_archetype = arch.get("id")
                self.message = f"Showing: {arch.get('label', '?')}"
                self.message_color = Colors.STAT_VALUE

            self.roster_index = 0
            self.mode = "roster"
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "roster"

    def _toggle_agent_selection(self):
        """Toggle current agent in/out of shortlist."""
        if not self.current_agent:
            return

        agent_id = self.current_agent["id"]
        agent_name = self.current_agent["identity"]["codename"]

        if agent_id in self.selected_agents:
            self.selected_agents.remove(agent_id)
            self.message = f"{agent_name} removed from shortlist ({len(self.selected_agents)} selected)"
            self.message_color = Colors.DIM
        else:
            self.selected_agents.append(agent_id)
            self.message = f"{agent_name} added to shortlist ({len(self.selected_agents)} selected)"
            self.message_color = Colors.GOLD

    def _seed_agents(self):
        """Generate sample agents."""
        gen = AgentGenerator(datastore=self.ds)
        for _ in range(5):
            gen.create_and_save(status_id=2)
        self._load_roster()
        self._refresh_setup_stats()
        self.message = "Generated 5 new agents"
        self.message_color = Colors.STAT_VALUE

    def _generate_agent(self):
        """Generate and save a random agent."""
        agent = self.gen.create_and_save(status_id=2)
        self._load_roster()
        # Find and select the new agent
        for i, a in enumerate(self.roster):
            if a["id"] == agent["id"]:
                self.roster_index = i
                break
        self.current_agent = agent
        self.mode = "view"


    # -------------------------------------------------------------------------
    # Substations Mode Methods
    # -------------------------------------------------------------------------

