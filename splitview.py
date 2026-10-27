"""Two linked maps that stay the same size and the same extent."""

from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QResizeEvent
from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect, QSizePolicy, QSplitter, QStackedWidget
from qgis.core import QgsProject
from qgis.gui import QgsMapCanvas, QgsMapToolPan

from .compare import CanvasOverride, CompareSettings, CompareView, _disconnect
from .comparemath import normalize_direction, side_opacity
from .compat import enum_member


class SplitView(CompareView):
    """A second map beside or below the main one. Panning either one moves the other.

    Extent signals are ignored while a sync is already running, and a matching extent
    is not applied again. That guard is what keeps the two maps from updating forever.
    """

    def __init__(self, iface):
        super().__init__(iface)
        self._canvas_override = CanvasOverride(iface)
        self._splitter = None
        self._second = None
        self._pan = None
        self._parent = None
        self._stacked = None
        self._mode = ""
        self._syncing = False
        self._sizing = False
        self._main_hooks = False
        self._hooks = _SplitHooks(self)
        self._main_policy = None
        self._main_minimum = None
        self._main_maximum = None
        self._stack_hooked = False

    def set_position(self, _position: int) -> None:
        super().set_position(50)

    def _accepted(self, settings: CompareSettings) -> CompareSettings:
        settings = settings.copy()
        settings.position = 50
        return settings

    def _show(self) -> None:
        if not self._ensure_splitter():
            self._active = False
            return
        self._apply_direction()
        self._apply_layers()
        self._apply_opacity()
        self._copy_onto(self._second, self.iface.mapCanvas())
        self._reveal()
        self._connect_maps()
        self._apply_equal_sizes()
        QTimer.singleShot(0, self._keep_splitter_current)
        QTimer.singleShot(0, self._apply_equal_sizes)

    def _hide(self) -> None:
        self._clear_pane_effects()
        self._disconnect_maps()
        self._canvas_override.restore()
        self._remove_splitter()

    def _on_position(self) -> None:
        self._apply_equal_sizes()

    def _apply_opacity(self) -> None:
        main = self.iface.mapCanvas() if self.iface is not None else None
        self._set_pane_opacity(main, "left")
        self._set_pane_opacity(self._second, "right")

    def _on_opacity(self) -> None:
        return None

    def _set_pane_opacity(self, canvas, side: str) -> None:
        if canvas is None:
            return
        value = side_opacity(self._settings.opacity, self._settings.opacity_side, side)
        if value >= 0.999:
            canvas.setGraphicsEffect(None)
            return
        effect = canvas.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(canvas)
            canvas.setGraphicsEffect(effect)
        effect.setOpacity(value)

    def _clear_pane_effects(self) -> None:
        main = self.iface.mapCanvas() if self.iface is not None else None
        for canvas in (main, self._second):
            if canvas is not None:
                canvas.setGraphicsEffect(None)

    def _ensure_splitter(self) -> bool:
        if self._splitter is not None:
            return True
        main = self.iface.mapCanvas() if self.iface is not None else None
        if main is None:
            return False
        parent = main.parentWidget()
        if parent is None:
            return False
        stacked = parent if isinstance(parent, QStackedWidget) else None
        layout = None if stacked is not None else parent.layout()
        if stacked is None and layout is None:
            return False
        self._parent = parent
        self._second = QgsMapCanvas()
        self._second.setObjectName("hkmapservicesSplitCanvas")
        if hasattr(self._second, "setProject"):
            self._second.setProject(QgsProject.instance())
        self._second.enableAntiAliasing(True)
        self._second.setCanvasColor(main.canvasColor())
        self._remember_main_constraints(main)
        _let_splitter_size(main)
        _let_splitter_size(self._second)
        self._pan = QgsMapToolPan(self._second)
        self._second.setMapTool(self._pan)
        self._splitter = QSplitter()
        self._splitter.setObjectName("hkmapservicesSplit")
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(4)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        if stacked is not None:
            stacked.removeWidget(main)
            self._splitter.addWidget(main)
            self._splitter.addWidget(self._second)
            stacked.addWidget(self._splitter)
            self._stacked = stacked
            self._mode = "stack"
            if not self._stack_hooked:
                stacked.currentChanged.connect(self._on_stack_changed)
                self._stack_hooked = True
            stacked.setCurrentWidget(self._splitter)
        else:
            self._splitter.addWidget(main)
            self._splitter.addWidget(self._second)
            layout.addWidget(self._splitter)
            self._mode = "layout"
        handle = self._splitter.handle(1)
        if handle is not None:
            handle.setEnabled(False)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self._splitter.installEventFilter(self._hooks)
        return True

    def _reveal(self) -> None:
        main = self.iface.mapCanvas() if self.iface is not None else None
        if main is not None:
            main.show()
        if self._second is not None:
            self._second.show()
        if self._splitter is not None:
            self._splitter.show()
        self._keep_splitter_current()

    def _keep_splitter_current(self) -> None:
        stacked = self._stacked
        splitter = self._splitter
        if not self._active or stacked is None or splitter is None:
            return
        if stacked.currentWidget() is not splitter:
            stacked.setCurrentWidget(splitter)

    def _on_stack_changed(self, _index: int) -> None:
        QTimer.singleShot(0, self._keep_splitter_current)

    def _remove_splitter(self) -> None:
        main = self.iface.mapCanvas() if self.iface is not None else None
        splitter = self._splitter
        second = self._second
        self._splitter = None
        self._second = None
        self._pan = None
        if splitter is None or main is None:
            self._restore_main_constraints()
            if second is not None:
                second.setParent(None)
                second.deleteLater()
            if splitter is not None:
                splitter.hide()
                splitter.deleteLater()
            self._stacked = None
            self._parent = None
            self._mode = ""
            return
        _disconnect(splitter.splitterMoved, self._on_splitter_moved)
        splitter.removeEventFilter(self._hooks)
        stacked = self._stacked
        if stacked is not None and self._stack_hooked:
            _disconnect(stacked.currentChanged, self._on_stack_changed)
            self._stack_hooked = False
        splitter.hide()
        self._restore_main_constraints()
        if splitter.indexOf(main) >= 0:
            main.setParent(None)
        if self._mode == "stack" and stacked is not None:
            stacked.removeWidget(splitter)
            stacked.addWidget(main)
            stacked.setCurrentWidget(main)
            main.show()
        elif self._mode == "layout" and self._parent is not None and self._parent.layout() is not None:
            self._parent.layout().removeWidget(splitter)
            self._parent.layout().addWidget(main)
        if second is not None:
            second.setParent(None)
            second.deleteLater()
        splitter.deleteLater()
        self._stacked = None
        self._parent = None
        self._mode = ""

    def _apply_direction(self) -> None:
        if self._splitter is None:
            return
        self._splitter.setOrientation(_splitter_orientation(self._settings.direction))

    def _apply_equal_sizes(self) -> None:
        splitter = self._splitter
        if splitter is None or self._sizing:
            return
        total = self._span(splitter)
        if total < 2:
            return
        half = total // 2
        sizes = splitter.sizes()
        if len(sizes) == 2 and sizes[0] > 0 and abs(sizes[0] - sizes[1]) <= splitter.handleWidth() + 1:
            return
        self._sizing = True
        try:
            splitter.setSizes([half, total - half])
            for index in range(splitter.count()):
                _refresh_canvas_size(splitter.widget(index))
        finally:
            self._sizing = False

    def _span(self, splitter) -> int:
        horizontal = splitter.orientation() == _qt("Horizontal")
        total = splitter.width() if horizontal else splitter.height()
        if total >= 2:
            return total
        parent = splitter.parentWidget()
        if parent is None:
            return total
        return parent.width() if horizontal else parent.height()

    def _remember_main_constraints(self, main) -> None:
        self._main_policy = main.sizePolicy()
        self._main_minimum = main.minimumSize()
        self._main_maximum = main.maximumSize()

    def _restore_main_constraints(self) -> None:
        main = self.iface.mapCanvas() if self.iface is not None else None
        if main is None or self._main_policy is None:
            return
        main.setSizePolicy(self._main_policy)
        main.setMinimumSize(self._main_minimum)
        main.setMaximumSize(self._main_maximum)
        self._main_policy = None

    def _on_splitter_moved(self, *_args) -> None:
        self._apply_equal_sizes()

    def _apply_layers(self) -> None:
        self._canvas_override.show(self._layers(self._settings.left_key))
        if self._second is not None:
            self._second.setLayers(self._layers(self._settings.right_key))
            self._second.refresh()

    def _connect_maps(self) -> None:
        if self._main_hooks or self._second is None:
            return
        main = self.iface.mapCanvas()
        main.extentsChanged.connect(self._from_main)
        self._second.extentsChanged.connect(self._from_second)
        if hasattr(main, "destinationCrsChanged"):
            main.destinationCrsChanged.connect(self._from_main)
            self._second.destinationCrsChanged.connect(self._from_second)
        if hasattr(main, "rotationChanged"):
            main.rotationChanged.connect(self._from_main)
            self._second.rotationChanged.connect(self._from_second)
        self._main_hooks = True

    def _disconnect_maps(self) -> None:
        if not self._main_hooks:
            return
        main = self.iface.mapCanvas() if self.iface is not None else None
        second = self._second
        if main is not None:
            _disconnect(main.extentsChanged, self._from_main)
            if hasattr(main, "destinationCrsChanged"):
                _disconnect(main.destinationCrsChanged, self._from_main)
            if hasattr(main, "rotationChanged"):
                _disconnect(main.rotationChanged, self._from_main)
        if second is not None:
            _disconnect(second.extentsChanged, self._from_second)
            if hasattr(second, "destinationCrsChanged"):
                _disconnect(second.destinationCrsChanged, self._from_second)
            if hasattr(second, "rotationChanged"):
                _disconnect(second.rotationChanged, self._from_second)
        self._main_hooks = False

    def _from_main(self, *_args) -> None:
        if self._syncing or self._second is None or self.iface is None:
            return
        self._syncing = True
        try:
            self._copy_onto(self._second, self.iface.mapCanvas())
        finally:
            self._syncing = False

    def _from_second(self, *_args) -> None:
        if self._syncing or self._second is None or self.iface is None:
            return
        self._syncing = True
        try:
            self._copy_onto(self.iface.mapCanvas(), self._second)
        finally:
            self._syncing = False

    def _copy_onto(self, target, source) -> None:
        if target is None or source is None or _views_match(source, target):
            return
        _refresh_canvas_size(source)
        _refresh_canvas_size(target)
        target.blockSignals(True)
        try:
            target.setDestinationCrs(source.mapSettings().destinationCrs())
            target.setRotation(source.rotation())
            target.setExtent(source.extent())
            target.refresh()
        finally:
            target.blockSignals(False)


def _views_match(source, target) -> bool:
    source_settings = source.mapSettings()
    target_settings = target.mapSettings()
    if source_settings.destinationCrs() != target_settings.destinationCrs():
        return False
    if abs(source.rotation() - target.rotation()) > 1e-6:
        return False
    return _extents_match(source.extent(), target.extent())


def _extents_match(left, right) -> bool:
    tolerance_x = max(abs(left.width()) * 1e-6, 1e-8)
    tolerance_y = max(abs(left.height()) * 1e-6, 1e-8)
    return (
        abs(left.xMinimum() - right.xMinimum()) <= tolerance_x
        and abs(left.xMaximum() - right.xMaximum()) <= tolerance_x
        and abs(left.yMinimum() - right.yMinimum()) <= tolerance_y
        and abs(left.yMaximum() - right.yMaximum()) <= tolerance_y
    )


def _splitter_orientation(direction: str):
    direction = normalize_direction(direction)
    if direction == "vertical":
        return _qt("Horizontal")
    return _qt("Vertical")


class _SplitHooks(QObject):
    """Keep the panes equal when the map area is resized."""

    def __init__(self, view):
        super().__init__()
        self._view = view

    def eventFilter(self, _watched, event):
        if event.type() == _event_type("Resize"):
            self._view._apply_equal_sizes()
        return False


def _refresh_canvas_size(canvas) -> None:
    """Make the map use its current pane size before comparing extents."""
    if canvas is None:
        return
    size = canvas.size()
    if size.width() < 2 or size.height() < 2:
        return
    canvas.resizeEvent(QResizeEvent(size, size))


def _let_splitter_size(widget) -> None:
    widget.setMinimumSize(0, 0)
    widget.setSizePolicy(_ignored_policy())


def _ignored_policy():
    ignored = enum_member(QSizePolicy, "Policy", "Ignored")
    return QSizePolicy(ignored, ignored)


def _event_type(name: str):
    if hasattr(QEvent, name):
        return getattr(QEvent, name)
    return getattr(QEvent.Type, name)


def _qt(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.Orientation, name)
