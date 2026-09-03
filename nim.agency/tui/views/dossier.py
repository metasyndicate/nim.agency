"""
Dossier - agent sheet, profile viewer, depth views (op profile, service record, access, ledger).

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses
import json

from lib.config import report_dir
from lib.data.composer import get_composer
from lib.data.identity import is_enrolled, org_id
from tui.chrome import Colors
from tui.mugshots import MugshotLoader
from tui.textkit import clip, display_width, pad

class DossierMixin:
    """agent sheet, profile viewer, depth views (op profile, service record, access, ledger)."""

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

