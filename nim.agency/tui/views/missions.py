"""
Missions - saved mission browser.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses
import json

from lib.config import report_dir
from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class MissionsMixin:
    """saved mission browser."""

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

