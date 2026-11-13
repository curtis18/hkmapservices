"""Headless checks for basemap and dataset layers.

Run with the QGIS Python launcher, for example:

    python-qgis-ltr.bat tests\\smoke_qgis.py
"""

from __future__ import annotations

import gc
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_ROOT = os.path.dirname(PLUGIN_ROOT)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)


def main() -> int:
    from qgis.core import (
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsCoordinateTransform,
        QgsLineSymbol,
        QgsNetworkAccessManager,
        QgsPointXY,
        QgsProject,
        QgsRectangle,
        QgsSimpleMarkerSymbolLayerBase,
    )

    prefix = os.environ.get("QGIS_PREFIX_PATH", "")
    if prefix:
        QgsApplication.setPrefixPath(prefix, True)
    application = QgsApplication([], False)
    application.initQgis()
    failures = []
    try:
        from qgis.PyQt.QtGui import QValidator
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import (
            QAction,
            QGraphicsOpacityEffect,
            QLabel,
            QPushButton,
            QStackedWidget,
            QStyleOptionViewItem,
            QWidget,
        )
        from qgis.core import QgsVectorLayer
        from qgis.gui import QgsMapCanvas

        def _delete_on_close(dialog) -> bool:
            attribute = getattr(Qt, "WA_DeleteOnClose", None)
            if attribute is None:
                widget_attribute = getattr(Qt, "WidgetAttribute", None)
                attribute = getattr(widget_attribute, "WA_DeleteOnClose", None)
            if attribute is None:
                return False
            return bool(dialog.testAttribute(attribute))

        from hkmapservices.aboutdialog import AboutDialog
        from hkmapservices.catalog import hk80_raster_xml, raster_url
        from hkmapservices.compare import OpacityGuard, collect_sides
        from hkmapservices.comparedock import CompareDock
        from hkmapservices.convertdialog import (
            ConvertDialog,
            DecimalDelegate,
            add_converted_points,
            _locate_record,
            added_message,
            load_coordinate_file,
            save_template,
        )
        from hkmapservices.cursordock import CursorDock, _HOLD_SHORTCUT, _dms_copy_text, _dms_text
        from hkmapservices.hkmapservices import _keep_hold_shortcut
        from hkmapservices.locationdialog import LocationDialog, show_location
        from hkmapservices.splitview import SplitView
        from hkmapservices.swipeview import SwipeView
        from hkmapservices.coords import convert_values
        from hkmapservices.dialog import BasemapDialog, DatasetDialog
        from hkmapservices.layers import (
            _bounded_network,
            _preflight,
            make_basemap_layer,
            make_dataset_layer,
            make_label_layer,
        )

        line_symbol = QgsLineSymbol.createSimple({"color": "35,121,153,255", "width": "0.7"})
        if line_symbol is None:
            failures.append("line symbol could not be created")

        basemap_dialog = BasemapDialog()
        basemap_request = basemap_dialog.request()
        print(
            "dialog basemap",
            basemap_request.basemap,
            basemap_request.crs,
            basemap_request.label_kind,
            basemap_request.label_language,
        )
        if basemap_request.basemap != "imagery" or basemap_request.crs != "WGS84":
            failures.append("basemap dialog defaults are not Imagery Map and WGS84")
        if basemap_request.label_kind != "raster" or basemap_request.label_language != "tc":
            failures.append("basemap dialog label default is not raster Traditional Chinese")
        if not basemap_request.set_project_crs:
            failures.append("basemap dialog does not set the project CRS")

        dataset_dialog = DatasetDialog()
        dataset_request = dataset_dialog.request()
        visible_titles = [box.text() for _dataset_id, box, _row in dataset_dialog._dataset_boxes]
        print("dialog datasets", dataset_request.dataset_ids, dataset_request.name_language, visible_titles)
        if dataset_request.dataset_ids != ():
            failures.append(f"dataset dialog default is {dataset_request.dataset_ids}")
        if dataset_dialog._add_button.isEnabled():
            failures.append("Add to map starts enabled")
        if dataset_request.name_language != "tc":
            failures.append("dataset dialog language default is not Traditional Chinese")
        if visible_titles != [
            "Barbecue Sites",
            "District Boundary",
            "Fitness Rooms",
            "Libraries",
            "Outdoor Fitness Equipment",
            "Traffic Detectors",
            "Water Dispensers",
        ]:
            failures.append(f"dataset dialog order is {visible_titles}")
        barbecue_box = next(box for dataset_id, box, _row in dataset_dialog._dataset_boxes if dataset_id == "barbecue")
        barbecue_box.setChecked(True)
        if not dataset_dialog._add_button.isEnabled():
            failures.append("Add to map stays disabled after a dataset is checked")
        if dataset_dialog.request().dataset_ids != ("barbecue",):
            failures.append(f"checked dataset is {dataset_dialog.request().dataset_ids}")
        dataset_dialog._search.setText("fit")
        filtered = [
            box.text()
            for _dataset_id, box, row in dataset_dialog._dataset_boxes
            if not row.isHidden()
        ]
        if filtered != ["Fitness Rooms", "Outdoor Fitness Equipment"]:
            failures.append(f"dataset search showed {filtered}")
        traffic_row = next(row for dataset_id, _box, row in dataset_dialog._dataset_boxes if dataset_id == "traffic")
        traffic_link = traffic_row.findChild(QLabel)
        expected_link = "https://portal.csdi.gov.hk/geoportal/?lang=en&datasetId=td_rcd_1671693191724_92214"
        if traffic_link is None or expected_link not in traffic_link.text():
            failures.append(f"dataset link is {None if traffic_link is None else traffic_link.text()}")
        if dataset_dialog.request().dataset_ids != ("barbecue",):
            failures.append("dataset search changed the checked datasets")
        if not dataset_dialog._add_button.isEnabled():
            failures.append("search disabled Add to map while a hidden row stayed checked")
        barbecue_box.setChecked(False)
        if dataset_dialog._add_button.isEnabled():
            failures.append("Add to map stays enabled with nothing checked")

        grid_files = tempfile.TemporaryDirectory()
        directory = grid_files.name
        checks = (
            ("topographic", "WGS84", "EPSG:3857"),
            ("topographic", "HK80", "EPSG:2326"),
            ("imagery", "WGS84", "EPSG:3857"),
            ("imagery", "HK80", "EPSG:2326"),
            ("vector", "WGS84", "EPSG:3857"),
            ("vector", "HK80", "EPSG:2326"),
        )
        for kind, crs_key, expected in checks:
            layer = make_basemap_layer(kind, crs_key, directory)
            authid = layer.crs().authid() if layer.crs().isValid() else ""
            print(f"basemap {kind} {crs_key}: valid={layer.isValid()} crs={authid}")
            if not layer.isValid():
                failures.append(f"{kind} {crs_key} is not valid: {layer.error().summary()}")
            elif authid != expected:
                failures.append(f"{kind} {crs_key} CRS is {authid}, expected {expected}")
            if layer.hasScaleBasedVisibility():
                failures.append(f"{kind} {crs_key} is hidden outside a scale range")
            if kind == "vector" and layer.isValid():
                renderer = layer.renderer()
                style_count = len(renderer.styles()) if renderer is not None and hasattr(renderer, "styles") else 0
                print(f"  styles={style_count} sourceMaxZoom={layer.sourceMaxZoom()}")
                if style_count == 0:
                    failures.append(f"{kind} {crs_key} has no vector style rules")
                if layer.sourceMaxZoom() != 15:
                    failures.append(f"{kind} {crs_key} source max zoom is {layer.sourceMaxZoom()}")
            QgsProject.instance().removeMapLayer(layer.id()) if layer.id() else None

        label_checks = (
            ("raster", "WGS84"),
            ("raster", "HK80"),
            ("vector", "WGS84"),
            ("vector", "HK80"),
        )
        for label_kind, crs_key in label_checks:
            layer = make_label_layer(label_kind, crs_key, "tc", directory)
            print(f"label {label_kind} {crs_key}: valid={layer.isValid()}")
            if not layer.isValid():
                failures.append(f"label {label_kind} {crs_key} is not valid: {layer.error().summary()}")
            elif layer.hasScaleBasedVisibility():
                failures.append(f"label {label_kind} {crs_key} is hidden outside a scale range")
            elif label_kind == "vector" and layer.sourceMaxZoom() != 15:
                failures.append(f"label {label_kind} {crs_key} source max zoom is {layer.sourceMaxZoom()}")
            QgsProject.instance().removeMapLayer(layer.id()) if layer.id() else None

        datasets = (
            ("district", "EPSG:2326"),
            ("libraries", "EPSG:2326"),
            ("fitness", "EPSG:2326"),
            ("outdoor", "EPSG:4326"),
            ("traffic", "EPSG:2326"),
            ("water", "EPSG:4326"),
            ("barbecue", "EPSG:2326"),
        )
        for dataset_id, expected in datasets:
            layer = make_dataset_layer(dataset_id, "tc")
            authid = layer.crs().authid() if layer.crs().isValid() else ""
            print(f"dataset {dataset_id}: valid={layer.isValid()} crs={authid} features={layer.featureCount()}")
            if not layer.isValid():
                failures.append(f"{dataset_id} is not valid: {layer.error().summary()}")
            elif authid != expected:
                failures.append(f"{dataset_id} CRS is {authid}, expected {expected}")
            if dataset_id == "barbecue" and layer.isValid():
                marker = layer.renderer().symbol().symbolLayer(0)
                print(f"  marker={marker.shape()}")
                if marker.shape() != QgsSimpleMarkerSymbolLayerBase.Star:
                    failures.append(f"barbecue marker is {marker.shape()}")

        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        silent.settimeout(10)
        port = silent.getsockname()[1]

        def hold_connection():
            try:
                connection, _address = silent.accept()
            except OSError:
                return
            time.sleep(5)
            connection.close()

        threading.Thread(target=hold_connection, daemon=True).start()
        started = time.monotonic()
        problem = _preflight(f"http://127.0.0.1:{port}/", "Test service", 1500)
        elapsed = time.monotonic() - started
        print("timeout check", problem, round(elapsed, 2))
        silent.close()
        if problem is None or "timed out" not in problem:
            failures.append(f"preflight timeout message is {problem!r}")
        if elapsed > 5:
            failures.append(f"preflight timeout took {elapsed:.1f} seconds")

        manager = QgsNetworkAccessManager.instance()
        original_timeout = manager.timeout()
        with _bounded_network(1500):
            temporary_timeout = manager.timeout()
        restored_timeout = manager.timeout()
        expected_timeout = original_timeout if original_timeout and original_timeout < 1500 else 1500
        print(
            "timeout setting",
            original_timeout,
            "->",
            temporary_timeout,
            "->",
            restored_timeout,
        )
        if temporary_timeout != expected_timeout:
            failures.append(f"temporary network timeout is {temporary_timeout}")
        if restored_timeout != original_timeout:
            failures.append(
                f"network timeout was {original_timeout} and is now {restored_timeout}"
            )

        source = QgsCoordinateReferenceSystem("EPSG:2326")
        destination = QgsCoordinateReferenceSystem("EPSG:4326")
        converted = QgsCoordinateTransform(source, destination, QgsProject.instance()).transform(
            QgsPointXY(836694.05, 819069.8)
        )
        print("cursor transform", converted.x(), converted.y())
        if not (114.0 < converted.x() < 114.3 and 22.2 < converted.y() < 22.4):
            failures.append(f"cursor transform axis order is {converted.x()}, {converted.y()}")

        projected = convert_values("hkgrid", easting=836694.05, northing=819069.8)
        print("proj transform", projected["longitude"], projected["latitude"])
        if abs(projected["latitude"] - 22.3106) > 0.0001 or abs(projected["longitude"] - 114.1810) > 0.0001:
            failures.append(f"sample point is {projected}")
        restored = convert_values("wgsgeog", latitude=projected["latitude"], longitude=projected["longitude"])
        if abs(restored["easting"] - 836694.05) > 0.05 or abs(restored["northing"] - 819069.8) > 0.05:
            failures.append(f"reverse sample point is {restored}")

        convert_dialog = ConvertDialog()
        headers = [convert_dialog._table.horizontalHeaderItem(column).text() for column in range(6)]
        if convert_dialog.windowTitle() != "Convert Coordinates":
            failures.append(f"convert dialog title is {convert_dialog.windowTitle()}")
        if convert_dialog._direction.count() != 2 or headers != [
            "Point ID",
            "Northing (m)",
            "Easting (m)",
            "Latitude",
            "Longitude",
            "Remarks",
        ]:
            failures.append(f"convert dialog columns are {headers}")
        add_item = convert_dialog._table.item(0, 6)
        if (
            add_item is None
            or add_item.text()
            or add_item.toolTip() != "Add"
            or add_item.icon().isNull()
            or convert_dialog._table.cellWidget(0, 6) is not None
        ):
            failures.append("convert dialog row has no Add icon")
        fly_item = convert_dialog._table.item(0, 7)
        if fly_item is None or fly_item.text() or fly_item.toolTip() != "Fly to" or fly_item.icon().isNull():
            failures.append("convert dialog row has no Fly icon")
        status_host = convert_dialog._status
        while status_host is not None and convert_dialog.layout().indexOf(status_host) < 0:
            status_host = status_host.parentWidget()
        if status_host is None or convert_dialog.layout().indexOf(status_host) < convert_dialog.layout().indexOf(convert_dialog._table):
            failures.append("convert status is not under the buttons")
        convert_dialog._add_row()
        if convert_dialog._status.text() != "Added a row." or "#d9f2df" not in convert_dialog._status.styleSheet():
            failures.append("add row did not report success")
        convert_dialog._show_status("Enter a northing and easting, or a latitude and longitude.", "error")
        if "#f8d0d0" not in convert_dialog._status.styleSheet():
            failures.append("convert failure is not red")
        both_sides = {
            "northing": "819069.8",
            "easting": "836694.05",
            "latitude": "22.0",
            "longitude": "114.0",
        }
        grid_place = _locate_record(both_sides, "hkgrid")
        geographic_place = _locate_record(both_sides, "wgsgeog")
        if grid_place is None or grid_place[0] != "EPSG:2326" or geographic_place is None or geographic_place[0] != "EPSG:4326":
            failures.append("a filled row ignored the selected transform direction")
        if not isinstance(convert_dialog._table.itemDelegateForColumn(1), DecimalDelegate):
            failures.append("northing column has no decimal delegate")
        editor = convert_dialog._decimal_delegate.createEditor(
            convert_dialog,
            QStyleOptionViewItem(),
            convert_dialog._table.model().index(0, 1),
        )
        invalid = QValidator.Invalid if hasattr(QValidator, "Invalid") else QValidator.State.Invalid
        acceptable = QValidator.Acceptable if hasattr(QValidator, "Acceptable") else QValidator.State.Acceptable
        dot_state = editor.validator().validate(".", 0)[0]
        if (
            editor.validator().validate("asdf", 0)[0] != invalid
            or dot_state == acceptable
            or editor.validator().validate(".5", 0)[0] != acceptable
            or editor.validator().validate("22.13", 0)[0] != acceptable
        ):
            failures.append("coordinate editor accepted a non-decimal")
        editor.deleteLater()
        convert_dialog._write_row(
            0,
            {
                "point_id": "bad text stays",
                "northing": "asdf",
                "easting": "836694.05",
                "latitude": "22.13ds",
                "longitude": "",
                "remarks": "site note",
            },
        )
        if convert_dialog._table.item(0, 0).text() != "bad text stays" or convert_dialog._table.item(0, 1).text() != "":
            failures.append("typed coordinates were not limited to digits and a dot")
        if convert_dialog._table.item(0, 5).text() != "site note":
            failures.append("remarks text was cleared")
        if added_message(1, 0) != "Added 1 point." or added_message(3, 1) != (
            "Added 3 points. 1 row(s) were skipped because a coordinate pair was incomplete."
        ):
            failures.append("add message used the feature id list")
        if convert_dialog._table.item(0, 2).text() != "836694.05":
            failures.append("a decimal easting was cleared")
        wide_records = []
        for index in range(250):
            wide_records.append(
                {
                    "point_id": str(index),
                    "northing": "",
                    "easting": "",
                    "latitude": "",
                    "longitude": "",
                }
            )
        convert_dialog._fill_table(wide_records)
        for _step in range(10):
            application.processEvents()
            if convert_dialog._status.text() == "" and convert_dialog._table.item(249, 6) is not None:
                break
        loaded_add = convert_dialog._table.item(249, 6)
        if (
            convert_dialog._table.rowCount() != 250
            or loaded_add is None
            or loaded_add.toolTip() != "Add"
            or loaded_add.icon().isNull()
        ):
            failures.append("a long coordinate table did not finish loading")
        if convert_dialog._table.cellWidget(249, 6) is not None or convert_dialog._table.item(249, 7) is None:
            failures.append("a long coordinate table built a button for every row")
        convert_dialog._fill_table(wide_records + wide_records)
        convert_dialog.close()
        application.processEvents()
        if (
            convert_dialog._table.signalsBlocked()
            or not convert_dialog._table.updatesEnabled()
            or convert_dialog._table.rowCount() != 200
            or not convert_dialog._busy_buttons[0].isEnabled()
        ):
            failures.append("closing during a fill left the coordinate table locked")
        convert_dialog._fill_table(wide_records + wide_records)
        convert_dialog.reject()
        application.processEvents()
        if (
            convert_dialog._table.signalsBlocked()
            or not convert_dialog._table.updatesEnabled()
            or convert_dialog._table.rowCount() != 200
            or not convert_dialog._busy_buttons[0].isEnabled()
        ):
            failures.append("rejecting during a fill left the coordinate table locked")

        geographic_layer, geographic_ids, geographic_skipped = add_converted_points(
            [
                {
                    "point_id": "A",
                    "northing": "",
                    "easting": "",
                    "latitude": "22.3106",
                    "longitude": "114.1810",
                }
            ]
        )
        if geographic_layer is None or geographic_layer.crs().authid() != "EPSG:4326" or len(geographic_ids) != 1:
            failures.append("latitude and longitude did not add a WGS84 point")
        elif geographic_skipped != 0:
            failures.append(f"WGS84 point skipped {geographic_skipped}")
        grid_layer, grid_ids, _grid_skipped = add_converted_points(
            [
                {
                    "point_id": "B",
                    "northing": "819069.8",
                    "easting": "836694.05",
                    "latitude": "",
                    "longitude": "",
                }
            ]
        )
        if (
            geographic_layer is None
            or grid_layer is None
            or grid_layer.id() != geographic_layer.id()
            or grid_layer.featureCount() != 2
        ):
            failures.append("a later grid point did not reuse Converted points")
        elif len(grid_ids) != 1:
            failures.append("grid point was not added")
        _empty_layer, empty_ids, empty_skipped = add_converted_points(
            [{"point_id": "C", "northing": "1", "easting": "", "latitude": "", "longitude": ""}]
        )
        if empty_ids or empty_skipped != 1:
            failures.append(f"incomplete row added {empty_ids} and skipped {empty_skipped}")

        template_dir = os.path.join(directory, "template")
        os.makedirs(template_dir, exist_ok=True)
        template_xlsx = os.path.join(template_dir, "coordinates-template.xlsx")
        try:
            written_xlsx = save_template(template_xlsx)
        except ValueError as exc:
            failures.append(str(exc))
        else:
            try:
                xlsx_records, _truncated = load_coordinate_file(written_xlsx)
            except ValueError as exc:
                failures.append(str(exc))
            else:
                print("xlsx template", written_xlsx, xlsx_records[:1])
                if not xlsx_records or abs(float(xlsx_records[0].get("northing", "nan")) - 819069.8) > 0.001:
                    failures.append(f"Excel template is {xlsx_records[:1]}")

        first_place = {
            "name_en": "HONG KONG CULTURAL CENTRE",
            "name_zh": "香港文化中心",
            "address_en": "10 SALISBURY ROAD",
            "address_zh": "梳士巴利道 10號",
            "district_en": "Yau Tsim Mong District",
            "district_zh": "油尖旺區",
            "easting": 835599.0,
            "northing": 817190.0,
        }
        second_place = dict(first_place)
        second_place["name_en"] = "Second place"
        second_place["easting"] = 803285.0
        second_place["northing"] = 813131.0
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        location_layer = show_location(first_place)
        if location_layer is None or location_layer.crs().authid() != "EPSG:2326" or location_layer.featureCount() != 1:
            failures.append("location search did not add a HK1980 point")
        elif show_location(second_place).featureCount() != 2:
            failures.append("a later location did not keep the first point")

        canvas = QgsMapCanvas()
        canvas.resize(640, 480)
        existing_layer = QgsVectorLayer("Point?crs=EPSG:3857", "Existing canvas layer", "memory")
        QgsProject.instance().addMapLayer(existing_layer, False)
        canvas.setLayers([existing_layer])
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem())

        class _CanvasIface:
            def mapCanvas(self):
                return canvas

            def mainWindow(self):
                return None

        travelled = show_location(first_place, _CanvasIface())
        destination = canvas.mapSettings().destinationCrs()
        if not destination.isValid():
            failures.append("location search left the map without a CRS")
        elif travelled not in canvas.layers() or existing_layer not in canvas.layers():
            failures.append("location search dropped layers from the map")
        else:
            transform = QgsCoordinateTransform(
                QgsCoordinateReferenceSystem("EPSG:2326"),
                destination,
                QgsProject.instance(),
            )
            expected = transform.transform(QgsPointXY(first_place["easting"], first_place["northing"]))
            centre = canvas.extent().center()
            if abs(centre.x() - expected.x()) > 1.0 or abs(centre.y() - expected.y()) > 1.0:
                failures.append(f"location search centred on {centre.toString()} instead of {expected.toString()}")
            if canvas.scale() >= 1000000:
                failures.append(f"location search zoomed to an unusable scale of {canvas.scale()}")
        QgsProject.instance().removeMapLayer(existing_layer.id())
        canvas.deleteLater()
        application.processEvents()

        if _dms_text(22.310602434) != "22\u00b0 18' 38.17\" (DMS)":
            failures.append(f"DMS label is {_dms_text(22.310602434)!r}")
        if _dms_copy_text(22.310602434) != "22\u00b0 18' 38.17\"":
            failures.append(f"DMS copy is {_dms_copy_text(22.310602434)!r}")

        class _Iface:
            def mapCanvas(self):
                return None

            def mainWindow(self):
                return None

        location_dialog = LocationDialog(_Iface())
        location_dialog._closed = True
        location_dialog._start_search()
        if location_dialog._closed:
            failures.append("a new location search stayed closed")
        location_dialog.reject()
        if not location_dialog._closed:
            failures.append("rejecting the location dialog did not stop the search")

        cursor = CursorDock(_Iface())
        margins = cursor._latitude_dms_row.layout().contentsMargins()
        if cursor._geographic.minimumHeight() > 0 or margins.top() or margins.bottom() or cursor._latitude_dms.wordWrap():
            failures.append("DMS rows still have extra padding")
        cursor._latitude_value = 22.310602434
        cursor._refresh_text()
        if cursor._latitude_dms.text() != "22\u00b0 18' 38.17\" (DMS)":
            failures.append(f"DMS row is {cursor._latitude_dms.text()!r}")
        if cursor._latitude_dms_button.property("copy_text") != "22\u00b0 18' 38.17\"":
            failures.append(f"DMS copy button is {cursor._latitude_dms_button.property('copy_text')!r}")
        cursor.unload()

        hold_window = QWidget()
        hold_action = QAction(f"Hold ({_HOLD_SHORTCUT})", hold_window)
        hold_action.setObjectName("hkmapservicesHold")
        hold_action.setCheckable(True)
        hold_action.setShortcut("Ctrl+Alt+H")
        _keep_hold_shortcut(hold_action)
        application_shortcut = (
            Qt.ApplicationShortcut if hasattr(Qt, "ApplicationShortcut") else Qt.ShortcutContext.ApplicationShortcut
        )
        if (
            hold_action not in hold_window.actions()
            or hold_action.shortcut().toString() != _HOLD_SHORTCUT
            or hold_action.autoRepeat()
            or hold_action.shortcutContext() != application_shortcut
        ):
            failures.append("Hold shortcut is not installed on the window")
        held_cursor = CursorDock(_Iface())
        held_cursor.bind_hold(hold_action)
        held_cursor._northing_value = 819069.8
        held_cursor._easting_value = 836694.05
        held_cursor._latitude_value = 22.31060243
        held_cursor._longitude_value = 114.18100933
        hold_action.setChecked(True)
        if not held_cursor._held or held_cursor._hold_button.text() != f"Hold ({_HOLD_SHORTCUT})":
            failures.append("Hold shortcut did not freeze the cursor")
        else:
            held_cursor._on_xy(QgsPointXY(0, 0))
            if held_cursor._northing_value != 819069.8 or held_cursor._latitude_value != 22.31060243:
                failures.append("a held cursor reading changed")
        held_cursor.unload()

        split_view = SplitView(_Iface())
        split_view.set_position(25)
        if split_view.settings.position != 50:
            failures.append(f"split position is {split_view.settings.position}")

        class _SyncSettings:
            def __init__(self, canvas):
                self._canvas = canvas

            def destinationCrs(self):
                return self._canvas.crs

        class _SyncCanvas:
            def __init__(self, extent):
                self.extent_value = extent
                self.crs = QgsCoordinateReferenceSystem("EPSG:2326")
                self.rotation_value = 0.0
                self.resized_during_extent = False
                self.refreshed = False
                self._inside = False
                self._blocked = False
                self.on_extent = None

            def mapSettings(self):
                return _SyncSettings(self)

            def rotation(self):
                return self.rotation_value

            def extent(self):
                return self.extent_value

            def setDestinationCrs(self, crs):
                self.crs = crs

            def setRotation(self, value):
                self.rotation_value = value

            def setExtent(self, extent):
                self._inside = True
                self.extent_value = extent
                if self.on_extent is not None and not self._blocked:
                    self.on_extent()
                self._inside = False

            def refresh(self):
                self.refreshed = True

            def blockSignals(self, blocked):
                self._blocked = bool(blocked)

            def resizeEvent(self, _event):
                if self._inside:
                    self.resized_during_extent = True

            def isDrawing(self):
                return False

        sync_source = _SyncCanvas(QgsRectangle(0, 0, 100, 100))
        sync_target = _SyncCanvas(QgsRectangle(0, 0, 1, 1))

        class _SyncIface:
            def mapCanvas(self):
                return sync_source

        sync_view = SplitView(_SyncIface())
        sync_view._active = True
        sync_view._second = sync_target
        sync_source.on_extent = sync_view._from_main
        sync_source.setExtent(QgsRectangle(10, 10, 50, 50))
        if sync_source.resized_during_extent or sync_target.resized_during_extent:
            failures.append("split resized the canvas inside setExtent")
        if sync_target.extent_value.xMinimum() == 10:
            failures.append("split applied the extent before setExtent returned")
        application.processEvents()
        if sync_target.extent_value.xMinimum() != 10 or not sync_target.refreshed:
            failures.append("split did not copy the extent after the zoom returned")
        sync_view._sync_timer.stop()
        sync_view._active = False
        sync_view._second = None
        swipe_view = SwipeView(_Iface())
        swipe_view.set_position(30)
        if swipe_view.settings.position != 30 or swipe_view.settings.direction != "vertical":
            failures.append("swipe position was not kept")

        class _JobSignal:
            def __init__(self):
                self._slots = []

            def connect(self, slot):
                self._slots.append(slot)

            def emit(self):
                for slot in list(self._slots):
                    slot()

        class _RenderJob:
            def __init__(self):
                self.finished = _JobSignal()
                self.deleted = 0
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

            def deleteLater(self):
                self.deleted += 1

            def renderedImage(self):
                return None

        render_job = _RenderJob()
        render_job.finished.connect(
            lambda side="left", token=1, job=render_job: swipe_view._job_finished(side, token, job)
        )
        swipe_view._generation = 2
        swipe_view._jobs["left"] = (1, render_job)
        swipe_view._cancel_job("left")
        if render_job.deleted or not render_job.cancelled or render_job not in swipe_view._retiring:
            failures.append("cancelled swipe job was deleted before it finished")
        render_job.finished.emit()
        if render_job.deleted != 1 or render_job in swipe_view._retiring:
            failures.append("finished swipe job was not deleted once")

        root = QgsProject.instance().layerTreeRoot()
        compare_group = root.addGroup("Compare group")
        grouped = QgsVectorLayer("Point?crs=EPSG:4326", "Grouped point", "memory")
        loose = QgsVectorLayer("Point?crs=EPSG:4326", "Loose point", "memory")
        QgsProject.instance().addMapLayer(grouped, False)
        QgsProject.instance().addMapLayer(loose, False)
        compare_group.addLayer(grouped)
        root.addLayer(loose)
        sides = {side.label: side.layer_ids for side in collect_sides()}
        group_side = sides.get("Group: Compare group")
        if group_side != [grouped.id()] or "Loose point" not in sides:
            failures.append(f"compare choices are {sorted(sides)}")
        first_twin = root.addGroup("Twin")
        second_twin = root.addGroup("Twin")
        first_layer = QgsVectorLayer("Point?crs=EPSG:4326", "Twin one", "memory")
        second_layer = QgsVectorLayer("Point?crs=EPSG:4326", "Twin two", "memory")
        QgsProject.instance().addMapLayer(first_layer, False)
        QgsProject.instance().addMapLayer(second_layer, False)
        first_twin.addLayer(first_layer)
        second_twin.addLayer(second_layer)
        twins = [side for side in collect_sides() if side.label.startswith("Group: Twin")]
        if len(twins) != 2 or twins[0].key == twins[1].key or not any(side.label.endswith(" (2)") for side in twins):
            failures.append(f"duplicate groups are {[side.label + ' ' + side.key for side in twins]}")
        grouped.setOpacity(0.8)
        swipe_view._settings.opacity = 0.34
        swipe_view._settings.opacity_side = "right"
        swipe_view._apply_opacity()
        if abs(grouped.opacity() - 0.8) > 1e-9:
            failures.append("swipe opacity changed the layer")
        left_pane = QWidget()
        right_pane = QWidget()
        split_view._settings.opacity = 0.34
        split_view._settings.opacity_side = "right"
        split_view._set_pane_opacity(left_pane, "left")
        split_view._set_pane_opacity(right_pane, "right")
        right_effect = right_pane.graphicsEffect()
        if (
            left_pane.graphicsEffect() is not None
            or not isinstance(right_effect, QGraphicsOpacityEffect)
            or abs(right_effect.opacity() - 0.34) > 1e-9
        ):
            failures.append("split opacity faded the wrong pane")
        split_view._clear_pane_effects()
        left_pane.setGraphicsEffect(None)
        right_pane.setGraphicsEffect(None)
        guard = OpacityGuard()
        guard.apply([grouped], 0.25)
        if abs(grouped.opacity() - 0.25) > 1e-9:
            failures.append("opacity was not applied to the group layer")
        guard.restore()
        if abs(grouped.opacity() - 0.8) > 1e-9:
            failures.append("opacity was not restored")

        compare_dock = CompareDock(swipe_view, split_view)
        if compare_dock._mode != "Swipe" or not compare_dock._position.isEnabled() or compare_dock._left_label.text() != "Left:":
            failures.append("swipe position or side labels are missing")
        compare_dock._horizontal.setChecked(True)
        if compare_dock._left_label.text() != "Top:" or compare_dock._right_label.text() != "Bottom:":
            failures.append("horizontal direction did not rename the sides")
        if compare_dock._opacity_left.text() != "Top" or compare_dock._opacity_right.text() != "Bottom" or not compare_dock._opacity_both.isChecked():
            failures.append("opacity sides did not follow the direction")
        compare_dock._mode_split.setChecked(True)
        if compare_dock._mode != "Split" or compare_dock._position.isEnabled() or compare_dock._position.value() != 50 or compare_dock._presets[0].isEnabled():
            failures.append("split position can move away from 50/50")
        compare_dock._mode_swipe.setChecked(True)
        if compare_dock._mode != "Swipe" or not compare_dock._position.isEnabled():
            failures.append("swipe position stays locked after leaving split")
        if compare_dock._intro.parent() is None or not compare_dock._intro.text():
            failures.append("mode description is missing")
        compare_dock._set_running(True)
        if compare_dock._mode_swipe.isEnabled() or compare_dock._mode_split.isEnabled():
            failures.append("mode can change while the tool is active")
        compare_dock._set_running(False)
        if not compare_dock._mode_swipe.isEnabled() or not compare_dock._mode_split.isEnabled():
            failures.append("mode stays locked after deactivate")
        about = AboutDialog()
        if about._tabs.count() != 2 or about._tabs.tabText(0) != "Info" or about._tabs.tabText(1) != "License":
            failures.append("about dialog tabs are not Info and License")
        license_text = about._license.toPlainText()
        if (
            "Curtis Yung" not in about._info.text()
            or "<b>Developer:</b>" not in about._info.text()
            or "Swipe and Split" not in about._info.text()
            or not license_text.splitlines()[0].endswith("GNU GENERAL PUBLIC LICENSE")
            or not license_text.startswith(" ")
            or license_text.startswith("Hong Kong Map Services")
        ):
            failures.append("about dialog is missing the developer, the description, or the GNU heading")
        if not about._license.font().fixedPitch():
            failures.append("license text is not fixed width")
        if about.minimumWidth() != 552:
            failures.append(f"about dialog width is {about.minimumWidth()}")
        icon = about._icon.pixmap()
        if icon is None or icon.isNull():
            failures.append("about dialog has no icon")
        welcome = QWidget()
        splitter = QWidget()
        stack = QStackedWidget()
        stack.addWidget(welcome)
        stack.addWidget(splitter)
        split_view._active = True
        split_view._stacked = stack
        split_view._splitter = splitter
        stack.setCurrentWidget(welcome)
        split_view._keep_splitter_current()
        if stack.currentWidget() is not splitter:
            failures.append("split left the welcome page in front of the maps")
        split_view._active = False
        split_view._stacked = None
        split_view._splitter = None
        compare_dock.shutdown()
        application.processEvents()
        QgsProject.instance().removeMapLayer(grouped.id())
        QgsProject.instance().removeMapLayer(loose.id())

        from qgis.core import (
            QgsAnnotationLayer,
            QgsCoordinateTransform,
            QgsFeature,
            QgsField,
            QgsGeometry,
            QgsMapSettings,
            QgsMarkerSymbol,
            QgsRasterLayer,
            QgsSingleSymbolRenderer,
            QgsRectangle,
            QgsUnitTypes,
            QgsVectorFileWriter,
        )
        from qgis.PyQt.QtCore import QSize, QVariant

        from osgeo import gdal
        from osgeo import ogr
        from osgeo import osr

        from hkmapservices import fileconvert as fileconvert_module
        from hkmapservices.convertdialog import ConvertDialog
        from hkmapservices.downloader import database_fields
        from hkmapservices.downloaddialog import DownloadDialog
        from hkmapservices.fileconvert import _metres, _publish_directory, convert_files
        from hkmapservices.filedialog import FileDialog
        from hkmapservices.hkmapservices import HkMapServices
        from hkmapservices.svgexport import _ground_metres, _wait_for_render, export_map_svg
        from hkmapservices.svgdialog import SvgDialog

        file_dir = os.path.join(directory, "files")
        os.makedirs(file_dir, exist_ok=True)
        source_gpkg = os.path.join(file_dir, "survey.gpkg")
        site = QgsVectorLayer("Point?crs=EPSG:2326", "site", "memory")
        site.dataProvider().addAttributes([QgsField("name", QVariant.String)])
        site.updateFields()
        site_feature = QgsFeature(site.fields())
        site_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(836694.05, 819069.8)))
        site_feature["name"] = "A"
        site.dataProvider().addFeatures([site_feature])
        line = QgsVectorLayer("LineString?crs=EPSG:2326", "road", "memory")
        line_feature = QgsFeature()
        line_feature.setGeometry(
            QgsGeometry.fromPolylineXY([QgsPointXY(836000, 819000), QgsPointXY(836100, 819050)])
        )
        line.dataProvider().addFeatures([line_feature])
        block = QgsVectorLayer("Polygon?crs=EPSG:2326", "block", "memory")
        block_feature = QgsFeature()
        block_feature.setGeometry(
            QgsGeometry.fromPolygonXY(
                [[
                    QgsPointXY(836000, 819000),
                    QgsPointXY(836100, 819000),
                    QgsPointXY(836100, 819100),
                    QgsPointXY(836000, 819100),
                    QgsPointXY(836000, 819000),
                ]]
            )
        )
        block.dataProvider().addFeatures([block_feature])
        notes = QgsVectorLayer("NoGeometry", "notes", "memory")
        if not notes.isValid():
            notes = QgsVectorLayer("None", "notes", "memory")
        notes.dataProvider().addAttributes([QgsField("name", QVariant.String), QgsField("url", QVariant.String)])
        notes.updateFields()
        note = QgsFeature(notes.fields())
        note["name"] = "table"
        note["url"] = "https://example.com/photo.tif"
        notes.dataProvider().addFeatures([note])
        named = QgsVectorLayer("Point?crs=EPSG:2326", "named", "memory")
        named.dataProvider().addAttributes(
            [
                QgsField("NORTHING", QVariant.Double),
                QgsField("EASTING", QVariant.Double),
                QgsField("LATITUDE", QVariant.Double),
                QgsField("LONGITUDE", QVariant.Double),
            ]
        )
        named.updateFields()
        named_feature = QgsFeature(named.fields())
        named_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(836694.05, 819069.8)))
        named.dataProvider().addFeatures([named_feature])
        transform_context = QgsProject.instance().transformContext()
        for index, memory_layer in enumerate((site, line, block, notes, named)):
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = memory_layer.name()
            options.actionOnExistingFile = (
                QgsVectorFileWriter.CreateOrOverwriteFile
                if index == 0
                else QgsVectorFileWriter.CreateOrOverwriteLayer
            )
            writer_error = QgsVectorFileWriter.writeAsVectorFormatV3(
                memory_layer,
                source_gpkg,
                transform_context,
                options,
            )[0]
            if writer_error != QgsVectorFileWriter.NoError:
                failures.append(f"sample geopackage layer {memory_layer.name()} was not written")
        typed_dataset = ogr.Open(source_gpkg, 1)
        typed_srs = osr.SpatialReference()
        typed_srs.ImportFromEPSG(2326)
        typed_layer = typed_dataset.CreateLayer("typed", typed_srs, ogr.wkbPoint)
        typed_layer.CreateField(ogr.FieldDefn("survey_date", ogr.OFTDate))
        typed_layer.CreateField(ogr.FieldDefn("survey_time", ogr.OFTDateTime))
        typed_layer.CreateField(ogr.FieldDefn("photo", ogr.OFTBinary))
        label_defn = ogr.FieldDefn("label", ogr.OFTString)
        label_defn.SetAlternativeName("Display label")
        label_defn.SetComment("Site label")
        typed_layer.CreateField(label_defn)
        typed_feature = ogr.Feature(typed_layer.GetLayerDefn())
        typed_feature.SetField("survey_date", "2024-02-29")
        typed_feature.SetField("survey_time", "2024-02-29T13:45:00")
        typed_feature.SetFieldBinaryFromHexString("photo", "00FF10")
        typed_feature.SetField("label", "typed")
        typed_point = ogr.Geometry(ogr.wkbPoint)
        typed_point.AddPoint_2D(836694.05, 819069.8)
        typed_feature.SetGeometry(typed_point)
        typed_layer.CreateFeature(typed_feature)
        typed_feature = None
        typed_layer = None
        typed_dataset = None
        converted_dir = os.path.join(file_dir, "out")
        os.makedirs(converted_dir, exist_ok=True)
        converted_text, converted_kind = convert_files(
            source_gpkg, converted_dir, ("gpkg",), True, True, False
        )
        converted_path = os.path.join(converted_dir, "survey.gpkg")
        converted_site = QgsVectorLayer(converted_path + "|layername=site", "site", "ogr")
        converted_block = QgsVectorLayer(converted_path + "|layername=block", "block", "ogr")
        converted_line = QgsVectorLayer(converted_path + "|layername=road", "road", "ogr")
        converted_notes = QgsVectorLayer(converted_path + "|layername=notes", "notes", "ogr")
        converted_named = QgsVectorLayer(converted_path + "|layername=named", "named", "ogr")
        if converted_kind == "error" or not converted_site.isValid() or converted_site.featureCount() != 1:
            failures.append(f"file conversion did not write the point: {converted_text}")
        else:
            row = next(converted_site.getFeatures())
            if abs(row["Easting"] - 836694.05) > 0.05 or abs(row["Northing"] - 819069.8) > 0.05:
                failures.append(f"point grid columns are {row['Easting']}, {row['Northing']}")
            if abs(row["Latitude"] - 22.31060243) > 0.0001 or abs(row["Longitude"] - 114.18100933) > 0.0001:
                failures.append(f"point geographic columns are {row['Latitude']}, {row['Longitude']}")
            point_names = [field.name().casefold() for field in converted_site.fields()]
            if "length" in point_names or "area" in point_names:
                failures.append("point layer received a length or area")
        if not converted_notes.isValid() or converted_notes.featureCount() != 1:
            failures.append(f"table without geometry was not copied: {converted_text}")
        if not converted_block.isValid() or converted_block.featureCount() != 1:
            failures.append("file conversion did not write the polygon")
        else:
            center = next(converted_block.getFeatures())
            if abs(center["Easting"] - 836050) > 0.05 or abs(center["Northing"] - 819050) > 0.05:
                failures.append(f"polygon centroid is {center['Easting']}, {center['Northing']}")
            column_names = [
                field.name()
                for field in converted_block.fields()
                if field.name().casefold() in ("northing", "easting", "latitude", "longitude", "length", "area")
            ]
            if column_names != ["Northing", "Easting", "Latitude", "Longitude", "Length", "Area"]:
                failures.append(f"polygon columns are {column_names}")
            if abs(center["Length"] - 400) > 1 or abs(center["Area"] - 10000) > 1:
                failures.append(f"polygon measures are {center['Length']}, {center['Area']}")
        if converted_line.isValid() and converted_line.featureCount() == 1:
            road = next(converted_line.getFeatures())
            if abs(road["Length"] - 111.80339887) > 0.05:
                failures.append(f"line length is {road['Length']}")
            if any(field.name().casefold() == "area" for field in converted_line.fields()):
                failures.append("line layer received an area")
        if not converted_named.isValid() or converted_named.featureCount() != 1:
            failures.append("named coordinate columns were not written")
        else:
            named_fields = [field.name() for field in converted_named.fields()]
            if any(name.casefold().endswith("_1") for name in named_fields):
                failures.append(f"existing columns were duplicated: {named_fields}")
            named_row = next(converted_named.getFeatures())
            northing_name = next(name for name in named_fields if name.casefold() == "northing")
            if abs(named_row[northing_name] - 819069.8) > 0.05:
                failures.append(f"existing northing is {named_row[northing_name]}")
        skip_dir = os.path.join(file_dir, "skip")
        os.makedirs(skip_dir, exist_ok=True)
        skip_text, _skip_kind = convert_files(source_gpkg, skip_dir, ("gpkg",), False, False, True)
        skip_notes = QgsVectorLayer(os.path.join(skip_dir, "survey.gpkg") + "|layername=notes", "notes", "ogr")
        if "notes" not in skip_text or (skip_notes.isValid() and skip_notes.featureCount() > 0):
            failures.append(f"table without geometry was not skipped: {skip_text}")
        same_dir = os.path.join(file_dir, "same")
        os.makedirs(same_dir, exist_ok=True)
        same_source = os.path.join(same_dir, "survey.gpkg")
        shutil.copy(source_gpkg, same_source)
        same_text, same_kind = convert_files(same_source, same_dir, ("gpkg",), False)
        same_site = QgsVectorLayer(same_source + "|layername=site", "site", "ogr")
        if same_kind == "error" or not same_site.isValid() or same_site.featureCount() != 1:
            failures.append(f"same-folder geopackage was not written: {same_text}")
        typed_copy = QgsVectorLayer(same_source + "|layername=typed", "typed", "ogr")
        if not typed_copy.isValid() or typed_copy.featureCount() != 1:
            failures.append("in-place conversion lost the typed layer")
        else:
            typed_fields = {field.name(): field.type() for field in typed_copy.fields()}
            if typed_fields.get("survey_date") != QVariant.Date:
                failures.append(f"date field became {typed_fields.get('survey_date')}")
            if typed_fields.get("survey_time") != QVariant.DateTime:
                failures.append(f"datetime field became {typed_fields.get('survey_time')}")
            if typed_fields.get("photo") != QVariant.ByteArray:
                failures.append(f"binary field became {typed_fields.get('photo')}")
            label_field = next(
                (field for field in typed_copy.fields() if field.name() == "label"), None
            )
            if (
                label_field is None
                or label_field.alias() != "Display label"
                or label_field.comment() != "Site label"
            ):
                failures.append("field alias or comment was not preserved")
        typed_copy = None
        skip_notes = None
        same_site = None
        gdb_text, gdb_kind = convert_files(source_gpkg, converted_dir, ("fgdb",), False)
        gdb_path = os.path.join(converted_dir, "survey.gdb")
        if gdb_kind == "error":
            failures.append(f"file geodatabase was not created: {gdb_text}")
        elif not os.path.isdir(gdb_path):
            failures.append("file geodatabase folder is missing")

        rollback_dir = os.path.join(file_dir, "rollback")
        os.makedirs(rollback_dir, exist_ok=True)
        rollback_target = os.path.join(rollback_dir, "existing.gdb")
        os.makedirs(rollback_target)
        with open(os.path.join(rollback_target, "marker.txt"), "w", encoding="utf-8") as handle:
            handle.write("old")

        def write_rollback(temp):
            os.makedirs(temp)
            with open(os.path.join(temp, "marker.txt"), "w", encoding="utf-8") as handle:
                handle.write("new")
            return True

        real_rename = fileconvert_module.os.rename

        def fail_second(source, destination):
            if ".building-" in os.path.basename(source) and destination == rollback_target:
                raise OSError("simulated publish failure")
            return real_rename(source, destination)

        fileconvert_module.os.rename = fail_second
        try:
            rolled_back, rollback_message = _publish_directory(rollback_target, write_rollback)
        finally:
            fileconvert_module.os.rename = real_rename
        marker = os.path.join(rollback_target, "marker.txt")
        if rolled_back:
            failures.append(f"failed publish reported success: {rollback_message}")
        elif not os.path.exists(marker):
            failures.append("failed publish removed the previous file geodatabase")
        else:
            with open(marker, encoding="utf-8") as handle:
                if handle.read() != "old":
                    failures.append("failed publish did not restore the previous file geodatabase")

        generic_dir = os.path.join(file_dir, "generic")
        os.makedirs(generic_dir, exist_ok=True)
        generic_path = os.path.join(generic_dir, "roads.gpkg")
        if hasattr(ogr, "DontUseExceptions"):
            ogr.DontUseExceptions()
        generic_driver = ogr.GetDriverByName("GPKG")
        generic_dataset = generic_driver.CreateDataSource(generic_path)
        generic_srs = osr.SpatialReference()
        generic_srs.ImportFromEPSG(2326)
        generic_layer = generic_dataset.CreateLayer("RoadCentreLine", generic_srs, ogr.wkbUnknown)
        generic_layer.CreateField(ogr.FieldDefn("name", ogr.OFTString))
        generic_feature = ogr.Feature(generic_layer.GetLayerDefn())
        generic_feature.SetField("name", "centre")
        generic_line = ogr.Geometry(ogr.wkbLineString)
        generic_line.AddPoint(836000, 819000)
        generic_line.AddPoint(836100, 819050)
        generic_feature.SetGeometry(generic_line)
        generic_layer.CreateFeature(generic_feature)
        generic_feature = None
        generic_layer = None
        generic_dataset = None
        generic_check = ogr.Open(generic_path, 0)
        generic_type = generic_check.GetLayer(0).GetGeomType() if generic_check is not None else None
        generic_check = None
        if generic_type != ogr.wkbUnknown:
            failures.append(f"sample GeoPackage geometry type is {generic_type}")
        held = QgsVectorLayer(generic_path + "|layername=RoadCentreLine", "RoadCentreLine", "ogr")
        QgsProject.instance().addMapLayer(held, False)
        held_text, held_kind = convert_files(generic_path, generic_dir, ("gpkg",), False)
        if held_kind != "error" or held_text != (
            "Close RoadCentreLine in roads.gpkg in QGIS, or choose another folder."
        ):
            failures.append(f"open layer message is {held_text}")
        QgsProject.instance().removeMapLayer(held.id())
        held = None
        gc.collect()
        generic_text, generic_kind = convert_files(generic_path, generic_dir, ("gpkg",), False)
        generic_out = QgsVectorLayer(generic_path + "|layername=RoadCentreLine", "roads", "ogr")
        if generic_kind == "error" or not generic_out.isValid() or generic_out.featureCount() != 1:
            failures.append(f"generic geometry was not written: {generic_text}")
        generic_out = None
        gc.collect()

        file_dialog = FileDialog()
        svg_dialog = SvgDialog(_Iface(), None)
        if file_dialog._status.height() < 16 or svg_dialog._status.height() < 16:
            failures.append("status strip has no reserved height")
        if file_dialog._status.text() or svg_dialog._status.text():
            failures.append("status strip starts with a message")
        file_dialog._running = True
        file_dialog.shutdown()
        file_closed = file_dialog.close()
        if (
            not file_dialog._stop
            or not file_dialog._closing
            or not file_closed
            or file_dialog.isVisible()
            or not file_dialog._closed
            or _delete_on_close(file_dialog)
        ):
            failures.append("FileDialog shutdown did not accept the unload close")
        file_dialog._running = True
        file_dialog._unload = False
        file_dialog._closed = False
        file_dialog._stop = False
        file_dialog._closing = False
        if file_dialog.close():
            failures.append("a running file dialog accepted a user close")
        file_dialog._running = False
        file_dialog._closing = False
        file_dialog._stop = False
        svg_dialog._running = True
        svg_dialog.shutdown()
        svg_closed = svg_dialog.close()
        if (
            not svg_dialog._stop
            or not svg_dialog._closing
            or not svg_closed
            or svg_dialog.isVisible()
            or not svg_dialog._closed
            or _delete_on_close(svg_dialog)
        ):
            failures.append("SvgDialog shutdown did not accept the unload close")
        svg_dialog._running = False
        svg_dialog._closing = False
        svg_dialog._stop = False
        if file_dialog._convert.isEnabled():
            failures.append("Convert starts enabled")
        file_dialog._source.setText(source_gpkg)
        file_dialog._destination.setText(converted_dir)
        if file_dialog._convert.isEnabled():
            failures.append("Convert enabled before an output is chosen")
        file_dialog._gpkg.setChecked(True)
        if not file_dialog._convert.isEnabled():
            failures.append("Convert stayed off")
        file_dialog._show_status("Processing...", "ok")
        if "#d9f2df" not in file_dialog._status.styleSheet() or file_dialog._status.wordWrap():
            failures.append("Processing status is not a green single line")
        file_dialog._show_status("Skipped " + ("RoadCentreline " * 30), "error")
        if file_dialog._status.width() <= 200:
            failures.append("long status does not extend for scrolling")
        fields = database_fields(source_gpkg)
        if "url" not in [name.casefold() for name in fields.get("notes", [])]:
            failures.append(f"GeoPackage did not supply a URL column: {fields}")
        class _StatusBar:
            def __init__(self):
                self.widgets = []

            def addPermanentWidget(self, widget):
                self.widgets.append(widget)

            def removeWidget(self, widget):
                if widget in self.widgets:
                    self.widgets.remove(widget)

        class _Window:
            def __init__(self):
                self._bar = _StatusBar()

            def statusBar(self):
                return self._bar

        class _DownloadIface:
            def __init__(self):
                self.window = _Window()

            def mainWindow(self):
                return self.window

        download_iface = _DownloadIface()
        download_dialog = DownloadDialog(download_iface)
        download_dialog._open_source(source_gpkg)
        notes_index = download_dialog._layer.findText("notes")
        if notes_index >= 0:
            download_dialog._layer.setCurrentIndex(notes_index)
        if download_dialog._column.findText("url") < 0:
            failures.append("download dialog did not list the URL column")
        if download_dialog._download.isEnabled() or download_dialog._status.height() < 16:
            failures.append("Download starts enabled or has no status strip")
        if download_dialog._unzip.text() != "Unzip" or download_dialog._unzip.isEnabled():
            failures.append("Unzip is missing or starts enabled")
        if download_dialog._cancel.isEnabled():
            failures.append("Cancel starts enabled")
        download_dialog._folder.setText(converted_dir)
        if not download_dialog._download.isEnabled():
            failures.append("Download stayed off")
        if not download_dialog._unzip.isEnabled():
            failures.append("Unzip stayed off")
        download_dialog._show_status("Downloading 3 of 40: name.tif", "ok")
        if "#d9f2df" not in download_dialog._status.styleSheet():
            failures.append("download progress is not green")
        download_dialog._minimize()
        if download_dialog._stop[0]:
            failures.append("Minimize stopped the download")
        labels = download_iface.window.statusBar().widgets
        if not labels or download_dialog._bar_label is None or download_dialog._bar_label.text() != "Downloading 3 of 40: name.tif":
            failures.append("Minimize did not keep the download status")
        else:
            buttons = labels[0].findChildren(QPushButton)
            show = next((button for button in buttons if button.text() == "Show"), None)
            if show is None:
                failures.append("Minimize did not add Show")
            else:
                download_dialog.show = lambda: None
                download_dialog.raise_ = lambda: None
                download_dialog.activateWindow = lambda: None
                show.click()
                if download_iface.window.statusBar().widgets:
                    failures.append("Show left the status label in place")

        import zipfile

        unzip_folder = os.path.join(file_dir, "unzip-ui")
        os.makedirs(unzip_folder, exist_ok=True)
        with zipfile.ZipFile(os.path.join(unzip_folder, "sheet.zip"), "w") as archive:
            archive.writestr("sheet/sheet.tif", b"map")
            archive.writestr("sheet/notes.txt", b"notes")
        download_dialog._folder.setText(unzip_folder)
        download_dialog._start_unzip()
        if download_dialog._running_kind != "unzip" or not download_dialog._running:
            failures.append("Unzip did not start a worker")
        if download_dialog._cancel.isEnabled():
            failures.append("Cancel is enabled while unzipping")
        deadline = time.monotonic() + 10
        while download_dialog._running and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.01)
        application.processEvents()
        if download_dialog._running:
            failures.append("Unzip did not finish")
        expected_unzip = os.path.join(unzip_folder, "sheet.tif")
        unzip_data = b""
        if os.path.isfile(expected_unzip):
            with open(expected_unzip, "rb") as handle:
                unzip_data = handle.read()
        if unzip_data != b"map":
            failures.append("Unzip did not write the extracted file")
        if "Unzipped 1 zip file." not in download_dialog._status.text():
            failures.append(f"Unzip status is {download_dialog._status.text()!r}")
        if any(name.startswith(".hkmap-unzip-") for name in os.listdir(unzip_folder)):
            failures.append("Unzip left its staging folder behind")
        download_dialog._running = True
        download_dialog._running_kind = "unzip"
        download_dialog._set_busy(True)
        download_dialog.reject()
        if not download_dialog._running or "must finish" not in download_dialog._status.text():
            failures.append("Close was not refused while unzipping")
        download_dialog._running = False
        download_dialog._running_kind = None
        download_dialog._set_busy(False)
        download_dialog.shutdown()

        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkProxy

        from hkmapservices import downloader as downloader_module
        from hkmapservices.compat import enum_member as _enum

        class _RangeHandler(BaseHTTPRequestHandler):
            body = b"ABCDEF"
            mode = "bad"

            def log_message(self, *_args):
                return None

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(self.body)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Disposition", 'attachment; filename="resume.bin"')
                self.end_headers()

            def do_GET(self):
                requested = self.headers.get("Range")
                if requested and self.mode == "good":
                    start = int(requested.split("=", 1)[1].split("-")[0])
                    payload = self.body[start:]
                    self.send_response(206)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header(
                        "Content-Range", f"bytes {start}-{len(self.body) - 1}/{len(self.body)}"
                    )
                    self.end_headers()
                    self.wfile.write(payload)
                elif requested:
                    self.send_response(206)
                    self.send_header("Content-Length", "3")
                    self.send_header("Content-Range", "bytes 0-2/6")
                    self.end_headers()
                    self.wfile.write(self.body[:3])
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(self.body)))
                    self.end_headers()
                    self.wfile.write(self.body)

        class _Sink:
            def emit(self, *_args):
                return None

        range_server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        range_server.daemon_threads = True
        threading.Thread(target=range_server.serve_forever, daemon=True).start()
        no_proxy = _enum(QNetworkProxy, "ProxyType", "NoProxy")
        resume_url = f"http://127.0.0.1:{range_server.server_port}/resume.bin"
        for mode, expected in (("bad", b"ABCDEF"), ("good", b"AAADEF")):
            _RangeHandler.mode = mode
            resume_folder = os.path.join(file_dir, "resume-" + mode)
            os.makedirs(resume_folder, exist_ok=True)
            partial = os.path.join(resume_folder, "resume.bin.part")
            with open(partial, "wb") as handle:
                handle.write(b"AAA")
            downloader_module.write_sidecar(resume_folder, "resume.bin", resume_url, "", 6)
            resume_manager = QNetworkAccessManager()
            resume_manager.setProxy(QNetworkProxy(no_proxy))
            outcome = downloader_module._save_url(
                resume_manager, resume_url, resume_folder, [False], _Sink(), 1, 1, set()
            )
            final = os.path.join(resume_folder, "resume.bin")
            data = b""
            if os.path.exists(final):
                with open(final, "rb") as handle:
                    data = handle.read()
            if outcome != "saved" or data != expected:
                failures.append(f"resume {mode} gave {outcome} with {data!r}")
            if os.path.exists(partial):
                failures.append(f"resume {mode} left its .part file behind")

        occupied_folder = os.path.join(file_dir, "resume-occupied")
        os.makedirs(occupied_folder, exist_ok=True)
        with open(os.path.join(occupied_folder, "Resume.Bin"), "wb") as handle:
            handle.write(b"XYZ")
        occupied_manager = QNetworkAccessManager()
        occupied_manager.setProxy(QNetworkProxy(no_proxy))
        _RangeHandler.mode = "good"
        occupied_outcome = downloader_module._save_url(
            occupied_manager, resume_url, occupied_folder, [False], _Sink(), 1, 1, set()
        )
        occupied_final = os.path.join(occupied_folder, "resume_2.bin")
        occupied_data = b""
        if os.path.exists(occupied_final):
            with open(occupied_final, "rb") as handle:
                occupied_data = handle.read()
        with open(os.path.join(occupied_folder, "Resume.Bin"), "rb") as handle:
            untouched = handle.read()
        if occupied_outcome != "saved" or occupied_data != b"ABCDEF":
            failures.append(f"an existing file was not worked around: {occupied_outcome} {occupied_data!r}")
        if untouched != b"XYZ":
            failures.append("an unrelated file in the folder was modified")

        skip_folder = os.path.join(file_dir, "resume-complete")
        os.makedirs(skip_folder, exist_ok=True)
        with open(os.path.join(skip_folder, "resume.bin"), "wb") as handle:
            handle.write(b"ABCDEF")
        skip_manager = QNetworkAccessManager()
        skip_manager.setProxy(QNetworkProxy(no_proxy))
        skip_outcome = downloader_module._save_url(
            skip_manager, resume_url, skip_folder, [False], _Sink(), 1, 1, set()
        )
        if skip_outcome != "skipped" or os.path.exists(os.path.join(skip_folder, "resume.bin.part")):
            failures.append(f"a complete existing file was not skipped: {skip_outcome}")
        range_server.shutdown()
        range_server.server_close()

        class _SilentHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return None

            def do_HEAD(self):
                time.sleep(3)

            def do_GET(self):
                time.sleep(3)

        silent_server = ThreadingHTTPServer(("127.0.0.1", 0), _SilentHandler)
        silent_server.daemon_threads = True
        threading.Thread(target=silent_server.serve_forever, daemon=True).start()
        idle_backup = downloader_module._IDLE_TIMEOUT_SECONDS
        downloader_module._IDLE_TIMEOUT_SECONDS = 0.5
        try:
            silent_manager = QNetworkAccessManager()
            silent_manager.setProxy(QNetworkProxy(no_proxy))
            timed = downloader_module._exchange(
                silent_manager,
                f"http://127.0.0.1:{silent_server.server_port}/stall.bin",
                None,
                None,
                [False],
                headers_only=True,
            )
            if not timed["timed_out"]:
                failures.append(f"stalled download did not time out: {timed}")
            started_at = time.monotonic()
            stalled = downloader_module._save_url(
                silent_manager,
                f"http://127.0.0.1:{silent_server.server_port}/stall.bin",
                file_dir,
                [False],
                _Sink(),
                1,
                1,
                set(),
            )
            elapsed = time.monotonic() - started_at
            if stalled != "failed":
                failures.append(f"stalled download reported {stalled}")
            if elapsed > 6:
                failures.append(f"stalled download took {elapsed:.1f} seconds to give up")
        finally:
            downloader_module._IDLE_TIMEOUT_SECONDS = idle_backup
            silent_server.shutdown()
            silent_server.server_close()

        tls_folder = os.path.join(file_dir, "tls-failure")
        os.makedirs(tls_folder, exist_ok=True)
        original_exchange = downloader_module._exchange
        tls_calls = []

        def tls_exchange(*_args, **_kwargs):
            tls_calls.append(1)
            return {
                "error": True,
                "tls_error": True,
                "ssl_errors": ["No certificates could be verified"],
            }

        try:
            downloader_module._exchange = tls_exchange
            tls_manager = QNetworkAccessManager()
            tls_manager.setProxy(QNetworkProxy(no_proxy))
            tls_url = "https://download.example.gov.hk/maps/sheet.zip"
            tls_failures = []
            tls_outcome = downloader_module._save_url(
                tls_manager,
                tls_url,
                tls_folder,
                [False],
                _Sink(),
                1,
                10,
                set(),
                failures=tls_failures,
            )
        finally:
            downloader_module._exchange = original_exchange
        if tls_outcome != "tls_failed":
            failures.append(f"TLS failure reported {tls_outcome}")
        if len(tls_calls) != 1:
            failures.append(f"TLS failure made {len(tls_calls)} requests")
        if os.listdir(tls_folder):
            failures.append("TLS failure left part files behind")
        if not tls_failures or tls_failures[-1][0] != tls_url:
            failures.append(f"TLS failure recorded {tls_failures!r}")

        original_save = downloader_module._save_url
        worker_calls = []
        worker_messages = []

        def stop_save(_manager, url, *_args, **kwargs):
            worker_calls.append(url)
            recorded = kwargs.get("failures")
            if recorded is not None:
                recorded.append((url, ["No certificates could be verified"]))
            return "tls_failed"

        try:
            downloader_module._save_url = stop_save
            worker = downloader_module.DownloadWorker(
                [
                    "https://download.example.gov.hk/maps/one.zip",
                    "https://download.example.gov.hk/maps/two.zip",
                ],
                tls_folder,
                [QNetworkProxy(no_proxy), QNetworkProxy(no_proxy)],
                [False],
            )
            worker.finished.connect(lambda text, kind: worker_messages.append((text, kind)))
            worker.run()
        finally:
            downloader_module._save_url = original_save
        if len(worker_calls) != 1:
            failures.append(f"TLS failure continued to {len(worker_calls)} URLs")
        if not worker_messages:
            failures.append("TLS failure did not report a message")
        else:
            text, kind = worker_messages[-1]
            if (
                kind != "error"
                or "download.example.gov.hk" not in text
                or "1 file was not attempted" not in text
            ):
                failures.append(f"TLS failure message is {text!r} ({kind})")

        convert_dialog = ConvertDialog()
        convert_dialog._show_status("Processing...", "ok")
        if "#d9f2df" not in convert_dialog._status.styleSheet() or convert_dialog._status.wordWrap():
            failures.append("Convert Coordinates processing is not a green single line")
        convert_dialog.close()
        convert_dialog = None
        menu = HkMapServices.__new__(HkMapServices)
        menu.basemap_action = "Basemap"
        menu.dataset_action = "Datasets"
        menu.location_action = "Location Search"
        menu.download_action = "Download Files"
        menu.swipe_action = "Swipe and Split"
        menu.convert_action = "Convert Coordinates"
        menu.file_action = "Convert FGDB / GPKG Files"
        menu.svg_action = "Export SVG"
        menu.cursor_action = "Cursor Coordinates"
        if menu._ordered_actions()[2:4] != ("Location Search", "Download Files"):
            failures.append(f"menu order is {menu._ordered_actions()}")

        class _Job:
            def __init__(self):
                self.done = False

            def start(self):
                return None

            def isActive(self):
                return not self.done

            def cancel(self):
                self.done = True

            def waitForFinished(self):
                self.done = True

        render_job = _Job()
        pumped = []

        def _pump():
            pumped.append(render_job.isActive())
            render_job.done = True
            return True

        if not _wait_for_render(render_job, _pump) or pumped != [True]:
            failures.append(f"basemap pump waited for the job to finish: {pumped}")
        file_dialog.close()
        svg_dialog.close()
        download_dialog.close()
        file_dialog = None
        svg_dialog = None
        download_dialog = None

        shown = QgsVectorLayer("Polygon?crs=EPSG:2326", "Shown parcels", "memory")
        shown_feature = QgsFeature()
        shown_feature.setGeometry(
            QgsGeometry.fromPolygonXY(
                [[
                    QgsPointXY(1, 1),
                    QgsPointXY(9, 1),
                    QgsPointXY(9, 9),
                    QgsPointXY(1, 9),
                    QgsPointXY(1, 1),
                ]]
            )
        )
        outside_feature = QgsFeature()
        outside_feature.setGeometry(
            QgsGeometry.fromPolygonXY(
                [[
                    QgsPointXY(500, 500),
                    QgsPointXY(510, 500),
                    QgsPointXY(510, 510),
                    QgsPointXY(500, 510),
                    QgsPointXY(500, 500),
                ]]
            )
        )
        shown.dataProvider().addFeatures([shown_feature, outside_feature])
        hidden = QgsVectorLayer("Polygon?crs=EPSG:2326", "Hidden parcels", "memory")
        hidden_feature = QgsFeature()
        hidden_feature.setGeometry(
            QgsGeometry.fromPolygonXY(
                [[
                    QgsPointXY(2, 2),
                    QgsPointXY(8, 2),
                    QgsPointXY(8, 8),
                    QgsPointXY(2, 8),
                    QgsPointXY(2, 2),
                ]]
            )
        )
        hidden.dataProvider().addFeatures([hidden_feature])

        class _View:
            def __init__(self, width, height, layers):
                self._width = width
                self._height = height
                self._extent = QgsRectangle(0, 0, 20, 20)
                self._layers = layers
                self._settings = QgsMapSettings()
                self._settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:2326"))
                self._settings.setExtent(self._extent)
                self._settings.setOutputSize(QSize(width, height))
                self._settings.setLayers(layers)

            def width(self):
                return self._width

            def height(self):
                return self._height

            def extent(self):
                return self._extent

            def layers(self):
                return self._layers

            def mapSettings(self):
                return self._settings

            def mapUnits(self):
                return QgsUnitTypes.DistanceMeters

        raster_path = os.path.join(file_dir, "basemap.tif")
        raster_driver = gdal.GetDriverByName("GTiff")
        raster_dataset = raster_driver.Create(raster_path, 4, 4, 1, gdal.GDT_Byte)
        raster_dataset.SetGeoTransform((0, 5, 0, 20, 0, -5))
        raster_srs = osr.SpatialReference()
        raster_srs.ImportFromEPSG(2326)
        raster_dataset.SetProjection(raster_srs.ExportToWkt())
        raster_dataset.GetRasterBand(1).Fill(120)
        raster_dataset = None
        raster_layer = QgsRasterLayer(raster_path, "Raster basemap", "gdal")
        if not raster_layer.isValid():
            failures.append("sample raster basemap is not valid")
        else:
            raster_svg = os.path.join(file_dir, "raster.svg")
            raster_text, raster_kind = export_map_svg(
                _View(800, 600, [raster_layer, shown]), raster_svg, False, False
            )
            raster_body = ""
            if os.path.exists(raster_svg):
                with open(raster_svg, encoding="utf-8") as handle:
                    raster_body = handle.read()
            if raster_kind == "error" or "<image " not in raster_body:
                failures.append(f"raster basemap was not embedded: {raster_text}")
            if raster_body.count("data:image/png;base64,") != 1:
                failures.append("raster basemap data was embedded more than once")
            if 'inkscape:label="Shown parcels"' not in raster_body or 'inkscape:label="Raster basemap"' not in raster_body:
                failures.append("raster export is missing a layer group")
            elif raster_body.index('inkscape:label="Shown parcels"') > raster_body.index('inkscape:label="Raster basemap"'):
                failures.append("raster and vector layer order was reversed")
            labels_layer = QgsRasterLayer(raster_path, "Raster labels", "gdal")
            layered_svg = os.path.join(file_dir, "layered.svg")
            layered_text, layered_kind = export_map_svg(
                _View(800, 600, [labels_layer, raster_layer, shown]), layered_svg, False, False
            )
            layered_body = ""
            if os.path.exists(layered_svg):
                with open(layered_svg, encoding="utf-8") as handle:
                    layered_body = handle.read()
            if layered_kind == "error" or layered_body.count("data:image/png;base64,") != 1:
                failures.append(f"consecutive raster layers were not combined: {layered_text}")
            if "Raster labels" in layered_body:
                failures.append("a combined raster run was split into separate pictures")
            labels_layer = None
        raster_layer = None
        annotation = QgsAnnotationLayer(
            "Annotations",
            QgsAnnotationLayer.LayerOptions(QgsProject.instance().transformContext()),
        )
        annotation_svg = os.path.join(file_dir, "annotation.svg")
        annotation_text, annotation_kind = export_map_svg(
            _View(800, 600, [annotation, shown]), annotation_svg, False, False
        )
        if annotation_kind != "warn" or "Skipped 1 layer" not in annotation_text:
            failures.append(f"unsupported layer was not skipped with a warning: {annotation_text}")
        rotated_view = _View(800, 600, [shown])
        rotated_view._settings.setRotation(30)
        rotated_svg = os.path.join(file_dir, "rotated.svg")
        rotated_text, rotated_kind = export_map_svg(rotated_view, rotated_svg)
        rotated_body = ""
        if os.path.exists(rotated_svg):
            with open(rotated_svg, encoding="utf-8") as handle:
                rotated_body = handle.read()
        if rotated_kind == "error" or 'transform="rotate(30 ' not in rotated_body:
            failures.append(f"rotated map did not rotate its north arrow: {rotated_text}")
        wide_svg = os.path.join(file_dir, "wide.svg")
        wide_text, wide_kind = export_map_svg(_View(800, 600, [shown]), wide_svg)
        wide_body = ""
        if os.path.exists(wide_svg):
            with open(wide_svg, encoding="utf-8") as handle:
                wide_body = handle.read()
        if wide_kind == "error" or "landscape" not in wide_text or 'width="420mm"' not in wide_body:
            failures.append(f"wide map did not export A3 landscape: {wide_text}")
        if 'inkscape:label="Shown parcels"' not in wide_body or "Hidden parcels" in wide_body:
            failures.append("SVG did not keep only the visible layer")
        star_layer = QgsVectorLayer("Point?crs=EPSG:2326", "Stars", "memory")
        star_layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsMarkerSymbol.createSimple(
                    {
                        "name": "star",
                        "color": "20,20,20,255",
                        "outline_color": "0,0,0,255",
                        "outline_width": "0.2",
                        "size": "3.2",
                    }
                )
            )
        )
        star_feature = QgsFeature(star_layer.fields())
        star_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(10, 10)))
        star_layer.dataProvider().addFeature(star_feature)
        star_svg = os.path.join(file_dir, "star.svg")
        star_text, star_kind = export_map_svg(_View(800, 600, [star_layer]), star_svg, False, False)
        star_body = ""
        if os.path.exists(star_svg):
            with open(star_svg, encoding="utf-8") as handle:
                star_body = handle.read()
        if star_kind == "error" or "<circle " in star_body or ' Z"' not in star_body:
            failures.append(f"star marker was drawn as a circle: {star_text}")
        star_layer = None
        hidden_svg = os.path.join(file_dir, "hidden.svg")
        hidden_text, hidden_kind = export_map_svg(_View(800, 600, [hidden]), hidden_svg, False, False)
        hidden_body = ""
        if os.path.exists(hidden_svg):
            with open(hidden_svg, encoding="utf-8") as handle:
                hidden_body = handle.read()
        if hidden_kind == "error" or 'inkscape:label="Hidden parcels"' not in hidden_body:
            failures.append(f"a hidden layer was not exportable when it is on the canvas: {hidden_text}")
        if 'inkscape:groupmode="layer"' not in wide_body or "Scale bar" not in wide_body:
            failures.append("SVG is missing a layer group or the scale bar")
        if "<image " in wide_body or "data:image/png;base64," in wide_body:
            failures.append("a vector-only export embedded a blank basemap picture")
        path_numbers = [
            float(token)
            for match in re.findall(r'<path d="([^"]*)"', wide_body)
            for token in re.findall(r"-?\d+(?:\.\d+)?", match)
        ]
        if not path_numbers or any(abs(number) > 430 for number in path_numbers):
            failures.append("SVG geometry is outside the map view")
        tall_svg = os.path.join(file_dir, "tall.svg")
        tall_text, tall_kind = export_map_svg(_View(600, 800, [shown]), tall_svg)
        tall_body = ""
        if os.path.exists(tall_svg):
            with open(tall_svg, encoding="utf-8") as handle:
                tall_body = handle.read()
        if tall_kind == "error" or "portrait" not in tall_text or 'height="420mm"' not in tall_body:
            failures.append(f"tall map did not export A3 portrait: {tall_text}")
        mercator = QgsCoordinateReferenceSystem("EPSG:3857")
        grid = QgsCoordinateReferenceSystem("EPSG:2326")
        mercator_extent = QgsCoordinateTransform(grid, mercator, QgsProject.instance()).transformBoundingBox(
            QgsRectangle(0, 0, 20, 20)
        )
        mercator_view = _View(800, 600, [shown])
        mercator_view._extent = mercator_extent
        mercator_view._settings.setDestinationCrs(mercator)
        mercator_view._settings.setExtent(mercator_extent)
        mercator_svg = os.path.join(file_dir, "mercator.svg")
        mercator_text, mercator_kind = export_map_svg(mercator_view, mercator_svg, False, False)
        mercator_body = ""
        if os.path.exists(mercator_svg):
            with open(mercator_svg, encoding="utf-8") as handle:
                mercator_body = handle.read()
        shown_markup = ""
        if 'inkscape:label="Shown parcels"' in mercator_body:
            shown_markup = mercator_body.split('inkscape:label="Shown parcels"', 1)[1].split("</g>", 1)[0]
        if mercator_kind == "error" or "<path " not in shown_markup:
            failures.append(f"HK1980 polygon was not drawn on a Web Mercator view: {mercator_text}")
        if 'inkscape:label="Scale bar"' in mercator_body or 'inkscape:label="North arrow"' in mercator_body:
            failures.append("unchecked scale bar or north arrow was exported")
        mercator_point = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem("EPSG:4326"), mercator, QgsProject.instance()
        ).transform(QgsPointXY(114.18, 22.31))
        mercator_y = mercator_point.y()
        mercator_line = QgsGeometry.fromPolylineXY(
            [QgsPointXY(0, mercator_y), QgsPointXY(1000, mercator_y)]
        )
        mercator_length, _unused = _metres(mercator_line, mercator, "line")
        if not 915 < mercator_length < 935:
            failures.append(f"Web Mercator line length is {mercator_length}, expected about 925 m")
        mercator_polygon = QgsGeometry.fromPolygonXY(
            [[
                QgsPointXY(0, mercator_y),
                QgsPointXY(1000, mercator_y),
                QgsPointXY(1000, mercator_y + 1000),
                QgsPointXY(0, mercator_y + 1000),
                QgsPointXY(0, mercator_y),
            ]]
        )
        _unused, mercator_area = _metres(mercator_polygon, mercator, "polygon")
        if not 840000 < mercator_area < 870000:
            failures.append(f"Web Mercator area is {mercator_area}, expected about 856000 m2")
        mercator_ground = _ground_metres(QgsRectangle(0, mercator_y, 1000, mercator_y), mercator)
        if not 915 < mercator_ground < 935:
            failures.append(f"Web Mercator scale width is {mercator_ground}, expected about 925 m")
        geographic_ground = _ground_metres(
            QgsRectangle(114.175, 22.31, 114.185, 22.31),
            QgsCoordinateReferenceSystem("EPSG:4326"),
        )
        if not 1000 < geographic_ground < 1060:
            failures.append(f"WGS84 scale width is {geographic_ground}, expected about 1030 m")
        if not any("1.1.0" in child.text() for child in about.findChildren(QLabel)):
            failures.append("about dialog version is not 1.1.0")
        converted_site = None
        converted_block = None
        converted_line = None
        converted_notes = None
        converted_named = None
        site = None
        line = None
        block = None
        notes = None
        named = None
        shown = None
        hidden = None
        gc.collect()

        xml_path = os.path.join(directory, "hk80_topographic.xml")
        if not os.path.exists(xml_path):
            with open(xml_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(hk80_raster_xml(raster_url("topographic", "HK80")))
        print(f"xml={xml_path}")
    except Exception:
        traceback.print_exc()
        failures.append("smoke test raised an exception")
    finally:
        from hkmapservices.layers import shutdown_background_services

        shutdown_background_services()
        application.exitQgis()

    if failures:
        print("FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
