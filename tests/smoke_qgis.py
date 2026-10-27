"""Headless checks for basemap and dataset layers.

Run with the QGIS Python launcher, for example:

    python-qgis-ltr.bat tests\\smoke_qgis.py
"""

from __future__ import annotations

import os
import sys
import tempfile
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
        QgsPointXY,
        QgsProject,
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
            QStackedWidget,
            QStyleOptionViewItem,
            QWidget,
        )
        from qgis.core import QgsVectorLayer
        from qgis.gui import QgsMapCanvas

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
        if convert_dialog.layout().indexOf(convert_dialog._status) < convert_dialog.layout().indexOf(convert_dialog._table):
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
        swipe_view = SwipeView(_Iface())
        swipe_view.set_position(30)
        if swipe_view.settings.position != 30 or swipe_view.settings.direction != "vertical":
            failures.append("swipe position was not kept")

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

        xml_path = os.path.join(directory, "hk80_topographic.xml")
        if not os.path.exists(xml_path):
            with open(xml_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(hk80_raster_xml(raster_url("topographic", "HK80")))
        print(f"xml={xml_path}")
    except Exception:
        traceback.print_exc()
        failures.append("smoke test raised an exception")
    finally:
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
