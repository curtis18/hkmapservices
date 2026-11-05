"""Export the current map view to one SVG for Adobe Illustrator."""

from __future__ import annotations

import os
import time

from qgis.core import (
    QgsCsException,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMapLayer,
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsRenderContext,
    QgsSimpleMarkerSymbolLayerBase,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QBuffer, QIODevice, QSize, QThread, Qt
from qgis.PyQt.QtGui import QColor, QImage, QPainter, qAlpha
from qgis.PyQt.QtWidgets import QApplication

from .compat import enum_member
from .config import WGS84
from .filemath import geometry_kind
from .svgdraw import (
    build_svg,
    clip_polygon,
    clip_polyline,
    point_inside,
    scale_bar,
    sheet_for_view,
)


def export_map_svg(
    canvas,
    path: str,
    include_scale: bool = True,
    include_north: bool = True,
    progress=None,
) -> tuple:
    """Write one A3 SVG of the visible map. Return the status text and its kind."""
    if canvas is None:
        return "The map is not open.", "error"
    if not path or not str(path).strip():
        return "Choose where to save the SVG.", "error"
    if not str(path).lower().endswith(".svg"):
        path = str(path) + ".svg"
    try:
        sheet = sheet_for_view(canvas.width(), canvas.height())
    except ValueError as exc:
        return str(exc), "error"
    extent = canvas.extent()
    if extent is None or extent.isEmpty():
        return "The map view is empty.", "error"
    layers = list(canvas.layers() or [])
    items = []
    plain = 0
    skipped = []
    map_crs = canvas.mapSettings().destinationCrs()
    rotation = float(canvas.mapSettings().rotation() or 0.0)
    box = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    try:
        # QgsMapCanvas lists the topmost layer first. SVG paints in document order.
        ordered = list(reversed(layers))
        index = 0
        while index < len(ordered):
            layer = ordered[index]
            index += 1
            _check_progress(progress)
            if _is_picture(layer):
                run = [layer]
                while index < len(ordered) and _is_picture(ordered[index]):
                    run.append(ordered[index])
                    index += 1
                picture = _render_pictures(canvas, list(reversed(run)), sheet, progress)
                if picture:
                    items.append({"name": run[0].name() or "Basemap", "image": picture})
                continue
            if not _is_vector(layer):
                skipped.append(layer.name() or "layer")
                continue
            kind = geometry_kind(QgsWkbTypes.displayString(layer.wkbType()))
            if kind is None:
                skipped.append(layer.name() or "layer")
                continue
            features, used_plain = _vector_features(layer, box, map_crs, progress)
            plain += used_plain
            items.append({"name": layer.name(), "features": features})
        if not items:
            return "The map has no visible layers that can be exported.", "error"
        svg = build_svg(
            sheet,
            box,
            items,
            None,
            _scale(extent, canvas, sheet) if include_scale else None,
            include_scale,
            include_north,
            rotation,
        )
        _check_progress(progress)
    except _Stopped:
        return "Export stopped.", "warn"
    if not _write_text(path, svg):
        return "The SVG could not be written.", "error"
    direction = "landscape" if sheet["landscape"] else "portrait"
    message = (
        f"Exported an A3 {direction} sheet, "
        f"{sheet['page_width_mm']:.0f} mm by {sheet['page_height_mm']:.0f} mm."
    )
    warnings = []
    if plain:
        noun = "feature" if plain == 1 else "features"
        warnings.append(f"{plain} {noun} used a plain style.")
    if skipped:
        noun = "layer" if len(skipped) == 1 else "layers"
        warnings.append(f"Skipped {len(skipped)} {noun} that cannot be drawn as vectors.")
    if warnings:
        return f"{message} {' '.join(warnings)}", "warn"
    return message, "ok"


class _Stopped(Exception):
    """The dialog asked the export to finish the current step and stop."""


def _vector_features(layer, box, map_crs, progress) -> tuple:
    kind = geometry_kind(QgsWkbTypes.displayString(layer.wkbType()))
    request = QgsFeatureRequest()
    layer_rect = _layer_extent(box, layer, map_crs)
    if layer_rect is not None:
        request.setFilterRect(layer_rect)
    drawn = []
    plain = 0
    last = time.monotonic()
    for feature in layer.getFeatures(request):
        now = time.monotonic()
        if now - last >= 0.1:
            _check_progress(progress)
            last = now
        paint, is_plain = _paint(layer, feature, kind)
        if is_plain:
            plain += 1
        geometry = _map_geometry(feature.geometry(), layer, map_crs)
        drawn.extend(_clip_feature(geometry, kind, box, paint))
    return drawn, plain


def _layer_extent(box, layer, map_crs):
    rectangle = _rectangle(box)
    source = layer.crs()
    if source is None or map_crs is None or not source.isValid() or not map_crs.isValid():
        return rectangle
    if source.authid() == map_crs.authid():
        return rectangle
    transform = QgsCoordinateTransform(map_crs, source, QgsProject.instance())
    try:
        return transform.transformBoundingBox(rectangle)
    except QgsCsException:
        return None


def _map_geometry(geometry, layer, map_crs):
    if geometry is None or geometry.isEmpty():
        return geometry
    source = layer.crs()
    if source is None or map_crs is None or not source.isValid() or not map_crs.isValid():
        return geometry
    if source.authid() == map_crs.authid():
        return geometry
    cloned = QgsGeometry(geometry)
    transform = QgsCoordinateTransform(source, map_crs, QgsProject.instance())
    try:
        cloned.transform(transform)
    except QgsCsException:
        return None
    return cloned


def _check_progress(progress) -> None:
    if progress is not None and progress() is False:
        raise _Stopped()


def _clip_feature(geometry, kind: str, box, paint) -> list:
    if geometry is None or geometry.isEmpty():
        return []
    if kind == "point":
        points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
        return [
            {"kind": "point", "x": point.x(), "y": point.y(), **paint}
            for point in points
            if point_inside((point.x(), point.y()), box)
        ]
    if kind == "line":
        lines = geometry.asMultiPolyline() if geometry.isMultipart() else [geometry.asPolyline()]
        drawn = []
        for line in lines:
            coords = [(point.x(), point.y()) for point in line]
            for run in clip_polyline(coords, box):
                drawn.append({"kind": "line", "points": run, **paint})
        return drawn
    if kind == "polygon":
        polygons = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
        drawn = []
        for polygon in polygons:
            rings = []
            for ring in polygon:
                clipped = clip_polygon([(point.x(), point.y()) for point in ring], box)
                if len(clipped) >= 3:
                    rings.append(clipped)
            if rings:
                drawn.append({"kind": "polygon", "rings": rings, **paint})
        return drawn
    raise ValueError(f"Unknown SVG geometry {kind!r}.")


def _paint(layer, feature, kind: str) -> tuple:
    if kind == "point":
        plain = {"fill": "#c0392b", "stroke": "#1c4b3a", "stroke_width": 0.3, "radius": 1.2}
    elif kind == "line":
        plain = {"fill": "none", "stroke": "#1c4b3a", "stroke_width": 0.4}
    elif kind == "polygon":
        plain = {"fill": "#d7e6c8", "stroke": "#1c4b3a", "stroke_width": 0.4}
    else:
        raise ValueError(f"Unknown SVG geometry {kind!r}.")
    symbol = _symbol(layer, feature)
    if symbol is None:
        return plain, True
    child = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None
    opacity = _symbol_opacity(symbol)
    fill_colour = symbol.color() if hasattr(symbol, "color") else None
    stroke_colour = fill_colour
    if child is not None and hasattr(child, "strokeColor"):
        stroke_colour = child.strokeColor()
    width = plain["stroke_width"]
    if child is not None and hasattr(child, "strokeWidth"):
        try:
            width = min(2.0, max(0.15, float(child.strokeWidth())))
        except (TypeError, ValueError):
            width = plain["stroke_width"]
    fill, fill_alpha = _colour(fill_colour, opacity)
    stroke, stroke_alpha = _colour(stroke_colour, opacity)
    paint = {
        "fill": fill,
        "fill_opacity": fill_alpha,
        "stroke": stroke,
        "stroke_opacity": stroke_alpha,
        "stroke_width": width,
    }
    if kind == "point":
        paint["radius"] = _marker_radius(child)
        paint["shape"] = _marker_shape(child)
        paint["angle"] = _marker_angle(child)
        if fill == "none" and stroke == "none":
            paint["fill"] = plain["fill"]
            paint["fill_opacity"] = 1.0
    elif kind == "line":
        paint["fill"] = "none"
        dash = _dash(child)
        if dash:
            paint["stroke_dasharray"] = dash
    elif kind == "polygon":
        if _no_brush(child):
            paint["fill"] = "none"
            paint["fill_opacity"] = 0.0
        dash = _dash(child)
        if dash:
            paint["stroke_dasharray"] = dash
    return paint, False


def _colour(color, opacity: float = 1.0) -> tuple:
    if color is None or not color.isValid():
        return "none", 0.0
    alpha = min(1.0, max(0.0, float(color.alphaF()) * opacity))
    if alpha <= 0.0:
        return "none", 0.0
    return _hex(color), alpha


def _symbol_opacity(symbol) -> float:
    try:
        return min(1.0, max(0.0, float(symbol.opacity())))
    except (AttributeError, TypeError, ValueError):
        return 1.0


def _marker_shape(child) -> str:
    reader = getattr(child, "shape", None)
    if not callable(reader):
        return "circle"
    try:
        return str(QgsSimpleMarkerSymbolLayerBase.encodeShape(reader()) or "circle")
    except (TypeError, ValueError):
        return "circle"


def _marker_angle(child) -> float:
    reader = getattr(child, "angle", None)
    if not callable(reader):
        return 0.0
    try:
        return float(reader())
    except (TypeError, ValueError):
        return 0.0


def _marker_radius(child) -> float:
    if child is None or not hasattr(child, "size"):
        return 1.2
    try:
        return min(4.0, max(0.4, float(child.size()) / 2))
    except (TypeError, ValueError):
        return 1.2


def _no_brush(child) -> bool:
    style = getattr(child, "brushStyle", None)
    if not callable(style):
        return False
    try:
        value = style()
    except Exception:
        return False
    no_brush = getattr(Qt, "NoBrush", None)
    if no_brush is None:
        brush_style = getattr(Qt, "BrushStyle", None)
        no_brush = getattr(brush_style, "NoBrush", None) if brush_style is not None else None
    return no_brush is not None and value == no_brush


def _dash(child) -> str:
    pattern = getattr(child, "dashPattern", None)
    if not callable(pattern):
        return ""
    try:
        values = [float(value) for value in pattern() if float(value) > 0]
    except (TypeError, ValueError):
        return ""
    if len(values) < 2:
        return ""
    return ",".join(f"{value:g}" for value in values)


def _symbol(layer, feature):
    renderer = layer.renderer() if hasattr(layer, "renderer") else None
    if renderer is None or not hasattr(renderer, "symbolForFeature"):
        return None
    try:
        return renderer.symbolForFeature(feature, QgsRenderContext())
    except TypeError:
        try:
            return renderer.symbolForFeature(feature)
        except Exception:
            return None
    except Exception:
        return None


def _is_picture(layer) -> bool:
    provider = str(layer.providerType() or "").casefold()
    if "vectortile" in provider:
        return True
    return layer.type() == enum_member(QgsMapLayer, "LayerType", "RasterLayer")


def _is_vector(layer) -> bool:
    return layer.type() == enum_member(QgsMapLayer, "LayerType", "VectorLayer")


def _wait_for_render(job, progress) -> bool:
    """Let the map window paint while a basemap picture is drawn."""
    job.start()
    while job.isActive():
        if progress is None:
            QApplication.processEvents()
        elif progress() is False:
            job.cancel()
            job.waitForFinished()
            return False
        if job.isActive():
            QThread.msleep(50)
    return True


def _render_pictures(canvas, layers, sheet, progress=None):
    """Render consecutive raster or vector-tile layers as one picture.

    ``layers`` is ordered topmost first, matching QgsMapSettings.
    """
    width = max(1, int(round(sheet["map_width_mm"] / 25.4 * 300)))
    height = max(1, int(round(sheet["map_height_mm"] / 25.4 * 300)))
    image = QImage(width, height, enum_member(QImage, "Format", "Format_ARGB32"))
    image.fill(QColor(0, 0, 0, 0))
    settings = QgsMapSettings(canvas.mapSettings())
    settings.setLayers(list(layers))
    settings.setExtent(canvas.extent())
    settings.setOutputSize(QSize(width, height))
    settings.setOutputDpi(300)
    settings.setBackgroundColor(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    try:
        if not _wait_for_render(job, progress):
            raise _Stopped()
    except _Stopped:
        raise
    except Exception:
        pass
    finally:
        if painter.isActive():
            painter.end()
    _check_progress(progress)
    if not _image_has_content(image):
        return None
    buffer = QBuffer()
    buffer.open(_write_only())
    image.save(buffer, "PNG")
    payload = buffer.data()
    buffer.close()
    if hasattr(payload, "data"):
        return bytes(payload.data())
    return bytes(payload)


def _image_has_content(image) -> bool:
    """Sample the image so a layer that drew nothing is not embedded as a blank page."""
    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return False
    step_x = max(1, width // 64)
    step_y = max(1, height // 64)
    for y_value in range(0, height, step_y):
        for x_value in range(0, width, step_x):
            if qAlpha(image.pixel(x_value, y_value)):
                return True
    return False


def _scale(extent, canvas, sheet):
    ground = _ground_metres(extent, canvas.mapSettings().destinationCrs())
    if ground <= 0:
        return None
    return scale_bar(ground, sheet["map_width_mm"])


def _ground_metres(extent, crs) -> float:
    width = abs(extent.width())
    if width <= 0:
        return 0.0
    if crs is None or not crs.isValid():
        return width
    metres = enum_member(QgsUnitTypes, "DistanceUnit", "DistanceMeters")
    if crs.mapUnits() == metres and not _needs_geodesic(crs):
        return width
    geographic = QgsCoordinateReferenceSystem(WGS84)
    transform = QgsCoordinateTransform(crs, geographic, QgsProject.instance())
    y_value = (extent.yMinimum() + extent.yMaximum()) / 2
    try:
        start = transform.transform(QgsPointXY(extent.xMinimum(), y_value))
        end = transform.transform(QgsPointXY(extent.xMaximum(), y_value))
    except QgsCsException:
        return width
    calculator = QgsDistanceArea()
    calculator.setSourceCrs(geographic, QgsProject.instance().transformContext())
    calculator.setEllipsoid(_ellipsoid_name(crs))
    return calculator.measureLength(QgsGeometry.fromPolylineXY([start, end]))


_WEB_MERCATOR_AUTHIDS = {"EPSG:3857", "EPSG:3785", "EPSG:900913", "EPSG:102100", "EPSG:102113"}


def _needs_geodesic(crs) -> bool:
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


def _rectangle(box):
    return QgsRectangle(box[0], box[1], box[2], box[3])


def _write_text(path: str, text: str) -> bool:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp = os.path.join(directory, f"hkmap-{os.path.basename(path)}")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(temp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp, path)
        return True
    except OSError:
        return False
    finally:
        if os.path.exists(temp) and os.path.abspath(temp) != os.path.abspath(path):
            try:
                os.remove(temp)
            except OSError:
                pass


def _hex(color) -> str:
    return f"#{color.red():02x}{color.green():02x}{color.blue():02x}"


def _write_only():
    return enum_member(QIODevice, "OpenModeFlag", "WriteOnly")
