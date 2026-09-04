"""
Viewer - generic scrolling text viewer with markdown-ish coloring.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from tui.chrome import Colors
from tui.textkit import clip, display_width, pad

class ViewerMixin:
    """generic scrolling text viewer with markdown-ish coloring."""

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

