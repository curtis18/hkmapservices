"""Search Hong Kong places and fly the map to the chosen one."""

from __future__ import annotations

import json
import math

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

from .compat import enum_member
from .config import HK1980, WEB_MERCATOR
from .locations import parse_results, search_url


_TITLE = "Location Search"
_LAYER_ROLE = "location-search"
_LAYER_NAME = "Location search"
_COLUMNS = ("Name", "Address", "District")
_VIEW_SIZE = 2000.0


class LocationDialog(QDialog):
    """List every match from one search, then fly to the row that is clicked."""

    def __init__(self, iface_object, parent=None):
        super().__init__(parent or iface_object.mainWindow())
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(720, 420)
        self._iface = iface_object
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
            show_location(self._results[row], self._iface)


def show_location(place: dict, iface_object=None):
    """Add a point of interest and move the map to it. Earlier points stay."""
    canvas = _map_canvas(iface_object)
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
        _show_on_canvas(canvas, layer, iface_object or iface)
        _fly_to(canvas, place["easting"], place["northing"])
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


def _map_canvas(iface_object):
    if iface_object is None:
        iface_object = iface
    if iface_object is None:
        return None
    finder = getattr(iface_object, "mapCanvas", None)
    return finder() if callable(finder) else None


def _show_on_canvas(canvas, layer, iface_object=None) -> None:
    if canvas is None or layer is None:
        return
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    if node is not None and not node.isVisible():
        node.setItemVisibilityChecked(True)
    if layer in canvas.layers():
        return
    finder = getattr(iface_object, "layerTreeCanvasBridge", None) if iface_object is not None else None
    bridge = finder() if callable(finder) else None
    if bridge is not None and hasattr(bridge, "setCanvasLayers"):
        bridge.setCanvasLayers()
        if layer in canvas.layers():
            return
    canvas.setLayers([layer] + list(canvas.layers()))


def _fly_to(canvas, easting: float, northing: float) -> bool:
    if canvas is None:
        return False
    destination = _destination_crs(canvas)
    if not destination.isValid():
        return False
    half = _VIEW_SIZE / 2.0
    rectangle = QgsRectangle(easting - half, northing - half, easting + half, northing + half)
    source = QgsCoordinateReferenceSystem(HK1980)
    if destination == source:
        output = rectangle
    else:
        transform = QgsCoordinateTransform(source, destination, QgsProject.instance())
        if not transform.isValid():
            return False
        try:
            output = transform.transformBoundingBox(rectangle)
        except QgsCsException:
            return False
        if not _round_trips(destination, source, output, easting, northing):
            return False
    if not _usable_extent(output):
        return False
    canvas.setExtent(output)
    canvas.refresh()
    return True


def _destination_crs(canvas) -> QgsCoordinateReferenceSystem:
    """Never hand QGIS an undefined target CRS, which blanks the map."""
    project = QgsProject.instance()
    destination = canvas.mapSettings().destinationCrs()
    if destination.isValid():
        return destination
    destination = project.crs()
    if destination.isValid():
        canvas.setDestinationCrs(destination)
        return destination
    destination = _fallback_crs(project)
    project.setCrs(destination)
    canvas.setDestinationCrs(destination)
    return destination


def _fallback_crs(project) -> QgsCoordinateReferenceSystem:
    for layer in project.mapLayers().values():
        if layer.isValid() and layer.crs().isValid():
            return layer.crs()
    return QgsCoordinateReferenceSystem(WEB_MERCATOR)


def _usable_extent(rectangle) -> bool:
    if rectangle is None or rectangle.isEmpty():
        return False
    values = (
        rectangle.xMinimum(),
        rectangle.yMinimum(),
        rectangle.xMaximum(),
        rectangle.yMaximum(),
    )
    return all(math.isfinite(value) for value in values) and rectangle.width() > 0 and rectangle.height() > 0


def _round_trips(destination, source, rectangle, easting: float, northing: float) -> bool:
    """Reject a transform that quietly left the point in the source CRS."""
    inverse = QgsCoordinateTransform(destination, source, QgsProject.instance())
    if not inverse.isValid():
        return False
    try:
        check = inverse.transform(rectangle.center())
    except QgsCsException:
        return False
    return abs(check.x() - easting) <= 10.0 and abs(check.y() - northing) <= 10.0


def _pair(english: str, chinese: str) -> str:
    lines = [text for text in (english, chinese) if text]
    return "\n".join(lines)


def _no_error():
    return enum_member(QNetworkReply, "NetworkError", "NoError")


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
