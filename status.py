"""One-line status messages shared by the plugin dialogs."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QFrame, QLabel, QScrollArea

from .compat import enum_member


class StatusStrip(QScrollArea):
    """One status line that scrolls sideways when the message is wider than the window."""

    def __init__(self, label: QLabel):
        super().__init__()
        self._label = label
        label.setWordWrap(False)
        label.setAlignment(_alignment("AlignLeft") | _alignment("AlignVCenter"))
        line = label.fontMetrics().lineSpacing() + 12
        label.setFixedHeight(line)
        self.setWidget(label)
        self.setWidgetResizable(False)
        self.setFrameShape(_frame_none())
        self.setHorizontalScrollBarPolicy(_scroll_policy("ScrollBarAsNeeded"))
        self.setVerticalScrollBarPolicy(_scroll_policy("ScrollBarAlwaysOff"))
        self.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.setFixedHeight(line)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def show_message(self, text: str, kind: str) -> None:
        self._label.setText(text)
        self._label.setStyleSheet("" if text == "" else _status_style(kind))
        background = "transparent" if text == "" else _status_background(kind)
        self.setStyleSheet(f"QScrollArea {{ border: none; background: {background}; }}")
        self._fit()

    def _fit(self) -> None:
        metrics = self._label.fontMetrics()
        line = metrics.lineSpacing() + 12
        text = self._label.text()
        width = _text_width(metrics, text) + 16 if text else 0
        viewport = self.viewport().width()
        self._label.setFixedSize(max(width, viewport, 1), line)
        extra = 0
        if viewport > 0 and width > viewport:
            extra = self.horizontalScrollBar().sizeHint().height()
        self.setFixedHeight(line + extra)


def prepare_status(label: QLabel) -> StatusStrip:
    """Reserve one text line, matching the Convert Coordinates strip."""
    return StatusStrip(label)


def apply_status(label: QLabel, text: str, kind: str) -> None:
    """Show one status line, scrolling sideways when it is wider than the window."""
    strip = label.parentWidget()
    while strip is not None and not isinstance(strip, StatusStrip):
        strip = strip.parentWidget()
    if isinstance(strip, StatusStrip):
        strip.show_message(text, kind)
        return
    label.setText(text)
    label.setStyleSheet("" if text == "" else _status_style(kind))


def _text_width(metrics, text: str) -> int:
    advance = getattr(metrics, "horizontalAdvance", None)
    if advance is not None:
        return int(advance(text))
    return int(metrics.width(text))


def _alignment(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.AlignmentFlag, name)


def _scroll_policy(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.ScrollBarPolicy, name)


def _frame_none():
    return enum_member(QFrame, "Shape", "NoFrame")


def _status_background(kind: str) -> str:
    if kind == "plain":
        return "transparent"
    if kind == "ok":
        return "#d9f2df"
    if kind == "warn":
        return "#fdecc8"
    if kind == "error":
        return "#f8d0d0"
    raise ValueError(f"Unknown status {kind!r}.")


def _status_color(kind: str) -> str:
    if kind == "ok":
        return "#14532d"
    if kind == "warn":
        return "#7c4a03"
    if kind == "error":
        return "#7f1d1d"
    raise ValueError(f"Unknown status {kind!r}.")


def _status_style(kind: str) -> str:
    if kind == "plain":
        return ""
    return f"background-color: {_status_background(kind)}; color: {_status_color(kind)}; padding: 6px;"
