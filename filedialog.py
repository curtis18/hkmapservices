"""Choose a database and write a GeoPackage, a File Geodatabase, or both."""

from __future__ import annotations

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .compat import enum_member, exec_dialog
from .fileconvert import convert_files
from .status import apply_status, prepare_status


_TITLE = "Convert FGDB / GPKG Files"
_GRID_KEY = "hkmapservices/file_grid_decimals"
_GEO_KEY = "hkmapservices/file_geo_decimals"
_LENGTH_KEY = "hkmapservices/file_length_decimals"
_AREA_KEY = "hkmapservices/file_area_decimals"


class FileDialog(QDialog):
    """Copy point, line, and polygon layers into GeoPackage or File Geodatabase."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._stop = False
        self._closing = False
        self._unload = False
        self._closed = False
        self.setWindowTitle(_TITLE)
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Copy point, line, and polygon layers between a File Geodatabase and a GeoPackage, "
            "one database at a time. Add Northing, Easting, Latitude, and Longitude on their own. "
            "A point uses that point. A line or a polygon uses the centre of the geometry. "
            "Add Length and Area on their own. Length is in metres, and area is in square metres."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._source = QLineEdit()
        self._source.setReadOnly(True)
        self._source.setPlaceholderText("File Geodatabase, GeoPackage, or a folder of geodatabases")
        source_row = QHBoxLayout()
        source_row.addWidget(self._source, 1)
        browse_gpkg = QPushButton("GeoPackage")
        browse_gpkg.clicked.connect(self._browse_gpkg)
        browse_gdb = QPushButton("File Geodatabase")
        browse_gdb.clicked.connect(self._browse_gdb)
        source_row.addWidget(browse_gpkg)
        source_row.addWidget(browse_gdb)
        layout.addLayout(source_row)

        self._destination = QLineEdit()
        self._destination.setReadOnly(True)
        self._destination.setPlaceholderText("Destination folder")
        destination_row = QHBoxLayout()
        destination_row.addWidget(self._destination, 1)
        browse_destination = QPushButton("Folder")
        browse_destination.clicked.connect(self._browse_destination)
        destination_row.addWidget(browse_destination)
        layout.addLayout(destination_row)

        self._gpkg = QCheckBox("GeoPackage")
        self._fgdb = QCheckBox("File Geodatabase")
        self._coordinates = QCheckBox("Add Northing, Easting, Latitude, and Longitude")
        self._measures = QCheckBox("Add Length and Area")
        self._skip_tables = QCheckBox("Skip tables without geometry")
        layout.addWidget(self._gpkg)
        layout.addWidget(self._fgdb)
        layout.addWidget(self._coordinates)
        layout.addWidget(self._measures)
        layout.addWidget(self._skip_tables)

        buttons = QHBoxLayout()
        settings = QPushButton("Settings")
        settings.clicked.connect(self._edit_settings)
        convert = QPushButton("Convert")
        convert.clicked.connect(self._convert)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(settings)
        buttons.addWidget(convert)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._convert = convert
        self._busy_buttons = (browse_gpkg, browse_gdb, browse_destination, settings)
        self._source.textChanged.connect(self._refresh_convert)
        self._destination.textChanged.connect(self._refresh_convert)
        self._gpkg.toggled.connect(self._refresh_convert)
        self._fgdb.toggled.connect(self._refresh_convert)

        self._status = QLabel("")
        layout.addWidget(prepare_status(self._status))
        self._refresh_convert()

    def reject(self) -> None:
        if self._running:
            self._stop = True
            self._closing = True
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._running and not self._unload:
            self._stop = True
            self._closing = True
            event.ignore()
            return
        if self._running:
            self._stop = True
            self.hide()
        self._closed = True
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop a running conversion before the plugin drops this dialog."""
        self._stop = True
        self._unload = True
        self.hide()
        if self._running:
            self._closing = True

    def _browse_gpkg(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "GeoPackage", self._source.text(), "GeoPackage (*.gpkg)")
        if path:
            self._source.setText(path)

    def _browse_gdb(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "File Geodatabase", self._source.text())
        if path:
            self._source.setText(path)

    def _browse_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Destination folder", self._destination.text())
        if path:
            self._destination.setText(path)

    def _edit_settings(self) -> None:
        dialog = FileSettingsDialog(self)
        if exec_dialog(dialog):
            dialog.save()

    def _convert(self) -> None:
        if self._running:
            return
        outputs = []
        if self._gpkg.isChecked():
            outputs.append("gpkg")
        if self._fgdb.isChecked():
            outputs.append("fgdb")
        self._stop = False
        self._running = True
        self._set_busy(True)
        self._show_status("Processing...", "ok")
        QApplication.processEvents()
        try:
            text, kind = convert_files(
                self._source.text().strip(),
                self._destination.text().strip(),
                outputs,
                self._coordinates.isChecked(),
                self._measures.isChecked(),
                self._skip_tables.isChecked(),
                file_places(),
                self._pulse,
            )
        except Exception as exc:
            text, kind = str(exc) or "The conversion failed.", "error"
        finally:
            self._running = False
            self._set_busy(False)
        if not self._closed:
            self._show_status(text, kind)
        self._stop = False
        if self._closing and not self._closed:
            self._closing = False
            self.reject()

    def _pulse(self) -> bool:
        QApplication.processEvents()
        return not self._stop

    def _refresh_convert(self, *_args) -> None:
        ready = bool(
            self._source.text().strip()
            and self._destination.text().strip()
            and (self._gpkg.isChecked() or self._fgdb.isChecked())
        )
        self._convert.setEnabled(ready and not self._running)

    def _set_busy(self, busy: bool) -> None:
        for button in self._busy_buttons:
            button.setEnabled(not busy)
        self._refresh_convert()

    def _show_status(self, text: str, kind: str) -> None:
        apply_status(self._status, text, kind)


class FileSettingsDialog(QDialog):
    """Choose decimal places for coordinates, length, and area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_TITLE)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        places = file_places()
        self._grid = _places_box(places["grid"])
        self._geo = _places_box(places["geo"])
        self._length = _places_box(places["length"])
        self._area = _places_box(places["area"])
        form.addRow("HK1980 decimal places", self._grid)
        form.addRow("Latitude and longitude decimal places", self._geo)
        form.addRow("Length decimal places", self._length)
        form.addRow("Area decimal places", self._area)
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
        settings.setValue(_LENGTH_KEY, self._length.value())
        settings.setValue(_AREA_KEY, self._area.value())


def file_places() -> dict:
    """Return the stored decimal places for a file conversion."""
    return {
        "grid": _stored_places(_GRID_KEY, 3),
        "geo": _stored_places(_GEO_KEY, 6),
        "length": _stored_places(_LENGTH_KEY, 2),
        "area": _stored_places(_AREA_KEY, 2),
    }


def _places_box(value: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(0, 8)
    box.setValue(value)
    return box


def _stored_places(key: str, default: int) -> int:
    value = QSettings().value(key, default)
    try:
        places = int(value)
    except (TypeError, ValueError):
        return default
    return min(8, max(0, places))


def _button_role(name: str):
    return enum_member(QDialogButtonBox, "ButtonRole", name)



