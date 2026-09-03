"""
Substations - hosts: connect (real ssh), permissions, vault, key import, deploy gate. Owns the optional-remote import guard.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses
import os

from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

# Optional remote imports (dependencies may be absent; screens degrade)
try:
    from lib.remote import (
        CredentialVault, VaultScope, VaultLocked, VaultNotInitialized,
        SSHKeyDiscovery, SSHKeyInfo, KeyStatus,
        SSHConnectionConfig, HostKeyPolicy,
        SubstationPermissions,
    )
    from cryptography.fernet import Fernet
    HAS_REMOTE = True
    REMOTE_ERROR = None
except ImportError as e:
    HAS_REMOTE = False
    REMOTE_ERROR = str(e)

class SubstationsMixin:
    """hosts: connect (real ssh), permissions, vault, key import, deploy gate. Owns the optional-remote import guard."""

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

