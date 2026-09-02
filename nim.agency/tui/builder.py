#!/usr/bin/env python3
"""
Agent Builder TUI

Classic RPG-inspired character creation interface using curses.
Navigate with arrow keys, enter to select, 'q' to quit.
"""

import curses
import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Callable

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.data import DataStore, AgentGenerator
from lib.data.composer import get_composer
from lib.data.economy import train as dojo_train
from lib.data.identity import enroll as issue_badge, is_enrolled, org_id
from lib.data.roster import match_agents, draft_hire
from lib.dispatch import Dispatcher, adhoc_task_mission, get_provider
from lib.config import get_config as agency_config, report_dir
from tui.textkit import clip, display_width, pad

# Optional remote imports (may not be available if dependencies missing)
try:
    from lib.remote import (
        CredentialVault, VaultScope, VaultLocked, VaultNotInitialized,
        SSHKeyDiscovery, SSHKeyInfo, KeyStatus,
        SSHConnectionConfig, HostKeyPolicy,
        SubstationPermissions,
    )
    # Also verify cryptography is actually available (vault needs it)
    from cryptography.fernet import Fernet
    HAS_REMOTE = True
    REMOTE_ERROR = None
except ImportError as e:
    HAS_REMOTE = False
    REMOTE_ERROR = str(e)


# Minimum terminal dimensions required for TUI
MIN_TERMINAL_WIDTH = 80
MIN_TERMINAL_HEIGHT = 24


class Colors:
    """Color pair definitions."""
    NORMAL = 1
    HIGHLIGHT = 2
    TITLE = 3
    STAT_LABEL = 4
    STAT_VALUE = 5
    GOLD = 6
    HEALTH = 7
    XP = 8
    BORDER = 9
    DIM = 10
    PORTRAIT = 11
    ERROR = 12


class MugshotLoader:
    """Load ASCII art portraits from the mugshots directory."""

    # Map rank slugs to mugshot pools (diverse options per rank tier)
    RANK_MUGSHOTS = {
        # Junior tier - fresh faces, eager rookies
        "grunt": ["grunt_01", "rookie", "junior", "wirehead", "analyst"],
        "tinkerer": ["tinkerer_01", "junior", "rookie", "wirehead", "developer"],
        # Mid tier - competent operators
        "operator": ["operator_01", "operator_02", "operator", "analyst2", "developer"],
        "ranger": ["ranger_01", "operator", "analyst3", "security", "dispatch"],
        # Senior tier - experienced specialists
        "synthesizer": ["synthesizer_01", "senior", "analyst", "shaman", "uiux"],
        "theorist": ["theorist_01", "senior", "shaman", "owl", "security"],
        # Elite tier - legendary operators
        "neckbeard": ["neckbeard_01", "owl", "shaman", "hq", "dispatch"],
        "berserker": ["berserker_01", "owl", "hq", "shaman", "security"],
    }

    # Archetype-based pools for thematic matching (used as secondary selection)
    ARCHETYPE_MUGSHOTS = {
        "linux_admin": ["operator", "operator_01", "dispatch", "security"],
        "devops_engineer": ["developer", "operator", "dispatch", "wirehead"],
        "security_analyst": ["security", "analyst", "wirehead", "owl"],
        "network_engineer": ["wirehead", "operator", "dispatch", "security"],
        "dba": ["analyst", "analyst2", "analyst3", "senior"],
        "cloud_engineer": ["developer", "dispatch", "uiux", "operator"],
        "backend_dev": ["developer", "wirehead", "analyst", "uiux"],
        "data_engineer": ["analyst", "analyst2", "analyst3", "developer"],
        "platform_engineer": ["dispatch", "operator", "developer", "hq"],
        "sre": ["operator", "dispatch", "security", "owl"],
    }

    # All available mugshots for fallback/random selection
    ALL_MUGSHOTS = [
        "grunt_01", "tinkerer_01", "operator_01", "operator_02", "ranger_01",
        "synthesizer_01", "theorist_01", "neckbeard_01", "berserker_01",
        "analyst", "analyst2", "analyst3", "developer", "uiux",
        "security", "wirehead", "shaman", "owl", "dispatch", "hq",
        "senior", "junior", "rookie", "operator"
    ]

    _cache: dict[str, list[str]] = {}
    _mugshots_path: Path = None

    @classmethod
    def get_path(cls) -> Path:
        if cls._mugshots_path is None:
            cls._mugshots_path = Path(__file__).parent / "mugshots"
        return cls._mugshots_path

    @classmethod
    def list_available(cls) -> list[str]:
        """List all available mugshot names."""
        path = cls.get_path()
        if not path.exists():
            return []
        return [f.stem for f in path.glob("*.txt")]

    @classmethod
    def load(cls, name: str) -> list[str]:
        """Load a mugshot by name, returning list of lines."""
        if name in cls._cache:
            return cls._cache[name]

        filepath = cls.get_path() / f"{name}.txt"
        if not filepath.exists():
            filepath = cls.get_path() / "unknown.txt"

        lines = []
        if filepath.exists():
            with open(filepath, 'r') as f:
                lines = [line.rstrip() for line in f.readlines()]

        cls._cache[name] = lines
        return lines

    @classmethod
    def get_for_rank(cls, rank_slug: str, agent_id: str) -> list[str]:
        """Get a mugshot appropriate for the rank, deterministic by agent_id."""
        import hashlib
        options = cls.RANK_MUGSHOTS.get(rank_slug, cls.ALL_MUGSHOTS)
        # Use agent_id to deterministically pick one
        idx = int(hashlib.md5(agent_id.encode()).hexdigest(), 16) % len(options)
        return cls.load(options[idx])

    @classmethod
    def get_blended(cls, rank_slug: str, archetype_slug: str, agent_id: str) -> list[str]:
        """Mugshot from the merged rank + archetype pools, deterministic
        per agent. Takes resolved slugs so callers with reference-table
        access (the dossier) get archetype-flavored portraits."""
        import hashlib

        combined = list(cls.RANK_MUGSHOTS.get(rank_slug, []))
        for opt in cls.ARCHETYPE_MUGSHOTS.get(archetype_slug, []):
            if opt not in combined:
                combined.append(opt)
        if not combined:
            combined = cls.ALL_MUGSHOTS

        idx = int(hashlib.md5(agent_id.encode()).hexdigest(), 16) % len(combined)
        return cls.load(combined[idx])

    @classmethod
    def get_for_agent(cls, agent: dict) -> list[str]:
        """
        Get a mugshot for an agent, considering rank and archetype.

        Uses a blend of rank-based and archetype-based selection for variety.
        """
        import hashlib

        agent_id = agent.get("id", "unknown")
        cls_data = agent.get("classification", {})
        rank_slug = cls_data.get("rank_slug", "grunt")
        archetype_slug = cls_data.get("archetype_slug", "")

        # Build combined pool: rank options + archetype options
        rank_options = cls.RANK_MUGSHOTS.get(rank_slug, [])
        arch_options = cls.ARCHETYPE_MUGSHOTS.get(archetype_slug, [])

        # Merge pools, preferring rank but adding archetype variety
        combined = list(rank_options)
        for opt in arch_options:
            if opt not in combined:
                combined.append(opt)

        if not combined:
            combined = cls.ALL_MUGSHOTS

        # Deterministic selection based on agent_id
        idx = int(hashlib.md5(agent_id.encode()).hexdigest(), 16) % len(combined)
        return cls.load(combined[idx])


class Panel:
    """Base panel class for TUI components."""

    def __init__(self, win, y: int, x: int, height: int, width: int):
        self.parent = win
        self.y = y
        self.x = x
        self.height = height
        self.width = width
        self.win = win.subwin(height, width, y, x)

    def draw_border(self, title: str = ""):
        """Draw panel border with optional title."""
        self.win.attron(curses.color_pair(Colors.BORDER))
        self.win.border()
        if title:
            title_str = f" {title} "
            self.win.addstr(0, 2, title_str, curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self.win.attroff(curses.color_pair(Colors.BORDER))

    def clear(self):
        """Clear panel content (not border)."""
        for row in range(1, self.height - 1):
            self.win.addstr(row, 1, " " * (self.width - 2))

    def refresh(self):
        self.win.refresh()


class AgentBuilder:
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
        self.substation_edit_index = 0  # Field being edited in substation
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

    def _init_colors(self):
        """Initialize color pairs."""
        curses.start_color()
        curses.use_default_colors()

        # Define color pairs
        curses.init_pair(Colors.NORMAL, curses.COLOR_WHITE, -1)
        curses.init_pair(Colors.HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(Colors.TITLE, curses.COLOR_CYAN, -1)
        curses.init_pair(Colors.STAT_LABEL, curses.COLOR_WHITE, -1)
        curses.init_pair(Colors.STAT_VALUE, curses.COLOR_GREEN, -1)
        curses.init_pair(Colors.GOLD, curses.COLOR_YELLOW, -1)
        curses.init_pair(Colors.HEALTH, curses.COLOR_RED, -1)
        curses.init_pair(Colors.XP, curses.COLOR_MAGENTA, -1)
        curses.init_pair(Colors.BORDER, curses.COLOR_BLUE, -1)
        curses.init_pair(Colors.DIM, curses.COLOR_WHITE, -1)
        curses.init_pair(Colors.PORTRAIT, curses.COLOR_CYAN, -1)
        curses.init_pair(Colors.ERROR, curses.COLOR_RED, -1)

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
        elif self.mode == "substation_edit":
            self._draw_substation_edit(height, width)
        elif self.mode == "substation_perms":
            self._draw_substation_perms(height, width)
        elif self.mode == "crues":
            self._draw_crues(height, width)
        elif self.mode == "crue_view":
            self._draw_crue_view(height, width)
        elif self.mode == "crue_edit":
            self._draw_crue_edit(height, width)
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

        self.stdscr.refresh()

    def _draw_size_error(self, height: int, width: int):
        """Draw terminal size error message.

        Displayed when terminal is smaller than MIN_TERMINAL_WIDTH x MIN_TERMINAL_HEIGHT.
        Uses minimal drawing to work even in very small terminals.
        """
        # Build error messages
        lines = [
            "TERMINAL TOO SMALL",
            "",
            f"Current:  {width}x{height}",
            f"Required: {MIN_TERMINAL_WIDTH}x{MIN_TERMINAL_HEIGHT}",
            "",
            "Resize window to continue",
        ]

        # Calculate safe drawing area (leave margin for curses)
        safe_width = max(1, width - 1)
        safe_height = max(1, height - 1)

        # Find longest line for centering
        max_line_len = max(len(line) for line in lines)

        # Calculate vertical start position
        start_y = max(0, (safe_height - len(lines)) // 2)

        # Draw each line, centered and truncated to fit
        for i, line in enumerate(lines):
            y = start_y + i
            if y >= safe_height:
                break

            # Truncate line if needed
            if len(line) > safe_width:
                line = line[:safe_width]

            # Center horizontally
            x = max(0, (safe_width - len(line)) // 2)

            try:
                if i == 0:  # Title in red
                    self.stdscr.addstr(y, x, line,
                        curses.color_pair(Colors.ERROR) | curses.A_BOLD)
                elif "Required" in line or "Current" in line:
                    self.stdscr.addstr(y, x, line, curses.color_pair(Colors.DIM))
                else:
                    self.stdscr.addstr(y, x, line)
            except curses.error:
                # Silently ignore any remaining drawing errors
                pass

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

    def _draw_agent_view(self, height: int, width: int):
        """Draw detailed agent view - 3-column layout for 120+ columns."""
        if not self.current_agent:
            return

        agent = self.current_agent
        ident = agent["identity"]
        cls = agent["classification"]
        eco = agent["economy"]
        profile = agent.get("profile", {})
        ratings = agent.get("ratings", {})

        rank = self.ranks.get(cls["rank_id"], {"label": "?", "icon": "?", "slug": "grunt"})
        status = self.statuses.get(cls["status_id"], {"label": "?", "icon": "?"})
        agent_class = self.classes.get(cls.get("class_id", 1), {"label": "?", "icon": "?"})
        archetype = self.archetypes.get(cls.get("archetype_id", 1), {"label": "Unknown", "icon": "?"})
        focus = self.focuses.get(cls.get("focus_id", 1), {"label": "?", "abbrev": "?"})

        # Calculate XP to next rank
        next_rank_info = self._get_next_rank_info(eco["xp"])

        # Layout for 120+ columns: Portrait | Identity | Capabilities/Tools
        col1_width = max(26, int(width * 0.22))  # Portrait (wider for more info)
        col2_width = max(44, int(width * 0.38))  # Identity/Economy
        col3_width = width - col1_width - col2_width - 6  # Capabilities/Tools

        # Below ~110 columns the third column collapses to a sliver; drop
        # it and widen the dossier instead of drawing broken boxes
        show_col3 = col3_width >= 22
        if not show_col3:
            col2_width = width - col1_width - 5

        top_height = max(18, int(height * 0.60))
        profile_height = height - top_height - 4

        # === COLUMN 1: Portrait & Character Info ===
        portrait_x = 2
        portrait_y = 2
        portrait_box_height = top_height - 8  # Leave room for info below

        self._draw_box(portrait_y, portrait_x, portrait_box_height, col1_width, "MUGSHOT")

        mugshot_lines = MugshotLoader.get_blended(
            rank.get("slug", "grunt"), archetype.get("slug", ""), agent["id"])
        for i, line in enumerate(mugshot_lines[:portrait_box_height - 2]):
            if portrait_y + 1 + i < portrait_y + portrait_box_height - 1:
                padded = line[:col1_width - 4].center(col1_width - 4)
                try:
                    self.stdscr.addstr(portrait_y + 1 + i, portrait_x + 2, padded,
                                       curses.color_pair(Colors.PORTRAIT))
                except curses.error:
                    pass

        # Character info under portrait
        char_y = portrait_y + portrait_box_height

        # Archetype (icon + label, centered, prominent)
        arch_display = f"{archetype.get('icon', '')} {archetype.get('label', 'Unknown')}"
        self._safe_addstr(char_y, portrait_x + 1,
                          pad(arch_display, col1_width - 2, "center"),
                          curses.color_pair(Colors.GOLD) | curses.A_BOLD)
        char_y += 1

        # Role title (qualifier + focus + rank)
        role_title = ident.get("role_title", "Unknown")
        try:
            self.stdscr.addstr(char_y, portrait_x + 1,
                               role_title[:col1_width - 2].center(col1_width - 2),
                               curses.color_pair(Colors.TITLE))
        except curses.error:
            pass
        char_y += 1

        # Specialty (if any)
        specialty = ident.get("specialty", "")
        if specialty:
            try:
                self.stdscr.addstr(char_y, portrait_x + 1,
                                   f'"{specialty[:col1_width - 4]}"'.center(col1_width - 2),
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass
            char_y += 1

        # Origin/Zodiac
        zodiac = profile.get("zodiac", "")
        if zodiac:
            try:
                self.stdscr.addstr(char_y, portrait_x + 1,
                                   f"Origin: {zodiac}"[:col1_width - 2].center(col1_width - 2),
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass
            char_y += 1

        # Interests (likes)
        interests = profile.get("interests", [])
        if interests:
            likes_str = f"Likes: {', '.join(interests[:2])}"
            try:
                self.stdscr.addstr(char_y, portrait_x + 1,
                                   likes_str[:col1_width - 2],
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass
            char_y += 1

        # Motto (wrapped if needed)
        motto = profile.get("motto", "")
        if motto and char_y < portrait_y + top_height - 2:
            motto_display = f'"{motto[:col1_width - 4]}"'
            try:
                self.stdscr.addstr(char_y, portrait_x + 1,
                                   motto_display[:col1_width - 2],
                                   curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass

        # === COLUMN 2: Identity & Economy ===
        info_x = portrait_x + col1_width + 1
        info_y = 2
        info_height = top_height

        self._draw_box(info_y, info_x, info_height, col2_width, "AGENT DOSSIER")

        y = info_y + 1

        # Identity header
        tag = ident.get('tag', 0)
        tag_str = f"{tag:04d}" if isinstance(tag, int) else "0000"
        try:
            self.stdscr.addstr(y, info_x + 2, f"#{tag_str} ",
                               curses.color_pair(Colors.GOLD) | curses.A_BOLD)
            self.stdscr.addstr(f"{ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass
        y += 1

        try:
            self.stdscr.addstr(y, info_x + 2, f"Codename: ",
                               curses.color_pair(Colors.STAT_LABEL))
            self.stdscr.addstr(f"{ident['codename'][:col2_width - 14]}",
                               curses.color_pair(Colors.STAT_VALUE))
        except curses.error:
            pass
        y += 1

        # Access identity: badge + primary key fingerprint
        crypto = agent.get("crypto") or {}
        if crypto.get("fingerprint"):
            self._safe_addstr(y, info_x + 2, "Badge: ",
                              curses.color_pair(Colors.STAT_LABEL))
            badge = f"{org_id(agent)} {crypto.get('key_type', '?')} {crypto.get('fingerprint', '')}"
            self._safe_addstr(y, info_x + 9, clip(badge, col2_width - 12),
                              curses.color_pair(Colors.DIM))
        else:
            self._safe_addstr(y, info_x + 2, "Badge: not enrolled - press B",
                              curses.color_pair(Colors.HEALTH))
        y += 1

        # Classification row (removed redundant "Class:" label)
        class_num = agent_class.get('label', '?').replace('Class ', '')
        try:
            self.stdscr.addstr(y, info_x + 2,
                               pad(f"{rank.get('icon', '')} {rank['label']}", 15),
                               curses.color_pair(Colors.STAT_VALUE))
            self.stdscr.addstr(f"[{class_num}] ",
                               curses.color_pair(Colors.DIM))
            self.stdscr.addstr(f"{status.get('icon', '●')} {status['label']}",
                               curses.color_pair(Colors.STAT_VALUE))
        except curses.error:
            pass
        y += 2

        # Economy bars with max values
        try:
            self.stdscr.addstr(y, info_x + 2, "HP ",
                               curses.color_pair(Colors.HEALTH) | curses.A_BOLD)
            health_pct = eco["health"]["current"] / max(1, eco["health"]["max"])
            health_bar = self._progress_bar(10, health_pct)
            self.stdscr.addstr(f"[{health_bar}] {eco['health']['current']:>3}/{eco['health']['max']}",
                               curses.color_pair(Colors.HEALTH))
        except curses.error:
            pass
        y += 1

        try:
            self.stdscr.addstr(y, info_x + 2, "XP ",
                               curses.color_pair(Colors.XP) | curses.A_BOLD)
            self.stdscr.addstr(f"{eco['xp']:>6,}",
                               curses.color_pair(Colors.XP))
            self.stdscr.addstr(f"   GOLD ",
                               curses.color_pair(Colors.GOLD) | curses.A_BOLD)
            self.stdscr.addstr(f"{eco['gold']:>5,}",
                               curses.color_pair(Colors.GOLD))
            sp = eco.get("skill_points", {})
            self.stdscr.addstr(f"   SP ",
                               curses.color_pair(Colors.STAT_LABEL) | curses.A_BOLD)
            self.stdscr.addstr(f"{sp.get('assigned', 0)}/{sp.get('total', 0)}",
                               curses.color_pair(Colors.STAT_VALUE))
        except curses.error:
            pass
        y += 1

        # XP to next rank
        if next_rank_info:
            try:
                self.stdscr.addstr(y, info_x + 2, f"Next: {next_rank_info['xp_needed']:,} XP → ",
                                   curses.color_pair(Colors.DIM))
                self.stdscr.addstr(f"{next_rank_info['rank']}",
                                   curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass
        y += 2

        # Skills section with FULL NAMES and allocated/max format
        skills_height = min(7, info_height - y + info_y - 2)
        if skills_height > 2:
            try:
                self.stdscr.addstr(y, info_x + 2, "─" * (col2_width - 4),
                                   curses.color_pair(Colors.BORDER))
                self.stdscr.addstr(y, info_x + 2, " SKILLS ",
                                   curses.color_pair(Colors.TITLE))
            except curses.error:
                pass
            y += 1

            skills = agent.get("skills", [])
            max_skill = 20  # Max skill points
            for i, skill in enumerate(skills[:min(6, skills_height - 1)]):
                domain = self.skill_domains.get(skill["domain_id"], {"label": "Unknown"})
                label = domain.get("label", "?")[:12]
                points = skill["points"]
                bar = self._progress_bar(6, points / max_skill)
                try:
                    self.stdscr.addstr(y + i, info_x + 2,
                                       f"{label:<12} [{bar}] {points:>2}/{max_skill}",
                                       curses.color_pair(Colors.STAT_VALUE))
                except curses.error:
                    pass

        # === COLUMN 3: Capabilities, Tools & Awards (wide terminals only) ===
        if show_col3:
            cap_x = info_x + col2_width + 1
            cap_y = 2
            cap_height = top_height

            self._draw_box(cap_y, cap_x, cap_height, col3_width, "CAPABILITIES")

            cy = cap_y + 1

            # Capabilities section with level/max
            capabilities = agent.get("capabilities", [])
            if capabilities:
                self._safe_addstr(cy, cap_x + 2, "CAPABILITIES",
                                  curses.color_pair(Colors.TITLE))
                cy += 1

                for cap in capabilities[:max(0, min(5, (cap_height - 10) // 2))]:
                    cap_ref = self.capability_refs.get(cap.get("capability_id"), {})
                    prof = cap.get("proficiency", 1)
                    max_prof = 5
                    lead = pad(f"{cap_ref.get('icon', '●')} {cap_ref.get('label', 'Unknown')}", 17)
                    bar = self._progress_bar(5, prof / max_prof)
                    self._safe_addstr(cy, cap_x + 2,
                                      clip(f"{lead} [{bar}] {prof}/{max_prof}", col3_width - 4),
                                      curses.color_pair(Colors.STAT_VALUE))
                    cy += 1
            cy += 1

            # Tools section (compact grid; 11 cells per entry - wide icons fit)
            tools = agent.get("tools", [])
            if tools and cy < cap_y + cap_height - 6:
                self._safe_addstr(cy, cap_x + 2, f"TOOLS ({len(tools)})",
                                  curses.color_pair(Colors.TITLE))
                cy += 1

                cell = 11
                tool_per_row = max(1, (col3_width - 4) // cell)
                shown = 0
                for i, tool in enumerate(tools[:min(8, max(1, (cap_height - cy + cap_y - 4)) * tool_per_row)]):
                    tool_ref = self.tool_refs.get(tool.get("tool_id"), {})
                    entry = pad(f"{tool_ref.get('icon', '▪')} {tool_ref.get('slug', '???')}", cell - 1)
                    col = i % tool_per_row
                    row = i // tool_per_row
                    ty = cy + row
                    if ty < cap_y + cap_height - 4:
                        self._safe_addstr(ty, cap_x + 2 + (col * cell), entry,
                                          curses.color_pair(Colors.STAT_VALUE))
                        shown = i + 1
                cy += (max(1, shown) - 1) // tool_per_row + 2

            # Awards/Ratings section
            if cy < cap_y + cap_height - 3:
                self._safe_addstr(cy, cap_x + 2, "─" * (col3_width - 4),
                                  curses.color_pair(Colors.BORDER))
                self._safe_addstr(cy, cap_x + 2, " RATINGS ",
                                  curses.color_pair(Colors.TITLE))
                cy += 1

                missions = ratings.get("missions_completed", 0)
                peer_count = ratings.get("peer", {}).get("count", 0)
                peer_avg = ratings.get("peer", {}).get("sum", 0) / max(1, peer_count) if peer_count else 0

                line = f"Missions: {missions}"
                if peer_count > 0:
                    line += f"  Peer: {peer_avg:.1f}/5 ({peer_count})"
                self._safe_addstr(cy, cap_x + 2, clip(line, col3_width - 4),
                                  curses.color_pair(Colors.STAT_VALUE))
                cy += 1

                achievements = agent.get("achievements", [])
                if achievements:
                    self._safe_addstr(cy, cap_x + 2, f"Awards: {len(achievements)}",
                                      curses.color_pair(Colors.GOLD))
                else:
                    self._safe_addstr(cy, cap_x + 2, "No citations yet",
                                      curses.color_pair(Colors.DIM))

        # === BOTTOM: Profile Panel ===
        profile_y = 2 + top_height
        profile_x = 2
        profile_width = width - 4

        if profile_height > 3 and profile:
            self._draw_box(profile_y, profile_x, profile_height, profile_width, "PROFILE")

            py = profile_y + 1

            # Bio (word-wrapped)
            bio = profile.get("bio", "")
            if bio:
                bio_width = profile_width - 6
                words = bio.split()
                line = ""
                for word in words:
                    if len(line) + len(word) + 1 <= bio_width:
                        line += (" " if line else "") + word
                    else:
                        try:
                            self.stdscr.addstr(py, profile_x + 2, line,
                                               curses.color_pair(Colors.DIM))
                        except curses.error:
                            pass
                        py += 1
                        line = word
                        if py >= profile_y + profile_height - 2:
                            break
                if line and py < profile_y + profile_height - 2:
                    try:
                        self.stdscr.addstr(py, profile_x + 2, line,
                                           curses.color_pair(Colors.DIM))
                    except curses.error:
                        pass
                    py += 1

            # Traits
            personality = profile.get("personality", {})
            traits = personality.get("traits", [])
            if traits and py < profile_y + profile_height - 1:
                try:
                    self.stdscr.addstr(py, profile_x + 2, f"Traits: {', '.join(traits[:4])}",
                                       curses.color_pair(Colors.STAT_VALUE))
                except curses.error:
                    pass

        # Agent ID and navigation indicator at bottom
        try:
            # Show agent ID
            self.stdscr.addstr(height - 4, 4, f"ID: {agent['id']}",
                               curses.color_pair(Colors.DIM))

            # Show selection status
            if agent["id"] in self.selected_agents:
                self.stdscr.addstr(height - 4, 4 + len(agent['id']) + 8, " [SELECTED]",
                                   curses.color_pair(Colors.GOLD) | curses.A_BOLD)

            # Rolodex navigation indicator
            display_roster = self._get_filtered_roster()
            if len(display_roster) > 1:
                # Find current position
                curr_idx = next((i for i, a in enumerate(display_roster) if a["id"] == agent["id"]), 0)
                total = len(display_roster)
                prev_idx = (curr_idx - 1) % total
                next_idx = (curr_idx + 1) % total
                prev_name = display_roster[prev_idx]["identity"]["codename"][:12]
                next_name = display_roster[next_idx]["identity"]["codename"][:12]
                nav_str = f"<< {prev_name}  [{curr_idx + 1}/{total}]  {next_name} >>"
                nav_x = width - len(nav_str) - 4
                self.stdscr.addstr(height - 4, nav_x, nav_str, curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Show status message if any (important for ping/deploy feedback)
        self._draw_message(height - 3, width)

        # Updated footer with rolodex controls
        footer = ("H/L: Nav | D: Dispatch | O: OpProfile | W: Service | "
                  "A: Access | E: Edit | S: Skills | T: Tools | J: Dojo | "
                  "B: Badge | V: Bio | R: Roll | X: Del | Q: Back")
        self._draw_footer(height, width, footer)

    def _get_next_rank_info(self, current_xp: int) -> dict:
        """Calculate XP needed for next rank promotion."""
        # Find next rank threshold
        sorted_ranks = sorted(self.ranks.values(), key=lambda r: r.get("xp_threshold", 0))

        for rank in sorted_ranks:
            threshold = rank.get("xp_threshold", 0)
            if threshold > current_xp:
                return {
                    "rank": rank["label"],
                    "xp_needed": threshold - current_xp,
                    "threshold": threshold
                }

        # Already at max rank
        return None

    def _draw_shop(self, height: int, width: int):
        """Draw tool shop interface - purchase tools with gold."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        gold = agent["economy"]["gold"]
        rank_id = agent["classification"]["rank_id"]
        owned_tools = {t.get("tool_id") for t in agent.get("tools", [])}

        self._draw_header(width)

        # Header with agent info
        ident = agent["identity"]
        try:
            self.stdscr.addstr(5, 2, f"TOOL SHOP - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(5, width - 20, f"GOLD: ",
                               curses.color_pair(Colors.STAT_LABEL))
            self.stdscr.addstr(f"{gold:,}",
                               curses.color_pair(Colors.GOLD) | curses.A_BOLD)
        except curses.error:
            pass

        # Tool list (sorted copy - the reference cache must not be mutated)
        tools = sorted(self.tool_refs.values(),
                       key=lambda t: (t.get("cost", 0), t.get("label", "")))

        list_y = 7
        list_height = height - 12
        visible_count = list_height - 2

        # Filter to show available tools (not owned)
        available = [t for t in tools if t["id"] not in owned_tools]

        # Header
        header = f"{'TOOL':<20} {'COST':>6} {'RISK':<10} {'REQ RANK':<10} {'STATUS':<12}"
        try:
            self.stdscr.addstr(list_y, 4, header[:width - 8],
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Scroll handling
        start_idx = max(0, self.shop_index - visible_count + 3)

        for i, tool in enumerate(available[start_idx:start_idx + visible_count]):
            row_y = list_y + 2 + i
            if row_y >= list_y + list_height:
                break

            cost = tool.get("cost", 0)
            risk = tool.get("risk", {}).get("level", "?")
            min_rank = tool.get("requirements", {}).get("min_rank_id", 1)

            # Determine status
            can_afford = gold >= cost
            meets_rank = rank_id >= min_rank

            if can_afford and meets_rank:
                status = "AVAILABLE"
                status_color = Colors.STAT_VALUE
            elif not meets_rank:
                status = f"RANK {min_rank}+"
                status_color = Colors.DIM
            else:
                status = "NO GOLD"
                status_color = Colors.HEALTH

            icon = tool.get("icon", "▪")
            label = pad(f"{icon} {tool.get('label', '???')}", 20)
            row = f" {clip(f'{label} {cost:>5}g {risk:<10} {min_rank:<10} {status:<12}', width - 10)} "

            if start_idx + i == self.shop_index:
                self._safe_addstr(row_y, 4, row, curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(row_y, 4, row, curses.color_pair(status_color))

        # Show tool description at bottom
        if available and 0 <= self.shop_index < len(available):
            selected = available[self.shop_index]
            desc = selected.get("description", "")[:width - 10]
            try:
                self.stdscr.addstr(height - 5, 4, desc,
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Purchase | Q: Back to Agent")

    def _draw_skills(self, height: int, width: int):
        """Draw capability upgrade interface - spend XP to level up."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        xp = agent["economy"]["xp"]
        caps = agent.get("capabilities", [])

        self._draw_header(width)

        # Header with agent info
        ident = agent["identity"]
        try:
            self.stdscr.addstr(5, 2, f"CAPABILITY TRAINING - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(5, width - 20, f"XP: ",
                               curses.color_pair(Colors.STAT_LABEL))
            self.stdscr.addstr(f"{xp:,}",
                               curses.color_pair(Colors.XP) | curses.A_BOLD)
        except curses.error:
            pass

        # Capability list
        cap_refs = self.capability_refs

        list_y = 7
        list_height = height - 12

        # Header
        header = f"{'CAPABILITY':<25} {'LEVEL':>6} {'NEXT COST':>10} {'STATUS':<15}"
        try:
            self.stdscr.addstr(list_y, 4, header[:width - 8],
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        for i, cap in enumerate(caps[:list_height - 4]):
            row_y = list_y + 2 + i
            cap_ref = cap_refs.get(cap.get("capability_id"), {})
            prof = cap.get("proficiency", 1)
            xp_costs = cap_ref.get("xp_costs", [100, 300, 600, 1000, 1500])

            icon = cap_ref.get("icon", "●")
            label = pad(f"{icon} {cap_ref.get('label', 'Unknown')}", 25)

            if prof >= 5:
                next_cost = "MAX"
                status = "MASTERED"
                status_color = Colors.GOLD
            else:
                next_cost = xp_costs[prof] if prof < len(xp_costs) else 9999
                if xp >= next_cost:
                    status = "CAN UPGRADE"
                    status_color = Colors.STAT_VALUE
                else:
                    status = f"NEED {next_cost - xp} XP"
                    status_color = Colors.DIM
                next_cost = f"{next_cost:,}"

            bar = self._progress_bar(5, prof / 5)
            row = f" {clip(f'{label} [{bar}] {next_cost:>10} {status:<15}', width - 10)} "

            if i == self.skill_index:
                self._safe_addstr(row_y, 4, row, curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(row_y, 4, row, curses.color_pair(status_color))

        # Show capability description at bottom
        if caps and 0 <= self.skill_index < len(caps):
            cap = caps[self.skill_index]
            cap_ref = cap_refs.get(cap.get("capability_id"), {})
            desc = cap_ref.get("description", "")[:width - 10]
            prof = cap.get("proficiency", 1)
            prof_desc = cap_ref.get("proficiency_levels", {}).get(str(prof), "")
            try:
                self.stdscr.addstr(height - 6, 4, desc,
                                   curses.color_pair(Colors.DIM))
                if prof_desc:
                    self.stdscr.addstr(height - 5, 4, f"Level {prof}: {prof_desc[:width - 20]}",
                                       curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Upgrade | Q: Back to Agent")

    def _draw_profile_viewer(self, height: int, width: int):
        """Draw paginated full profile viewer."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        ident = agent["identity"]
        profile = agent.get("profile", {})

        self._draw_header(width)

        # Header
        try:
            self.stdscr.addstr(5, 2, f"AGENT PROFILE - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Build profile lines if not cached or agent changed
        if not self.profile_lines or self.profile_lines[0] != agent["id"]:
            self.profile_lines = self._build_profile_lines(agent, width - 8)
            self.profile_scroll = 0

        # Content area
        content_y = 7
        content_height = height - 10
        content_width = width - 4

        self._draw_box(content_y - 1, 2, content_height + 2, content_width, "")

        # Display lines with scrolling
        lines = self.profile_lines[1:]  # Skip agent ID marker
        total_lines = len(lines)
        visible_lines = content_height

        # Ensure scroll is in bounds
        max_scroll = max(0, total_lines - visible_lines)
        self.profile_scroll = max(0, min(self.profile_scroll, max_scroll))

        for i, line in enumerate(lines[self.profile_scroll:self.profile_scroll + visible_lines]):
            row_y = content_y + i
            # Determine color based on line prefix
            color = Colors.DIM
            if line.startswith("##"):
                color = Colors.TITLE
                line = line[2:].strip()
            elif line.startswith("#"):
                color = Colors.STAT_LABEL
                line = line[1:].strip()
            elif line.startswith("  *"):
                color = Colors.STAT_VALUE
            elif line.startswith('"'):
                color = Colors.STAT_VALUE

            try:
                self.stdscr.addstr(row_y, 4, line[:content_width - 4],
                                   curses.color_pair(color))
            except curses.error:
                pass

        # Scroll indicator (kept inside the box - it used to overwrite the border)
        if total_lines > visible_lines:
            pct = self.profile_scroll / max(1, max_scroll)
            indicator_y = content_y + int(pct * (visible_lines - 1))
            self._safe_addstr(indicator_y, content_width - 1, "█",
                              curses.color_pair(Colors.STAT_VALUE))
            range_str = (f" {self.profile_scroll + 1}-"
                         f"{min(self.profile_scroll + visible_lines, total_lines)}"
                         f"/{total_lines} ")
            self._safe_addstr(content_y - 1, max(4, content_width - len(range_str) - 2),
                              range_str, curses.color_pair(Colors.DIM))

        self._draw_footer(height, width, "UP/DOWN/PgUp/PgDn: Scroll | Q: Back to Agent")

    def _build_profile_lines(self, agent: dict, width: int) -> list[str]:
        """Build content lines for profile viewer."""
        lines = [agent["id"]]  # First line is agent ID marker

        ident = agent["identity"]
        cls = agent["classification"]
        eco = agent["economy"]
        profile = agent.get("profile", {})

        # Identity section
        lines.append("## IDENTITY")
        lines.append("")
        lines.append(f"#Name: {ident['name']}")
        lines.append(f"  Codename: {ident['codename']}")
        lines.append(f"  Agent Tag: #{ident.get('tag', 0):04d}")
        lines.append(f"  Role: {ident.get('role_title', 'Unknown')}")
        if ident.get("specialty"):
            lines.append(f"  Specialty: {ident['specialty']}")
        lines.append("")

        # Classification
        rank = self.ranks.get(cls["rank_id"], {"label": "?"})
        status = self.statuses.get(cls["status_id"], {"label": "?"})
        agent_class = self.classes.get(cls.get("class_id", 1), {"label": "?"})
        focus = self.focuses.get(cls.get("focus_id"), {"label": "?"})
        ops_domain = self.ops_domains.get(cls.get("ops_domain_id"), {"label": "?"})

        lines.append("## CLASSIFICATION")
        lines.append("")
        lines.append(f"  * Rank: {rank['label']}")
        lines.append(f"  * Class: {agent_class['label']}")
        lines.append(f"  * Status: {status['label']}")
        lines.append(f"  * Domain: {ops_domain.get('label', '?')}")
        lines.append(f"  * Focus: {focus.get('label', '?')}")
        lines.append("")

        # Economy
        lines.append("## RESOURCES")
        lines.append("")
        lines.append(f"  Health: {eco['health']['current']}/{eco['health']['max']}")
        lines.append(f"  XP: {eco['xp']:,}")
        lines.append(f"  Gold: {eco['gold']:,}")
        lines.append("")

        # Profile/Bio
        if profile:
            lines.append("## PROFILE")
            lines.append("")

            if profile.get("bio"):
                lines.append("#Biography:")
                # Word wrap bio
                bio = profile["bio"]
                words = bio.split()
                line = "  "
                for word in words:
                    if len(line) + len(word) + 1 <= width:
                        line += word + " "
                    else:
                        lines.append(line.rstrip())
                        line = "  " + word + " "
                if line.strip():
                    lines.append(line.rstrip())
                lines.append("")

            if profile.get("motto"):
                lines.append(f'"{profile["motto"]}"')
                lines.append("")

            if profile.get("personality"):
                pers = profile["personality"]
                lines.append("#Personality:")
                if pers.get("temperament"):
                    lines.append(f"  Temperament: {pers['temperament']}")
                if pers.get("traits"):
                    lines.append(f"  Traits: {', '.join(pers['traits'])}")
                if pers.get("tone"):
                    lines.append(f"  Tone: {pers['tone']}")
                lines.append("")

            if profile.get("interests"):
                lines.append(f"#Interests: {', '.join(profile['interests'])}")
                lines.append("")

            if profile.get("zodiac"):
                lines.append(f"  Zodiac: {profile['zodiac']}")
                lines.append("")

        # Capabilities
        caps = agent.get("capabilities", [])
        if caps:
            lines.append("## CAPABILITIES")
            lines.append("")
            cap_refs = {c["id"]: c for c in self.ds.load_reference("capability")}
            for cap in caps:
                cap_ref = cap_refs.get(cap.get("capability_id"), {})
                prof = cap.get("proficiency", 1)
                lines.append(f"  * {cap_ref.get('label', '?')} (Level {prof})")
                prof_desc = cap_ref.get("proficiency_levels", {}).get(str(prof))
                if prof_desc:
                    lines.append(f"    {prof_desc}")
            lines.append("")

        # Tools
        tools = agent.get("tools", [])
        if tools:
            lines.append("## TOOLS")
            lines.append("")
            tool_refs = {t["id"]: t for t in self.ds.load_reference("tool")}
            for tool in tools:
                tool_ref = tool_refs.get(tool.get("tool_id"), {})
                lines.append(f"  * {tool_ref.get('label', '?')}: {tool_ref.get('description', '')[:width - 20]}")
            lines.append("")

        # Origin
        origin = agent.get("origin", {})
        if origin:
            lines.append("## ORIGIN")
            lines.append("")
            lines.append(f"  Created: {origin.get('timestamp', '?')[:19]}")
            lines.append(f"  By: {origin.get('operator', '?')} via {origin.get('client', '?')}")
            lines.append("")

        # Agent ID
        lines.append("## SYSTEM")
        lines.append("")
        lines.append(f"  ID: {agent['id']}")

        return lines

    def _draw_create(self, height: int, width: int):
        """Draw the new-agent creation screen: candidate preview + controls."""
        self._draw_header(width)

        candidate = self.create_candidate
        if not candidate:
            self._safe_addstr(10, max(0, (width - 24) // 2), "No candidate - press R",
                              curses.color_pair(Colors.DIM))
            self._draw_footer(height, width, "R: Roll Candidate | Q: Cancel")
            return

        ident = candidate["identity"]
        cls = candidate["classification"]
        rank = self.ranks.get(cls.get("rank_id"), {})
        archetype = self.archetypes.get(cls.get("archetype_id"), {})
        agent_class = self.classes.get(cls.get("class_id"), {})
        focus = self.focuses.get(cls.get("focus_id"), {})
        profile = candidate.get("profile", {})

        box_width = min(70, width - 4)
        box_height = 15
        box_y = 6
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width, "NEW AGENT - CANDIDATE")

        def field(y, label, value, color=Colors.STAT_VALUE, editing_key=None):
            self._safe_addstr(y, box_x + 2, f"{label:<10}",
                              curses.color_pair(Colors.STAT_LABEL))
            if self.editing and self.create_edit_field == editing_key:
                self._safe_addstr(y, box_x + 12, f"[{self.edit_buffer}_]",
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(y, box_x + 12, clip(str(value), box_width - 14),
                                  curses.color_pair(color))

        y = box_y + 2
        field(y, "Name", ident.get("name", "?"), Colors.TITLE, "name"); y += 1
        field(y, "Codename", ident.get("codename", "?"), Colors.STAT_VALUE, "codename"); y += 1
        field(y, "Dogtag", ident.get("dogtag", "?"), Colors.DIM); y += 2
        field(y, "Rank", pad(f"{rank.get('icon', '')} {rank.get('label', '?')}", 20)
              + f"Class: {agent_class.get('label', '?')}"); y += 1
        field(y, "Role", f"{archetype.get('label', '?')} / {focus.get('label', '?')}"); y += 1
        field(y, "Title", ident.get("role_title", "?"), Colors.DIM); y += 2

        skills = sorted(candidate.get("skills", []),
                        key=lambda s: s.get("points", 0), reverse=True)[:3]
        skill_str = "  ".join(
            f"{self.skill_domains.get(s['domain_id'], {}).get('label', '?')}:{s['points']}"
            for s in skills)
        field(y, "Skills", skill_str); y += 1
        field(y, "Motto", f'"{profile.get("motto", "")}"', Colors.DIM); y += 1

        self._draw_message(box_y + box_height + 1, width)
        if self.editing:
            self._draw_footer(height, width, "Type to edit | ENTER: Apply | ESC: Cancel")
        else:
            self._draw_footer(height, width,
                              "ENTER: Hire (enrolls + badge) | R: Reroll | "
                              "E: Edit Name | C: Edit Codename | Q: Cancel")

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

    def _draw_header(self, width: int):
        """Draw application header with org/unit/substation context."""
        agency_name = self.config.get("agency", {}).get("name", "Biomimetic Agency")

        # Get org context
        org_name = self.config.get("organization", {}).get("name", "NIM")
        unit_name = self.config.get("unit", {}).get("name", "Operations")
        substation_name = self.config.get("substation", {}).get("name", "HQ")

        # Get operator
        operator = self.config.get("operator", {}).get("display_name") or self.system_operator.get("username", "operator")

        # Build context line: ORG > UNIT > SUBSTATION
        context_line = f"{org_name} > {unit_name} > {substation_name}"

        # Row 1: Agency name centered
        try:
            self.stdscr.addstr(1, (width - len(agency_name)) // 2, agency_name,
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Row 2: Context breadcrumb (left) and operator (right)
        try:
            self.stdscr.addstr(2, 2, context_line[:width//2 - 4],
                               curses.color_pair(Colors.DIM))
            operator_str = f"@{operator}"
            self.stdscr.addstr(2, width - len(operator_str) - 2, operator_str,
                               curses.color_pair(Colors.STAT_VALUE))
        except curses.error:
            pass

        # Row 3: separator
        try:
            self.stdscr.addstr(4, 0, "=" * width, curses.color_pair(Colors.BORDER))
        except curses.error:
            pass

    def _get_context_string(self) -> str:
        """Get org/unit/substation context string for display."""
        org = self.config.get("organization", {}).get("name", "NIM")
        unit = self.config.get("unit", {}).get("name", "Ops")
        sub = self.config.get("substation", {}).get("name", "HQ")
        return f"{org} > {unit} > {sub}"

    def _get_operator_string(self) -> str:
        """Get operator display name."""
        return self.config.get("operator", {}).get("display_name") or self.system_operator.get("username", "operator")

    def _safe_addstr(self, y: int, x: int, text: str, attr: int = 0):
        """Bounds-checked, cell-clipped addstr - the only safe way to draw.

        curses raises on any write past the screen edge (including the
        legal bottom-right cell), and len()-based layout drifts on wide
        glyphs; clamp both here so callers can't crash a frame.
        """
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width:
            return
        try:
            self.stdscr.addstr(y, x, clip(text, width - x, ellipsis=""), attr)
        except curses.error:
            pass

    def _draw_message(self, y: int, width: int):
        """Draw the centered status message line, clipped to fit."""
        if not self.message:
            return
        msg = clip(self.message, max(0, width - 4))
        x = max(0, (width - display_width(msg)) // 2)
        self._safe_addstr(y, x, msg,
                          curses.color_pair(self.message_color) | curses.A_BOLD)

    def _draw_footer(self, height: int, width: int, text: str):
        """Draw footer with help text."""
        self._safe_addstr(height - 2, 0, "=" * width, curses.color_pair(Colors.BORDER))
        text = clip(text, max(0, width - 2))
        x = max(0, (width - display_width(text)) // 2)
        self._safe_addstr(height - 1, x, text, curses.color_pair(Colors.DIM))

    def _draw_box(self, y: int, x: int, height: int, width: int, title: str = ""):
        """Draw a box with optional title (clamped to the box width)."""
        if height < 2 or width < 2:
            return
        border = curses.color_pair(Colors.BORDER)
        self._safe_addstr(y, x, "+" + "-" * (width - 2) + "+", border)
        if title:
            self._safe_addstr(y, x + 2, clip(f" {title} ", max(0, width - 4)),
                              curses.color_pair(Colors.TITLE) | curses.A_BOLD)

        for row in range(1, height - 1):
            self._safe_addstr(y + row, x, "|", border)
            self._safe_addstr(y + row, x + width - 1, "|", border)

        self._safe_addstr(y + height - 1, x, "+" + "-" * (width - 2) + "+", border)

    def _progress_bar(self, width: int, pct: float) -> str:
        """Create a progress bar string."""
        filled = int(width * min(1.0, max(0.0, pct)))
        return "#" * filled + "-" * (width - filled)

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
        elif self.mode == "substation_edit":
            self._handle_substation_edit_input(key)
        elif self.mode == "substation_perms":
            self._handle_substation_perms_input(key)
        elif self.mode == "crues":
            self._handle_crues_input(key)
        elif self.mode == "crue_view":
            self._handle_crue_view_input(key)
        elif self.mode == "crue_edit":
            self._handle_crue_edit_input(key)
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

    def _handle_menu_input(self, key: int):
        """Handle menu input."""
        menu_count = 11

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
        elif key == ord('s') or key == ord('S'):
            self.mode = "setup"
            self.setup_index = 0
            self._refresh_setup_stats()

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

    def _handle_view_input(self, key: int):
        """Handle view input with rolodex navigation."""
        display_roster = self._get_filtered_roster()

        # Rolodex navigation - LEFT/RIGHT or H/L for vim users
        if key in (curses.KEY_LEFT, ord('h'), ord('H')):
            if display_roster and self.current_agent:
                curr_idx = next((i for i, a in enumerate(display_roster) if a["id"] == self.current_agent["id"]), 0)
                new_idx = (curr_idx - 1) % len(display_roster)
                self.current_agent = display_roster[new_idx]
                self.roster_index = new_idx
                self.profile_lines = []  # Clear cached profile
        elif key in (curses.KEY_RIGHT, ord('l'), ord('L')):
            if display_roster and self.current_agent:
                curr_idx = next((i for i, a in enumerate(display_roster) if a["id"] == self.current_agent["id"]), 0)
                new_idx = (curr_idx + 1) % len(display_roster)
                self.current_agent = display_roster[new_idx]
                self.roster_index = new_idx
                self.profile_lines = []  # Clear cached profile

        # C: Add/remove from crew shortlist
        elif key == ord('c') or key == ord('C'):
            self._toggle_agent_selection()

        # P: Ping agent
        elif key == ord('p') or key == ord('P'):
            self._ping_agent()

        # D: Direct dispatch - this agent, prefilled mission form
        elif key == ord('d') or key == ord('D'):
            if self.current_agent:
                self.dispatch_crew_override = [self.current_agent]
                self.dispatch_crew_label = (
                    f"direct: {self.current_agent['identity']['codename']}")
                self._enter_dispatch_mode()

        # O: Operational profile (composed instructions/behavior)
        elif key == ord('o') or key == ord('O'):
            self._open_operational_profile()

        # W: Work history / service record
        elif key == ord('w') or key == ord('W'):
            self._open_service_record()

        # A: Access - badge, host deployments, project engagements
        elif key == ord('a') or key == ord('A'):
            self._open_access_view()

        # E: Edit agent form
        elif key == ord('e') or key == ord('E'):
            self._enter_agent_edit()

        # S: Skills/capabilities
        elif key == ord('s') or key == ord('S'):
            self.skill_index = 0
            self.mode = "skills"

        # T: Tools shop
        elif key == ord('t') or key == ord('T'):
            self.shop_index = 0
            self.mode = "shop"

        # J: Dojo (skill domain training)
        elif key == ord('j') or key == ord('J'):
            self.dojo_index = 0
            self.mode = "dojo"

        # B: Badge (enroll identity)
        elif key == ord('b') or key == ord('B'):
            self._enroll_current_agent()

        # V: View full profile
        elif key == ord('v') or key == ord('V'):
            self.profile_lines = []  # Force rebuild
            self.profile_scroll = 0
            self.mode = "profile"

        # R: Reroll menu
        elif key == ord('r') or key == ord('R'):
            self.reroll_index = 0
            self.mode = "reroll"

        # X: Delete agent (moved from D)
        elif key == ord('x') or key == ord('X'):
            if self.current_agent:
                agent_name = self.current_agent["identity"]["name"]
                self.ds.delete("agent", self.current_agent["id"])
                self._load_roster()
                self.roster_index = min(self.roster_index, len(self.roster) - 1)
                self.message = f"{agent_name} terminated."
                self.message_color = Colors.DIM
                self.mode = "roster"

        # Q/ESC: Back to roster
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

    def _ping_agent(self):
        """Ping the current agent - simulated response."""
        if not self.current_agent:
            return

        import random
        agent = self.current_agent
        codename = agent["identity"]["codename"]
        rank = self.ranks.get(agent["classification"]["rank_id"], {})
        rank_label = rank.get("label", "Operative")

        # Fun responses based on rank/personality
        responses = {
            1: ["*nervous beep*", "H-hello?", "Reporting... I think?", "Did I do that right?"],
            2: ["Oi!", "Present.", "Standing by.", "Copy that."],
            3: ["Ready and waiting.", "Online.", "Oi! What's the mission?", "Standing by."],
            4: ["Acknowledged.", "In position.", "What do you need?", "Ready to roll."],
            5: ["At your service.", "Systems nominal.", "Go ahead.", "Ready for tasking."],
            6: ["*confident ping*", "Proceed.", "Listening.", "State your requirements."],
            7: ["What.", "Make it quick.", "I'm busy.", "*sighs* Yes?"],
            8: ["...", "*intimidating silence*", "Speak.", "You rang?"]
        }

        rank_id = agent["classification"]["rank_id"]
        response_pool = responses.get(rank_id, responses[4])
        response = random.choice(response_pool)

        self.message = f"[{codename}] ({rank_label}): {response}"
        self.message_color = Colors.STAT_VALUE

    def _deploy_agent_prompt(self):
        """Initiate agent deployment to a substation."""
        if not self.current_agent:
            return

        agent_name = self.current_agent["identity"]["codename"]
        agent_rank = self.ranks.get(self.current_agent["classification"]["rank_id"], {})
        rank_label = agent_rank.get("label", "Operative")

        # Check if substations module is available
        if not self.substations:
            # Try to load substations
            try:
                self.substations = self.ds.list("substation")
            except Exception:
                self.substations = []

        if not self.substations:
            self.message = f"[{agent_name}] No substations available. Configure a remote host first."
            self.message_color = Colors.DIM
            return

        # For now, show status - full deployment UI coming with Substations Phase 4
        sub_count = len(self.substations)
        self.message = f"[{agent_name}] Ready for deployment. {sub_count} substation(s) available. (Phase 4)"
        self.message_color = Colors.XP

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
        elif self.menu_index == 9:  # Setup
            self.mode = "setup"
            self.setup_index = 0
            self._refresh_setup_stats()
        elif self.menu_index == 10:  # Quit
            raise KeyboardInterrupt

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

    def _handle_profile_input(self, key: int):
        """Handle profile viewer input."""
        if not self.profile_lines:
            self.mode = "view"
            return

        total_lines = len(self.profile_lines) - 1  # -1 for agent ID marker
        visible_lines = 20  # Approximate, will be recalculated in draw

        if key == curses.KEY_UP:
            self.profile_scroll = max(0, self.profile_scroll - 1)
        elif key == curses.KEY_DOWN:
            self.profile_scroll = min(max(0, total_lines - visible_lines), self.profile_scroll + 1)
        elif key == curses.KEY_PPAGE:  # Page Up
            self.profile_scroll = max(0, self.profile_scroll - visible_lines)
        elif key == curses.KEY_NPAGE:  # Page Down
            self.profile_scroll = min(max(0, total_lines - visible_lines), self.profile_scroll + visible_lines)
        elif key == curses.KEY_HOME:
            self.profile_scroll = 0
        elif key == curses.KEY_END:
            self.profile_scroll = max(0, total_lines - visible_lines)
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _draw_reroll(self, height: int, width: int):
        """Draw reroll/shuffle menu for agent attributes."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        ident = agent["identity"]

        self._draw_header(width)

        # Header
        try:
            self.stdscr.addstr(5, 2, f"DICE ROLL - {ident['name']}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            self.stdscr.addstr(5, width - 30, "Roll the dice to shuffle attributes",
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Reroll options
        reroll_options = [
            ("🎲 Reroll Skills", "Randomize skill point distribution"),
            ("🎲 Reroll Capabilities", "Shuffle capability assignments and proficiency"),
            ("🎲 Reroll Tools", "Re-assign starter tools"),
            ("🎲 Reroll Gold", "Randomize gold amount based on XP"),
            ("🎲 Reroll Profile", "New bio, motto, personality, interests"),
            ("⚠️  Full Regenerate", "Complete rebuild (keeps ID and tag only)"),
        ]

        list_y = 8
        box_height = len(reroll_options) + 4
        box_width = 60
        box_x = (width - box_width) // 2

        self._draw_box(list_y, box_x, box_height, box_width, "SELECT ATTRIBUTE TO REROLL")

        for i, (label, desc) in enumerate(reroll_options):
            row_y = list_y + 2 + i
            row = f" {pad(label, 30)} "

            if i == self.reroll_index:
                self._safe_addstr(row_y, box_x + 2, row,
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                # Highlight the warning option differently
                color = Colors.HEALTH if "Full" in label else Colors.STAT_VALUE
                self._safe_addstr(row_y, box_x + 2, row, curses.color_pair(color))

        # Show description for selected option
        if 0 <= self.reroll_index < len(reroll_options):
            _, desc = reroll_options[self.reroll_index]
            try:
                self.stdscr.addstr(list_y + box_height + 1, box_x + 2, desc,
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

        # Show current values preview
        preview_y = list_y + box_height + 4
        try:
            self.stdscr.addstr(preview_y, box_x, "Current Values:",
                               curses.color_pair(Colors.TITLE))
            preview_y += 1

            # Skills preview
            skills = agent.get("skills", [])
            skill_str = ", ".join([f"{self.skill_domains.get(s['domain_id'], {}).get('abbrev', '?')}:{s['points']}"
                                   for s in skills[:4]])
            self.stdscr.addstr(preview_y, box_x, f"Skills: {skill_str}...",
                               curses.color_pair(Colors.DIM))
            preview_y += 1

            # Capabilities preview
            caps = agent.get("capabilities", [])
            cap_str = ", ".join([f"{self.capability_refs.get(c['capability_id'], {}).get('icon', '?')}"
                                 for c in caps[:5]])
            self.stdscr.addstr(preview_y, box_x, f"Capabilities: {cap_str}",
                               curses.color_pair(Colors.DIM))
            preview_y += 1

            # Gold preview
            self.stdscr.addstr(preview_y, box_x, f"Gold: {agent['economy']['gold']:,}g  XP: {agent['economy']['xp']:,}",
                               curses.color_pair(Colors.DIM))

        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Roll | Q: Back to Agent")

    def _handle_reroll_input(self, key: int):
        """Handle reroll menu input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        option_count = 6

        if key == curses.KEY_UP:
            self.reroll_index = (self.reroll_index - 1) % option_count
        elif key == curses.KEY_DOWN:
            self.reroll_index = (self.reroll_index + 1) % option_count
        elif key in (curses.KEY_ENTER, 10, 13):
            self._execute_reroll()
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _execute_reroll(self):
        """Execute the selected reroll option with animation."""
        if not self.current_agent:
            return

        agent = self.current_agent
        reroll_labels = ["Skills", "Capabilities", "Tools", "Gold", "Profile", "FULL REGEN"]
        selected_label = reroll_labels[self.reroll_index]

        # Run shuffle animation
        self._animate_reroll(selected_label)

        if self.reroll_index == 0:
            # Reroll Skills
            self._reroll_skills(agent)
        elif self.reroll_index == 1:
            # Reroll Capabilities
            self._reroll_capabilities(agent)
        elif self.reroll_index == 2:
            # Reroll Tools
            self._reroll_tools(agent)
        elif self.reroll_index == 3:
            # Reroll Gold
            self._reroll_gold(agent)
        elif self.reroll_index == 4:
            # Reroll Profile
            self._reroll_profile(agent)
        elif self.reroll_index == 5:
            # Full Regenerate
            self._full_regenerate(agent)

        # Save changes and refresh
        self.ds.update("agent", agent["id"], agent)
        self._load_roster()

        # Re-find current agent
        for a in self.roster:
            if a["id"] == agent["id"]:
                self.current_agent = a
                break

        # Return to agent view (not roster)
        self.mode = "view"

    def _animate_reroll(self, label: str):
        """Animate the dice roll/shuffle effect."""
        import time
        import random

        height, width = self.stdscr.getmaxyx()
        box_width = 40
        box_height = 5
        box_x = (width - box_width) // 2
        box_y = (height - box_height) // 2

        # Dice faces and shuffle chars
        dice_frames = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"]
        shuffle_chars = ["◐", "◓", "◑", "◒"]

        # Animation: rolling dice
        for frame in range(12):
            self._draw_box(box_y, box_x, box_height, box_width, "ROLLING")

            # Draw dice animation
            dice_str = " ".join([random.choice(dice_frames) for _ in range(5)])
            spinner = shuffle_chars[frame % len(shuffle_chars)]

            try:
                self.stdscr.addstr(box_y + 2, box_x + 4,
                                   f"{spinner} {pad(label, 15)} {dice_str} {spinner}",
                                   curses.color_pair(Colors.GOLD) | curses.A_BOLD)
            except curses.error:
                pass

            self.stdscr.refresh()
            time.sleep(0.08)

        # Final frame: landed
        self._draw_box(box_y, box_x, box_height, box_width, "ROLLED!")
        try:
            final_dice = " ".join([random.choice(dice_frames) for _ in range(5)])
            self.stdscr.addstr(box_y + 2, box_x + 4,
                               f"  {label:<15} {final_dice}  ",
                               curses.color_pair(Colors.STAT_VALUE) | curses.A_BOLD)
        except curses.error:
            pass
        self.stdscr.refresh()
        time.sleep(0.3)

    def _reroll_skills(self, agent: dict):
        """Reroll skill point distribution."""
        skill_points = agent["economy"]["skill_points"]["assigned"]
        agent["skills"] = self.gen.random_skills(skill_points)
        self.message = "Skills rerolled!"
        self.message_color = Colors.STAT_VALUE

    def _reroll_capabilities(self, agent: dict):
        """Reroll capability assignments."""
        focus_id = agent["classification"]["focus_id"]
        rank_id = agent["classification"]["rank_id"]
        agent["capabilities"] = self.gen.random_capabilities(focus_id, rank_id)
        self.message = "Capabilities rerolled!"
        self.message_color = Colors.STAT_VALUE

    def _reroll_tools(self, agent: dict):
        """Reroll tool assignments."""
        rank_id = agent["classification"]["rank_id"]
        # Give some gold back from existing tools
        tool_refs = {t["id"]: t for t in self.ds.load_reference("tool")}
        refund = sum(tool_refs.get(t.get("tool_id"), {}).get("cost", 0) for t in agent.get("tools", []))

        # Re-assign tools with budget
        budget = min(100, agent["economy"]["gold"] + refund)
        new_tools, spent = self.gen.random_starter_tools(rank_id, budget)
        agent["tools"] = new_tools
        # Adjust gold (refund minus new purchases)
        agent["economy"]["gold"] += refund - spent
        self.message = f"Tools rerolled! ({refund}g refunded, {spent}g spent)"
        self.message_color = Colors.STAT_VALUE

    def _reroll_gold(self, agent: dict):
        """Reroll gold amount based on XP."""
        xp = agent["economy"]["xp"]
        config = self.ds.get_config()
        base_gold = config.get("defaults", {}).get("starting_gold", 50)
        new_gold = self.gen._scale_gold(xp, base_gold)
        old_gold = agent["economy"]["gold"]
        agent["economy"]["gold"] = new_gold
        diff = new_gold - old_gold
        diff_str = f"+{diff}" if diff >= 0 else str(diff)
        self.message = f"Gold rerolled: {old_gold} → {new_gold} ({diff_str})"
        self.message_color = Colors.GOLD

    def _reroll_profile(self, agent: dict):
        """Reroll profile (bio, motto, personality, interests)."""
        name = agent["identity"]["name"]

        mottos = self.gen._get_lex('mottos')
        interests = self.gen._get_lex('interests')
        zodiac = self.gen._get_lex('zodiac')

        import random
        agent["profile"] = {
            "bio": self.gen.random_bio(name),
            "avatar": agent.get("profile", {}).get("avatar"),  # Keep avatar
            "motto": random.choice(mottos) if mottos else "Knowledge through observation.",
            "personality": self.gen.random_personality(),
            "interests": random.sample(interests, min(random.randint(2, 4), len(interests))) if interests else [],
            "zodiac": random.choice(zodiac) if zodiac else "Orion",
            "quotes": agent.get("profile", {}).get("quotes", [])  # Keep quotes
        }
        self.message = "Profile rerolled!"
        self.message_color = Colors.STAT_VALUE

    def _full_regenerate(self, agent: dict):
        """Full regenerate - keeps ID and tag only."""
        # Preserve key identifiers
        old_id = agent["id"]
        old_tag = agent["identity"]["tag"]
        old_created = agent["audit"]["created_at"]

        # Generate new agent
        new_agent = self.gen.generate()

        # Restore identifiers
        new_agent["id"] = old_id
        new_agent["identity"]["tag"] = old_tag
        new_agent["audit"]["created_at"] = old_created

        # Copy to current agent (update in place)
        agent.clear()
        agent.update(new_agent)

        self.message = "Full regenerate complete!"
        self.message_color = Colors.HEALTH

    def _handle_shop_input(self, key: int):
        """Handle tool shop input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        tools = self.ds.load_reference("tool")
        owned_tools = {t.get("tool_id") for t in self.current_agent.get("tools", [])}
        available = [t for t in tools if t["id"] not in owned_tools]
        available.sort(key=lambda t: (t.get("cost", 0), t.get("label", "")))

        if key == curses.KEY_UP:
            self.shop_index = max(0, self.shop_index - 1)
        elif key == curses.KEY_DOWN:
            self.shop_index = min(len(available) - 1, self.shop_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            self._purchase_tool(available)
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _purchase_tool(self, available: list):
        """Purchase selected tool for current agent."""
        if not available or self.shop_index >= len(available):
            return

        tool = available[self.shop_index]
        agent = self.current_agent
        gold = agent["economy"]["gold"]
        rank_id = agent["classification"]["rank_id"]

        cost = tool.get("cost", 0)
        min_rank = tool.get("requirements", {}).get("min_rank_id", 1)

        if rank_id < min_rank:
            self.message = f"Requires rank {min_rank}+"
            self.message_color = Colors.HEALTH
            return

        if gold < cost:
            self.message = f"Not enough gold (need {cost})"
            self.message_color = Colors.HEALTH
            return

        # Purchase the tool
        from datetime import datetime, timezone
        agent["economy"]["gold"] -= cost
        agent["tools"].append({
            "tool_id": tool["id"],
            "assigned_at": datetime.now(timezone.utc).isoformat(),
            "assigned_by": "operator",
            "proficiency": 2
        })

        # Save agent
        self.ds.update("agent", agent["id"], agent)
        self._load_roster()
        # Re-find current agent
        for a in self.roster:
            if a["id"] == agent["id"]:
                self.current_agent = a
                break

        self.message = f"Purchased {tool.get('label', 'tool')} for {cost}g"
        self.message_color = Colors.STAT_VALUE
        self.shop_index = max(0, self.shop_index - 1)

    def _handle_skills_input(self, key: int):
        """Handle capability upgrade input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        caps = self.current_agent.get("capabilities", [])

        if key == curses.KEY_UP:
            self.skill_index = max(0, self.skill_index - 1)
        elif key == curses.KEY_DOWN:
            self.skill_index = min(len(caps) - 1, self.skill_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13):
            self._upgrade_capability()
        elif key == ord('q') or key == ord('Q'):
            self.mode = "view"

    def _upgrade_capability(self):
        """Upgrade selected capability for current agent."""
        agent = self.current_agent
        caps = agent.get("capabilities", [])

        if not caps or self.skill_index >= len(caps):
            return

        cap = caps[self.skill_index]
        xp = agent["economy"]["xp"]
        prof = cap.get("proficiency", 1)

        if prof >= 5:
            self.message = "Already at max level"
            self.message_color = Colors.DIM
            return

        cap_refs = {c["id"]: c for c in self.ds.load_reference("capability")}
        cap_ref = cap_refs.get(cap.get("capability_id"), {})
        xp_costs = cap_ref.get("xp_costs", [100, 300, 600, 1000, 1500])

        cost = xp_costs[prof] if prof < len(xp_costs) else 9999

        if xp < cost:
            self.message = f"Need {cost - xp} more XP"
            self.message_color = Colors.HEALTH
            return

        # Upgrade
        agent["economy"]["xp"] -= cost
        cap["proficiency"] = prof + 1

        # Save agent
        self.ds.update("agent", agent["id"], agent)
        self._load_roster()
        # Re-find current agent
        for a in self.roster:
            if a["id"] == agent["id"]:
                self.current_agent = a
                break

        self.message = f"Upgraded to level {prof + 1}! (-{cost} XP)"
        self.message_color = Colors.STAT_VALUE

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

    def _enter_substations_mode(self):
        """Enter substations mode, initializing vault if needed."""
        if not HAS_REMOTE:
            err = REMOTE_ERROR or "dependencies missing"
            self.message = f"Substations unavailable: {err[:45]}"
            self.message_color = Colors.HEALTH
            return

        # Initialize vault if not done
        if self.vault is None:
            try:
                self.vault = CredentialVault(VaultScope.OPERATOR)
            except ImportError as e:
                self.message = f"Missing: {str(e)[:50]}"
                self.message_color = Colors.HEALTH
                return
            except Exception as e:
                self.message = f"Vault error: {str(e)[:45]}"
                self.message_color = Colors.HEALTH
                return

        # Check if vault needs initialization or unlock
        try:
            if not self.vault.is_initialized():
                self.mode = "vault_unlock"
                self.message = "Vault not initialized - set a passphrase"
                return

            if not self.vault.is_unlocked():
                self.mode = "vault_unlock"
                self.message = "Enter vault passphrase"
                return
        except Exception as e:
            self.message = f"Vault error: {str(e)[:45]}"
            self.message_color = Colors.HEALTH
            return

        self.vault_unlocked = True
        self._load_substations()
        self.mode = "substations"

    def _load_substations(self):
        """Load substations from local storage."""
        results = self.ds.query("substation", sort_key="identity.name")
        self.substations = results.data

    def _draw_substations(self, height: int, width: int):
        """Draw substations list view."""
        self._draw_header(width)

        # Title with context
        substation_name = self.config.get("substation", {}).get("name", "HQ")
        try:
            self.stdscr.addstr(5, 2, f"SUBSTATIONS ({len(self.substations)} registered)",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
            # Show current substation context and vault status
            vault_status = "UNLOCKED" if self.vault_unlocked else "LOCKED"
            vault_color = Colors.STAT_VALUE if self.vault_unlocked else Colors.HEALTH
            self.stdscr.addstr(5, width - 35, f"[{substation_name}]",
                               curses.color_pair(Colors.DIM))
            self.stdscr.addstr(5, width - 18, f"Vault: {vault_status}",
                               curses.color_pair(vault_color))
        except curses.error:
            pass

        if not self.substations:
            try:
                self.stdscr.addstr(10, (width - 30) // 2, "No substations registered.",
                                   curses.color_pair(Colors.DIM))
                self.stdscr.addstr(12, (width - 40) // 2, "Press N to add a new substation",
                                   curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass
        else:
            # List header
            list_y = 7
            header = f"{'NAME':<20} {'HOSTNAME':<25} {'STATUS':<10} {'AGENTS':>6} {'ENVIRONMENT':<12}"
            try:
                self.stdscr.addstr(list_y, 4, header[:width-8],
                                   curses.color_pair(Colors.TITLE) | curses.A_BOLD)
                self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

            # List substations
            list_height = height - 12
            visible_count = list_height - 2
            start_idx = max(0, self.substation_index - visible_count + 3)

            for i, sub in enumerate(self.substations[start_idx:start_idx + visible_count]):
                row_y = list_y + 2 + i
                if row_y >= list_y + list_height:
                    break

                ident = sub.get("identity", {})
                conn = sub.get("connection", {})
                status = sub.get("status", {})
                deployments = sub.get("deployments", [])

                name = ident.get("name", "?")[:20]
                hostname = conn.get("hostname", "?")[:25]
                state = status.get("state", "unknown")[:10]
                agent_count = len(deployments)
                env = ident.get("environment", "?")[:12]

                # Status color
                if state == "online":
                    state_color = Colors.STAT_VALUE
                elif state == "offline":
                    state_color = Colors.HEALTH
                else:
                    state_color = Colors.DIM

                row = f"{name:<20} {hostname:<25} {state:<10} {agent_count:>6} {env:<12}"

                color = (Colors.HIGHLIGHT if start_idx + i == self.substation_index
                         else Colors.NORMAL)
                self._safe_addstr(row_y, 4, f" {row[:width-10]} ",
                                  curses.color_pair(color))

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "ENTER: View | N: New | E: Edit | C: Connect | K: Keys | X: Delete | Q: Back")

    def _draw_substation_view(self, height: int, width: int):
        """Draw detailed substation view."""
        if not self.current_substation:
            self.mode = "substations"
            return

        sub = self.current_substation
        ident = sub.get("identity", {})
        conn = sub.get("connection", {})
        perms = sub.get("permissions", {})
        status = sub.get("status", {})
        deployments = sub.get("deployments", [])

        self._draw_header(width)

        # Title
        try:
            self.stdscr.addstr(5, 2, f"SUBSTATION: {ident.get('name', '?')}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Two-column layout
        col1_x = 4
        col2_x = width // 2 + 2
        col_width = width // 2 - 6

        # Column 1: Connection & Status
        y = 7
        try:
            self.stdscr.addstr(y, col1_x, "CONNECTION", curses.color_pair(Colors.TITLE))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Hostname: {conn.get('hostname', '?')}",
                               curses.color_pair(Colors.STAT_VALUE))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Port: {conn.get('port', 22)}",
                               curses.color_pair(Colors.STAT_VALUE))
            y += 1
            self.stdscr.addstr(y, col1_x + 2, f"Username: {conn.get('username', '?')}",
                               curses.color_pair(Colors.STAT_VALUE))
            y += 1
            cred_id = conn.get('credential_id', 'none')[:8]
            self.stdscr.addstr(y, col1_x + 2, f"Credential: {cred_id}...",
                               curses.color_pair(Colors.DIM))
            y += 2

            self.stdscr.addstr(y, col1_x, "STATUS", curses.color_pair(Colors.TITLE))
            y += 1
            state = status.get('state', 'unknown')
            state_color = Colors.STAT_VALUE if state == 'online' else Colors.HEALTH
            self.stdscr.addstr(y, col1_x + 2, f"State: {state}",
                               curses.color_pair(state_color))
            y += 1
            last_check = status.get('last_check', 'never')[:19]
            self.stdscr.addstr(y, col1_x + 2, f"Last Check: {last_check}",
                               curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Column 2: Permissions & Deployments
        y = 7
        try:
            self.stdscr.addstr(y, col2_x, "PERMISSIONS", curses.color_pair(Colors.TITLE))
            y += 1
            read_icon = "+" if perms.get('read_enabled', True) else "-"
            write_icon = "+" if perms.get('write_enabled', False) else "-"
            deploy_icon = "+" if perms.get('deploy_enabled', False) else "-"
            self.stdscr.addstr(y, col2_x + 2, f"[{read_icon}] Read   [{write_icon}] Write   [{deploy_icon}] Deploy",
                               curses.color_pair(Colors.STAT_VALUE))
            y += 1
            confirm = "Required" if perms.get('requires_confirmation', True) else "Not required"
            self.stdscr.addstr(y, col2_x + 2, f"Confirmation: {confirm}",
                               curses.color_pair(Colors.DIM))
            y += 2

            self.stdscr.addstr(y, col2_x, f"DEPLOYMENTS ({len(deployments)})", curses.color_pair(Colors.TITLE))
            y += 1
            if deployments:
                for dep in deployments[:5]:
                    agent_id = dep.get('agent_id', '?')[:8]
                    dep_status = dep.get('status', 'unknown')
                    self.stdscr.addstr(y, col2_x + 2, f"{agent_id}... [{dep_status}]",
                                       curses.color_pair(Colors.STAT_VALUE))
                    y += 1
            else:
                self.stdscr.addstr(y, col2_x + 2, "No agents deployed",
                                   curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "C: Connect | E: Edit | P: Permissions | D: Deploy | X: Delete | Q: Back")

    def _draw_substation_edit(self, height: int, width: int):
        """Draw substation edit view."""
        self._draw_header(width)

        if not self.current_substation:
            return

        ident = self.current_substation.get("identity", {})
        conn = self.current_substation.get("connection", {})

        # Title
        try:
            self.stdscr.addstr(5, 2, f"EDIT SUBSTATION: {ident.get('name', '?')}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Field definitions
        fields = [
            ("Name", ident.get("name", "")),
            ("Codename", ident.get("codename", "")),
            ("Environment", ident.get("environment", "development")),
            ("Hostname", conn.get("hostname", "")),
            ("Port", str(conn.get("port", 22))),
            ("Username", conn.get("username", "")),
        ]

        y = 8
        for i, (label, value) in enumerate(fields):
            is_selected = (i == self.substation_edit_index)

            # If editing this field, show edit buffer
            if is_selected and self.editing:
                display_value = self.edit_buffer + "_"
            else:
                display_value = value

            if is_selected:
                self._safe_addstr(y, 4, f" {label:12}: {display_value:<40} ",
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(y, 4, f" {label:12}: ",
                                  curses.color_pair(Colors.STAT_LABEL))
                self._safe_addstr(y, 4 + 16, f"{display_value:<40}",
                                  curses.color_pair(Colors.STAT_VALUE))
            y += 1

        # Help text
        try:
            if self.editing:
                help_text = "Type to edit | ENTER: Save | ESC: Cancel"
            else:
                help_text = "UP/DOWN: Navigate | ENTER: Edit field | Q: Back"
            self.stdscr.addstr(y + 2, 4, help_text, curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "ENTER: Edit | Q: Back to View")

    def _draw_substation_perms(self, height: int, width: int):
        """Draw substation permissions edit view."""
        self._draw_header(width)

        if not self.current_substation:
            return

        ident = self.current_substation.get("identity", {})
        perms = self.current_substation.get("permissions", {})

        # Title
        try:
            self.stdscr.addstr(5, 2, f"PERMISSIONS: {ident.get('name', '?')}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Permission definitions with descriptions
        perm_items = [
            ("read_enabled", "Read Operations", "Allow read-only commands (ls, cat, ps, etc.)"),
            ("write_enabled", "Write Operations", "Allow file modifications and writes (CAUTION)"),
            ("deploy_enabled", "Deploy Operations", "Allow agent deployment to this substation"),
            ("requires_confirmation", "Require Confirmation", "Prompt before executing write/deploy ops"),
        ]

        y = 8
        for i, (key, label, desc) in enumerate(perm_items):
            is_selected = (i == self.substation_perm_index)
            is_enabled = perms.get(key, False)

            # Checkbox display
            checkbox = "[X]" if is_enabled else "[ ]"

            # Color based on permission type
            if key == "write_enabled":
                value_color = Colors.HEALTH if is_enabled else Colors.DIM
            elif key == "deploy_enabled":
                value_color = Colors.GOLD if is_enabled else Colors.DIM
            else:
                value_color = Colors.STAT_VALUE if is_enabled else Colors.DIM

            if is_selected:
                self._safe_addstr(y, 4, f" {checkbox} {label:<25} ",
                                  curses.color_pair(Colors.HIGHLIGHT))
                self._safe_addstr(y + 1, 8, desc, curses.color_pair(Colors.DIM))
            else:
                self._safe_addstr(y, 4, f" {checkbox} ", curses.color_pair(value_color))
                self._safe_addstr(y, 4 + len(checkbox) + 2, f"{label:<25}",
                                  curses.color_pair(Colors.STAT_LABEL))
            y += 2

        # Safety warning
        try:
            self.stdscr.addstr(y + 2, 4, "WARNING: Write and Deploy require explicit enable",
                               curses.color_pair(Colors.HEALTH))
        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER/SPACE: Toggle | Q: Back to View")

    def _draw_ssh_keys(self, height: int, width: int):
        """Draw SSH key discovery view."""
        self._draw_header(width)

        try:
            self.stdscr.addstr(5, 2, f"SSH KEY DISCOVERY ({len(self.ssh_keys)} keys found)",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        if not self.ssh_keys:
            try:
                self.stdscr.addstr(10, (width - 30) // 2, "No SSH keys found in ~/.ssh/",
                                   curses.color_pair(Colors.DIM))
                self.stdscr.addstr(12, (width - 40) // 2, "Press R to rescan or G to generate",
                                   curses.color_pair(Colors.STAT_VALUE))
            except curses.error:
                pass
        else:
            # List header
            list_y = 7
            header = f"{'NAME':<30} {'TYPE':<10} {'BITS':>6} {'STATUS':<15} {'FINGERPRINT':<30}"
            try:
                self.stdscr.addstr(list_y, 4, header[:width-8],
                                   curses.color_pair(Colors.TITLE) | curses.A_BOLD)
                self.stdscr.addstr(list_y + 1, 4, "-" * min(width - 8, len(header)),
                                   curses.color_pair(Colors.DIM))
            except curses.error:
                pass

            # List keys
            list_height = height - 12
            visible_count = list_height - 2

            for i, key in enumerate(self.ssh_keys[:visible_count]):
                row_y = list_y + 2 + i
                if row_y >= list_y + list_height:
                    break

                name = key.display_name[:30] if hasattr(key, 'display_name') else key.filename[:30]
                key_type = key.key_type.value.upper()[:10] if hasattr(key, 'key_type') else "?"
                bits = key.bits if hasattr(key, 'bits') else 0
                status = key.status.value[:15] if hasattr(key, 'status') else "?"
                fp = key.fingerprint[:30] if hasattr(key, 'fingerprint') else ""

                # Status color
                if hasattr(key, 'status'):
                    if key.status == KeyStatus.AVAILABLE:
                        status_color = Colors.STAT_VALUE
                    elif key.status == KeyStatus.IMPORTED:
                        status_color = Colors.GOLD
                    else:
                        status_color = Colors.DIM
                else:
                    status_color = Colors.DIM

                row = f"{name:<30} {key_type:<10} {bits:>6} {status:<15} {fp:<30}"

                color = Colors.HIGHLIGHT if i == self.ssh_key_index else status_color
                self._safe_addstr(row_y, 4, f" {row[:width-10]} ",
                                  curses.color_pair(color))

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "UP/DOWN: Navigate | ENTER: Import | R: Rescan | G: Generate | Q: Back")

    def _draw_vault_unlock(self, height: int, width: int):
        """Draw vault unlock/initialize prompt."""
        self._draw_header(width)

        box_height = 10
        box_width = 50
        box_y = (height - box_height) // 2
        box_x = (width - box_width) // 2

        is_init = not self.vault.is_initialized() if self.vault else True
        title = "INITIALIZE VAULT" if is_init else "UNLOCK VAULT"

        self._draw_box(box_y, box_x, box_height, box_width, title)

        try:
            if is_init:
                self.stdscr.addstr(box_y + 2, box_x + 4, "Create a master passphrase:",
                                   curses.color_pair(Colors.STAT_LABEL))
                self.stdscr.addstr(box_y + 3, box_x + 4, "(Used to encrypt your credentials)",
                                   curses.color_pair(Colors.DIM))
            else:
                self.stdscr.addstr(box_y + 2, box_x + 4, "Enter vault passphrase:",
                                   curses.color_pair(Colors.STAT_LABEL))

            # Password input field
            passphrase_display = "*" * len(self.edit_buffer)
            self.stdscr.addstr(box_y + 5, box_x + 4, f"[{passphrase_display:<38}]",
                               curses.color_pair(Colors.HIGHLIGHT if self.editing else Colors.NORMAL))

            if self.message:
                msg_color = Colors.HEALTH if "error" in self.message.lower() or "invalid" in self.message.lower() else Colors.STAT_VALUE
                self.stdscr.addstr(box_y + 7, box_x + 4, self.message[:box_width-8],
                                   curses.color_pair(msg_color))
        except curses.error:
            pass

        self._draw_footer(height, width, "Type passphrase | ENTER: Submit | ESC: Cancel")

    def _handle_substations_input(self, key: int):
        """Handle substations list input."""
        if key == curses.KEY_UP and self.substations:
            self.substation_index = max(0, self.substation_index - 1)
        elif key == curses.KEY_DOWN and self.substations:
            self.substation_index = min(len(self.substations) - 1, self.substation_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13) and self.substations:
            self.current_substation = self.substations[self.substation_index]
            self.mode = "substation_view"
        elif key == ord('n') or key == ord('N'):
            self._create_substation()
        elif key == ord('k') or key == ord('K'):
            self._scan_ssh_keys()
            self.mode = "ssh_keys"
        elif key == ord('c') or key == ord('C'):
            if self.substations:
                self._connect_substation(self.substations[self.substation_index])
        elif key == ord('e') or key == ord('E'):
            # Quick edit from list
            if self.substations:
                self.current_substation = self.substations[self.substation_index]
                self.substation_edit_index = 0
                self.editing = False
                self.edit_buffer = ""
                self.mode = "substation_edit"
        elif key == ord('x') or key == ord('X'):
            # Quick delete from list
            if self.substations:
                self._delete_substation(self.substations[self.substation_index])
        elif key == ord('q') or key == ord('Q'):
            self.mode = "menu"

    def _handle_substation_view_input(self, key: int):
        """Handle substation detail view input."""
        if key == ord('c') or key == ord('C'):
            self._connect_substation(self.current_substation)
        elif key == ord('d') or key == ord('D'):
            self._deploy_from_substation(self.current_substation)
        elif key == ord('e') or key == ord('E'):
            # Enter edit mode
            self.substation_edit_index = 0
            self.editing = False
            self.edit_buffer = ""
            self.mode = "substation_edit"
        elif key == ord('p') or key == ord('P'):
            # Enter permissions mode
            self.substation_perm_index = 0
            self.mode = "substation_perms"
        elif key == ord('x') or key == ord('X'):
            self._delete_substation(self.current_substation)
            self.mode = "substations"
        elif key == ord('q') or key == ord('Q'):
            self.mode = "substations"

    def _handle_ssh_keys_input(self, key: int):
        """Handle SSH keys view input."""
        if key == curses.KEY_UP and self.ssh_keys:
            self.ssh_key_index = max(0, self.ssh_key_index - 1)
        elif key == curses.KEY_DOWN and self.ssh_keys:
            self.ssh_key_index = min(len(self.ssh_keys) - 1, self.ssh_key_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13) and self.ssh_keys:
            self._import_ssh_key(self.ssh_keys[self.ssh_key_index])
        elif key == ord('r') or key == ord('R'):
            self._scan_ssh_keys()
        elif key == ord('q') or key == ord('Q'):
            self.mode = "substations"

    def _handle_vault_unlock_input(self, key: int):
        """Handle vault unlock/init input."""
        if key == 27:  # ESC
            self.edit_buffer = ""
            self.editing = False
            self.mode = "menu"
        elif key in (curses.KEY_ENTER, 10, 13):
            if self.edit_buffer:
                self._submit_vault_passphrase()
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.edit_buffer = self.edit_buffer[:-1]
        elif 32 <= key <= 126:  # Printable ASCII
            self.edit_buffer += chr(key)
            self.editing = True

    def _handle_substation_edit_input(self, key: int):
        """Handle substation edit view input."""
        # Fields: 0=name, 1=codename, 2=environment, 3=hostname, 4=port, 5=username
        field_count = 6

        if self.editing:
            # Text input mode
            if key == 27:  # ESC
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                self._save_substation_field()
                self.editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:  # Printable ASCII
                self.edit_buffer += chr(key)
        else:
            # Navigation mode
            if key == curses.KEY_UP:
                self.substation_edit_index = (self.substation_edit_index - 1) % field_count
            elif key == curses.KEY_DOWN:
                self.substation_edit_index = (self.substation_edit_index + 1) % field_count
            elif key in (curses.KEY_ENTER, 10, 13):
                self._start_substation_field_edit()
            elif key == ord('q') or key == ord('Q'):
                self.mode = "substation_view"

    def _start_substation_field_edit(self):
        """Start editing the selected substation field."""
        if not self.current_substation:
            return

        ident = self.current_substation.get("identity", {})
        conn = self.current_substation.get("connection", {})

        field_values = {
            0: ident.get("name", ""),
            1: ident.get("codename", ""),
            2: ident.get("environment", "development"),
            3: conn.get("hostname", ""),
            4: str(conn.get("port", 22)),
            5: conn.get("username", ""),
        }

        self.edit_buffer = field_values.get(self.substation_edit_index, "")
        self.editing = True

    def _save_substation_field(self):
        """Save the current edit to substation."""
        if not self.current_substation:
            return

        field_map = {
            0: ("identity", "name"),
            1: ("identity", "codename"),
            2: ("identity", "environment"),
            3: ("connection", "hostname"),
            4: ("connection", "port"),
            5: ("connection", "username"),
        }

        section, field = field_map.get(self.substation_edit_index, (None, None))
        if section and field:
            if section not in self.current_substation:
                self.current_substation[section] = {}

            value = self.edit_buffer
            # Handle port as integer
            if field == "port":
                try:
                    value = int(value)
                except ValueError:
                    value = 22

            self.current_substation[section][field] = value

            # Update timestamp
            from datetime import datetime, timezone
            if "audit" not in self.current_substation:
                self.current_substation["audit"] = {}
            self.current_substation["audit"]["updated_at"] = datetime.now(timezone.utc).isoformat()

            try:
                self.ds.update("substation", self.current_substation["id"], self.current_substation)
                self._load_substations()
                self.message = f"{field.title()} updated"
                self.message_color = Colors.STAT_VALUE
            except Exception as e:
                self.message = f"Save error: {str(e)[:30]}"
                self.message_color = Colors.HEALTH

        self.edit_buffer = ""

    def _handle_substation_perms_input(self, key: int):
        """Handle substation permissions view input."""
        # Permissions: 0=read, 1=write, 2=deploy, 3=requires_confirmation
        perm_count = 4

        if key == curses.KEY_UP:
            self.substation_perm_index = (self.substation_perm_index - 1) % perm_count
        elif key == curses.KEY_DOWN:
            self.substation_perm_index = (self.substation_perm_index + 1) % perm_count
        elif key in (curses.KEY_ENTER, 10, 13) or key == ord(' '):
            self._toggle_substation_perm()
        elif key == ord('q') or key == ord('Q'):
            self.mode = "substation_view"

    def _toggle_substation_perm(self):
        """Toggle the selected permission."""
        if not self.current_substation:
            return

        perm_map = {
            0: "read_enabled",
            1: "write_enabled",
            2: "deploy_enabled",
            3: "requires_confirmation",
        }

        perm_key = perm_map.get(self.substation_perm_index)
        if not perm_key:
            return

        perms = self.current_substation.get("permissions", {})
        current_value = perms.get(perm_key, False)
        perms[perm_key] = not current_value
        self.current_substation["permissions"] = perms

        # Update timestamp
        from datetime import datetime, timezone
        if "audit" not in self.current_substation:
            self.current_substation["audit"] = {}
        self.current_substation["audit"]["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            self.ds.update("substation", self.current_substation["id"], self.current_substation)
            self._load_substations()
            status = "enabled" if not current_value else "disabled"
            self.message = f"{perm_key.replace('_', ' ').title()} {status}"
            self.message_color = Colors.STAT_VALUE
        except Exception as e:
            self.message = f"Save error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    def _submit_vault_passphrase(self):
        """Submit vault passphrase for init or unlock."""
        if not self.vault:
            return

        try:
            if not self.vault.is_initialized():
                self.vault.initialize(self.edit_buffer)
                self.message = "Vault initialized successfully"
                self.message_color = Colors.STAT_VALUE
                self.vault_unlocked = True
            else:
                self.vault.unlock(self.edit_buffer)
                self.message = "Vault unlocked"
                self.message_color = Colors.STAT_VALUE
                self.vault_unlocked = True

            self.edit_buffer = ""
            self.editing = False
            self._load_substations()
            self.mode = "substations"

        except Exception as e:
            self.message = f"Error: {str(e)[:40]}"
            self.message_color = Colors.HEALTH
            self.edit_buffer = ""

    def _scan_ssh_keys(self):
        """Scan for SSH keys."""
        if not HAS_REMOTE:
            return

        try:
            discovery = SSHKeyDiscovery()
            self.ssh_keys = discovery.scan()
            self.ssh_key_index = 0
            self.message = f"Found {len(self.ssh_keys)} SSH keys"
            self.message_color = Colors.STAT_VALUE
        except Exception as e:
            self.message = f"Scan error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH
            self.ssh_keys = []

    def _import_ssh_key(self, key_info):
        """Import an SSH key to the vault."""
        if not self.vault or not self.vault_unlocked:
            self.message = "Vault must be unlocked"
            self.message_color = Colors.HEALTH
            return

        try:
            discovery = SSHKeyDiscovery()
            cred_id = discovery.import_to_vault(key_info, self.vault)
            self.message = f"Key imported: {cred_id[:8]}..."
            self.message_color = Colors.STAT_VALUE
            # Re-scan to update status
            self._scan_ssh_keys()
        except Exception as e:
            self.message = f"Import error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    def _create_substation(self):
        """Create a new substation (placeholder - would need input form)."""
        import uuid
        from datetime import datetime, timezone

        # Create basic substation
        sub_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        substation = {
            "id": sub_id,
            "identity": {
                "name": f"New Substation",
                "codename": f"sub.{sub_id[:8]}",
                "environment": "development"
            },
            "connection": {
                "hostname": "localhost",
                "port": 22,
                "username": os.environ.get("USER", "user")
            },
            "permissions": {
                "read_enabled": True,
                "write_enabled": False,
                "deploy_enabled": False,
                "requires_confirmation": True
            },
            "status": {
                "state": "unknown"
            },
            "deployments": [],
            "audit": {
                "created_at": now,
                "updated_at": now,
                "retired_at": None
            }
        }

        try:
            self.ds.create("substation", substation, validate=False)
            self._load_substations()
            # Set current and enter edit mode immediately
            self.current_substation = substation
            self.substation_edit_index = 0
            self.editing = False
            self.edit_buffer = ""
            self.mode = "substation_edit"
            self.message = "New substation - configure connection"
            self.message_color = Colors.STAT_VALUE
        except Exception as e:
            self.message = f"Create error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    def _connect_substation(self, substation):
        """Open an interactive SSH session to the substation: suspend the
        HUD, hand the terminal to ssh(1), resume on exit. Failures stay on
        screen (verbose stderr) until the operator returns."""
        import subprocess
        from datetime import datetime, timezone
        from lib.remote.ssh import ssh_command

        conn = substation.get("connection") or {}
        if not conn.get("hostname"):
            self.message = "No hostname configured - E: Edit first"
            self.message_color = Colors.HEALTH
            return

        argv = ssh_command(conn)
        codename = substation.get("identity", {}).get("codename", "?")
        exit_code = None

        curses.def_prog_mode()
        curses.endwin()
        try:
            print("=" * 60)
            print(f"CONNECT: {codename}")
            print("=" * 60)
            user = conn.get("username") or ""
            target = f"{user}@{conn['hostname']}" if user else conn["hostname"]
            print(f"target:  {target}:{conn.get('port', 22)}")
            jump = conn.get("jump_host") or {}
            if jump.get("hostname"):
                print(f"jump:    {jump.get('username', '')}@"
                      f"{jump['hostname']}:{jump.get('port', 22)}")
            print(f"command: {' '.join(argv)}")
            print("-" * 60, flush=True)
            try:
                exit_code = subprocess.call(argv)
            except FileNotFoundError:
                print("ERROR: ssh binary not found on PATH")
            print("-" * 60)
            if exit_code == 0:
                print("[session closed cleanly]")
            elif exit_code == 255:
                print("[ssh exited 255: connection/auth failure - "
                      "stderr above tells the story]")
            elif exit_code is not None:
                print(f"[session closed - remote exit {exit_code}]")
            try:
                input("Press ENTER to return to the HUD...")
            except (EOFError, KeyboardInterrupt):
                pass
        finally:
            curses.reset_prog_mode()
            self.stdscr.refresh()

        now = datetime.now(timezone.utc).isoformat()
        status = dict(substation.get("status") or {})
        status["last_check"] = now
        if exit_code is None:
            self.message = "ssh binary not found on PATH"
            self.message_color = Colors.HEALTH
        elif exit_code == 255:
            status["state"] = "unreachable"
            self.message = f"{codename}: unreachable (ssh exit 255)"
            self.message_color = Colors.HEALTH
        else:
            # Any other exit means the session ran - the code is whatever
            # the remote shell last returned
            status["state"] = "online"
            status["last_connected"] = now
            self.message = f"{codename}: session closed (exit {exit_code})"
            self.message_color = Colors.STAT_VALUE
        try:
            self.ds.update("substation", substation["id"], {"status": status})
            self._load_substations()
            if self.current_substation:
                self.current_substation = next(
                    (s for s in self.substations
                     if s["id"] == substation["id"]), self.current_substation)
        except Exception as e:
            self.message = f"Status update error: {str(e)[:40]}"
            self.message_color = Colors.HEALTH

    def _deploy_from_substation(self, substation):
        """Deploy prerequisites, checked honestly - the first missing one
        is reported instead of the key fizzling."""
        try:
            from lib.remote.ssh import HAS_ASYNCSSH
        except ImportError:
            HAS_ASYNCSSH = False

        perms = substation.get("permissions") or {}
        conn = substation.get("connection") or {}
        if not HAS_ASYNCSSH:
            self.message = ("Deploy blocked: asyncssh not installed "
                            "(pip install asyncssh)")
        elif not perms.get("deploy_enabled"):
            self.message = "Deploy blocked: deploy disabled - P: Permissions"
        elif not conn.get("credential_id"):
            self.message = "Deploy blocked: no credential bound to this host"
        elif not (self.vault and self.vault.is_unlocked()):
            self.message = "Deploy blocked: vault is locked"
        else:
            self.message = ("Deploy prerequisites met - remote deploy ships "
                            "with access grants (phase D)")
            self.message_color = Colors.STAT_VALUE
            return
        self.message_color = Colors.HEALTH

    def _delete_substation(self, substation):
        """Delete a substation."""
        try:
            self.ds.delete("substation", substation["id"], hard=True)
            self._load_substations()
            self.substation_index = max(0, self.substation_index - 1)
            self.current_substation = None
            self.message = "Substation deleted"
            self.message_color = Colors.HEALTH
        except Exception as e:
            self.message = f"Delete error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

    # ==========================================================================
    # Crues Mode Methods
    # ==========================================================================

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
                crue_type = crue.get("type", "team")[:18]
                member_count = crue.get("capacity", {}).get("current_members", 0)
                is_active = crue.get("is_active", True)
                status = "Active" if is_active else "Inactive"

                # Get crue_type icon if available
                type_slug = crue.get("type_slug")
                crue_type_data = None
                if type_slug:
                    for ct in self.crue_types.values():
                        if ct.get("slug") == type_slug:
                            crue_type_data = ct
                            break

                icon = crue_type_data.get("icon", "👥") if crue_type_data else "👥"

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
            icon = "👥"
            type_slug = crue.get("type_slug")
            if type_slug:
                for ct in self.crue_types.values():
                    if ct.get("slug") == type_slug:
                        icon = ct.get("icon", "👥")
                        break
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
            self.stdscr.addstr(y, col1_x + 2, f"Type: {crue.get('type', 'team')}",
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

    def _draw_crue_edit(self, height: int, width: int):
        """Draw crue edit view."""
        self._draw_header(width)

        if not self.current_crue:
            return

        crue = self.current_crue

        # Title
        try:
            self.stdscr.addstr(5, 2, f"EDIT CRUE: {crue.get('name', '?')}",
                               curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        except curses.error:
            pass

        # Editable fields
        fields = [
            ("Name", crue.get("name", "")),
            ("Slug", crue.get("slug", "")),
            ("Type", crue.get("type", "team")),
            ("Description", crue.get("description", "")[:40]),
            ("Max Members", str(crue.get("capacity", {}).get("max_members", 10))),
        ]

        y = 8
        for i, (label, value) in enumerate(fields):
            is_selected = (i == self.crue_edit_index)

            if is_selected and self.editing:
                display_value = self.edit_buffer + "_"
            else:
                display_value = value

            if is_selected:
                self._safe_addstr(y, 4, f" {label:15}: {display_value:<45} ",
                                  curses.color_pair(Colors.HIGHLIGHT))
            else:
                self._safe_addstr(y, 4, f" {label:15}: ",
                                  curses.color_pair(Colors.STAT_LABEL))
                self._safe_addstr(y, 4 + 19, f"{display_value:<45}",
                                  curses.color_pair(Colors.STAT_VALUE))
            y += 1

        # Help text
        try:
            if self.editing:
                help_text = "Type to edit | ENTER: Save | ESC: Cancel"
            else:
                help_text = "UP/DOWN: Navigate | ENTER: Edit field | Q: Back"
            self.stdscr.addstr(y + 2, 4, help_text, curses.color_pair(Colors.DIM))
        except curses.error:
            pass

        # Status message
        self._draw_message(height - 4, width)

        self._draw_footer(height, width, "ENTER: Edit | Q: Back to View")

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
                self.current_crue = self.crues[self.crue_index]
                self.crue_edit_index = 0
                self.editing = False
                self.edit_buffer = ""
                self.mode = "crue_edit"
        elif key == ord('x') or key == ord('X'):
            if self.crues:
                self._delete_crue(self.crues[self.crue_index])
        elif key == ord('q') or key == ord('Q'):
            self.mode = "menu"

    def _handle_crue_view_input(self, key: int):
        """Handle crue detail view input."""
        if key == ord('e') or key == ord('E'):
            self.crue_edit_index = 0
            self.editing = False
            self.edit_buffer = ""
            self.mode = "crue_edit"
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

    def _handle_crue_edit_input(self, key: int):
        """Handle crue edit view input."""
        field_count = 5

        if self.editing:
            if key == 27:  # ESC
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                self._save_crue_field()
                self.editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
        else:
            if key == curses.KEY_UP:
                self.crue_edit_index = (self.crue_edit_index - 1) % field_count
            elif key == curses.KEY_DOWN:
                self.crue_edit_index = (self.crue_edit_index + 1) % field_count
            elif key in (curses.KEY_ENTER, 10, 13):
                self._start_crue_field_edit()
            elif key == ord('q') or key == ord('Q'):
                self.mode = "crue_view"

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

    def _start_crue_field_edit(self):
        """Start editing the selected crue field."""
        if not self.current_crue:
            return

        crue = self.current_crue
        field_values = {
            0: crue.get("name", ""),
            1: crue.get("slug", ""),
            2: crue.get("type", "team"),
            3: crue.get("description", ""),
            4: str(crue.get("capacity", {}).get("max_members", 10)),
        }

        self.edit_buffer = field_values.get(self.crue_edit_index, "")
        self.editing = True

    def _save_crue_field(self):
        """Save the current edit to crue."""
        if not self.current_crue:
            return

        field_map = {
            0: "name",
            1: "slug",
            2: "type",
            3: "description",
            4: "max_members",
        }

        field = field_map.get(self.crue_edit_index)
        if not field:
            return

        if field == "max_members":
            if "capacity" not in self.current_crue:
                self.current_crue["capacity"] = {}
            try:
                self.current_crue["capacity"]["max_members"] = int(self.edit_buffer)
            except ValueError:
                self.current_crue["capacity"]["max_members"] = 10
        else:
            self.current_crue[field] = self.edit_buffer

        # Update timestamp
        from datetime import datetime, timezone
        if "audit" not in self.current_crue:
            self.current_crue["audit"] = {}
        self.current_crue["audit"]["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            self.ds.update("group", self.current_crue["id"], self.current_crue)
            self._load_crues()
            self.message = f"{field.title()} updated"
            self.message_color = Colors.STAT_VALUE
        except Exception as e:
            self.message = f"Save error: {str(e)[:30]}"
            self.message_color = Colors.HEALTH

        self.edit_buffer = ""

    def _create_crue_from_type(self, crue_type: dict):
        """Create a new crue from a crue_type template."""
        import uuid
        from datetime import datetime, timezone

        crue_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Build slug from type
        type_slug = crue_type.get("slug", "team")
        suffix = crue_id[:4]
        slug = f"{type_slug}.{suffix}"

        crue = {
            "id": crue_id,
            "slug": slug,
            "name": f"New {crue_type.get('label', 'Crue')}",
            "type": crue_type.get("label", "Team"),
            "type_slug": type_slug,
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
            self.ds.create("group", crue, validate=False)
            self._load_crues()
            self.current_crue = crue
            self.crue_edit_index = 0
            self.editing = False
            self.edit_buffer = ""
            self.mode = "crue_edit"
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

    # ==========================================================================
    # New Agent Creation
    # ==========================================================================

    def _enter_create_mode(self):
        """Roll a candidate and open the creation screen (nothing saved yet)."""
        self.create_candidate = self.gen.generate(status_id=2)
        self.create_edit_field = None
        self.editing = False
        self.edit_buffer = ""
        self.mode = "create"

    def _handle_create_input(self, key: int):
        """Handle new-agent creation input."""
        if self.editing:
            if key == 27:  # ESC
                self.editing = False
                self.edit_buffer = ""
                self.create_edit_field = None
            elif key in (curses.KEY_ENTER, 10, 13):
                self._apply_create_edit()
                self.editing = False
                self.create_edit_field = None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
            return

        if key == ord('r') or key == ord('R'):
            self._enter_create_mode()
            self.message = "New candidate rolled"
            self.message_color = Colors.DIM
        elif key == ord('e') or key == ord('E'):
            self.create_edit_field = "name"
            self.edit_buffer = self.create_candidate["identity"].get("name", "")
            self.editing = True
        elif key == ord('c') or key == ord('C'):
            self.create_edit_field = "codename"
            self.edit_buffer = self.create_candidate["identity"].get("codename", "")
            self.editing = True
        elif key in (curses.KEY_ENTER, 10, 13):
            self._hire_candidate()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.create_candidate = None
            self.mode = "menu"

    def _apply_create_edit(self):
        """Apply a name/codename edit to the candidate, keeping the derived
        identity fields (dogtag) consistent."""
        value = self.edit_buffer.strip()
        if not value or not self.create_candidate:
            return
        ident = self.create_candidate["identity"]

        if self.create_edit_field == "codename":
            ident["codename"] = value.lower().replace(" ", ".")
            return

        parts = value.split(None, 2)
        if len(parts) >= 3:
            title, first, last = parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            title, first, last = ident.get("title", "Agt."), parts[0], parts[1]
        else:
            title, first, last = ident.get("title", "Agt."), parts[0], "Unknown"
        ident["name"] = f"{title} {first} {last}"
        ident["title"] = title
        ident["dogtag"] = self.gen.compute_dogtag(title, first, last)

    def _hire_candidate(self):
        """Persist the candidate and enroll it (badge + keypair)."""
        if not self.create_candidate:
            return
        try:
            agent = self.ds.create("agent", self.create_candidate, validate=False)
        except Exception as e:
            self.message = f"Hire failed: {str(e)[:40]}"
            self.message_color = Colors.HEALTH
            return

        from lib.data.identity import try_enroll
        enrollment = try_enroll(self.ds, agent)
        if enrollment:
            agent = enrollment.agent
            self.message = (f"Hired {agent['identity']['name']} - "
                            f"badge {org_id(agent)} {enrollment.fingerprint[:19]}...")
        else:
            self.message = f"Hired {agent['identity']['name']} (enrollment deferred)"
        self.message_color = Colors.GOLD

        self._load_roster()
        self.current_agent = next(
            (a for a in self.roster if a["id"] == agent["id"]), agent)
        self.create_candidate = None
        self.mode = "view"

    # ==========================================================================
    # Identity (badge enrollment)
    # ==========================================================================

    def _enroll_current_agent(self):
        """Issue the current agent's badge + primary keypair."""
        if not self.current_agent:
            return
        if is_enrolled(self.current_agent):
            crypto = self.current_agent.get("crypto", {})
            self.message = (f"Badge on file: {org_id(self.current_agent)} "
                            f"{crypto.get('fingerprint', '')[:19]}...")
            self.message_color = Colors.DIM
            return
        try:
            enrollment = issue_badge(self.ds, self.current_agent)
        except Exception as e:
            self.message = f"Enrollment failed: {str(e)[:40]}"
            self.message_color = Colors.HEALTH
            return
        self._load_roster()
        self.current_agent = next(
            (a for a in self.roster if a["id"] == enrollment.agent["id"]),
            enrollment.agent)
        self.message = (f"BADGE ISSUED {org_id(self.current_agent)} - "
                        f"{enrollment.fingerprint[:26]}...")
        self.message_color = Colors.GOLD

    # ==========================================================================
    # Dojo (skill domain training)
    # ==========================================================================

    def _draw_dojo(self, height: int, width: int):
        """Draw the dojo - spend unassigned skill points on skill domains."""
        if not self.current_agent:
            self.mode = "roster"
            return

        agent = self.current_agent
        ident = agent["identity"]
        sp = agent.get("economy", {}).get("skill_points", {})
        unassigned = sp.get("total", 0) - sp.get("assigned", 0)

        self._draw_header(width)
        self._safe_addstr(5, 2, f"DOJO - {ident['name']}",
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self._safe_addstr(5, max(0, width - 26), "UNASSIGNED SP: ",
                          curses.color_pair(Colors.STAT_LABEL))
        self._safe_addstr(5, max(0, width - 11), str(unassigned),
                          curses.color_pair(Colors.STAT_VALUE) | curses.A_BOLD)

        skills = {s["domain_id"]: s for s in agent.get("skills", [])}
        domains = sorted(self.skill_domains.values(), key=lambda d: d["id"])

        list_y = 7
        header = f"{'DOMAIN':<24} {'POINTS':<20} {'STATUS':<12}"
        self._safe_addstr(list_y, 4, header,
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self._safe_addstr(list_y + 1, 4, "-" * min(width - 8, 60),
                          curses.color_pair(Colors.DIM))

        for i, domain in enumerate(domains):
            row_y = list_y + 2 + i
            points = skills.get(domain["id"], {}).get("points", 0)
            cap = domain.get("max_points", 20)
            bar = self._progress_bar(10, points / max(1, cap))
            if points >= cap:
                status = "MASTERED"
            elif unassigned > 0:
                status = "TRAINABLE"
            else:
                status = "NO SP"
            lead = pad(f"{domain.get('icon', '')} {domain.get('label', '?')}", 24)
            row = f" {lead} [{bar}] {points:>2}/{cap}  {status:<10} "

            color = Colors.HIGHLIGHT if i == self.dojo_index else Colors.STAT_VALUE
            self._safe_addstr(row_y, 4, clip(row, width - 8),
                              curses.color_pair(color))

        self._draw_message(height - 4, width)
        self._draw_footer(height, width,
                          "UP/DOWN: Navigate | ENTER: Train +1 | Q: Back to Agent")

    def _handle_dojo_input(self, key: int):
        """Handle dojo input."""
        if not self.current_agent:
            self.mode = "roster"
            return

        domains = sorted(self.skill_domains.values(), key=lambda d: d["id"])

        if key == curses.KEY_UP:
            self.dojo_index = (self.dojo_index - 1) % len(domains)
        elif key == curses.KEY_DOWN:
            self.dojo_index = (self.dojo_index + 1) % len(domains)
        elif key in (curses.KEY_ENTER, 10, 13):
            domain = domains[self.dojo_index]
            try:
                payout = dojo_train(self.ds, self.current_agent,
                                    domain["slug"], 1)
            except ValueError as e:
                self.message = str(e)
                self.message_color = Colors.HEALTH
                return
            self.current_agent = payout.agent
            self._load_roster()
            self.message = "; ".join(payout.notes) or "Trained"
            self.message_color = Colors.STAT_VALUE
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "view"

    # ==========================================================================
    # Statistics
    # ==========================================================================

    def _enter_stats_mode(self):
        """Compute agency statistics once and open the stats screen."""
        self._load_roster()
        self.stats_data = self._compute_stats()
        self.mode = "stats"

    def _compute_stats(self) -> dict:
        """Aggregate roster + ledger statistics."""
        roster = self.roster
        rank_dist = []
        for rank in sorted(self.ranks.values(), key=lambda r: r["id"]):
            count = sum(1 for a in roster
                        if a.get("classification", {}).get("rank_id") == rank["id"])
            rank_dist.append((rank.get("label", "?"), count))

        leaders = sorted(roster, key=lambda a: a.get("economy", {}).get("xp", 0),
                         reverse=True)[:5]

        ledger_in = ledger_out = ledger_count = 0
        try:
            for tx in self.ds.query("transaction", include_retired=True).data:
                amount = tx.get("amount", 0)
                ledger_count += 1
                if tx.get("from_agent_id"):
                    ledger_out += amount
                else:
                    ledger_in += amount
        except Exception:
            pass

        return {
            "total": len(roster),
            "enrolled": sum(1 for a in roster
                            if (a.get("crypto") or {}).get("fingerprint")),
            "violations": sum((a.get("conduct") or {}).get("violations", 0)
                              for a in roster),
            "missions": sum(a.get("ratings", {}).get("missions_completed", 0)
                            for a in roster),
            "total_xp": sum(a.get("economy", {}).get("xp", 0) for a in roster),
            "total_gold": sum(a.get("economy", {}).get("gold", 0) for a in roster),
            "rank_dist": rank_dist,
            "leaders": leaders,
            "ledger": (ledger_count, ledger_in, ledger_out),
        }

    def _draw_stats(self, height: int, width: int):
        """Draw agency statistics."""
        self._draw_header(width)
        stats = self.stats_data or {}

        col_width = (width - 6) // 2
        box_height = height - 10

        # Left: rank distribution
        self._draw_box(6, 2, box_height, col_width, "RANK DISTRIBUTION")
        rank_dist = stats.get("rank_dist", [])
        max_count = max((c for _, c in rank_dist), default=1) or 1
        bar_width = max(6, col_width - 26)
        for i, (label, count) in enumerate(rank_dist):
            y = 8 + i
            if y >= 6 + box_height - 1:
                break
            bar = self._progress_bar(bar_width, count / max_count)
            self._safe_addstr(y, 4, f"{label:<12} [{bar}] {count:>3}",
                              curses.color_pair(Colors.STAT_VALUE))

        summary_y = 8 + len(rank_dist) + 1
        for line, color in [
            (f"Agents:    {stats.get('total', 0)}  "
             f"(badged: {stats.get('enrolled', 0)})", Colors.NORMAL),
            (f"Missions:  {stats.get('missions', 0)} completed", Colors.NORMAL),
            (f"Conduct:   {stats.get('violations', 0)} violation(s)",
             Colors.HEALTH if stats.get("violations") else Colors.DIM),
        ]:
            if summary_y < 6 + box_height - 1:
                self._safe_addstr(summary_y, 4, clip(line, col_width - 4),
                                  curses.color_pair(color))
                summary_y += 1

        # Right: economy + XP leaders
        right_x = col_width + 4
        self._draw_box(6, right_x, box_height, col_width, "ECONOMY")
        ledger_count, ledger_in, ledger_out = stats.get("ledger", (0, 0, 0))
        y = 8
        for line, color in [
            (f"XP in circulation:   {stats.get('total_xp', 0):,}", Colors.XP),
            (f"Gold in circulation: {stats.get('total_gold', 0):,}g", Colors.GOLD),
            (f"Ledger: {ledger_count} entries  "
             f"(+{ledger_in}g / -{ledger_out}g)", Colors.DIM),
        ]:
            self._safe_addstr(y, right_x + 2, clip(line, col_width - 4),
                              curses.color_pair(color))
            y += 1
        y += 1

        self._safe_addstr(y, right_x + 2, "XP LEADERS",
                          curses.color_pair(Colors.TITLE))
        y += 1
        for agent in stats.get("leaders", []):
            if y >= 6 + box_height - 1:
                break
            ident = agent.get("identity", {})
            eco = agent.get("economy", {})
            rank = self.ranks.get(
                agent.get("classification", {}).get("rank_id"), {})
            line = (f"{pad(ident.get('codename', '?'), 24)} "
                    f"{eco.get('xp', 0):>6,}xp  {rank.get('label', '?')}")
            self._safe_addstr(y, right_x + 2, clip(line, col_width - 4),
                              curses.color_pair(Colors.STAT_VALUE))
            y += 1

        self._draw_footer(height, width, "L: Ledger | R: Refresh | Q: Back")

    def _handle_stats_input(self, key: int):
        """Handle stats screen input."""
        if key == ord('r') or key == ord('R'):
            self._enter_stats_mode()
            self.message = "Statistics refreshed"
            self.message_color = Colors.DIM
        elif key == ord('l') or key == ord('L'):
            self._open_ledger_viewer()
        elif key in (curses.KEY_ENTER, 10, 13) or key in (ord('q'), ord('Q'), 27):
            self.mode = "menu"

    # ==========================================================================
    # Dispatch (mission form -> briefing -> live run)
    # ==========================================================================

    DISPATCH_FIELDS = [
        ("task", "Task", "What the agent(s) should do (required)"),
        ("skill", "Skill terms", "Comma-separated competency terms for crew matching"),
        ("target", "Target dir", "Working directory for the mission"),
        ("count", "Crew size", "Agents to assign when matching by skill"),
    ]

    def _enter_dispatch_mode(self):
        """Open the dispatch mission form."""
        if self.dispatch_running:
            self.mode = "dispatch_run"
            return
        self.dispatch_field_index = 0
        self.dispatch_brief = None
        self.mode = "dispatch"

    def _draw_dispatch_form(self, height: int, width: int):
        """Draw the ad-hoc mission form."""
        self._draw_header(width)

        box_width = min(72, width - 4)
        box_height = 14
        box_y = 6
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width, "DISPATCH - MISSION FORM")

        y = box_y + 2
        for i, (field_key, label, hint) in enumerate(self.DISPATCH_FIELDS):
            is_selected = (i == self.dispatch_field_index)
            value = self.dispatch_form.get(field_key, "")
            if is_selected and self.editing:
                display = f"[{self.edit_buffer}_]"
                color = Colors.HIGHLIGHT
            else:
                display = value or "(unset)" if field_key == "task" else value
                color = Colors.HIGHLIGHT if is_selected else Colors.STAT_VALUE
            self._safe_addstr(y, box_x + 2, f"{label:<12}",
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(y, box_x + 15, clip(display, box_width - 18),
                              curses.color_pair(color))
            y += 1
            if is_selected:
                self._safe_addstr(y, box_x + 15, clip(hint, box_width - 18),
                                  curses.color_pair(Colors.DIM))
            y += 1

        # Crew source disclosure
        y += 1
        if self.dispatch_crew_override:
            crew_note = f"Crew: {self.dispatch_crew_label}"
        elif self.selected_agents:
            crew_note = f"Crew: operator shortlist ({len(self.selected_agents)} agent(s))"
        elif self.dispatch_form.get("skill"):
            crew_note = "Crew: competency-matched from roster (draft-hire fallback)"
        else:
            crew_note = "Crew: first available from roster"
        self._safe_addstr(y, box_x + 2, clip(crew_note, box_width - 4),
                          curses.color_pair(Colors.GOLD))

        self._draw_message(box_y + box_height + 1, width)
        if self.editing:
            self._draw_footer(height, width, "Type to edit | ENTER: Apply | ESC: Cancel")
        else:
            self._draw_footer(height, width,
                              "UP/DOWN: Field | ENTER: Edit | P: Plan Briefing | Q: Back")

    def _handle_dispatch_form_input(self, key: int):
        """Handle dispatch form input."""
        if self.editing:
            if key == 27:
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_ENTER, 10, 13):
                field_key = self.DISPATCH_FIELDS[self.dispatch_field_index][0]
                self.dispatch_form[field_key] = self.edit_buffer.strip()
                self.editing = False
                self.edit_buffer = ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.edit_buffer = self.edit_buffer[:-1]
            elif 32 <= key <= 126:
                self.edit_buffer += chr(key)
            return

        field_count = len(self.DISPATCH_FIELDS)
        if key == curses.KEY_UP:
            self.dispatch_field_index = (self.dispatch_field_index - 1) % field_count
        elif key == curses.KEY_DOWN:
            self.dispatch_field_index = (self.dispatch_field_index + 1) % field_count
        elif key in (curses.KEY_ENTER, 10, 13):
            field_key = self.DISPATCH_FIELDS[self.dispatch_field_index][0]
            self.edit_buffer = self.dispatch_form.get(field_key, "")
            self.editing = True
        elif key == ord('p') or key == ord('P'):
            self._prepare_dispatch_brief()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.dispatch_crew_override = []
            self.dispatch_crew_label = ""
            self.mode = "menu"

    def _prepare_dispatch_brief(self):
        """Resolve crew + build the mission; open the briefing gate."""
        task = self.dispatch_form.get("task", "").strip()
        if not task:
            self.message = "Task prompt is required"
            self.message_color = Colors.HEALTH
            return

        target = Path(self.dispatch_form.get("target", ".") or ".").expanduser().resolve()
        if not target.is_dir():
            self.message = f"Target not found: {target}"
            self.message_color = Colors.HEALTH
            return

        try:
            count = max(1, int(self.dispatch_form.get("count") or "1"))
        except ValueError:
            count = 1

        provider = get_provider("claude-cli")
        if not provider.is_available():
            self.message = "claude CLI not found - install it first"
            self.message_color = Colors.HEALTH
            return

        # Crew resolution, fully disclosed
        crew, reasons = [], []
        if self.dispatch_crew_override:
            crew = self.dispatch_crew_override
            reasons = [(a["identity"]["codename"], self.dispatch_crew_label)
                       for a in crew]
        elif self.selected_agents:
            crew = [a for a in self.roster if a["id"] in self.selected_agents]
            reasons = [(a["identity"]["codename"], "operator shortlist")
                       for a in crew]
        elif self.dispatch_form.get("skill", "").strip():
            terms = [t.strip() for t in self.dispatch_form["skill"].split(",")
                     if t.strip()]
            matches = match_agents(terms, count=count, ds=self.ds)
            if matches:
                crew = [m.agent for m in matches]
                reasons = [(m.codename,
                            f"score:{m.score} " + "; ".join(m.reasons[:3]))
                           for m in matches]
            else:
                hire = draft_hire(self.ds)
                self._load_roster()
                crew = [hire]
                reasons = [(hire["identity"]["codename"],
                            "drafted - no qualified agent on roster")]
        else:
            crew = self.roster[:count]
            reasons = [(a["identity"]["codename"], "first available")
                       for a in crew]

        if not crew:
            self.message = "No agents available - generate some first"
            self.message_color = Colors.HEALTH
            return

        mission = adhoc_task_mission(crew, str(target), task)
        self.dispatch_brief = {
            "mission": mission,
            "crew": crew,
            "reasons": reasons,
            "provider": provider,
        }
        self.mode = "dispatch_brief"

    def _draw_dispatch_brief(self, height: int, width: int):
        """Draw the briefing gate - full disclosure before any agent work."""
        self._draw_header(width)
        brief = self.dispatch_brief
        if not brief:
            self.mode = "dispatch"
            return

        mission = brief["mission"]
        box_width = min(76, width - 4)
        box_height = min(11 + len(brief["crew"]) + len(mission.tasks), height - 9)
        box_y = 5
        box_x = (width - box_width) // 2
        self._draw_box(box_y, box_x, box_height, box_width,
                       f"MISSION BRIEFING [{mission.id}]")

        y = box_y + 1
        inner = box_width - 4

        def line(label, value, color=Colors.STAT_VALUE):
            nonlocal y
            self._safe_addstr(y, box_x + 2, f"{label:<10}",
                              curses.color_pair(Colors.STAT_LABEL))
            self._safe_addstr(y, box_x + 12, clip(str(value), inner - 10),
                              curses.color_pair(color))
            y += 1

        line("OBJECTIVE", mission.objective)
        line("TARGET", mission.constraints.working_directory)
        scope = "READ-ONLY" if mission.constraints.read_only else "READ/WRITE"
        scope += " | LOCAL" if mission.constraints.local_only else " | REMOTE OK"
        line("SCOPE", scope, Colors.GOLD)
        line("PROVIDER", f"{brief['provider'].binary_path} "
             f"(--print --dangerously-skip-permissions)", Colors.DIM)
        line("TIMEOUT", f"{mission.constraints.timeout_seconds}s per task")
        y += 1

        self._safe_addstr(y, box_x + 2, f"CREW ({len(brief['crew'])}):",
                          curses.color_pair(Colors.TITLE))
        y += 1
        for codename, reason in brief["reasons"]:
            if y >= box_y + box_height - 2:
                break
            self._safe_addstr(y, box_x + 4,
                              clip(f"~ {pad(codename, 24)} {reason}", inner - 2),
                              curses.color_pair(Colors.STAT_VALUE))
            y += 1

        self._draw_message(box_y + box_height + 1, width)
        self._draw_footer(height, width,
                          "Y/ENTER: DISPATCH | Q: Back to Form")

    def _handle_dispatch_brief_input(self, key: int):
        """Handle briefing gate input."""
        if key in (curses.KEY_ENTER, 10, 13, ord('y'), ord('Y')):
            self._launch_dispatch()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "dispatch"

    def _launch_dispatch(self):
        """Start the dispatcher on a worker thread; the draw loop streams
        events as they land."""
        brief = self.dispatch_brief
        if not brief or self.dispatch_running:
            return

        self.dispatch_events = []
        self.dispatch_result = None
        self.dispatch_running = True
        self.dispatch_crew_override = []
        self.dispatch_crew_label = ""
        self.dispatch_thread = threading.Thread(
            target=self._dispatch_worker, args=(brief["mission"],), daemon=True)
        self.dispatch_thread.start()
        self.mode = "dispatch_run"

    def _dispatch_worker(self, mission):
        """Worker thread: run the mission, save reports, settle payroll."""
        import asyncio
        from datetime import datetime as _dt

        def on_event(event):
            stamp = _dt.now().strftime("%H:%M:%S")
            self.dispatch_events.append(
                f"{stamp} [{event.event_type}] {event.message}")

        try:
            dispatcher = Dispatcher(datastore=self.ds)
            result = asyncio.run(dispatcher.execute(
                mission, on_event=on_event,
                max_concurrent=agency_config()["dispatch"]["max_concurrent"]))

            rdir = report_dir()
            rdir.mkdir(parents=True, exist_ok=True)
            report_path = rdir / f"mission-{mission.id}-report.md"
            if result.final_report:
                report_path.write_text(result.final_report)
            result.save(report_path.with_suffix(".json"))
            self.dispatch_events.append(f"         report: {report_path}")
            self.dispatch_result = result
            self._load_roster()
        except Exception as e:
            self.dispatch_events.append(f"         [error] {e}")
        finally:
            self.dispatch_running = False

    def _draw_dispatch_run(self, height: int, width: int):
        """Draw the live dispatch event stream."""
        self._draw_header(width)

        result = self.dispatch_result
        if self.dispatch_running:
            status, color = "DISPATCHED - agents in the field...", Colors.XP
        elif result is not None:
            status_value = result.status.value
            if status_value == "complete":
                status, color = "MISSION COMPLETE", Colors.STAT_VALUE
            else:
                status, color = f"MISSION {status_value.upper()}", Colors.HEALTH
        else:
            status, color = "MISSION ERROR - see events", Colors.HEALTH
        self._safe_addstr(5, 2, status, curses.color_pair(color) | curses.A_BOLD)

        box_y = 7
        box_height = height - 10
        box_width = width - 4
        self._draw_box(box_y, 2, box_height, box_width, "EVENT LOG")

        visible = box_height - 2
        events = self.dispatch_events[-visible:]
        for i, event in enumerate(events):
            if "payroll" in event:
                ecolor = Colors.GOLD
            elif "complete" in event:
                ecolor = Colors.STAT_VALUE
            elif "fail" in event or "error" in event:
                ecolor = Colors.HEALTH
            else:
                ecolor = Colors.NORMAL
            self._safe_addstr(box_y + 1 + i, 4, clip(event, box_width - 4),
                              curses.color_pair(ecolor))

        if self.dispatch_running:
            self._draw_footer(height, width,
                              "Mission running - events stream live | Q: Background it")
        else:
            self._draw_footer(height, width, "ENTER/Q: Back to Menu")

    def _handle_dispatch_run_input(self, key: int):
        """Handle live-run screen input."""
        if key in (curses.KEY_ENTER, 10, 13) and not self.dispatch_running:
            self.mode = "menu"
        elif key == ord('q') or key == ord('Q') or key == 27:
            if self.dispatch_running:
                self.message = "Mission continues in background - check Dispatch later"
                self.message_color = Colors.XP
            self.mode = "menu"

    # ==========================================================================
    # Generic text viewer (scrolling, markdown-ish coloring)
    # ==========================================================================

    def _open_viewer(self, title: str, lines: list[str], return_mode: str):
        """Open the scrolling text viewer over prepared content lines."""
        self.viewer_title = title
        self.viewer_lines = lines
        self.viewer_scroll = 0
        self.viewer_return_mode = return_mode
        self.mode = "viewer"

    def _draw_viewer(self, height: int, width: int):
        """Draw the generic text viewer."""
        self._draw_header(width)
        self._safe_addstr(5, 2, clip(self.viewer_title, width - 4),
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)

        content_y = 7
        content_height = height - 10
        content_width = width - 4
        self._draw_box(content_y - 1, 2, content_height + 2, content_width, "")

        total = len(self.viewer_lines)
        max_scroll = max(0, total - content_height)
        self.viewer_scroll = max(0, min(self.viewer_scroll, max_scroll))

        for i, line in enumerate(
                self.viewer_lines[self.viewer_scroll:
                                  self.viewer_scroll + content_height]):
            color = Colors.DIM
            if line.startswith("##"):
                color = Colors.TITLE
                line = line[2:].strip()
            elif line.startswith("#"):
                color = Colors.STAT_LABEL
                line = line[1:].strip()
            elif line.strip().startswith("*"):
                color = Colors.STAT_VALUE
            elif line.startswith('"'):
                color = Colors.STAT_VALUE
            self._safe_addstr(content_y + i, 4, clip(line, content_width - 4),
                              curses.color_pair(color))

        if total > content_height:
            pct = self.viewer_scroll / max(1, max_scroll)
            indicator_y = content_y + int(pct * (content_height - 1))
            self._safe_addstr(indicator_y, content_width - 1, "█",
                              curses.color_pair(Colors.STAT_VALUE))
            range_str = (f" {self.viewer_scroll + 1}-"
                         f"{min(self.viewer_scroll + content_height, total)}/{total} ")
            self._safe_addstr(content_y - 1,
                              max(4, content_width - len(range_str) - 2),
                              range_str, curses.color_pair(Colors.DIM))

        self._draw_footer(height, width, "UP/DOWN/PgUp/PgDn/HOME/END: Scroll | Q: Back")

    def _handle_viewer_input(self, key: int):
        """Handle viewer scrolling."""
        page = 20
        total = len(self.viewer_lines)
        if key == curses.KEY_UP:
            self.viewer_scroll = max(0, self.viewer_scroll - 1)
        elif key == curses.KEY_DOWN:
            self.viewer_scroll = min(max(0, total - 1), self.viewer_scroll + 1)
        elif key == curses.KEY_PPAGE:
            self.viewer_scroll = max(0, self.viewer_scroll - page)
        elif key == curses.KEY_NPAGE:
            self.viewer_scroll = min(max(0, total - 1), self.viewer_scroll + page)
        elif key == curses.KEY_HOME:
            self.viewer_scroll = 0
        elif key == curses.KEY_END:
            self.viewer_scroll = max(0, total - 1)
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = self.viewer_return_mode

    # ==========================================================================
    # Dossier depth views: operational profile + service record
    # ==========================================================================

    def _open_operational_profile(self):
        """Compose and display the agent's full operational profile - the
        canonical role/behavior/constraints bundle the provider receives."""
        if not self.current_agent:
            return
        agent = self.current_agent
        try:
            profile = get_composer().compose(agent, scope="read_only")
        except Exception as e:
            self.message = f"Compose failed: {str(e)[:40]}"
            self.message_color = Colors.HEALTH
            return

        ident = agent["identity"]
        lines = [
            f"## OPERATIONAL PROFILE - {ident['name']} ({ident['codename']})",
            f"#Archetype: {profile.archetype_label} ({profile.archetype_slug})",
            f"#Role: {profile.role_title}",
            "",
            "## COMPOSED SYSTEM PROMPT (what the provider receives, verbatim)",
            "",
        ]
        lines.extend(profile.render_system_prompt().split("\n"))
        sources = getattr(profile, "sources", None)
        if sources:
            lines.extend(["", "## PROVENANCE (instruction sources)", ""])
            for source in sources:
                lines.append(f"  * {source}")
        self._open_viewer(f"OPERATIONAL PROFILE - {ident['codename']}",
                          lines, "view")

    def _open_service_record(self):
        """Build the agent's work history from saved missions + the ledger."""
        if not self.current_agent:
            return
        agent = self.current_agent
        ident = agent["identity"]
        agent_id = agent["id"]

        # Mission participation from saved mission records
        entries = []
        for path in sorted(report_dir().glob("mission-*-report.json")):
            try:
                with open(path) as f:
                    record = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            if agent_id not in record.get("crew_ids", []):
                continue
            my_tasks = [t for t in record.get("tasks", [])
                        if t.get("agent_id") == agent_id]
            entries.append({
                "id": record.get("id", "?"),
                "created": (record.get("created_at") or "")[:16].replace("T", " "),
                "status": record.get("status", "?"),
                "title": record.get("title", ""),
                "tasks": [(t.get("role", "?"), t.get("status", "?"))
                          for t in my_tasks],
            })
        entries.sort(key=lambda e: e["created"], reverse=True)

        # Gold movements from the ledger
        earned = spent = tx_count = 0
        by_mission = {}
        try:
            for tx in self.ds.query("transaction", include_retired=True).data:
                if tx.get("to_agent_id") == agent_id:
                    earned += tx.get("amount", 0)
                    tx_count += 1
                    ref = tx.get("reference") or {}
                    if ref.get("type") == "mission":
                        by_mission[ref.get("id")] = (
                            by_mission.get(ref.get("id"), 0) + tx.get("amount", 0))
                elif tx.get("from_agent_id") == agent_id:
                    spent += tx.get("amount", 0)
                    tx_count += 1
        except Exception:
            pass

        eco = agent.get("economy", {})
        conduct = agent.get("conduct") or {}
        lines = [
            f"## SERVICE RECORD - {ident['name']} ({ident['codename']})",
            "",
            "#Summary",
            f"  Missions completed: "
            f"{agent.get('ratings', {}).get('missions_completed', 0)}",
            f"  Current: {eco.get('xp', 0):,} XP, {eco.get('gold', 0):,}g",
            f"  Ledger: +{earned}g earned / -{spent}g spent "
            f"({tx_count} transaction(s))",
        ]
        if conduct.get("violations"):
            lines.append(f"  Conduct: {conduct['violations']} policy violation(s)")
        lines.extend(["", "#Mission History", ""])

        if not entries:
            lines.append("  No missions on record. Send them somewhere.")
        for entry in entries:
            pay = by_mission.get(entry["id"])
            pay_str = f"  pay: +{pay}g" if pay else ""
            lines.append(f"  * {entry['created']}  [{entry['id']}]  "
                         f"{entry['status'].upper()}{pay_str}")
            lines.append(f"    {entry['title']}")
            for role, status in entry["tasks"]:
                lines.append(f"      {role} -> {status}")
            lines.append("")
        self._open_viewer(f"SERVICE RECORD - {ident['codename']}", lines, "view")

    def _open_access_view(self):
        """Assigned network/host/resource access: badge identity, host
        deployments, and project engagements (worktree bindings)."""
        if not self.current_agent:
            return
        from lib.data.project import agent_bookings, engagement_status

        agent = self.current_agent
        ident = agent["identity"]
        crypto = agent.get("crypto") or {}

        lines = [f"## ACCESS - {ident['name']} ({ident['codename']})",
                 "", "#Badge / Identity"]
        if crypto.get("fingerprint"):
            lines.extend([
                f"  org id:      {org_id(agent)}",
                f"  remote user: {ident.get('dogtag', '?')}",
                f"  key:         {crypto.get('key_type', '?')} "
                f"{crypto.get('fingerprint')}",
                f"  key status:  {crypto.get('status', 'active')}",
                f"  vault:       {crypto.get('vault_path', '?')}/",
            ])
        else:
            lines.append("  Not enrolled - no badge or keypair on file "
                         "(B in the dossier issues one)")

        lines.extend(["", "#Host Deployments"])
        deployments = []
        try:
            for substation in self.ds.query("substation").data:
                for dep in substation.get("deployments") or []:
                    if dep.get("agent_id") == agent["id"]:
                        deployments.append((substation, dep))
        except Exception:
            pass
        if deployments:
            for substation, dep in deployments:
                sub_ident = substation.get("identity", {})
                conn = substation.get("connection", {})
                lines.append(
                    f"  * {sub_ident.get('codename', '?')}  "
                    f"{conn.get('hostname', '?')}  [{dep.get('status', '?')}]")
                lines.append(
                    f"    path: {dep.get('remote_path', '?')}  "
                    f"deployed: {(dep.get('deployed_at') or '?')[:10]}")
        else:
            lines.append("  (no host deployments on record)")

        lines.extend(["", "#Project Engagements"])
        bookings = agent_bookings(self.ds, agent["id"])
        if bookings:
            for booking in bookings:
                project = booking["project"]
                row = booking["engagement"]
                state = engagement_status(self.ds, row)
                lines.append(f"  * {project['slug']}  "
                             f"role: {row.get('role', '?')}  "
                             f"[{state.get('label', '?')}]")
                binding = project.get("host_binding") or {}
                for worktree in binding.get("worktrees", []):
                    mode = ("read/write" if worktree.get("writable")
                            else "read-only")
                    lines.append(f"    worktree: {worktree.get('path')} "
                                 f"({mode})")
        else:
            lines.append("  (no active project engagements)")

        lines.extend(["", "#Access Grants",
                      "  (agent key -> host grants land in phase D)"])
        self._open_viewer(f"ACCESS - {ident['codename']}", lines, "view")

    def _open_ledger_viewer(self):
        """Display the gold ledger, newest first."""
        codenames = {a["id"]: a["identity"]["codename"] for a in self.roster}
        lines = ["## GOLD LEDGER (immutable, newest first)", ""]
        try:
            entries = self.ds.query("transaction", include_retired=True).data
        except Exception:
            entries = []
        for tx in sorted(entries, key=lambda t: t.get("id", 0), reverse=True)[:200]:
            if tx.get("from_agent_id"):
                who = codenames.get(tx["from_agent_id"], "?")
                direction = f"{who} -"
            else:
                who = codenames.get(tx.get("to_agent_id"), "?")
                direction = f"{who} +"
            lines.append(f"  * #{tx.get('id', '?'):>3}  {direction}"
                         f"{tx.get('amount', 0)}g  {tx.get('type', '?')}  "
                         f"{tx.get('notes', '')}")
        if len(lines) == 2:
            lines.append("  No transactions yet - nobody has been paid.")
        self._open_viewer("GOLD LEDGER", lines, "stats")

    # ==========================================================================
    # Missions browser
    # ==========================================================================

    def _enter_missions_mode(self):
        """Load saved mission records and open the browser."""
        self.mission_list = []
        for path in sorted(report_dir().glob("mission-*-report.json"),
                           reverse=True):
            try:
                with open(path) as f:
                    record = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            tasks = record.get("tasks", [])
            self.mission_list.append({
                "id": record.get("id", "?"),
                "status": record.get("status", "?"),
                "created": (record.get("created_at") or "")[:16].replace("T", " "),
                "title": record.get("title", ""),
                "crew": len(record.get("crew_ids", [])),
                "done": sum(1 for t in tasks if t.get("status") == "complete"),
                "total": len(tasks),
                "report": record.get("final_report"),
                "path": path,
            })
        self.mission_index = 0
        self.mode = "missions"

    def _draw_missions(self, height: int, width: int):
        """Draw the mission history browser."""
        self._draw_header(width)
        self._safe_addstr(5, 2, f"MISSIONS ({len(self.mission_list)} on file)",
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)

        if not self.mission_list:
            self._safe_addstr(10, max(0, (width - 40) // 2),
                              "No missions on file. Dispatch something.",
                              curses.color_pair(Colors.DIM))
            self._draw_footer(height, width, "Q: Back")
            return

        list_y = 7
        header = f"{'ID':<10} {'STATUS':<10} {'TASKS':<7} {'CREW':<5} {'DATE':<17} TITLE"
        self._safe_addstr(list_y, 4, clip(header, width - 8),
                          curses.color_pair(Colors.TITLE) | curses.A_BOLD)
        self._safe_addstr(list_y + 1, 4, "-" * min(width - 8, 90),
                          curses.color_pair(Colors.DIM))

        list_height = height - 12
        visible = list_height - 2
        start = max(0, self.mission_index - visible + 3)
        for i, m in enumerate(self.mission_list[start:start + visible]):
            row_y = list_y + 2 + i
            if row_y >= list_y + list_height:
                break
            row = (f"{m['id']:<10} {m['status']:<10} "
                   f"{m['done']}/{m['total']:<5} {m['crew']:<5} "
                   f"{m['created']:<17} {m['title']}")
            if start + i == self.mission_index:
                color = Colors.HIGHLIGHT
            elif m["status"] == "complete":
                color = Colors.NORMAL
            else:
                color = Colors.HEALTH
            self._safe_addstr(row_y, 4, f" {clip(row, width - 10)} ",
                              curses.color_pair(color))

        self._draw_message(height - 4, width)
        self._draw_footer(height, width, "ENTER: Read Report | R: Refresh | Q: Back")

    def _handle_missions_input(self, key: int):
        """Handle missions browser input."""
        count = len(self.mission_list)
        if key == curses.KEY_UP and count:
            self.mission_index = max(0, self.mission_index - 1)
        elif key == curses.KEY_DOWN and count:
            self.mission_index = min(count - 1, self.mission_index + 1)
        elif key in (curses.KEY_ENTER, 10, 13) and count:
            m = self.mission_list[self.mission_index]
            md_path = m["path"].with_suffix(".md")
            if md_path.exists():
                lines = md_path.read_text().split("\n")
            elif m["report"]:
                lines = m["report"].split("\n")
            else:
                lines = ["(no report captured for this mission)"]
            self._open_viewer(f"MISSION {m['id']} - {m['status'].upper()}",
                              lines, "missions")
        elif key == ord('r') or key == ord('R'):
            self._enter_missions_mode()
        elif key == ord('q') or key == ord('Q') or key == 27:
            self.mode = "menu"

    # ==========================================================================
    # Agent edit form
    # ==========================================================================

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

    # ==========================================================================
    # Type Foundry - operator-defined reference types
    # ==========================================================================

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


def main():
    """Standalone entry point."""
    builder = AgentBuilder()
    builder.run()


if __name__ == "__main__":
    main()
