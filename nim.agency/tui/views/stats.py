"""
Stats - agency-wide statistics.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class StatsMixin:
    """agency-wide statistics."""

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

