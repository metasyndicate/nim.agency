#!/usr/bin/env python3
"""
Agent Builder TUI - core shell.

Owns the event loop, mode dispatch, menu, and shared state; every screen
lives in tui/views/*.py as a mixin over this class's state (move-only
split of the former monolith). The sys.path bootstrap lives HERE and
only here - view modules rely on it.
"""

import curses
import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Add lib to path (canonical bootstrap - do not copy into view modules)
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.data import AgentGenerator, DataStore
from tui.chrome import (
    MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH, ChromeMixin, Colors,
)
from tui.mugshots import MugshotLoader
from tui.textkit import clip, display_width, pad
from tui.views.roster import RosterMixin
from tui.views.dossier import DossierMixin
from tui.views.progression import ProgressionMixin
from tui.views.create import CreateMixin
from tui.views.agent_edit import AgentEditMixin
from tui.views.setup import SetupMixin
from tui.views.substations import SubstationsMixin
from tui.views.crues import CruesMixin
from tui.views.dispatch import DispatchMixin
from tui.views.viewer import ViewerMixin
from tui.views.missions import MissionsMixin
from tui.views.stats import StatsMixin
from tui.views.foundry import FoundryMixin
from tui.views.composer import ComposerMixin
from tui.views.substations import HAS_REMOTE, REMOTE_ERROR


class AgentBuilder(ChromeMixin, RosterMixin, DossierMixin, ProgressionMixin, CreateMixin, AgentEditMixin, SetupMixin, SubstationsMixin, CruesMixin, DispatchMixin, ViewerMixin, MissionsMixin, StatsMixin, FoundryMixin, ComposerMixin):
    """
    Main TUI application for agent creation and management.

    Features:
    - RPG-style character sheet view
    - Skill point allocation
    - Random generation
    - Agent roster browsing
    """

    # Box drawing characters
    BOX_CHARS = {
        "single": {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"},
        "double": {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "=", "v": "|"},
        "unicode": {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}
    }


    def __init__(self, datastore: Optional[DataStore] = None, generator: Optional[AgentGenerator] = None):
        self.ds = datastore or DataStore()
        self.gen = generator or AgentGenerator(datastore=self.ds)
        self.stdscr = None
        self.current_agent = None
        self.roster = []
        self.roster_index = 0
        self.mode = "menu"  # menu, roster, roster_filter, create, view, setup, shop, skills, profile, substations, crues, ...
        self.menu_index = 0
        self.filter_index = 0  # Filter selection index
        self.setup_index = 0  # Setup submenu index
        self.setup_field_index = 0  # Field being edited in setup
        self.editing = False  # Text input mode
        self.edit_buffer = ""  # Current edit text
        self.message = ""  # Status message to display
        self.message_color = Colors.STAT_VALUE  # Message color
        self.shop_index = 0  # Tool shop selection
        self.skill_index = 0  # Capability upgrade selection
        self.profile_scroll = 0  # Profile viewer scroll position
        self.profile_lines = []  # Cached profile content lines
        self.reroll_index = 0  # Reroll menu selection

        # Substations state
        self.substations = []
        self.substation_index = 0
        self.current_substation = None
        self.ssh_keys = []
        self.ssh_key_index = 0
        self.vault_unlocked = False
        self.vault = None
        self.substation_perm_index = 0  # Permission being toggled

        # Crues state
        self.crues = []  # Loaded groups
        self.crue_index = 0
        self.current_crue = None
        self.crue_edit_index = 0
        self.crue_member_index = 0  # For member selection

        # Agent selection/shortlist state
        self.selected_agents = []  # Agent IDs in shortlist for bulk ops
        self.deploy_target_index = 0  # Target substation for deploy

        # Stats screen state
        self.stats_data = None

        # New-agent creation state
        self.create_candidate = None
        self.create_edit_field = None

        # Dojo (skill training) state
        self.dojo_index = 0

        # Crue member picker state
        self.crue_member_intent = "add"
        self.crue_member_candidates = []

        # Dispatch state
        self.dispatch_field_index = 0
        self.dispatch_form = {"task": "", "skill": "", "target": ".", "count": "1"}
        self.dispatch_preset = None        # applied task_preset entry
        self.dispatch_brief = None
        self.dispatch_events = []
        self.dispatch_thread = None
        self.dispatch_running = False
        self.dispatch_result = None
        self.dispatch_crew_override = []   # direct-dispatch crew (dossier/crue)
        self.dispatch_crew_label = ""

        # Generic text viewer state
        self.viewer_title = ""
        self.viewer_lines = []
        self.viewer_scroll = 0
        self.viewer_return_mode = "menu"

        # Missions browser state
        self.mission_list = []
        self.mission_index = 0

        # Agent edit form state
        self.agent_edit_index = 0
        self.agent_edit_form = {}

        # Type foundry state
        self.foundry_table = None
        self.foundry_table_index = 0
        self.foundry_entries = []
        self.foundry_index = 0
        self.foundry_form = {}
        self.foundry_field_index = 0
        self.foundry_entry_id = None   # None = creating new

        # Composer state (tui/views/composer.py)
        self.compose_sm = None
        self.compose_entity = None
        self.compose_entity_rows = []
        self.compose_entity_index = 0
        self.compose_records = []
        self.compose_record_index = 0
        self.compose_delete_armed = None
        self.compose_draft = None
        self.compose_return = "menu"
        self.compose_row_index = 0
        self.compose_scroll = 0
        self.compose_discard_armed = False
        self.compose_pick = None

        # Load reference data
        self.ranks = {r["id"]: r for r in self.ds.load_reference("rank")}
        self.statuses = {s["id"]: s for s in self.ds.load_reference("status")}
        self.classes = {c["id"]: c for c in self.ds.load_reference("class")}
        self.skill_domains = {d["id"]: d for d in self.ds.load_reference("skill_domain")}
        self.qualifiers = {q["id"]: q for q in self.ds.load_reference("qualifier")}
        self.focuses = {f["id"]: f for f in self.ds.load_reference("focus")}
        self.ops_domains = {d["id"]: d for d in self.ds.load_reference("ops_domain")}
        self.archetypes = {a["id"]: a for a in self.ds.load_reference("archetype")}
        self.crue_types = {c["id"]: c for c in self.ds.load_reference("crue_type")}
        self.capability_refs = {c["id"]: c for c in self.ds.load_reference("capability")}
        self.tool_refs = {t["id"]: t for t in self.ds.load_reference("tool")}
        self.config = self.ds.get_config()

        # Roster filter state
        self.roster_filter_archetype = None  # Filter by archetype_id
        self.roster_filter_focus = None      # Filter by focus_id
        self.roster_filtered = []            # Filtered roster cache
        self.filter_mode = False             # Filter selection mode

        # Derive operator info from system
        self.system_operator = self._get_system_operator()

    def run(self):
        """Main entry point - initialize curses and run event loop."""
        try:
            curses.wrapper(self._main)
        except curses.error as e:
            # Provide helpful error message for terminal size issues
            import shutil
            size = shutil.get_terminal_size((80, 24))
            print(f"\033[91mFATAL: Terminal too small for TUI\033[0m")
            print(f"  Current size:  {size.columns}x{size.lines}")
            print(f"  Required size: {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}")
            print(f"\nResize your terminal window and try again.")
            raise SystemExit(1)

    def _main(self, stdscr):
        """Curses main loop."""
        self.stdscr = stdscr
        self._init_colors()

        curses.curs_set(0)  # Hide cursor
        stdscr.keypad(True)
        stdscr.timeout(100)  # 100ms timeout for getch

        self._load_roster()

        while True:
            self._draw()
            key = stdscr.getch()

            if key in (ord('q'), ord('Q')) and not self.editing:
                if self.mode == "menu":
                    break
                else:
                    self._handle_input(key)
            elif key == curses.KEY_RESIZE:
                curses.update_lines_cols()
                stdscr.erase()
            else:
                self._handle_input(key)

    def _get_system_operator(self) -> dict:
        """Derive operator info from system/environment."""
        import time
        username = os.environ.get("USER", os.environ.get("USERNAME", "operator"))
        # Try to get git user info
        git_name = None
        git_email = None
        try:
            import subprocess
            result = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
            if result.returncode == 0:
                git_name = result.stdout.strip()
            result = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True)
            if result.returncode == 0:
                git_email = result.stdout.strip()
        except Exception:
            pass

        return {
            "username": username,
            "name": git_name,
            "email": git_email,
            "timezone": time.tzname[0] if time.tzname else "UTC"
        }

    def _load_roster(self):
        """Load agent roster from datastore."""
        results = self.ds.query("agent", sort_key="identity.codename")
        self.roster = results.data

    def _draw(self):
        """Draw current view."""
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        # Check minimum terminal size
        if width < MIN_TERMINAL_WIDTH or height < MIN_TERMINAL_HEIGHT:
            self._draw_size_error(height, width)
            self.stdscr.refresh()
            return

        if self.mode == "menu":
            self._draw_menu(height, width)
        elif self.mode == "roster":
            self._draw_roster(height, width)
        elif self.mode == "roster_filter":
            self._draw_roster_filter(height, width)
        elif self.mode == "view":
            self._draw_agent_view(height, width)
        elif self.mode == "create":
            self._draw_create(height, width)
        elif self.mode == "setup":
            self._draw_setup(height, width)
        elif self.mode == "shop":
            self._draw_shop(height, width)
        elif self.mode == "skills":
            self._draw_skills(height, width)
        elif self.mode == "profile":
            self._draw_profile_viewer(height, width)
        elif self.mode == "reroll":
            self._draw_reroll(height, width)
        elif self.mode == "substations":
            self._draw_substations(height, width)
        elif self.mode == "ssh_keys":
            self._draw_ssh_keys(height, width)
        elif self.mode == "vault_unlock":
            self._draw_vault_unlock(height, width)
        elif self.mode == "substation_view":
            self._draw_substation_view(height, width)
        elif self.mode == "substation_perms":
            self._draw_substation_perms(height, width)
        elif self.mode == "crues":
            self._draw_crues(height, width)
        elif self.mode == "crue_view":
            self._draw_crue_view(height, width)
        elif self.mode == "crue_create":
            self._draw_crue_create(height, width)
        elif self.mode == "crue_member_select":
            self._draw_crue_member_select(height, width)
        elif self.mode == "stats":
            self._draw_stats(height, width)
        elif self.mode == "dojo":
            self._draw_dojo(height, width)
        elif self.mode == "dispatch":
            self._draw_dispatch_form(height, width)
        elif self.mode == "dispatch_brief":
            self._draw_dispatch_brief(height, width)
        elif self.mode == "dispatch_run":
            self._draw_dispatch_run(height, width)
        elif self.mode == "viewer":
            self._draw_viewer(height, width)
        elif self.mode == "missions":
            self._draw_missions(height, width)
        elif self.mode == "agent_edit":
            self._draw_agent_edit(height, width)
        elif self.mode == "foundry":
            self._draw_foundry(height, width)
        elif self.mode == "foundry_list":
            self._draw_foundry_list(height, width)
        elif self.mode == "foundry_form":
            self._draw_foundry_form(height, width)
        elif self.mode == "compose_entities":
            self._draw_compose_entities(height, width)
        elif self.mode == "compose_records":
            self._draw_compose_records(height, width)
        elif self.mode == "compose_form":
            self._draw_compose_form(height, width)
        elif self.mode == "compose_pick":
            self._draw_compose_pick(height, width)

        self.stdscr.refresh()

    def _draw_menu(self, height: int, width: int):
        """Draw main menu."""
        # Header
        self._draw_header(width)

        # Menu box
        menu_items = [
            ("R", "Agent Roster", "Browse registered agents"),
            ("N", "New Agent", "Create a new agent"),
            ("G", "Generate Random", "Generate random agent"),
            ("D", "Dispatch", "Send agents on a mission"),
            ("M", "Missions", "Browse mission history and reports"),
            ("X", "Substations", "Manage remote substations"),
            ("C", "Crues", "Manage teams and groups"),
            ("T", "Statistics", "View agency statistics"),
            ("F", "Type Foundry", "Define specialties, foci, tools, crue types"),
            ("P", "Composer", "Compose any record from its schema"),
            ("S", "Setup", "Configuration and data management"),
            ("Q", "Quit", "Exit the application")
        ]

        box_height = len(menu_items) + 4
        box_width = 50
        box_y = 6
        box_x = (width - box_width) // 2

        self._draw_box(box_y, box_x, box_height, box_width, "MAIN MENU")

        for i, (key, label, desc) in enumerate(menu_items):
            y = box_y + 2 + i
            # Mark substations as unavailable if remote module missing
            is_unavailable = key == "X" and not HAS_REMOTE
            display_label = f"{label} (N/A)" if is_unavailable else label

            if i == self.menu_index:
                self._safe_addstr(y, box_x + 2, f" [{key}] {display_label:<20} ",
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                color = Colors.DIM if is_unavailable else Colors.NORMAL
                self._safe_addstr(y, box_x + 2, f" [{key}] {display_label:<20} ",
                                  curses.color_pair(color))

        # Show status message if any
        self._draw_message(box_y + box_height + 2, width)

        # Footer
        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Select | Q: Quit")

    def _get_filtered_roster(self) -> list:
        """Get roster filtered by current filter settings."""
        if not self.roster_filter_archetype and not self.roster_filter_focus:
            return self.roster

        filtered = []
        for agent in self.roster:
            cls = agent.get("classification", {})
            if self.roster_filter_archetype:
                if cls.get("archetype_id") != self.roster_filter_archetype:
                    continue
            if self.roster_filter_focus:
                if cls.get("focus_id") != self.roster_filter_focus:
                    continue
            filtered.append(agent)
        return filtered

    def _handle_input(self, key: int):
        """Handle keyboard input based on current mode."""
        # Clear message on any input
        self.message = ""

        if self.mode == "menu":
            self._handle_menu_input(key)
        elif self.mode == "roster":
            self._handle_roster_input(key)
        elif self.mode == "roster_filter":
            self._handle_roster_filter_input(key)
        elif self.mode == "view":
            self._handle_view_input(key)
        elif self.mode == "setup":
            self._handle_setup_input(key)
        elif self.mode == "shop":
            self._handle_shop_input(key)
        elif self.mode == "skills":
            self._handle_skills_input(key)
        elif self.mode == "profile":
            self._handle_profile_input(key)
        elif self.mode == "reroll":
            self._handle_reroll_input(key)
        elif self.mode == "substations":
            self._handle_substations_input(key)
        elif self.mode == "ssh_keys":
            self._handle_ssh_keys_input(key)
        elif self.mode == "vault_unlock":
            self._handle_vault_unlock_input(key)
        elif self.mode == "substation_view":
            self._handle_substation_view_input(key)
        elif self.mode == "substation_perms":
            self._handle_substation_perms_input(key)
        elif self.mode == "crues":
            self._handle_crues_input(key)
        elif self.mode == "crue_view":
            self._handle_crue_view_input(key)
        elif self.mode == "crue_create":
            self._handle_crue_create_input(key)
        elif self.mode == "crue_member_select":
            self._handle_crue_member_select_input(key)
        elif self.mode == "create":
            self._handle_create_input(key)
        elif self.mode == "stats":
            self._handle_stats_input(key)
        elif self.mode == "dojo":
            self._handle_dojo_input(key)
        elif self.mode == "dispatch":
            self._handle_dispatch_form_input(key)
        elif self.mode == "dispatch_brief":
            self._handle_dispatch_brief_input(key)
        elif self.mode == "dispatch_run":
            self._handle_dispatch_run_input(key)
        elif self.mode == "viewer":
            self._handle_viewer_input(key)
        elif self.mode == "missions":
            self._handle_missions_input(key)
        elif self.mode == "agent_edit":
            self._handle_agent_edit_input(key)
        elif self.mode == "foundry":
            self._handle_foundry_input(key)
        elif self.mode == "foundry_list":
            self._handle_foundry_list_input(key)
        elif self.mode == "foundry_form":
            self._handle_foundry_form_input(key)
        elif self.mode == "compose_entities":
            self._handle_compose_entities_input(key)
        elif self.mode == "compose_records":
            self._handle_compose_records_input(key)
        elif self.mode == "compose_form":
            self._handle_compose_form_input(key)
        elif self.mode == "compose_pick":
            self._handle_compose_pick_input(key)

    def _handle_menu_input(self, key: int):
        """Handle menu input."""
        menu_count = 12

        if key == curses.KEY_UP:
            self.menu_index = (self.menu_index - 1) % menu_count
        elif key == curses.KEY_DOWN:
            self.menu_index = (self.menu_index + 1) % menu_count
        elif key in (curses.KEY_ENTER, 10, 13):
            self._menu_select()
        elif key == ord('r') or key == ord('R'):
            self.mode = "roster"
        elif key == ord('n') or key == ord('N'):
            self._enter_create_mode()
        elif key == ord('g') or key == ord('G'):
            self._generate_agent()
        elif key == ord('d') or key == ord('D'):
            self._enter_dispatch_mode()
        elif key == ord('m') or key == ord('M'):
            self._enter_missions_mode()
        elif key == ord('x') or key == ord('X'):
            self._enter_substations_mode()  # Will show error if unavailable
        elif key == ord('c') or key == ord('C'):
            self._enter_crues_mode()
        elif key == ord('t') or key == ord('T'):
            self._enter_stats_mode()
        elif key == ord('f') or key == ord('F'):
            self._enter_foundry_mode()
        elif key == ord('p') or key == ord('P'):
            self._enter_compose_mode()
        elif key == ord('s') or key == ord('S'):
            self.mode = "setup"
            self.setup_index = 0
            self._refresh_setup_stats()

    def _menu_select(self):
        """Handle menu selection."""
        if self.menu_index == 0:  # Roster
            self.mode = "roster"
        elif self.menu_index == 1:  # New
            self._enter_create_mode()
        elif self.menu_index == 2:  # Generate
            self._generate_agent()
        elif self.menu_index == 3:  # Dispatch
            self._enter_dispatch_mode()
        elif self.menu_index == 4:  # Missions
            self._enter_missions_mode()
        elif self.menu_index == 5:  # Substations
            self._enter_substations_mode()  # Will show error if unavailable
        elif self.menu_index == 6:  # Crues
            self._enter_crues_mode()
        elif self.menu_index == 7:  # Stats
            self._enter_stats_mode()
        elif self.menu_index == 8:  # Type Foundry
            self._enter_foundry_mode()
        elif self.menu_index == 9:  # Composer
            self._enter_compose_mode()
        elif self.menu_index == 10:  # Setup
            self.mode = "setup"
            self.setup_index = 0
            self._refresh_setup_stats()
        elif self.menu_index == 11:  # Quit
            raise KeyboardInterrupt


def main():
    """Standalone entry point."""
    builder = AgentBuilder()
    builder.run()


if __name__ == "__main__":
    main()
