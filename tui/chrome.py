"""
Chrome - shared curses primitives: colors, header/footer, boxes, safe drawing.

Extracted move-only from tui/builder.py; methods run against shared
AgentBuilder state (mixin over `self`).
"""

import curses

from tui.textkit import clip, display_width, pad

# Minimum terminal dimensions required for the HUD
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


class ChromeMixin:
    """Shared drawing primitives for every screen."""

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

