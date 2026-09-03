"""
Create - candidate roll, hire, badge enrollment.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from lib.data.identity import enroll as issue_badge, is_enrolled, org_id
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class CreateMixin:
    """candidate roll, hire, badge enrollment."""

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

