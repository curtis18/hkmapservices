"""Live HK1980 and WGS84 coordinates under the map cursor."""

from __future__ import annotations

from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtGui import QFont, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsCsException

from .config import HK1980, WGS84
from .coords import transform_xy
from .transform import format_dms, format_rounded


_GRID_KEY = "hkmapservices/cursor_grid_decimals"
_GEO_KEY = "hkmapservices/cursor_geo_decimals"
_HOLD_SHORTCUT = "Ctrl+Alt+Shift+H"
_REPLACED_HOLD_SHORTCUTS = ("Ctrl+Shift+H", "Ctrl+Alt+H")


class CursorDock(QDockWidget):
    """Show both coordinate systems as the cursor moves across the canvas."""

    def __init__(self, iface):
        super().__init__("Cursor Coordinates", iface.mainWindow())
        self.iface = iface
        self.setObjectName("hkmapservicesCursorDock")
        self.setMinimumWidth(340)
        self._listening = False
        self._held = False
        self._hold_action = None
        self._outside = False
        self._northing_value = None
        self._easting_value = None
        self._latitude_value = None
        self._longitude_value = None

        body = QWidget()
        layout = QVBoxLayout(body)
        self._status = QLabel("Tracking. Move the cursor over the map.")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        controls = QHBoxLayout()
        self._hold_button = QPushButton(f"Hold ({_HOLD_SHORTCUT})")
        self._hold_button.setCheckable(True)
        self._hold_button.toggled.connect(self._set_held)
        settings = QPushButton("Settings")
        settings.clicked.connect(self._edit_settings)
        controls.addWidget(self._hold_button)
        controls.addWidget(settings)
        layout.addLayout(controls)

        grid = QGroupBox("HK1980 Grid")
        grid_layout = QVBoxLayout(grid)
        self._northing_row, self._northing_label, self._northing_button = _coordinate_row("Northing")
        self._easting_row, self._easting_label, self._easting_button = _coordinate_row("Easting")
        self._northing_button.clicked.connect(self._copy_northing)
        self._easting_button.clicked.connect(self._copy_easting)
        grid_layout.addWidget(self._northing_row)
        grid_layout.addWidget(self._easting_row)
        layout.addWidget(grid)

        geographic = QGroupBox("WGS84")
        self._geographic = geographic
        geographic_layout = QVBoxLayout(geographic)
        self._latitude_row, self._latitude_label, self._latitude_button = _coordinate_row("Latitude")
        self._longitude_row, self._longitude_label, self._longitude_button = _coordinate_row("Longitude")
        self._latitude_dms_row, self._latitude_dms, self._latitude_dms_button = _coordinate_row("")
        self._longitude_dms_row, self._longitude_dms, self._longitude_dms_button = _coordinate_row("")
        self._latitude_dms.setWordWrap(False)
        self._longitude_dms.setWordWrap(False)
        self._latitude_button.clicked.connect(self._copy_latitude)
        self._longitude_button.clicked.connect(self._copy_longitude)
        self._latitude_dms_button.clicked.connect(self._copy_latitude_dms)
        self._longitude_dms_button.clicked.connect(self._copy_longitude_dms)
        geographic_layout.addWidget(self._latitude_row)
        geographic_layout.addWidget(self._latitude_dms_row)
        geographic_layout.addWidget(self._longitude_row)
        geographic_layout.addWidget(self._longitude_dms_row)
        layout.addWidget(geographic)
        layout.addStretch(1)
        self.setWidget(body)
        self._refresh_text()
        self._connect_canvas()

    def bind_hold(self, action) -> None:
        """Share the checked state with the main-window Hold action."""
        self._hold_action = action
        self._sync_hold_button()
        action.changed.connect(self._sync_hold_button)
        action.toggled.connect(self._apply_hold)
        self._apply_hold(action.isChecked())

    def _sync_hold_button(self) -> None:
        action = self._hold_action
        if action is None:
            return
        sequence = action.shortcut().toString().replace(" ", "")
        self._hold_button.setText(f"Hold ({sequence})" if sequence else "Hold")

    def toggle_hold(self) -> None:
        self._hold_button.setChecked(not self._hold_button.isChecked())

    def closeEvent(self, event) -> None:
        self._disconnect_canvas()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:
        self._disconnect_canvas()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        self._connect_canvas()
        super().showEvent(event)

    def unload(self) -> None:
        self._disconnect_canvas()
        action = self._hold_action
        if action is None:
            return
        try:
            action.changed.disconnect(self._sync_hold_button)
        except TypeError:
            pass
        try:
            action.toggled.disconnect(self._apply_hold)
        except TypeError:
            pass

    def _connect_canvas(self) -> None:
        if self._listening:
            return
        canvas = self.iface.mapCanvas()
        if canvas is None:
            return
        canvas.xyCoordinates.connect(self._on_xy)
        self._listening = True

    def _disconnect_canvas(self) -> None:
        if not self._listening:
            return
        canvas = self.iface.mapCanvas()
        if canvas is not None:
            try:
                canvas.xyCoordinates.disconnect(self._on_xy)
            except TypeError:
                pass
        self._listening = False

    def _set_held(self, held: bool) -> None:
        held = bool(held)
        self._held = held
        action = self._hold_action
        if action is not None and action.isChecked() != held:
            action.setChecked(held)
        self._refresh_status()

    def _apply_hold(self, held: bool) -> None:
        held = bool(held)
        self._held = held
        self._hold_button.blockSignals(True)
        self._hold_button.setChecked(held)
        self._hold_button.blockSignals(False)
        self._refresh_status()

    def _edit_settings(self) -> None:
        dialog = CursorSettingsDialog(self)
        if _accepted(dialog):
            dialog.save()
            self._refresh_text()

    def _on_xy(self, point) -> None:
        if self._held:
            return
        canvas = self.iface.mapCanvas()
        source = canvas.mapSettings().destinationCrs()
        try:
            hk_point = transform_xy(point, source, HK1980)
            wgs_point = transform_xy(point, source, WGS84)
        except QgsCsException:
            self._outside = True
            self._northing_value = None
            self._easting_value = None
            self._latitude_value = None
            self._longitude_value = None
            self._refresh_text()
            return
        self._outside = False
        self._northing_value = hk_point.y()
        self._easting_value = hk_point.x()
        self._latitude_value = wgs_point.y()
        self._longitude_value = wgs_point.x()
        self._refresh_text()

    def _refresh_text(self) -> None:
        grid_places = grid_decimals()
        geo_places = geo_decimals()
        self._show_value(self._northing_label, self._northing_button, self._northing_value, grid_places, " m")
        self._show_value(self._easting_label, self._easting_button, self._easting_value, grid_places, " m")
        self._show_value(self._latitude_label, self._latitude_button, self._latitude_value, geo_places, "\u00b0")
        self._show_value(self._longitude_label, self._longitude_button, self._longitude_value, geo_places, "\u00b0")
        self._show_dms(self._latitude_dms, self._latitude_dms_button, self._latitude_value)
        self._show_dms(self._longitude_dms, self._longitude_dms_button, self._longitude_value)
        self._refresh_status()

    def _show_dms(self, label, button, value) -> None:
        if value is None:
            label.setText("")
            button.setEnabled(False)
            button.setProperty("copy_text", "")
            return
        label.setText(_dms_text(value))
        button.setEnabled(True)
        button.setProperty("copy_text", _dms_copy_text(value))

    def _show_value(self, label, button, value, places: int, suffix: str) -> None:
        if value is None:
            label.setText("—")
            button.setEnabled(False)
            button.setProperty("copy_text", "")
            return
        number = format_rounded(value, places)
        label.setText(number + suffix)
        button.setEnabled(True)
        button.setProperty("copy_text", number)

    def _refresh_status(self) -> None:
        if self._outside and not self._held:
            self._status.setText("Outside the transform.")
            return
        if self._northing_value is None and not self._held:
            self._status.setText("Tracking. Move the cursor over the map.")
            return
        if self._northing_value is None and self._held:
            self._status.setText("Held. Move the cursor over the map.")
            return
        self._status.setText("Held" if self._held else "Tracking")

    def _copy_northing(self) -> None:
        _copy_button(self._northing_button)

    def _copy_easting(self) -> None:
        _copy_button(self._easting_button)

    def _copy_latitude(self) -> None:
        _copy_button(self._latitude_button)

    def _copy_longitude(self) -> None:
        _copy_button(self._longitude_button)

    def _copy_latitude_dms(self) -> None:
        _copy_button(self._latitude_dms_button)

    def _copy_longitude_dms(self) -> None:
        _copy_button(self._longitude_dms_button)


class CursorSettingsDialog(QDialog):
    """Choose how many decimal places the cursor dock shows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cursor Coordinates")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._grid = QSpinBox()
        self._grid.setRange(0, 8)
        self._grid.setValue(grid_decimals())
        self._geo = QSpinBox()
        self._geo.setRange(0, 8)
        self._geo.setValue(geo_decimals())
        form.addRow("HK1980 decimal places", self._grid)
        form.addRow("Latitude and longitude decimal places", self._geo)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        buttons.addButton("OK", _button_role("AcceptRole"))
        buttons.addButton("Cancel", _button_role("RejectRole"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self) -> None:
        settings = QSettings()
        settings.setValue(_GRID_KEY, self._grid.value())
        settings.setValue(_GEO_KEY, self._geo.value())


def grid_decimals() -> int:
    return _stored_places(_GRID_KEY, 0)


def geo_decimals() -> int:
    return _stored_places(_GEO_KEY, 6)


def _stored_places(key: str, default: int) -> int:
    value = QSettings().value(key, default)
    try:
        places = int(value)
    except (TypeError, ValueError):
        return default
    return min(8, max(0, places))


def _coordinate_row(name: str):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    title = QLabel(name)
    title.setMinimumWidth(78)
    value = _value_label()
    button = QPushButton("Copy")
    button.setEnabled(False)
    layout.addWidget(title)
    layout.addWidget(value, 1)
    layout.addWidget(button)
    return row, value, button


def _dms_text(value) -> str:
    if value is None:
        return ""
    return f"{format_dms(value)} (DMS)"


def _dms_copy_text(value) -> str:
    if value is None:
        return ""
    return format_dms(value)


def _value_label() -> QLabel:
    label = QLabel("—")
    label.setFont(_number_font())
    label.setWordWrap(True)
    label.setTextInteractionFlags(_text_flag("TextSelectableByMouse"))
    return label


def _copy_button(button) -> None:
    text = button.property("copy_text")
    if not text:
        return
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(str(text))


def _number_font() -> QFont:
    font = QFont("Consolas")
    if hasattr(QFont, "Monospace"):
        font.setStyleHint(QFont.Monospace)
    else:
        font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def _text_flag(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.TextInteractionFlag, name)


def _button_role(name: str):
    if hasattr(QDialogButtonBox, name):
        return getattr(QDialogButtonBox, name)
    return getattr(QDialogButtonBox.ButtonRole, name)


def _accepted(dialog) -> bool:
    result = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
    return bool(result)
