"""Search Hong Kong places and fly the map to the chosen one."""

from __future__ import annotations

import json

from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsFeature,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsNetworkAccessManager,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.utils import iface

from .config import HK1980
from .locations import parse_results, search_url


_TITLE = "Location Search"
_LAYER_ROLE = "location-search"
_LAYER_NAME = "Location search"
_COLUMNS = ("Name", "Address", "District")


class LocationDialog(QDialog):
    """List every match from one search, then fly to the row that is clicked."""

    def __init__(self, iface_object, parent=None):
        super().__init__(parent or iface_object.mainWindow())
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(720, 420)
        self._reply = None
        self._closed = False
        self._results = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        layout = QVBoxLayout(self)
        intro = QLabel("Search a place, address, or facility. Click a result to fly there.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        query_row = QHBoxLayout()
        self._query = QLineEdit()
        self._query.setPlaceholderText("Place, address, or facility")
        self._query.returnPressed.connect(self._start_search)
        self._search_button = QPushButton("Search")
        self._search_button.clicked.connect(self._start_search)
        query_row.addWidget(self._query, 1)
        query_row.addWidget(self._search_button)
        layout.addLayout(query_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.setWordWrap(True)
        header = self._table.horizontalHeader()
        stretch = header.Stretch if hasattr(header, "Stretch") else header.ResizeMode.Stretch
        header.setSectionResizeMode(stretch)
        vertical = self._table.verticalHeader()
        contents = vertical.ResizeToContents if hasattr(vertical, "ResizeToContents") else vertical.ResizeMode.ResizeToContents
        vertical.setSectionResizeMode(contents)
        self._table.setSelectionBehavior(_selection("SelectRows"))
        self._table.setEditTriggers(_edit("NoEditTriggers"))
        self._table.cellClicked.connect(self._fly_to_row)
        layout.addWidget(self._table)

        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def closeEvent(self, event) -> None:
        self._closed = True
        self._abort_reply()
        super().closeEvent(event)

    def reject(self) -> None:
        """Abort an in-flight search when Close or Esc rejects the dialog."""
        self._closed = True
        self._abort_reply()
        super().reject()

    def _start_search(self) -> None:
        self._closed = False
        query = self._query.text().strip()
        if not query:
            self._status.setText("Enter a place, address, or facility name.")
            return
        try:
            url = search_url(query)
        except ValueError as exc:
            self._status.setText(str(exc))
            return
        self._abort_reply()
        self._results = []
        self._table.setRowCount(0)
        self._status.setText("Searching...")
        self._search_button.setEnabled(False)
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"hkmapservices")
        self._reply = QgsNetworkAccessManager.instance().get(request)
        self._reply.finished.connect(self._on_reply)
        self._timer.start(30000)

    def _on_reply(self) -> None:
        reply = self._reply
        self._reply = None
        self._timer.stop()
        self._search_button.setEnabled(True)
        if reply is None:
            return
        error = reply.error()
        raw = bytes(reply.readAll())
        message = reply.errorString()
        reply.deleteLater()
        if self._closed:
            return
        if error != _no_error():
            self._status.setText(message or "The location search failed.")
            return
        try:
            results = parse_results(json.loads(raw.decode("utf-8-sig")))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._status.setText(str(exc) or "The location search could not be read.")
            return
        self._show_results(results)

    def _on_timeout(self) -> None:
        if self._reply is None:
            return
        self._abort_reply()
        self._search_button.setEnabled(True)
        if not self._closed:
            self._status.setText("The location search timed out.")

    def _abort_reply(self) -> None:
        self._timer.stop()
        reply = self._reply
        self._reply = None
        if reply is None:
            return
        try:
            reply.finished.disconnect(self._on_reply)
        except TypeError:
            pass
        reply.abort()
        reply.deleteLater()

    def _show_results(self, results) -> None:
        self._results = results
        self._table.setRowCount(len(results))
        top = _alignment("AlignLeft") | _alignment("AlignTop")
        for row, place in enumerate(results):
            values = (
                _pair(place["name_en"], place["name_zh"]),
                _pair(place["address_en"], place["address_zh"]),
                _pair(place["district_en"], place["district_zh"]),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(top)
                self._table.setItem(row, column, item)
        self._table.resizeRowsToContents()
        if results:
            self._status.setText(f"{len(results)} locations.")
        else:
            self._status.setText("No locations found.")

    def _fly_to_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._results):
            show_location(self._results[row])


def show_location(place: dict):
    """Add a point of interest and move the map to it. Earlier points stay."""
    layer = _location_layer()
    created = layer is None
    if created:
        layer = _new_location_layer()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(place["easting"], place["northing"])))
    feature.setAttributes(
        [
            place.get("name_en", ""),
            place.get("name_zh", ""),
            place.get("address_en", ""),
            place.get("address_zh", ""),
            place.get("district_en", ""),
            place.get("district_zh", ""),
        ]
    )
    layer.startEditing()
    added = layer.addFeature(feature)
    if not layer.commitChanges():
        layer.rollBack()
        return None
    if created and added:
        QgsProject.instance().addMapLayer(layer)
    if added:
        _fly_to(place["easting"], place["northing"])
    return layer


def _location_layer():
    for layer in QgsProject.instance().mapLayers().values():
        if layer.customProperty("hkmapservices/role") == _LAYER_ROLE and layer.isValid():
            return layer
    return None


def _new_location_layer() -> QgsVectorLayer:
    uri = (
        f"Point?crs={HK1980}"
        "&field=name_en:string(255)"
        "&field=name_zh:string(255)"
        "&field=address_en:string(255)"
        "&field=address_zh:string(255)"
        "&field=district_en:string(80)"
        "&field=district_zh:string(80)"
    )
    layer = QgsVectorLayer(uri, _LAYER_NAME, "memory")
    layer.setCustomProperty("hkmapservices/role", _LAYER_ROLE)
    symbol = QgsMarkerSymbol.createSimple(
        {
            "name": "circle",
            "color": "192,57,43,255",
            "outline_color": "255,255,255,255",
            "outline_width": "0.4",
            "size": "4.5",
        }
    )
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def _fly_to(easting: float, northing: float) -> None:
    canvas = iface.mapCanvas() if iface is not None else None
    if canvas is None:
        return
    rectangle = QgsRectangle(easting - 250, northing - 250, easting + 250, northing + 250)
    source = QgsCoordinateReferenceSystem(HK1980)
    transform = QgsCoordinateTransform(source, canvas.mapSettings().destinationCrs(), QgsProject.instance())
    try:
        output = transform.transformBoundingBox(rectangle)
    except QgsCsException:
        canvas.refresh()
        return
    canvas.setExtent(output)
    canvas.refresh()


def _pair(english: str, chinese: str) -> str:
    lines = [text for text in (english, chinese) if text]
    return "\n".join(lines)


def _no_error():
    if hasattr(QNetworkReply, "NoError"):
        return QNetworkReply.NoError
    return QNetworkReply.NetworkError.NoError


def _alignment(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.AlignmentFlag, name)


def _selection(name: str):
    widget = QTableWidget
    if hasattr(widget, name):
        return getattr(widget, name)
    return getattr(widget.SelectionBehavior, name)


def _edit(name: str):
    widget = QTableWidget
    if hasattr(widget, name):
        return getattr(widget, name)
    return getattr(widget.EditTrigger, name)
