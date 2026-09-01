"""
Textkit - display-width-aware text utilities for the TUI.

curses aligns cells, not characters: East Asian wide/fullwidth glyphs (and
most emoji) occupy two terminal cells while len() counts one, so len()-based
padding and slicing drift box edges and overrun columns. Every string that
lands in a fixed-width cell should pass through clip()/pad() here.

No third-party deps - width classification uses unicodedata, which is
what the wcwidth package wraps anyway.
"""

import unicodedata

# Zero-width joiner / variation selectors collapse; treat as width 0
_ZERO_WIDTH_CATEGORIES = ("Mn", "Me", "Cf")

ELLIPSIS = "…"  # single-cell truncation marker


def char_width(ch: str) -> int:
    """Terminal cell width of a single character (0, 1, or 2)."""
    if ch in ("‍",):  # ZWJ
        return 0
    if unicodedata.category(ch) in _ZERO_WIDTH_CATEGORIES:
        return 0
    east = unicodedata.east_asian_width(ch)
    if east in ("W", "F"):
        return 2
    # Most emoji are East Asian "N"/"A" but render two cells; the
    # emoji-presentation ranges are the practical offenders
    cp = ord(ch)
    if 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF:
        return 2
    return 1


def display_width(text: str) -> int:
    """Terminal cell width of a string."""
    return sum(char_width(ch) for ch in text)


def clip(text: str, width: int, ellipsis: str = ELLIPSIS) -> str:
    """Truncate text to a display width, marking truncation.

    Guarantees display_width(result) <= width, including the marker.
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text

    marker_w = display_width(ellipsis)
    budget = width - marker_w
    if budget < 0:
        return ""

    out, used = [], 0
    for ch in text:
        w = char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


def pad(text: str, width: int, align: str = "left", fill: str = " ") -> str:
    """Clip then pad to an exact display width (cell-accurate ljust/rjust).

    align: "left" | "right" | "center"
    """
    text = clip(text, width)
    gap = width - display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return fill * gap + text
    if align == "center":
        left = gap // 2
        return fill * left + text + fill * (gap - left)
    return text + fill * gap
