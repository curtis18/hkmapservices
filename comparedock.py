"""Shared controls for swipe and split in one dock."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDockWidget,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsProject

from .compare import CompareSettings, collect_sides


class CompareDock(QDockWidget):
    """One dock. Swipe is the default mode, and Split uses the same controls."""

    def __init__(self, swipe_view, split_view, parent=None):
        super().__init__("Swipe and Split", parent)
        self._swipe_view = swipe_view
        self._split_view = split_view
        self._mode = "Swipe"
        self._swipe_position = 50
        self._running = False
        self._sides = []
        self.setObjectName("hkmapservicesSwipeDock")
        self.setMinimumWidth(380)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 2, 8, 4)
        layout.setSpacing(4)
        mode = QGroupBox("Mode")
        mode_layout = QVBoxLayout(mode)
        mode_layout.setSpacing(2)
        mode_row = QHBoxLayout()
        self._mode_swipe = QRadioButton("Swipe")
        self._mode_split = QRadioButton("Split")
        self._mode_swipe.setChecked(True)
        self._mode_swipe.setToolTip("Deactivate before changing mode.")
        self._mode_split.setToolTip("Deactivate before changing mode.")
        mode_buttons = QButtonGroup(self)
        mode_buttons.addButton(self._mode_swipe)
        mode_buttons.addButton(self._mode_split)
        self._mode_swipe.toggled.connect(self._on_mode)
        self._mode_split.toggled.connect(self._on_mode)
        mode_row.addWidget(self._mode_swipe)
        mode_row.addWidget(self._mode_split)
        mode_layout.addLayout(mode_row)
        self._intro = QLabel("")
        self._intro.setWordWrap(True)
        mode_layout.addWidget(self._intro)
        layout.addWidget(mode)

        layers = QGroupBox("Layer Selection")
        layer_layout = QVBoxLayout(layers)
        self._left_label = QLabel("Left:")
        self._right_label = QLabel("Right:")
        self._left = QComboBox()
        self._right = QComboBox()
        self._left.currentIndexChanged.connect(self._on_side_changed)
        self._right.currentIndexChanged.connect(self._on_side_changed)
        for label, combo in ((self._left_label, self._left), (self._right_label, self._right)):
            row = QHBoxLayout()
            label.setMinimumWidth(64)
            row.addWidget(label)
            row.addWidget(combo, 1)
            layer_layout.addLayout(row)
        layout.addWidget(layers)

        self._direction_box = QGroupBox("Swipe Direction")
        direction_row = QHBoxLayout(self._direction_box)
        self._vertical = QRadioButton("Vertical")
        self._horizontal = QRadioButton("Horizontal")
        self._vertical.setChecked(True)
        buttons = QButtonGroup(self)
        buttons.addButton(self._vertical)
        buttons.addButton(self._horizontal)
        self._vertical.toggled.connect(self._on_direction)
        self._horizontal.toggled.connect(self._on_direction)
        direction_row.addWidget(self._vertical)
        direction_row.addWidget(self._horizontal)
        layout.addWidget(self._direction_box)

        self._position_box = QGroupBox("Swipe Position")
        position_layout = QVBoxLayout(self._position_box)
        slider_row = QHBoxLayout()
        self._position = QSlider(_orientation("Horizontal"))
        self._position.setRange(0, 100)
        self._position.setValue(50)
        self._percent = QLabel("50%")
        self._percent.setMinimumWidth(40)
        self._position.valueChanged.connect(self._on_position_slider)
        slider_row.addWidget(self._position, 1)
        slider_row.addWidget(self._percent)
        position_layout.addLayout(slider_row)
        presets = QHBoxLayout()
        self._presets = []
        for value in (0, 25, 50, 75, 100):
            button = QPushButton(f"{value}%")
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, value=value: self._set_preset(value))
            presets.addWidget(button)
            self._presets.append(button)
        position_layout.addLayout(presets)
        layout.addWidget(self._position_box)

        opacity = QGroupBox("Opacity")
        opacity_layout = QVBoxLayout(opacity)
        opacity_sides = QHBoxLayout()
        self._opacity_left = QRadioButton("Left")
        self._opacity_right = QRadioButton("Right")
        self._opacity_both = QRadioButton("Both")
        self._opacity_both.setChecked(True)
        opacity_buttons = QButtonGroup(self)
        opacity_buttons.addButton(self._opacity_left)
        opacity_buttons.addButton(self._opacity_right)
        opacity_buttons.addButton(self._opacity_both)
        self._opacity_left.toggled.connect(self._on_opacity_side)
        self._opacity_right.toggled.connect(self._on_opacity_side)
        self._opacity_both.toggled.connect(self._on_opacity_side)
        opacity_sides.addWidget(self._opacity_left)
        opacity_sides.addWidget(self._opacity_right)
        opacity_sides.addWidget(self._opacity_both)
        opacity_layout.addLayout(opacity_sides)
        opacity_row = QHBoxLayout()
        self._opacity = QSlider(_orientation("Horizontal"))
        self._opacity.setRange(0, 100)
        self._opacity.setValue(100)
        self._opacity_value = QLabel("100%")
        self._opacity_value.setMinimumWidth(40)
        self._opacity.valueChanged.connect(self._on_opacity)
        opacity_row.addWidget(self._opacity, 1)
        opacity_row.addWidget(self._opacity_value)
        opacity_layout.addLayout(opacity_row)
        layout.addWidget(opacity)

        self._activate = QPushButton()
        self._activate.setAutoDefault(False)
        self._activate.clicked.connect(self._toggle)
        layout.addWidget(self._activate)

        actions = QHBoxLayout()
        refresh = QPushButton("Refresh Layers")
        refresh.setAutoDefault(False)
        refresh.clicked.connect(self._refresh)
        swap = QPushButton("Swap Layers")
        swap.setAutoDefault(False)
        swap.clicked.connect(self._swap)
        actions.addWidget(refresh)
        actions.addWidget(swap)
        layout.addLayout(actions)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addStretch(1)
        self.setWidget(body)

        self._apply_mode_chrome()
        self._set_running(False)
        self._relabel()
        self._reload_combos()
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._refresh)
        project = QgsProject.instance()
        project.layersAdded.connect(self._schedule_reload)
        project.layersRemoved.connect(self._schedule_reload)

    @property
    def _view(self):
        if self._mode == "Swipe":
            return self._swipe_view
        if self._mode == "Split":
            return self._split_view
        raise ValueError(f"Unknown compare mode {self._mode!r}.")

    @property
    def _locked_position(self) -> bool:
        return self._mode == "Split"

    def show_position(self, value: int) -> None:
        if self._locked_position:
            value = 50
        else:
            self._swipe_position = int(value)
        self._position.blockSignals(True)
        self._position.setValue(int(value))
        self._position.blockSignals(False)
        self._percent.setText(f"{int(value)}%")

    def mark_stopped(self) -> None:
        self._swipe_view.stop()
        self._split_view.stop()
        self._set_running(False)

    def shutdown(self) -> None:
        project = QgsProject.instance()
        _disconnect(project.layersAdded, self._schedule_reload)
        _disconnect(project.layersRemoved, self._schedule_reload)
        self._reload_timer.stop()
        self.mark_stopped()

    def closeEvent(self, event) -> None:
        self.mark_stopped()
        super().closeEvent(event)

    def _toggle(self) -> None:
        if self._running:
            self._view.stop()
            self._set_running(False)
            self._status.setText("")
            return
        settings = self._settings_from_ui()
        if not settings.left_key or not settings.right_key:
            self._status.setText("Choose a layer or a group for each side.")
            return
        if not self._view.start(settings):
            self._set_running(False)
            self._status.setText("Choose a layer or a group that is still on the map.")
            return
        self._set_running(True)
        self._status.setText(_active_message(self._mode))

    def _on_mode(self, checked: bool) -> None:
        if not checked:
            return
        mode = "Split" if self._mode_split.isChecked() else "Swipe"
        if self._running or mode == self._mode:
            return
        if self._mode == "Swipe":
            self._swipe_position = self._position.value()
        was_running = self._running
        if was_running:
            self._view.stop()
            self._set_running(False)
        self._mode = mode
        self._apply_mode_chrome()
        if mode == "Swipe":
            self.show_position(self._swipe_position)
        else:
            self.show_position(50)
        if not was_running:
            return
        if not self._view.start(self._settings_from_ui()):
            self._status.setText("Choose a layer or a group that is still on the map.")
            return
        self._set_running(True)
        self._status.setText(_active_message(self._mode))

    def _on_direction(self, checked: bool) -> None:
        if not checked:
            return
        self._relabel()
        if self._running:
            self._view.set_direction(self._direction_value())

    def _on_position_slider(self, value: int) -> None:
        self._percent.setText(f"{value}%")
        if not self._locked_position:
            self._swipe_position = value
        if self._running and not self._locked_position:
            self._view.set_position(value)

    def _set_preset(self, value: int) -> None:
        if self._locked_position:
            return
        self._position.setValue(value)

    def _on_opacity(self, value: int) -> None:
        self._opacity_value.setText(f"{value}%")
        if self._running:
            self._view.set_opacity(value / 100.0)

    def _on_opacity_side(self, checked: bool) -> None:
        if not checked or not self._running:
            return
        self._view.set_opacity_side(self._opacity_side_value())

    def _on_side_changed(self, _index: int) -> None:
        if not self._running:
            return
        if not self._view.start(self._settings_from_ui()):
            self._set_running(False)
            self._status.setText("Choose a layer or a group that is still on the map.")

    def _swap(self) -> None:
        if self._running:
            self._view.swap()
            self._select(self._view.settings.left_key, self._view.settings.right_key)
            return
        left = self._left.currentIndex()
        right = self._right.currentIndex()
        self._set_combo_index(self._left, right)
        self._set_combo_index(self._right, left)

    def _refresh(self) -> None:
        self._reload_combos()
        if self._running and not self._view.reload():
            self._set_running(False)
            self._status.setText("Choose a layer or a group that is still on the map.")

    def _schedule_reload(self, *_args) -> None:
        self._reload_timer.start(0)

    def _settings_from_ui(self) -> CompareSettings:
        return CompareSettings(
            left_key=str(self._left.currentData() or ""),
            right_key=str(self._right.currentData() or ""),
            direction=self._direction_value(),
            position=50 if self._locked_position else self._position.value(),
            opacity=self._opacity.value() / 100.0,
            opacity_side=self._opacity_side_value(),
        )

    def _opacity_side_value(self) -> str:
        if self._opacity_left.isChecked():
            return "left"
        if self._opacity_right.isChecked():
            return "right"
        if self._opacity_both.isChecked():
            return "both"
        raise ValueError("Choose left, right, or both for the opacity.")

    def _direction_value(self) -> str:
        if self._vertical.isChecked():
            return "vertical"
        if self._horizontal.isChecked():
            return "horizontal"
        raise ValueError("Choose a vertical or horizontal direction.")

    def _apply_mode_chrome(self) -> None:
        noun = self._mode
        self._intro.setText(_intro(noun))
        self._direction_box.setTitle(f"{noun} Direction")
        self._position_box.setTitle(f"{noun} Position")
        locked = self._locked_position
        self._position.setEnabled(not locked)
        for button in self._presets:
            button.setEnabled(not locked)
        if self._running:
            self._activate.setText(f"Deactivate {noun}")
        else:
            self._activate.setText(f"Activate {noun}")

    def _relabel(self) -> None:
        if self._vertical.isChecked():
            left, right = "Left", "Right"
        else:
            left, right = "Top", "Bottom"
        self._left_label.setText(f"{left}:")
        self._right_label.setText(f"{right}:")
        self._opacity_left.setText(left)
        self._opacity_right.setText(right)

    def _reload_combos(self) -> None:
        left_key = self._left.currentData()
        right_key = self._right.currentData()
        self._sides = collect_sides()
        self._fill_combo(self._left, left_key)
        self._fill_combo(self._right, right_key)
        if self._left.currentIndex() < 0 and self._left.count():
            self._set_combo_index(self._left, 0)
        if self._right.currentIndex() < 0 and self._right.count():
            self._set_combo_index(self._right, 1 if self._right.count() > 1 else 0)

    def _fill_combo(self, combo, key) -> None:
        combo.blockSignals(True)
        combo.clear()
        for side in self._sides:
            combo.addItem(side.label, side.key)
        index = combo.findData(key) if key else -1
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _select(self, left_key: str, right_key: str) -> None:
        self._set_combo_index(self._left, self._left.findData(left_key))
        self._set_combo_index(self._right, self._right.findData(right_key))

    def _set_combo_index(self, combo, index: int) -> None:
        combo.blockSignals(True)
        if index is not None and index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._mode_swipe.setEnabled(not running)
        self._mode_split.setEnabled(not running)
        noun = self._mode
        if running:
            self._activate.setText(f"Deactivate {noun}")
            self._activate.setStyleSheet(
                "background-color: #e07474; color: white; font-weight: bold; padding: 8px;"
            )
            return
        self._activate.setText(f"Activate {noun}")
        self._activate.setStyleSheet(
            "background-color: #6fbf73; color: white; font-weight: bold; padding: 8px;"
        )
        if not self._status.text().startswith("Choose"):
            self._status.setText("")


def _intro(noun: str) -> str:
    if noun == "Swipe":
        return (
            "Compare a layer or a group on each side of one map. "
            "Drag the arrow handle, or set the position. Both sides keep the same extent."
        )
    if noun == "Split":
        return (
            "Compare a layer or a group on two maps. Pan or zoom either map and the other follows. "
            "The two areas stay at 50/50."
        )
    raise ValueError(f"Unknown compare tool {noun!r}.")


def _active_message(noun: str) -> str:
    if noun == "Swipe":
        return "Swipe active. Drag the divider on the map."
    if noun == "Split":
        return "Split active. The two maps move together."
    raise ValueError(f"Unknown compare tool {noun!r}.")


def _orientation(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.Orientation, name)


def _disconnect(signal, slot) -> None:
    try:
        signal.disconnect(slot)
    except TypeError:
        return
