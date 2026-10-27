"""Dialog that converts HK1980 Grid and WGS84 coordinates."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QRegularExpression, QSize, Qt, QTimer, QVariant
from qgis.PyQt.QtGui import QIcon, QRegularExpressionValidator
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    QgsCsException,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.utils import iface

from .config import HK1980, WGS84
from .coords import convert_values, transform_xy
from .transform import (
    COORDINATE_PATTERN,
    CSV_HEADERS,
    DIRECTIONS,
    MAX_POINTS,
    TEMPLATE_RECORD,
    format_rounded,
    is_coordinate_text,
    preferred_pair,
    publish_file,
    normalize_record,
    read_csv_records,
    sanitize_records,
)


_TITLE = "Convert Coordinates"
_COLUMNS = CSV_HEADERS
_FIELDS = ("point_id", "northing", "easting", "latitude", "longitude", "remarks")
_MAP_COLUMN = len(_COLUMNS)
_FLY_COLUMN = _MAP_COLUMN + 1
_NUMERIC_COLUMNS = (1, 2, 3, 4)
_LAYER_ROLE = "converted-points"
_LAYER_NAME = "Converted points"
_FILL_BATCH = 200


class ConvertDialog(QDialog):
    """Type coordinates, or load a spreadsheet, and fill in the conversion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(860, 460)
        self._fill_token = 0
        self._fill_purpose = "replace"
        self._after_fill = None
        self._pending_records = []
        self._pending_row = 0
        self._rejected_cells = 0
        self._load_truncated = False
        self._load_count = 0

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Convert between HK1980 Grid and WGS84 geographic coordinates on this computer."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        direction_row = QHBoxLayout()
        direction_row.addWidget(QLabel("Transform"))
        self._direction = QComboBox()
        for key, label in DIRECTIONS:
            self._direction.addItem(label, key)
        direction_row.addWidget(self._direction, 1)
        layout.addLayout(direction_row)

        self._table = QTableWidget(1, _FLY_COLUMN + 1)
        self._table.setHorizontalHeaderLabels(list(_COLUMNS) + ["Map", "Fly"])
        header = self._table.horizontalHeader()
        stretch = header.Stretch if hasattr(header, "Stretch") else header.ResizeMode.Stretch
        contents = header.ResizeToContents if hasattr(header, "ResizeToContents") else header.ResizeMode.ResizeToContents
        for column in range(len(_COLUMNS)):
            header.setSectionResizeMode(column, stretch)
        header.setSectionResizeMode(_MAP_COLUMN, contents)
        header.setSectionResizeMode(_FLY_COLUMN, contents)
        self._table.setIconSize(QSize(16, 16))
        self._decimal_delegate = DecimalDelegate(self._table)
        for column in _NUMERIC_COLUMNS:
            self._table.setItemDelegateForColumn(column, self._decimal_delegate)
        self._write_row(0, _empty_record())
        self._table.cellClicked.connect(self._on_map_cell)
        self._table.itemChanged.connect(self._reject_coordinate_text)
        layout.addWidget(self._table)

        file_buttons = QHBoxLayout()
        add_row = QPushButton("Add row")
        add_row.clicked.connect(self._add_row)
        remove_row = QPushButton("Remove row")
        remove_row.clicked.connect(self._remove_row)
        load = QPushButton("Load file")
        load.clicked.connect(self._load_file)
        template = QPushButton("Download template")
        template.clicked.connect(self._download_template)
        for button in (add_row, remove_row, load, template):
            file_buttons.addWidget(button)
        layout.addLayout(file_buttons)

        action_buttons = QHBoxLayout()
        transform = QPushButton("Transform")
        transform.clicked.connect(self._transform_rows)
        add_all = QPushButton("Add all points")
        add_all.clicked.connect(self._add_all_points)
        save = QPushButton("Save XLSX")
        save.clicked.connect(self._save_xlsx)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        for button in (transform, add_all, save, close):
            action_buttons.addWidget(button)
        layout.addLayout(action_buttons)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

    def closeEvent(self, event) -> None:
        self._fill_token += 1
        super().closeEvent(event)

    def _add_row(self) -> None:
        self._insert_empty_row(True)

    def _insert_empty_row(self, announce: bool) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._write_row(row, _empty_record())
        if announce:
            self._show_status("Added a row.", "ok")

    def _remove_row(self) -> None:
        row = self._table.currentRow()
        if row < 0 and self._table.rowCount() > 0:
            row = self._table.rowCount() - 1
        if row >= 0:
            self._table.removeRow(row)
        if self._table.rowCount() == 0:
            self._insert_empty_row(False)
        self._show_status("Removed a row.", "ok")

    def _load_file(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Load coordinates",
            "",
            "Spreadsheets (*.csv *.xlsx *.xls);;CSV (*.csv);;Excel (*.xlsx *.xls)",
        )
        if not path:
            return
        try:
            records, truncated = load_coordinate_file(path)
        except ValueError as exc:
            self._show_status(str(exc), "error")
            return
        if not records:
            records = [_empty_record()]
            self._load_count = 0
        else:
            self._load_count = len(records)
        records, rejected = sanitize_records(records)
        self._rejected_cells = rejected
        self._load_truncated = truncated
        self._fill_table(records, "load")

    def _download_template(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Download template",
            "coordinates-template.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.casefold().endswith(".xlsx"):
            path = path + ".xlsx"
        try:
            save_template(path)
        except ValueError as exc:
            self._show_status(str(exc), "error")
            return
        self._show_status("Template saved.", "ok")

    def _transform_rows(self) -> None:
        direction = self._direction.currentData()
        records = self._records()
        used = 0
        failed = 0
        for record in records:
            if not _has_direction_input(direction, record):
                continue
            used += 1
            try:
                result = convert_values(direction, **_input_values(direction, record))
            except (ValueError, QgsCsException):
                failed += 1
            else:
                for key, value in result.items():
                    places = 3 if key in ("easting", "northing") else 8
                    record[key] = format_rounded(value, places)
        if used == 0:
            self._show_status("Enter at least one coordinate.", "error")
            return
        self._rejected_cells = 0
        if failed:
            notice = (f"{failed} row(s) could not be converted.", "error")
        else:
            notice = ("Transform finished.", "ok")
        self._fill_table(records, after=notice)

    def _add_all_points(self) -> None:
        self._add_records(self._records())

    def _on_map_cell(self, row: int, column: int) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        if column == _MAP_COLUMN:
            self._add_records([self._record(row)])
            return
        if column == _FLY_COLUMN:
            self._fly_to_row(row)

    def _fly_to_row(self, row: int) -> None:
        try:
            place = _locate_record(self._record(row), self._direction.currentData())
        except (ValueError, QgsCsException):
            place = None
        if place is None:
            self._show_status("Enter a northing and easting, or a latitude and longitude.", "error")
            return
        if not fly_to_place(*place):
            self._show_status("The map could not move to that coordinate.", "error")
            return
        self._show_status("Moved the map to that coordinate.", "ok")

    def _add_records(self, records) -> None:
        _layer, added, skipped = add_converted_points(records, self._direction.currentData())
        count = len(added)
        if count == 0:
            self._show_status("Enter a northing and easting, or a latitude and longitude.", "error")
            return
        kind = "warn" if skipped else "ok"
        self._show_status(added_message(count, skipped), kind)

    def _save_xlsx(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(
            self,
            "Save coordinates",
            "coordinates.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.casefold().endswith(".xlsx"):
            path = path + ".xlsx"
        if not write_xlsx_records(path, self._records()):
            self._show_status(
                "Excel could not be written. Close the file if it is open in another program, then try again.",
                "error",
            )
            return
        self._show_status("Excel file saved.", "ok")

    def _record(self, row: int) -> dict:
        record = {}
        for column, field in enumerate(_FIELDS):
            item = self._table.item(row, column)
            record[field] = "" if item is None else item.text().strip()
        return record

    def _records(self) -> list:
        return [self._record(row) for row in range(self._table.rowCount())]

    def _fill_table(self, records, purpose: str = "replace", after=None) -> None:
        self._fill_purpose = _fill_purpose(purpose)
        self._after_fill = after
        self._fill_token += 1
        token = self._fill_token
        self._pending_records = list(records)
        self._pending_row = 0
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._pending_records))
        if len(self._pending_records) > _FILL_BATCH:
            self._show_status("Loading...", "plain")
        self._fill_batch(token)

    def _fill_batch(self, token: int) -> None:
        if token != self._fill_token:
            return
        end = min(self._pending_row + _FILL_BATCH, len(self._pending_records))
        for row in range(self._pending_row, end):
            self._write_row(row, self._pending_records[row])
        self._pending_row = end
        if self._pending_row < len(self._pending_records):
            QTimer.singleShot(0, lambda token=token: self._fill_batch(token))
            return
        self._table.blockSignals(False)
        self._table.setUpdatesEnabled(True)
        rejected = self._rejected_cells
        self._rejected_cells = 0
        if self._fill_purpose == "load":
            self._show_load_result(rejected)
            return
        notice = self._after_fill
        self._after_fill = None
        if notice is not None:
            text, kind = notice
            self._show_status(text, kind)
            return
        if self._status.text() == "Loading...":
            self._show_status("", "plain")

    def _reject_coordinate_text(self, item) -> None:
        if item.column() not in _NUMERIC_COLUMNS:
            return
        text = item.text().strip()
        if is_coordinate_text(text):
            if item.text() != text:
                self._table.blockSignals(True)
                item.setText(text)
                self._table.blockSignals(False)
            return
        self._table.blockSignals(True)
        item.setText("")
        self._table.blockSignals(False)
        self._show_status("Coordinates can contain only digits and a dot.", "error")

    def _write_row(self, row: int, record: dict) -> None:
        for column, field in enumerate(_FIELDS):
            value = str(record.get(field, ""))
            if field in ("northing", "easting", "latitude", "longitude"):
                value = value.strip()
                if not is_coordinate_text(value):
                    value = ""
            self._table.setItem(row, column, QTableWidgetItem(value))
        self._table.setItem(row, _MAP_COLUMN, _action_item(_icon("icon_add.png"), "Add"))
        self._table.setItem(row, _FLY_COLUMN, _action_item(_icon("icon_fly.png"), "Fly to"))

    def _show_load_result(self, rejected: int) -> None:
        if self._load_count == 0:
            self._show_status("The file had no rows.", "warn")
            return
        parts = []
        if self._load_truncated:
            parts.append(f"Only the first {MAX_POINTS} rows were loaded.")
        if rejected:
            parts.append(f"{rejected} coordinate cell(s) were cleared. Use digits and one dot.")
        if parts:
            self._show_status(" ".join(parts), "warn")
            return
        noun = "row" if self._load_count == 1 else "rows"
        self._show_status(f"Loaded {self._load_count} {noun}.", "ok")

    def _show_status(self, text: str, kind: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet("" if text == "" else _status_style(kind))


def load_coordinate_file(path: str) -> tuple:
    """Return normalized records and whether the file was cut at the row limit."""
    suffix = os.path.splitext(path)[1].casefold()
    if suffix == ".csv":
        records = read_csv_records(path)
    elif suffix in (".xlsx", ".xls"):
        records = _read_ogr_records(path)
    else:
        raise ValueError("Load a CSV or Excel file.")
    truncated = len(records) > MAX_POINTS
    return records[:MAX_POINTS], truncated


def save_template(path: str) -> str:
    """Write a one-row Excel template. Return the path that was written."""
    if not path.casefold().endswith(".xlsx"):
        raise ValueError("Excel could not be written.")
    if not write_xlsx_records(path, [TEMPLATE_RECORD]):
        raise ValueError("Excel could not be written.")
    return path


def write_xlsx_records(path: str, records: list) -> bool:
    """Write rows with the GDAL Excel driver. Leave an existing file in place when the write fails."""
    return publish_file(path, lambda temp: _write_xlsx_file(temp, records))


def _write_xlsx_file(path: str, records: list) -> bool:
    layer = QgsVectorLayer("NoGeometry", "coordinates", "memory")
    if not layer.isValid():
        return False
    fields = [QgsField(name, QVariant.String) for name in CSV_HEADERS]
    if not layer.dataProvider().addAttributes(fields):
        return False
    layer.updateFields()
    features = []
    for record in records:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([str(record.get(field, "")) for field in _FIELDS])
        features.append(feature)
    if not layer.dataProvider().addFeatures(features):
        return False
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "XLSX"
    options.fileEncoding = "UTF-8"
    try:
        error, _message, _path, _layer_name = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            path,
            QgsProject.instance().transformContext(),
            options,
        )
    except Exception:
        return False
    return error == QgsVectorFileWriter.NoError


def added_message(count: int, skipped: int) -> str:
    """Sentence for a finished add. The count is how many points were added."""
    if count == 1:
        message = "Added 1 point."
    else:
        message = f"Added {count} points."
    if skipped:
        message += f" {skipped} row(s) were skipped because a coordinate pair was incomplete."
    return message


def add_converted_points(records: list, direction: str = "hkgrid") -> tuple:
    """Add complete rows to the Converted points layer. Return layer, ids, and skipped count."""
    located = []
    skipped = 0
    for record in records:
        try:
            place = _locate_record(record, direction)
        except (ValueError, QgsCsException):
            place = None
        if place is None:
            skipped += 1
            continue
        located.append((record, place))
    if not located:
        return None, [], skipped

    layer = _converted_layer()
    created = layer is None
    if created:
        layer = _new_points_layer(located[0][1][0])

    added = []
    layer.startEditing()
    for record, (authid, point) in located:
        try:
            if authid != layer.crs().authid():
                point = transform_xy(point, authid, layer.crs())
        except QgsCsException:
            skipped += 1
            continue
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromPointXY(point))
        feature.setAttributes(
            [
                record.get("point_id", ""),
                _stored_number(record.get("northing", "")),
                _stored_number(record.get("easting", "")),
                _stored_number(record.get("latitude", "")),
                _stored_number(record.get("longitude", "")),
            ]
        )
        if layer.addFeature(feature):
            added.append(feature.id())
        else:
            skipped += 1
    if not layer.commitChanges():
        layer.rollBack()
        return None, [], skipped + len(located)
    if created and added:
        QgsProject.instance().addMapLayer(layer)
    elif created:
        return None, [], skipped
    if added:
        _show_points(layer)
    return layer, added, skipped


def _read_ogr_records(path: str) -> list:
    layer = QgsVectorLayer(_ogr_uri(path), "coordinates", "ogr")
    if not layer.isValid():
        raise ValueError("Excel could not be opened. Save the sheet as CSV and load that instead.")
    names = [field.name() for field in layer.fields()]
    records = []
    for feature in layer.getFeatures():
        raw = {}
        for name in names:
            value = feature[name]
            if value is None or (hasattr(value, "isNull") and value.isNull()):
                raw[name] = ""
            else:
                raw[name] = value
        records.append(normalize_record(raw))
    return records


def _ogr_uri(path: str) -> str:
    suffix = os.path.splitext(path)[1].casefold()
    if suffix in (".xlsx", ".xls"):
        return path + "|option:HEADERS=FORCE"
    return path


def _converted_layer():
    for layer in QgsProject.instance().mapLayers().values():
        if layer.customProperty("hkmapservices/role") == _LAYER_ROLE and layer.isValid():
            return layer
    return None


def _new_points_layer(authid: str) -> QgsVectorLayer:
    uri = (
        f"Point?crs={authid}"
        "&field=point_id:string(80)"
        "&field=northing:double"
        "&field=easting:double"
        "&field=latitude:double"
        "&field=longitude:double"
    )
    layer = QgsVectorLayer(uri, _LAYER_NAME, "memory")
    layer.setCustomProperty("hkmapservices/role", _LAYER_ROLE)
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": "192,57,43,255",
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
            "size": "3.4",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def _locate_record(record: dict, direction: str = "hkgrid"):
    chosen = preferred_pair(direction, record)
    if chosen == "wgsgeog":
        latitude = _optional_number(record.get("latitude", ""))
        longitude = _optional_number(record.get("longitude", ""))
        if latitude is None or longitude is None:
            return None
        return WGS84, QgsPointXY(longitude, latitude)
    if chosen == "hkgrid":
        northing = _optional_number(record.get("northing", ""))
        easting = _optional_number(record.get("easting", ""))
        if northing is None or easting is None:
            return None
        return HK1980, QgsPointXY(easting, northing)
    if chosen is None:
        return None
    raise ValueError(f"Unknown direction {chosen!r}.")


def fly_to_place(authid: str, point) -> bool:
    """Move the map to one coordinate. Return False when the canvas cannot move."""
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None:
        return False
    destination = canvas.mapSettings().destinationCrs()
    try:
        center = transform_xy(point, authid, destination)
    except QgsCsException:
        return False
    pad = 0.01 if destination.isGeographic() else 400
    canvas.setExtent(QgsRectangle(center.x() - pad, center.y() - pad, center.x() + pad, center.y() + pad))
    canvas.refresh()
    return True


def _show_points(layer) -> None:
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None:
        return
    extent = layer.extent()
    if extent.isNull() or extent.isEmpty():
        canvas.refresh()
        return
    try:
        output = canvas.mapSettings().layerExtentToOutputExtent(layer, extent)
    except QgsCsException:
        canvas.refresh()
        return
    if output.width() == 0 or output.height() == 0:
        center = output.center()
        pad = 0.01 if canvas.mapSettings().destinationCrs().isGeographic() else 400
        output = QgsRectangle(center.x() - pad, center.y() - pad, center.x() + pad, center.y() + pad)
    else:
        output.scale(1.3)
    canvas.setExtent(output)
    canvas.refresh()


class DecimalDelegate(QStyledItemDelegate):
    """Accept only digits and one dot in a coordinate cell."""

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setValidator(QRegularExpressionValidator(QRegularExpression(COORDINATE_PATTERN), editor))
        return editor


def _icon(filename: str) -> QIcon:
    return QIcon(os.path.join(os.path.dirname(__file__), filename))


def _fill_purpose(purpose: str) -> str:
    if purpose == "load" or purpose == "replace":
        return purpose
    raise ValueError(f"Unknown table fill {purpose!r}.")


def _status_style(kind: str) -> str:
    if kind == "plain":
        return ""
    if kind == "ok":
        return "background-color: #d9f2df; color: #14532d; padding: 6px;"
    if kind == "warn":
        return "background-color: #fdecc8; color: #7c4a03; padding: 6px;"
    if kind == "error":
        return "background-color: #f8d0d0; color: #7f1d1d; padding: 6px;"
    raise ValueError(f"Unknown status {kind!r}.")


def _action_item(icon: QIcon, tip: str) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setIcon(icon)
    item.setToolTip(tip)
    item.setTextAlignment(_alignment("AlignCenter"))
    item.setFlags(item.flags() & ~_item_flag("ItemIsEditable"))
    return item


def _alignment(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.AlignmentFlag, name)


def _item_flag(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.ItemFlag, name)


def _input_values(direction: str, record: dict) -> dict:
    if direction == "hkgrid":
        return {
            "easting": _required_number(record.get("easting", "")),
            "northing": _required_number(record.get("northing", "")),
        }
    if direction == "wgsgeog":
        return {
            "latitude": _required_number(record.get("latitude", "")),
            "longitude": _required_number(record.get("longitude", "")),
        }
    raise ValueError(f"Unknown conversion direction {direction!r}.")


def _required_number(text: str) -> float:
    number = _optional_number(text)
    if number is None:
        raise ValueError("A coordinate is missing.")
    return number


def _optional_number(text: str):
    cleaned = str(text).strip().replace(",", "")
    if cleaned == "":
        return None
    return float(cleaned)


def _stored_number(text: str):
    try:
        return _optional_number(text)
    except ValueError:
        return None


def _has_direction_input(direction: str, record: dict) -> bool:
    if direction == "hkgrid":
        fields = ("northing", "easting")
    elif direction == "wgsgeog":
        fields = ("latitude", "longitude")
    else:
        fields = ("northing", "easting", "latitude", "longitude")
    return any(str(record.get(field, "")).strip() for field in fields)


def _empty_record() -> dict:
    return {field: "" for field in _FIELDS}

