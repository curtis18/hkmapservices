"""Pure helpers for swipe clipping and split settings.

This module does not import QGIS.
"""

from __future__ import annotations


def normalize_direction(direction: str) -> str:
    """Return vertical (left/right) or horizontal (top/bottom)."""
    if direction == "vertical" or direction == "horizontal":
        return direction
    raise ValueError(f"Unknown direction {direction!r}.")


def clamp_position(position: int) -> int:
    """Keep a divider percentage inside 0 to 100."""
    return max(0, min(100, int(position)))


def clamp_opacity(opacity: float) -> float:
    """Keep layer opacity inside 0 to 1."""
    return max(0.0, min(1.0, float(opacity)))


def normalize_opacity_side(side: str) -> str:
    """Return left, right, or both."""
    if side == "left" or side == "right" or side == "both":
        return side
    raise ValueError(f"Unknown opacity side {side!r}.")


def side_opacity(opacity: float, opacity_side: str, side: str) -> float:
    """Opacity for one side. The other side stays fully opaque."""
    opacity_side = normalize_opacity_side(opacity_side)
    if side != "left" and side != "right":
        raise ValueError(f"Unknown side {side!r}.")
    if opacity_side == "both" or opacity_side == side:
        return clamp_opacity(opacity)
    return 1.0


def clip_ranges(width: int, height: int, direction: str, position: int) -> tuple:
    """Return the two clip rectangles and the divider line, in pixels.

    The first rectangle is the left or top side. The second is the right or bottom.
    """
    direction = normalize_direction(direction)
    position = clamp_position(position)
    if direction == "vertical":
        split = int(round(width * position / 100))
        first = (0, 0, split, height)
        second = (split, 0, max(0, width - split), height)
        line = (split, 0, split, height)
        return first, second, line
    split = int(round(height * position / 100))
    first = (0, 0, width, split)
    second = (0, split, width, max(0, height - split))
    line = (0, split, width, split)
    return first, second, line


SWIPE_HANDLE_WIDTH = 44
SWIPE_HANDLE_HEIGHT = 36
HANDLE_INK = "#10241c"
HANDLE_CREAM = "#f4f1e8"
HANDLE_GREEN = "#1c4b3a"
DIVIDER_STROKES = (
    (HANDLE_INK, 7),
    (HANDLE_CREAM, 5),
    (HANDLE_GREEN, 3),
)


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two #rrggbb colors."""
    lighter = max(_relative_luminance(first), _relative_luminance(second))
    darker = min(_relative_luminance(first), _relative_luminance(second))
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: str) -> float:
    text = color[1:] if color.startswith("#") else color
    if len(text) != 6:
        raise ValueError(f"Unknown color {color!r}.")
    channels = tuple(int(text[index:index + 2], 16) / 255 for index in (0, 2, 4))
    red, green, blue = (_srgb_channel(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _srgb_channel(value: float) -> float:
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def swipe_handle_rect(width: int, height: int, direction: str, position: int) -> tuple:
    """Return the handle rectangle centered on the divider."""
    direction = normalize_direction(direction)
    _first, _second, (x1, y1, _x2, _y2) = clip_ranges(width, height, direction, position)
    if direction == "vertical":
        left = x1 - SWIPE_HANDLE_WIDTH // 2
        top = (height - SWIPE_HANDLE_HEIGHT) // 2
    else:
        left = (width - SWIPE_HANDLE_WIDTH) // 2
        top = y1 - SWIPE_HANDLE_HEIGHT // 2
    return (left, top, SWIPE_HANDLE_WIDTH, SWIPE_HANDLE_HEIGHT)


def divider_hit(width: int, height: int, direction: str, position: int, x: int, y: int, tolerance: int = 8) -> bool:
    """Return whether a pixel is close enough to drag the divider or its handle."""
    _first, _second, (x1, y1, _x2, _y2) = clip_ranges(width, height, direction, position)
    left, top, handle_width, handle_height = swipe_handle_rect(width, height, direction, position)
    if left <= x <= left + handle_width and top <= y <= top + handle_height:
        return True
    direction = normalize_direction(direction)
    if direction == "vertical":
        return abs(x - x1) <= tolerance
    return abs(y - y1) <= tolerance
