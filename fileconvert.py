"""Copy point, line, and polygon layers between GeoPackage and File Geodatabase."""

from __future__ import annotations

import gc
import os
import shutil
import uuid
from contextlib import contextmanager

from osgeo import ogr
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCsException,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsProviderRegistry,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QByteArray, QDate, QDateTime, Qt, QTime, QVariant

from .compat import enum_member
from .config import HK1980, WGS84
from .coords import transform_xy
from .filemath import (
    discover_sources,
    field_targets,
    geometry_kind,
    is_geometry_table,
    measure_names,
    normalize_outputs,
    round_places,
    source_stem,
)


_DEFAULT_PLACES = {"grid": 3, "geo": 6, "length": 2, "area": 2}


class _Stopped(Exception):
    """The dialog asked the conversion to finish the current step and stop."""


@contextmanager
def _quiet_ogr():
    """Turn off OGR exceptions for one read, then put the global setting back."""
    previous = None
    if hasattr(ogr, "GetUseExceptions"):
        try:
            previous = bool(ogr.GetUseExceptions())
        except Exception:
            previous = None
    if previous and hasattr(ogr, "DontUseExceptions"):
        ogr.DontUseExceptions()
    try:
        yield
    finally:
        if previous and hasattr(ogr, "UseExceptions"):
            ogr.UseExceptions()


def convert_files(
    source: str,
    destination: str,
    outputs,
    add_coordinates: bool = False,
    add_measures: bool = False,
    skip_tables: bool = False,
    places=None,
    progress=None,
) -> tuple:
    """Write the selected databases. Return the status text and its kind."""
    chosen = normalize_outputs(outputs)
    sources = discover_sources(source)
    if not sources:
        return "No File Geodatabase or GeoPackage was found.", "error"
    if not destination or not str(destination).strip():
        return "Choose a destination folder.", "error"
    folder = os.path.abspath(destination)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return "The destination folder could not be created.", "error"
    places = _places(places)

    written = []
    skipped = []
    missing = 0
    failed = []
    try:
        for path in sources:
            _check_progress(progress)
            stem = source_stem(path)
            targets = []
            for output in chosen:
                extension = ".gpkg" if output == "gpkg" else ".gdb"
                targets.append(os.path.join(folder, stem + extension))
            same = any(_same_path(path, target) for target in targets)
            replace_source = same and not _project_uses(path)
            close_text = _close_message(path)
            try:
                if replace_source:
                    layers, skipped_names = _read_ogr(path, skip_tables, progress)
                else:
                    layers, skipped_names = _open_layers(path, skip_tables)
            except _Stopped:
                raise
            except Exception as exc:
                failed.append(str(exc) or f"{source_stem(path)} could not be opened.")
                continue
            skipped.extend(skipped_names)
            if not layers:
                failed.append(f"{source_stem(path)} has no point, line, or polygon layers.")
                continue
            if same and not replace_source:
                _release(layers, False)
            for output_index, (output, target) in enumerate(zip(chosen, targets)):
                _check_progress(progress)
                if _same_path(path, target) and not replace_source:
                    failed.append(close_text)
                    continue
                report = [0] if output_index == 0 else None
                try:
                    ok, problem = _write_database(
                        layers,
                        target,
                        output,
                        progress,
                        add_coordinates,
                        add_measures,
                        places,
                        report,
                    )
                except _Stopped:
                    raise
                except Exception as exc:
                    ok, problem = False, str(exc)
                if report is not None:
                    missing += report[0]
                if ok:
                    written.append(os.path.basename(target))
                else:
                    failed.append(
                        problem
                        or (
                            "File Geodatabase could not be created."
                            if output == "fgdb"
                            else "GeoPackage could not be written."
                        )
                    )
    except _Stopped:
        if written:
            text, _kind = _status_text(written, skipped, missing, failed)
            return f"{text} Conversion stopped.", "warn"
        return "Conversion stopped.", "warn"
    return _status_text(written, skipped, missing, failed)


def _read_ogr(path: str, skip_tables: bool, progress) -> tuple:
    """Read a database with GDAL and close it before the caller replaces the file."""
    accepted = []
    skipped = []
    with _quiet_ogr():
        dataset = ogr.Open(path, 0)
        if dataset is None:
            raise ValueError(f"{os.path.basename(path)} could not be opened.")
        try:
            for index in range(dataset.GetLayerCount()):
                _check_progress(progress)
                ogr_layer = dataset.GetLayer(index)
                memory, label, keep, table = _memory_from_ogr(ogr_layer, progress)
                if table and skip_tables:
                    skipped.append(label)
                    continue
                if not keep or memory is None:
                    skipped.append(label)
                    continue
                accepted.append(memory)
        finally:
            dataset = None
    return accepted, skipped


def _memory_from_ogr(ogr_layer, progress) -> tuple:
    label = ogr_layer.GetName() or "layer"
    geom_type = ogr_layer.GetGeomType()
    if geom_type == ogr.wkbNone:
        type_name = "No Geometry"
    else:
        type_name = ogr.GeometryTypeToName(geom_type)
    kind = geometry_kind(type_name)
    if kind is None and geom_type != ogr.wkbNone:
        sampled = _sample_geometry_name(ogr_layer)
        if sampled:
            type_name = sampled
            kind = geometry_kind(type_name)
    table = kind is None and is_geometry_table(type_name)
    if kind is None and not table:
        return None, label, False, False
    token = "NoGeometry" if table else _memory_token(type_name, kind)
    authid = _layer_authid(ogr_layer.GetSpatialRef())
    uri = f"{token}?crs={authid}" if authid else token
    memory = QgsVectorLayer(uri, label, "memory")
    if not memory.isValid():
        return None, label, False, table
    if authid:
        memory.setCrs(QgsCoordinateReferenceSystem(authid))
    definition = ogr_layer.GetLayerDefn()
    qfields = []
    for index in range(definition.GetFieldCount()):
        field = definition.GetFieldDefn(index)
        qfields.append(_qgs_field(field))
    provider = memory.dataProvider()
    provider.addAttributes(qfields)
    memory.updateFields()
    features = []
    seen = 0
    ogr_layer.ResetReading()
    item = ogr_layer.GetNextFeature()
    while item is not None:
        seen += 1
        if seen % 200 == 0:
            _check_progress(progress)
        copied = QgsFeature(memory.fields())
        geometry = item.GetGeometryRef()
        if geometry is not None:
            copied.setGeometry(QgsGeometry.fromWkt(geometry.ExportToWkt()))
        for index, field in enumerate(qfields):
            if not item.IsFieldNull(index):
                copied[field.name()] = _ogr_value(item.GetField(index), field.type())
        features.append(copied)
        item = ogr_layer.GetNextFeature()
    if features:
        provider.addFeatures(features)
    memory.updateExtents()
    return memory, label, True, table


def _sample_geometry_name(ogr_layer) -> str:
    """Return the type of the first drawn feature when the layer type is generic."""
    ogr_layer.ResetReading()
    item = ogr_layer.GetNextFeature()
    checked = 0
    try:
        while item is not None and checked < 50:
            checked += 1
            geometry = item.GetGeometryRef()
            if geometry is not None and not geometry.IsEmpty():
                return ogr.GeometryTypeToName(geometry.GetGeometryType())
            item = ogr_layer.GetNextFeature()
    finally:
        ogr_layer.ResetReading()
    return ""


def _memory_token(type_name: str, kind: str) -> str:
    text = type_name.casefold().replace(" ", "")
    bases = {"point": "Point", "line": "LineString", "polygon": "Polygon"}
    if kind not in bases:
        raise ValueError(f"Unknown geometry {kind!r}.")
    base = bases[kind]
    if "multi" in text:
        base = "Multi" + base
    if "3d" in text or text.endswith("z"):
        base += "Z"
    return base


def _layer_authid(spatial) -> str:
    if spatial is None:
        return ""
    code = spatial.GetAuthorityCode(None)
    name = spatial.GetAuthorityName(None)
    if code and name:
        return f"{name}:{code}"
    return ""


def _ogr_variant(ogr_type, subtype=None):
    if ogr_type == ogr.OFTInteger:
        if subtype is not None and subtype == getattr(ogr, "OFTIntegerSubTypeBoolean", None):
            return QVariant.Bool
        return QVariant.Int
    if ogr_type == ogr.OFTInteger64:
        return QVariant.LongLong
    if ogr_type == ogr.OFTReal:
        return QVariant.Double
    if ogr_type == ogr.OFTDate:
        return QVariant.Date
    if ogr_type == ogr.OFTTime:
        return QVariant.Time
    if ogr_type == ogr.OFTDateTime:
        return QVariant.DateTime
    if ogr_type == ogr.OFTBinary:
        return QVariant.ByteArray
    if ogr_type in (ogr.OFTIntegerList, ogr.OFTRealList):
        return QVariant.List
    if ogr_type == ogr.OFTStringList:
        return QVariant.StringList
    return QVariant.String


def _qgs_field(field) -> QgsField:
    """Build a QGIS field that keeps the OGR type and field metadata."""
    variant = _ogr_variant(field.GetType(), _ogr_subtype(field))
    qfield = QgsField(field.GetName(), variant)
    width = field.GetWidth()
    precision = field.GetPrecision()
    if width > 0 and hasattr(qfield, "setLength"):
        qfield.setLength(width)
    if precision > 0 and hasattr(qfield, "setPrecision"):
        qfield.setPrecision(precision)
    alias = _ogr_text(field, "GetAlternativeName")
    if alias and hasattr(qfield, "setAlias"):
        qfield.setAlias(alias)
    comment = _ogr_text(field, "GetComment")
    if comment and hasattr(qfield, "setComment"):
        qfield.setComment(comment)
    return qfield


def _ogr_text(field, method: str) -> str:
    getter = getattr(field, method, None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "").strip()
    except (AttributeError, RuntimeError):
        return ""


def _ogr_subtype(field):
    try:
        return field.GetSubType()
    except (AttributeError, RuntimeError):
        return None


def _clone_field(field) -> QgsField:
    """Copy a QGIS field without losing its type, width, or precision."""
    try:
        return QgsField(field)
    except TypeError:
        return QgsField(field.name(), field.type())


def _ogr_value(value, variant):
    """Convert an OGR value to the Python type a QGIS field expects."""
    if value is None:
        return None
    text = str(value)
    if variant == QVariant.Date:
        parsed = QDate.fromString(text[:10], _iso_date())
        return parsed if parsed.isValid() else value
    if variant == QVariant.Time:
        parsed = QTime.fromString(text, _iso_date())
        return parsed if parsed.isValid() else value
    if variant == QVariant.DateTime:
        text = text.replace("Z", "+00:00")
        parsed = QDateTime.fromString(text, _iso_date())
        if not parsed.isValid():
            parsed = QDateTime.fromString(text, _iso_date_with_ms())
        return parsed if parsed.isValid() else value
    if variant == QVariant.ByteArray:
        try:
            return QByteArray(bytes(value))
        except (TypeError, ValueError):
            return value
    return value


def _iso_date():
    return getattr(Qt, "ISODate", getattr(Qt.DateFormat, "ISODate", None))


def _iso_date_with_ms():
    return getattr(Qt, "ISODateWithMs", getattr(Qt.DateFormat, "ISODateWithMs", None))


def _open_layers(path: str, skip_tables: bool) -> tuple:
    accepted = []
    skipped = []
    details = _sublayers(path)
    if not details:
        layer = QgsVectorLayer(path, os.path.basename(path), "ogr")
        if not layer.isValid():
            raise ValueError(f"{os.path.basename(path)} could not be opened.")
        _collect_layer(layer, "", accepted, skipped, skip_tables)
        return accepted, skipped
    for name, type_name in details:
        layer_name = name or "layer"
        layer = QgsVectorLayer(f"{path}|layername={layer_name}", layer_name, "ogr")
        _collect_layer(layer, type_name, accepted, skipped, skip_tables)
    return accepted, skipped


def _collect_layer(layer, type_name: str, accepted, skipped, skip_tables: bool) -> None:
    label = layer.name() or type_name or "layer"
    if not layer.isValid():
        skipped.append(label)
        return
    kind = _layer_kind(layer, type_name)
    if kind == "table":
        if skip_tables:
            skipped.append(label)
        else:
            accepted.append(layer)
        return
    if kind is None:
        skipped.append(label)
        return
    accepted.append(layer)


def _layer_kind(layer, type_name: str):
    display = QgsWkbTypes.displayString(layer.wkbType()) if layer.isValid() else type_name
    kind = geometry_kind(display) or geometry_kind(type_name)
    if kind is not None:
        return kind
    if is_geometry_table(display) or is_geometry_table(type_name):
        return "table"
    return None


def _sublayers(path: str) -> list:
    metadata = QgsProviderRegistry.instance().providerMetadata("ogr")
    if metadata is None or not hasattr(metadata, "querySublayers"):
        return []
    try:
        details = list(metadata.querySublayers(path))
    except (TypeError, RuntimeError):
        return []
    found = []
    for detail in details:
        name = detail.name()
        type_name = QgsWkbTypes.displayString(detail.wkbType())
        found.append((name, type_name))
    return found


def _prepare_layer(layer, add_coordinates: bool, add_measures: bool, places, progress) -> tuple:
    kind = _layer_kind(layer, "")
    wanted = measure_names(kind, add_coordinates, add_measures)
    if not wanted:
        return _memory_copy(layer, progress), 0
    crs = layer.crs()
    authid = crs.authid() if crs.isValid() else ""
    type_name = QgsWkbTypes.displayString(layer.wkbType())
    uri = f"{type_name}?crs={authid}" if authid else type_name
    memory = QgsVectorLayer(uri, layer.name(), "memory")
    if not memory.isValid():
        return _memory_copy(layer, progress), 0
    if crs.isValid():
        memory.setCrs(crs)
    existing = [field.name() for field in layer.fields()]
    added, targets = field_targets(existing, wanted)
    fields = [_clone_field(field) for field in layer.fields()]
    fields.extend(QgsField(name, QVariant.Double) for name in added)
    provider = memory.dataProvider()
    provider.addAttributes(fields)
    memory.updateFields()
    missing = 0
    features = []
    seen = 0
    for feature in layer.getFeatures():
        seen += 1
        if seen % 200 == 0:
            _check_progress(progress)
        copied = QgsFeature(memory.fields())
        copied.setGeometry(feature.geometry())
        for field in layer.fields():
            copied[field.name()] = feature[field.name()]
        if not _write_measures(copied, feature.geometry(), crs, kind, targets, places):
            missing += 1
        features.append(copied)
    if features:
        provider.addFeatures(features)
    memory.updateExtents()
    return memory, missing


def _memory_copy(layer, progress):
    crs = layer.crs()
    authid = crs.authid() if crs.isValid() else ""
    type_name = QgsWkbTypes.displayString(layer.wkbType())
    if geometry_kind(type_name) is None:
        type_name = "NoGeometry"
    uri = f"{type_name}?crs={authid}" if authid else type_name
    memory = QgsVectorLayer(uri, layer.name(), "memory")
    if not memory.isValid():
        return layer
    if crs.isValid():
        memory.setCrs(crs)
    provider = memory.dataProvider()
    provider.addAttributes([_clone_field(field) for field in layer.fields()])
    memory.updateFields()
    features = []
    seen = 0
    for feature in layer.getFeatures():
        seen += 1
        if seen % 200 == 0:
            _check_progress(progress)
        copied = QgsFeature(memory.fields())
        copied.setGeometry(feature.geometry())
        for field in layer.fields():
            copied[field.name()] = feature[field.name()]
        features.append(copied)
    if features:
        provider.addFeatures(features)
    memory.updateExtents()
    return memory


def _write_measures(feature, geometry, crs, kind: str, targets, places) -> bool:
    wrote = True
    if any(name in targets for name in ("Northing", "Easting", "Latitude", "Longitude")):
        values = _coordinate_values(geometry, crs)
        if values:
            feature[targets["Northing"]] = round_places(values["Northing"], places["grid"])
            feature[targets["Easting"]] = round_places(values["Easting"], places["grid"])
            feature[targets["Latitude"]] = round_places(values["Latitude"], places["geo"])
            feature[targets["Longitude"]] = round_places(values["Longitude"], places["geo"])
        else:
            wrote = False
    if "Length" in targets or "Area" in targets:
        length, area = _metres(geometry, crs, kind)
        if length is None:
            wrote = False
        elif "Length" in targets:
            feature[targets["Length"]] = round_places(length, places["length"])
        if "Area" in targets:
            if area is None:
                wrote = False
            else:
                feature[targets["Area"]] = round_places(area, places["area"])
    return wrote


def _coordinate_values(geometry, crs) -> dict:
    point = _measure_point(geometry)
    if point is None or not crs.isValid():
        return {}
    try:
        geographic = transform_xy(point, crs, WGS84)
        grid = transform_xy(point, crs, HK1980)
    except QgsCsException:
        return {}
    return {
        "Latitude": geographic.y(),
        "Longitude": geographic.x(),
        "Easting": grid.x(),
        "Northing": grid.y(),
    }


def _measure_point(geometry):
    if geometry is None or geometry.isEmpty():
        return None
    kind = geometry_kind(QgsWkbTypes.displayString(geometry.wkbType()))
    if kind == "point" and not geometry.isMultipart():
        return QgsPointXY(geometry.asPoint())
    centroid = geometry.centroid()
    if centroid is None or centroid.isEmpty():
        return None
    return QgsPointXY(centroid.asPoint())


def _metres(geometry, crs, kind: str) -> tuple:
    if geometry is None or geometry.isEmpty():
        return None, None
    if crs is None or not crs.isValid():
        area = geometry.area() if kind == "polygon" else None
        return geometry.length(), area
    if crs.mapUnits() == _metre_unit() and not _needs_geodesic(crs):
        area = geometry.area() if kind == "polygon" else None
        return geometry.length(), area
    calculator = QgsDistanceArea()
    calculator.setSourceCrs(crs, QgsProject.instance().transformContext())
    calculator.setEllipsoid(_ellipsoid_name(crs))
    try:
        if kind == "polygon":
            length = _perimeter(calculator, geometry)
            area = calculator.measureArea(geometry)
        else:
            length = calculator.measureLength(geometry)
            area = None
    except (QgsCsException, ValueError):
        length = geometry.length()
        area = geometry.area() if kind == "polygon" else None
    return length, area


_WEB_MERCATOR_AUTHIDS = {"EPSG:3857", "EPSG:3785", "EPSG:900913", "EPSG:102100", "EPSG:102113"}


def _needs_geodesic(crs) -> bool:
    """Return whether a projected CRS has a scale that changes across the map."""
    if crs is None or not crs.isValid():
        return True
    if crs.isGeographic():
        return True
    authid = (crs.authid() or "").upper()
    if authid in _WEB_MERCATOR_AUTHIDS:
        return True
    projection = (crs.projectionAcronym() or "").casefold()
    if "merc" in projection:
        return True
    return "mercator" in (crs.description() or "").casefold()


def _ellipsoid_name(crs) -> str:
    ellipsoid = QgsProject.instance().ellipsoid()
    if ellipsoid and str(ellipsoid).strip().upper() != "NONE":
        return ellipsoid
    try:
        return crs.ellipsoidAcronym() or "WGS84"
    except (AttributeError, RuntimeError):
        return "WGS84"


def _perimeter(calculator, geometry) -> float:
    polygons = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
    total = 0.0
    for polygon in polygons:
        for ring in polygon:
            if len(ring) < 2:
                continue
            total += calculator.measureLength(QgsGeometry.fromPolylineXY(ring))
    return total


def _metre_unit():
    return enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")


def _write_database(
    layers,
    target: str,
    output: str,
    progress,
    add_coordinates: bool = False,
    add_measures: bool = False,
    places=None,
    missing_box=None,
) -> tuple:
    driver = "GPKG" if output == "gpkg" else "OpenFileGDB"
    problem = [""]

    def write(path: str) -> bool:
        first = True
        for layer in layers:
            _check_progress(progress)
            outcome = _stream_layer(
                layer,
                path,
                driver,
                first,
                progress,
                add_coordinates,
                add_measures,
                places,
                missing_box,
            )
            if outcome < 0:
                problem[0] = problem[0] or "A feature could not be written."
                return False
            if outcome == 0 and not _copy_layer(
                layer,
                path,
                driver,
                first,
                progress,
                add_coordinates,
                add_measures,
                places,
                missing_box,
                problem,
            ):
                return False
            first = False
        return True

    if output == "fgdb":
        ok, publish_problem = _publish_directory(target, write)
    else:
        ok, publish_problem = _publish_file(target, write)
    return ok, publish_problem or problem[0]


def _stream_layer(
    layer,
    path: str,
    driver: str,
    first: bool,
    progress,
    add_coordinates: bool,
    add_measures: bool,
    places,
    missing_box,
) -> int:
    """Write one layer feature by feature. Return 1 on success, 0 to fall back, -1 on failure."""
    kind = _layer_kind(layer, "")
    wanted = measure_names(kind, add_coordinates, add_measures)
    existing = [field.name() for field in layer.fields()]
    added, targets = field_targets(existing, wanted)
    fields = QgsFields()
    for field in layer.fields():
        fields.append(_clone_field(field))
    for name in added:
        fields.append(QgsField(name, QVariant.Double))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.layerName = layer.name() or "layer"
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = _existing_action(
        "CreateOrOverwriteFile" if first else "CreateOrOverwriteLayer"
    )
    try:
        writer = QgsVectorFileWriter.create(
            path,
            fields,
            layer.wkbType(),
            layer.crs(),
            QgsProject.instance().transformContext(),
            options,
        )
    except Exception:
        return 0
    if writer is None:
        return 0
    missing = 0
    seen = 0
    try:
        if writer.hasError():
            return 0
        for feature in layer.getFeatures():
            seen += 1
            if seen % 200 == 0:
                _check_progress(progress)
            copied = QgsFeature(fields)
            copied.setGeometry(feature.geometry())
            for field in layer.fields():
                copied[field.name()] = feature[field.name()]
            if wanted and not _write_measures(copied, feature.geometry(), layer.crs(), kind, targets, places):
                missing += 1
            if not writer.addFeature(copied):
                return -1
        if missing_box is not None:
            missing_box[0] += missing
        return 1
    finally:
        flush = getattr(writer, "flushBuffer", None)
        if callable(flush):
            flush()
        writer = None


def _copy_layer(
    layer,
    path: str,
    driver: str,
    first: bool,
    progress,
    add_coordinates: bool,
    add_measures: bool,
    places,
    missing_box,
    problem,
) -> bool:
    """Fall back to the memory copy and the high-level writer for one layer."""
    prepared, missed = _prepare_layer(layer, add_coordinates, add_measures, places, progress)
    if missing_box is not None:
        missing_box[0] += missed
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.layerName = prepared.name() or "layer"
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = _existing_action(
        "CreateOrOverwriteFile" if first else "CreateOrOverwriteLayer"
    )
    error, message, _new_path, _new_layer = QgsVectorFileWriter.writeAsVectorFormatV3(
        prepared,
        path,
        QgsProject.instance().transformContext(),
        options,
    )
    if error != _no_error():
        problem[0] = str(message or "").strip()
        return False
    return True


def _publish_file(path: str, write) -> tuple:
    directory = os.path.dirname(path) or "."
    temp = os.path.join(directory, f"hkmap-{os.path.basename(path)}")
    try:
        if os.path.exists(temp):
            os.remove(temp)
        if not write(temp):
            return False, ""
        os.replace(temp, path)
        return True, ""
    except _Stopped:
        raise
    except PermissionError:
        return False, f"Close {os.path.basename(path)} in QGIS, or choose another folder."
    except OSError:
        return False, ""
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass


def _publish_directory(path: str, write) -> tuple:
    """Write a File Geodatabase beside the target, then swap it in safely."""
    directory = os.path.dirname(path) or "."
    stem = os.path.basename(path)
    if stem.lower().endswith(".gdb"):
        stem = stem[:-4]
    token = uuid.uuid4().hex[:12]
    temp = os.path.join(directory, f"hkmap-{stem}.building-{token}.gdb")
    backup = os.path.join(directory, f"hkmap-{stem}.previous-{token}.gdb")
    moved = False
    try:
        if os.path.exists(temp):
            shutil.rmtree(temp)
        if not write(temp):
            return False, ""
        if os.path.exists(path):
            os.rename(path, backup)
            moved = True
        try:
            os.rename(temp, path)
        except OSError:
            return _restore_previous(path, backup, moved)
        if moved:
            moved = False
            shutil.rmtree(backup, ignore_errors=True)
        return True, ""
    except _Stopped:
        raise
    except PermissionError:
        _restore_previous(path, backup, moved)
        return False, f"Close {os.path.basename(path)} in QGIS, or choose another folder."
    except OSError:
        return _restore_previous(path, backup, moved)
    finally:
        if os.path.exists(temp):
            shutil.rmtree(temp, ignore_errors=True)


def _restore_previous(path: str, backup: str, moved: bool) -> tuple:
    """Put the old File Geodatabase back after a failed swap."""
    if moved and not os.path.exists(path):
        try:
            os.rename(backup, path)
        except OSError:
            return False, f"The previous File Geodatabase is in {os.path.basename(backup)}."
    return False, ""


def _release(layers, unlock: bool) -> None:
    while layers:
        layer = layers.pop()
        provider = layer.dataProvider() if unlock else None
        if provider is not None and hasattr(provider, "invalidateConnections"):
            uri = provider.dataSourceUri() or ""
            file_path = uri.split("|", 1)[0]
            if uri:
                provider.invalidateConnections(uri)
            if file_path:
                provider.invalidateConnections(file_path)
        del provider
        del layer
    gc.collect()
    gc.collect()


def _project_uses(path: str) -> bool:
    return bool(_project_layers(path))


def _project_layers(path: str) -> list:
    target = os.path.normcase(os.path.abspath(path))
    matched = []
    for layer in QgsProject.instance().mapLayers().values():
        source = str(layer.source() or "").split("|", 1)[0]
        if not source:
            continue
        try:
            candidate = os.path.normcase(os.path.abspath(source))
        except OSError:
            continue
        if candidate == target:
            matched.append(layer)
    return matched


def _close_message(path: str) -> str:
    basename = os.path.basename(path)
    names = []
    for layer in _project_layers(path):
        name = layer.name() or basename
        if name not in names:
            names.append(name)
    if not names:
        return f"Close {basename} in QGIS, or choose another folder."
    label = ", ".join(names)
    if any(name != basename for name in names):
        return f"Close {label} in {basename} in QGIS, or choose another folder."
    return f"Close {label} in QGIS, or choose another folder."


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _places(places) -> dict:
    values = dict(_DEFAULT_PLACES)
    if places:
        values.update(places)
    for name in _DEFAULT_PLACES:
        try:
            values[name] = min(8, max(0, int(values[name])))
        except (TypeError, ValueError):
            values[name] = _DEFAULT_PLACES[name]
    return values


def _check_progress(progress) -> None:
    if progress is not None and progress() is False:
        raise _Stopped()


def _status_text(written, skipped, missing, failed) -> tuple:
    parts = []
    if written:
        if len(written) == 1:
            parts.append(f"Wrote {written[0]}.")
        else:
            parts.append(f"Wrote {', '.join(written)}.")
    if skipped:
        names = ", ".join(skipped[:8])
        more = len(skipped) - 8
        if more > 0:
            names = f"{names}, and {more} more"
        parts.append(f"Skipped {names}.")
    if missing:
        noun = "feature" if missing == 1 else "features"
        parts.append(f"{missing} {noun} had no coordinate.")
    if failed and not written:
        detail = list(failed[:2])
        if skipped:
            names = ", ".join(skipped[:8])
            more = len(skipped) - 8
            if more > 0:
                names = f"{names}, and {more} more"
            detail.append(f"Skipped {names}.")
        return " ".join(detail), "error"
    if failed:
        parts.extend(failed[:2])
    if not parts:
        return "No point, line, or polygon layers were found.", "error"
    kind = "warn" if skipped or missing or failed else "ok"
    return " ".join(parts), kind


def _existing_action(name: str):
    return enum_member(QgsVectorFileWriter, "ActionOnExistingFile", name)


def _no_error():
    return enum_member(QgsVectorFileWriter, "WriterError", "NoError")
