"""Clip two layers or groups on the main map and drag the divider."""

from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, QPoint, QRect, QTimer, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QPolygon
from qgis.PyQt.QtWidgets import QWidget
from qgis.core import QgsMapRendererParallelJob, QgsMapSettings

from .compare import CanvasOverride, CompareView, _disconnect, _layer_alive
from .comparemath import (
    DIVIDER_STROKES,
    HANDLE_CREAM,
    HANDLE_GREEN,
    HANDLE_INK,
    clip_ranges,
    divider_hit,
    normalize_direction,
    side_opacity,
    swipe_handle_rect,
)


class SwipeView(CompareView):
    """One map. The two sides share its extent, and the divider clips each choice."""

    def __init__(self, iface):
        super().__init__(iface)
        self._overlay = None
        self._filter = _DividerFilter(self)
        self._jobs = {}
        self._generation = 0
        self._canvas_override = CanvasOverride(iface)
        self._hooked_canvas = None
        self._timer = QTimer(self._filter)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render)

    def fit_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.fit()

    def near_divider(self, pos) -> bool:
        size = self._viewport_size()
        if size is None:
            return False
        width, height = size
        return divider_hit(width, height, self._settings.direction, self._settings.position, pos.x(), pos.y())

    def position_from_pixel(self, pos) -> None:
        size = self._viewport_size()
        if size is None:
            return
        width, height = size
        direction = normalize_direction(self._settings.direction)
        if direction == "vertical":
            value = int(round(pos.x() / max(width, 1) * 100))
        else:
            value = int(round(pos.y() / max(height, 1) * 100))
        self.set_position(value)

    def _show(self) -> None:
        canvas = self.iface.mapCanvas()
        if canvas is None:
            self._active = False
            return
        self._ensure_overlay(canvas)
        self._apply_opacity()
        self._canvas_override.show_without(self._compared_layers())
        self._schedule_render()

    def _hide(self) -> None:
        self._timer.stop()
        self._generation += 1
        self._cancel_jobs()
        self._remove_overlay()
        self._canvas_override.restore()

    def _on_position(self) -> None:
        if self._overlay is not None:
            self._overlay.update()

    def _apply_opacity(self) -> None:
        if self._overlay is not None:
            self._overlay.update()

    def _on_opacity(self) -> None:
        return None

    def _schedule_render(self, *_args) -> None:
        if self._active:
            self._timer.start(40)

    def _render(self) -> None:
        if not self._active:
            return
        self._generation += 1
        token = self._generation
        self._start_job("left", self._layers(self._settings.left_key), token)
        self._start_job("right", self._layers(self._settings.right_key), token)

    def _start_job(self, side: str, layers, token: int) -> None:
        self._cancel_job(side)
        if not layers or self.iface.mapCanvas() is None:
            if self._overlay is not None:
                self._overlay.set_image(side, None)
            return
        settings = QgsMapSettings(self.iface.mapCanvas().mapSettings())
        settings.setLayers([layer for layer in layers if _layer_alive(layer)])
        settings.setBackgroundColor(QColor(0, 0, 0, 0))
        job = QgsMapRendererParallelJob(settings)
        self._jobs[side] = (token, job)
        job.finished.connect(lambda side=side, token=token, job=job: self._job_finished(side, token, job))
        job.start()

    def _job_finished(self, side: str, token: int, job) -> None:
        current = self._jobs.get(side)
        if token != self._generation or current is None or current[1] is not job:
            job.deleteLater()
            return
        self._jobs.pop(side, None)
        if self._overlay is not None:
            self._overlay.set_image(side, job.renderedImage())
        job.deleteLater()

    def _cancel_jobs(self) -> None:
        for side in list(self._jobs):
            self._cancel_job(side)

    def _cancel_job(self, side: str) -> None:
        current = self._jobs.pop(side, None)
        if current is None:
            return
        _token, job = current
        job.cancel()
        job.deleteLater()

    def _ensure_overlay(self, canvas) -> None:
        if self._overlay is None or self._hooked_canvas is not canvas:
            self._remove_overlay()
            self._overlay = _SwipeOverlay(canvas, self)
            canvas.viewport().installEventFilter(self._filter)
            canvas.extentsChanged.connect(self._schedule_render)
            if hasattr(canvas, "destinationCrsChanged"):
                canvas.destinationCrsChanged.connect(self._schedule_render)
            self._hooked_canvas = canvas
        self._overlay.fit()
        self._overlay.show()

    def _remove_overlay(self) -> None:
        canvas = self._hooked_canvas
        if canvas is not None:
            viewport = canvas.viewport()
            if viewport is not None:
                viewport.removeEventFilter(self._filter)
            _disconnect(canvas.extentsChanged, self._schedule_render)
            if hasattr(canvas, "destinationCrsChanged"):
                _disconnect(canvas.destinationCrsChanged, self._schedule_render)
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None
        self._hooked_canvas = None

    def _viewport_size(self):
        canvas = self.iface.mapCanvas() if self.iface is not None else None
        if canvas is None or canvas.viewport() is None:
            return None
        viewport = canvas.viewport()
        return viewport.width(), viewport.height()


class _SwipeOverlay(QWidget):
    """Draw the two clipped maps and the divider above the canvas."""

    def __init__(self, canvas, view):
        super().__init__(canvas.viewport())
        self._canvas = canvas
        self._images = {"left": None, "right": None}
        self._view = view
        self.setAttribute(_attribute("WA_TransparentForMouseEvents"), True)
        self.setAttribute(_attribute("WA_NoSystemBackground"), True)
        self.setAttribute(_attribute("WA_TranslucentBackground"), True)

    def set_image(self, side: str, image) -> None:
        self._images[side] = image
        self.update()

    def fit(self) -> None:
        viewport = self._canvas.viewport()
        self.setGeometry(0, 0, viewport.width(), viewport.height())
        self.raise_()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        first, second, line = clip_ranges(self.width(), self.height(), self._view.settings.direction, self._view.settings.position)
        self._paint_side(painter, self._images.get("left"), first, "left")
        self._paint_side(painter, self._images.get("right"), second, "right")
        _paint_divider(painter, line)
        direction = self._view.settings.direction
        handle = QRect(*swipe_handle_rect(self.width(), self.height(), direction, self._view.settings.position))
        _paint_handle(painter, handle, direction)

    def _paint_side(self, painter, image, rect, side: str) -> None:
        if image is None or image.isNull():
            return
        settings = self._view.settings
        painter.save()
        painter.setClipRect(_rect(rect))
        painter.setOpacity(side_opacity(settings.opacity, settings.opacity_side, side))
        painter.drawImage(self.rect(), image)
        painter.restore()


class _DividerFilter(QObject):
    """Drag the divider without taking the normal map tool away."""

    def __init__(self, view):
        super().__init__()
        self._view = view
        self._dragging = False

    def eventFilter(self, _watched, event):
        kind = event.type()
        if kind == _event_type("Resize"):
            self._view.fit_overlay()
            return False
        if not self._view.active:
            return False
        pos = _event_pos(event)
        if pos is None:
            return False
        if kind == _event_type("MouseButtonPress") and _is_left_click(event) and self._view.near_divider(pos):
            self._dragging = True
            self._view.position_from_pixel(pos)
            return True
        if kind == _event_type("MouseMove") and self._dragging:
            self._view.position_from_pixel(pos)
            return True
        if kind == _event_type("MouseButtonRelease") and self._dragging:
            self._dragging = False
            self._view.position_from_pixel(pos)
            return True
        return False


def _rect(values):
    return QRect(*values)


def _paint_divider(painter, line) -> None:
    """Draw a dark, cream, and green stroke so the line shows on any map."""
    x1, y1, x2, y2 = line
    for color, width in DIVIDER_STROKES:
        pen = QPen(QColor(color))
        pen.setWidth(width)
        pen.setCapStyle(_pen_cap("FlatCap"))
        painter.setPen(pen)
        painter.drawLine(x1, y1, x2, y2)


def _paint_handle(painter, handle, direction: str) -> None:
    """Draw the grip inside its rectangle: dark edge, cream edge, green center."""
    painter.save()
    painter.setRenderHint(_render_hint("Antialiasing"), True)
    painter.setPen(_pen_style("NoPen"))
    painter.setBrush(QColor(HANDLE_INK))
    painter.drawRoundedRect(handle, 8, 8)
    painter.setBrush(QColor(HANDLE_CREAM))
    painter.drawRoundedRect(handle.adjusted(1, 1, -1, -1), 7, 7)
    painter.setBrush(QColor(HANDLE_GREEN))
    painter.drawRoundedRect(handle.adjusted(3, 3, -3, -3), 5, 5)
    _paint_arrows(painter, handle, direction)
    painter.restore()


def _paint_arrows(painter, handle, direction: str) -> None:
    """Draw the leafmap-style grip: sideways arrows, or up and down arrows."""
    direction = normalize_direction(direction)
    painter.setBrush(QColor(HANDLE_CREAM))
    painter.setPen(_pen_style("NoPen"))
    center = handle.center()
    if direction == "vertical":
        _paint_chevron(painter, center.x() - 10, center.y(), "left")
        _paint_chevron(painter, center.x() + 10, center.y(), "right")
        return
    if direction == "horizontal":
        _paint_chevron(painter, center.x(), center.y() - 8, "up")
        _paint_chevron(painter, center.x(), center.y() + 8, "down")
        return
    raise ValueError(f"Unknown direction {direction!r}.")


def _paint_chevron(painter, cx: int, cy: int, facing: str) -> None:
    arm = 5
    if facing == "left":
        points = ((cx + arm, cy - arm), (cx - arm, cy), (cx + arm, cy + arm))
    elif facing == "right":
        points = ((cx - arm, cy - arm), (cx + arm, cy), (cx - arm, cy + arm))
    elif facing == "up":
        points = ((cx - arm, cy + arm), (cx, cy - arm), (cx + arm, cy + arm))
    elif facing == "down":
        points = ((cx - arm, cy - arm), (cx, cy + arm), (cx + arm, cy - arm))
    else:
        raise ValueError(f"Unknown arrow {facing!r}.")
    painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))


def _pen_style(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.PenStyle, name)


def _pen_cap(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.PenCapStyle, name)


def _render_hint(name: str):
    if hasattr(QPainter, name):
        return getattr(QPainter, name)
    return getattr(QPainter.RenderHint, name)


def _attribute(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.WidgetAttribute, name)


def _event_type(name: str):
    if hasattr(QEvent, name):
        return getattr(QEvent, name)
    return getattr(QEvent.Type, name)


def _event_pos(event):
    if hasattr(event, "pos"):
        try:
            return event.pos()
        except AttributeError:
            return None
    if hasattr(event, "position"):
        point = event.position()
        return point.toPoint() if hasattr(point, "toPoint") else point
    return None


def _is_left_click(event) -> bool:
    left = Qt.LeftButton if hasattr(Qt, "LeftButton") else Qt.MouseButton.LeftButton
    try:
        return event.button() == left
    except AttributeError:
        return False
