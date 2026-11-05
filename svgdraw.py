"""Fit a map view onto A3 and draw it as one SVG.

This module does not import QGIS.
"""

from __future__ import annotations

import base64
import math


_A3_LONG = 420.0
_A3_SHORT = 297.0


def sheet_for_view(view_width: float, view_height: float) -> dict:
    """Fit the map window inside A3. A wide view is landscape and a tall view is portrait."""
    if view_width <= 0 or view_height <= 0:
        raise ValueError("The map view has no size.")
    landscape = view_width >= view_height
    page_width, page_height = (_A3_LONG, _A3_SHORT) if landscape else (_A3_SHORT, _A3_LONG)
    aspect = view_width / view_height
    if page_width / page_height > aspect:
        map_height = page_height
        map_width = page_height * aspect
    else:
        map_width = page_width
        map_height = page_width / aspect
    return {
        "landscape": landscape,
        "page_width_mm": page_width,
        "page_height_mm": page_height,
        "map_width_mm": map_width,
        "map_height_mm": map_height,
        "offset_x_mm": (page_width - map_width) / 2,
        "offset_y_mm": (page_height - map_height) / 2,
    }


def scale_bar(ground_width: float, map_width_mm: float) -> tuple:
    """Return a round ground distance, its printed width, and a label."""
    if ground_width <= 0 or map_width_mm <= 0:
        raise ValueError("The map scale needs a ground width and a printed width.")
    target = ground_width / 4
    magnitude = 10 ** math.floor(math.log10(target))
    chosen = magnitude
    for step in (1, 2, 5):
        candidate = step * magnitude
        if candidate <= target:
            chosen = candidate
    if chosen >= 1000 and chosen % 1000 == 0:
        label = f"{int(chosen / 1000)} km"
    elif chosen >= 1000:
        label = f"{chosen / 1000:.1f} km"
    else:
        label = f"{int(chosen) if chosen >= 1 else chosen} m"
    return chosen, map_width_mm * (chosen / ground_width), label


def clip_polyline(points, rect) -> list:
    """Clip a line to the map rectangle. Each result is one visible run."""
    if len(points) < 2:
        return []
    runs = []
    current = []
    for start, end in zip(points, points[1:]):
        clipped = _clip_segment(start, end, rect)
        if clipped is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        first, second = clipped
        if not current:
            current = [first, second]
        elif _same(current[-1], first):
            current.append(second)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = [first, second]
    if len(current) >= 2:
        runs.append(current)
    return runs


def clip_polygon(ring, rect) -> list:
    """Clip one polygon ring to the map rectangle."""
    output = [point for point in ring]
    if len(output) >= 2 and _same(output[0], output[-1]):
        output = output[:-1]
    if len(output) < 3:
        return []
    for edge in ("left", "right", "bottom", "top"):
        if not output:
            return []
        incoming = output
        output = []
        previous = incoming[-1]
        for current in incoming:
            current_inside = _inside(current, edge, rect)
            previous_inside = _inside(previous, edge, rect)
            if current_inside:
                if not previous_inside:
                    output.append(_edge_intersection(previous, current, edge, rect))
                output.append(current)
            elif previous_inside:
                output.append(_edge_intersection(previous, current, edge, rect))
            previous = current
    return output


def point_inside(point, rect) -> bool:
    """Return whether a coordinate is inside the map rectangle."""
    x_value, y_value = point
    return rect[0] <= x_value <= rect[2] and rect[1] <= y_value <= rect[3]


def build_svg(
    sheet,
    extent,
    layers,
    basemap_png: bytes | None,
    scale,
    include_scale: bool = True,
    include_north: bool = True,
    rotation: float = 0.0,
) -> str:
    """Draw layers in map order, then the selected page marks.

    Each layer is either a vector layer with ``features`` or a picture layer
    with ``image`` bytes. Layers are listed from bottom to top.
    """
    page_width = _num(sheet["page_width_mm"])
    page_height = _num(sheet["page_height_mm"])
    items = []
    if basemap_png:
        items.append({"name": "Basemap", "image": basemap_png})
    items.extend(layers or [])
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{page_width}mm" height="{page_height}mm" '
            f'viewBox="0 0 {page_width} {page_height}">'
        ),
    ]
    if any(item.get("image") for item in items):
        parts.append(
            f'<rect x="{_num(sheet["offset_x_mm"])}" y="{_num(sheet["offset_y_mm"])}" '
            f'width="{_num(sheet["map_width_mm"])}" height="{_num(sheet["map_height_mm"])}" '
            f'fill="#c5ddf0"/>'
        )
    for index, layer in enumerate(items):
        label = str(layer.get("name") or f"Layer {index + 1}")
        parts.append(_layer_open(_layer_id(label, index), label))
        image = layer.get("image")
        if image:
            parts.append(_image_markup(sheet, image))
        else:
            parts.extend(_feature_markup(layer.get("features") or [], sheet, extent, rotation))
        parts.append("</g>")
    if include_scale and scale is not None:
        parts.append(_layer_open("ScaleBar", "Scale bar"))
        parts.append(_scale_markup(sheet, scale))
        parts.append("</g>")
    if include_north:
        parts.append(_layer_open("NorthArrow", "North arrow"))
        parts.append(_north_markup(sheet, rotation))
        parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def _image_markup(sheet, payload: bytes) -> str:
    encoded = _base64(payload)
    return (
        f'<image x="{_num(sheet["offset_x_mm"])}" y="{_num(sheet["offset_y_mm"])}" '
        f'width="{_num(sheet["map_width_mm"])}" height="{_num(sheet["map_height_mm"])}" '
        f'xlink:href="data:image/png;base64,{encoded}"/>'
    )


def _feature_markup(features, sheet, extent, rotation: float = 0.0) -> list:
    markup = []
    page = _page_rect(sheet)
    for feature in features:
        kind = feature.get("kind")
        if kind == "point":
            center = _project((feature["x"], feature["y"]), sheet, extent, rotation)
            if center is None or not point_inside(center, page):
                continue
            radius = feature.get("radius") or 1.2
            style = _style_attrs(feature, "#c0392b", "#1c4b3a", 0.3)
            ring = marker_ring(str(feature.get("shape") or "circle"), float(radius))
            if ring:
                ring = rotate_marker(ring, float(feature.get("angle") or 0.0))
                placed = [(center[0] + point[0], center[1] + point[1]) for point in ring]
                markup.append(
                    f'<path d="{_commands(placed)} Z" {style} stroke-linejoin="miter"/>'
                )
            else:
                markup.append(
                    f'<circle cx="{_num(center[0])}" cy="{_num(center[1])}" '
                    f'r="{_num(radius)}" {style}/>'
                )
        elif kind == "line":
            projected = _project_line(feature.get("points") or [], sheet, extent, rotation)
            style = _style_attrs(feature, "none", "#1c4b3a", 0.4)
            for run in clip_polyline(projected, page):
                markup.append(
                    f'<path d="{_commands(run)}" {style} '
                    f'stroke-linejoin="round" stroke-linecap="round"/>'
                )
        elif kind == "polygon":
            rings = []
            for ring in feature.get("rings") or []:
                projected = _project_line(ring, sheet, extent, rotation)
                clipped = clip_polygon(projected, page)
                if len(clipped) >= 3:
                    rings.append(clipped)
            commands = _polygon_commands(rings)
            if not commands:
                continue
            style = _style_attrs(feature, "#d7e6c8", "#1c4b3a", 0.4)
            markup.append(
                f'<path d="{commands}" {style} fill-rule="evenodd" stroke-linejoin="round"/>'
            )
        else:
            raise ValueError(f"Unknown SVG geometry {kind!r}.")
    return markup


def _style_attrs(feature, fill: str, stroke: str, width: float) -> str:
    fill = feature.get("fill") if feature.get("fill") is not None else fill
    stroke = feature.get("stroke") if feature.get("stroke") is not None else stroke
    try:
        width = float(feature.get("stroke_width", width))
    except (TypeError, ValueError):
        pass
    attributes = [f'fill="{_xml(fill)}"']
    if fill != "none":
        opacity = _opacity(feature.get("fill_opacity"))
        if opacity < 0.999:
            attributes.append(f'fill-opacity="{_num(opacity)}"')
    attributes.append(f'stroke="{_xml(stroke)}"')
    if stroke != "none":
        opacity = _opacity(feature.get("stroke_opacity"))
        if opacity < 0.999:
            attributes.append(f'stroke-opacity="{_num(opacity)}"')
    attributes.append(f'stroke-width="{_num(width)}"')
    dash = feature.get("stroke_dasharray")
    if dash:
        attributes.append(f'stroke-dasharray="{_xml(dash)}"')
    return " ".join(attributes)


def _opacity(value) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


def marker_ring(shape: str, radius: float) -> list:
    """Marker outline around its centre, in millimetres. A circle has no outline."""
    name = str(shape or "circle").casefold().replace("-", "_").replace(" ", "_")
    radius = abs(float(radius))
    if name in ("", "circle"):
        return []
    if name in ("square", "rounded_square", "square_with_corners"):
        return [(-radius, -radius), (radius, -radius), (radius, radius), (-radius, radius)]
    if name in ("diamond",):
        return [(0.0, -radius), (radius, 0.0), (0.0, radius), (-radius, 0.0)]
    if name in ("triangle", "equilateral_triangle", "arrow", "filled_arrowhead", "arrowhead"):
        return _regular_polygon(3, radius)
    if name == "pentagon":
        return _regular_polygon(5, radius)
    if name == "hexagon":
        return _regular_polygon(6, radius)
    if name == "octagon":
        return _regular_polygon(8, radius)
    if name == "decagon":
        return _regular_polygon(10, radius)
    if name == "star":
        return _star(radius, 5, 0.4)
    if name == "star_diamond":
        return _star(radius, 4, 0.4)
    if name in ("cross", "cross2", "cross_fill", "asterisk_fill"):
        return _plus(radius)
    return []


def rotate_marker(points, degrees: float) -> list:
    """Turn a marker clockwise, matching QGIS marker rotation on the page."""
    if not points or not degrees:
        return list(points)
    radians = math.radians(float(degrees))
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return [
        (x_value * cosine - y_value * sine, x_value * sine + y_value * cosine)
        for x_value, y_value in points
    ]


def _regular_polygon(sides: int, radius: float) -> list:
    start = -math.pi / 2
    return [
        (
            radius * math.cos(start + index * 2 * math.pi / sides),
            radius * math.sin(start + index * 2 * math.pi / sides),
        )
        for index in range(sides)
    ]


def _star(radius: float, points: int, inner_ratio: float) -> list:
    start = -math.pi / 2
    step = math.pi / points
    ring = []
    for index in range(points * 2):
        used = radius if index % 2 == 0 else radius * inner_ratio
        angle = start + index * step
        ring.append((used * math.cos(angle), used * math.sin(angle)))
    return ring


def _plus(radius: float) -> list:
    arm = radius * 0.34
    return [
        (-arm, -radius),
        (arm, -radius),
        (arm, -arm),
        (radius, -arm),
        (radius, arm),
        (arm, arm),
        (arm, radius),
        (-arm, radius),
        (-arm, arm),
        (-radius, arm),
        (-radius, -arm),
        (-arm, -arm),
    ]


def _polygon_commands(rings) -> str:
    return " ".join(_commands(ring) + " Z" for ring in rings if len(ring) >= 3)


def _commands(points) -> str:
    return " ".join(
        f"{'M' if index == 0 else 'L'} {_num(point[0])} {_num(point[1])}"
        for index, point in enumerate(points)
    )


def _project_line(points, sheet, extent, rotation: float = 0.0) -> list:
    projected = []
    for point in points:
        placed = _project(point, sheet, extent, rotation)
        if placed is not None:
            projected.append(placed)
    return projected


def _project(point, sheet, extent, rotation: float = 0.0):
    xmin, ymin, xmax, ymax = extent
    width = xmax - xmin
    height = ymax - ymin
    if width <= 0 or height <= 0:
        return None
    x_value = sheet["offset_x_mm"] + (point[0] - xmin) / width * sheet["map_width_mm"]
    y_value = sheet["offset_y_mm"] + (ymax - point[1]) / height * sheet["map_height_mm"]
    if rotation:
        center_x = sheet["offset_x_mm"] + sheet["map_width_mm"] / 2
        center_y = sheet["offset_y_mm"] + sheet["map_height_mm"] / 2
        angle = math.radians(rotation)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        delta_x = x_value - center_x
        delta_y = y_value - center_y
        x_value = center_x + delta_x * cosine - delta_y * sine
        y_value = center_y + delta_x * sine + delta_y * cosine
    return x_value, y_value


def _page_rect(sheet) -> tuple:
    left = sheet["offset_x_mm"]
    top = sheet["offset_y_mm"]
    return (left, top, left + sheet["map_width_mm"], top + sheet["map_height_mm"])


def _scale_markup(sheet, scale) -> str:
    if scale is None:
        return ""
    metres, width, label = scale
    x_value = sheet["offset_x_mm"] + 6
    y_value = sheet["offset_y_mm"] + sheet["map_height_mm"] - 8
    return (
        f'<rect x="{_num(x_value)}" y="{_num(y_value - 1.4)}" width="{_num(width)}" height="1.4" '
        f'fill="#f4f1e8" stroke="#1c4b3a" stroke-width="0.3"/>'
        f'<text x="{_num(x_value)}" y="{_num(y_value - 2.2)}" fill="#1c4b3a" font-size="3.2" '
        f'font-family="sans-serif">{_xml(label)}</text>'
    )


def _north_markup(sheet, rotation: float = 0.0) -> str:
    x_value = sheet["offset_x_mm"] + sheet["map_width_mm"] - 10
    y_value = sheet["offset_y_mm"] + 8
    arrow = (
        f'<path d="M {_num(x_value)} {_num(y_value)} L {_num(x_value - 2.2)} {_num(y_value + 7)} '
        f'L {_num(x_value)} {_num(y_value + 5.2)} L {_num(x_value + 2.2)} {_num(y_value + 7)} Z" '
        f'fill="#c0392b" stroke="#1c4b3a" stroke-width="0.3"/>'
        f'<text x="{_num(x_value - 1.1)}" y="{_num(y_value - 1)}" fill="#1c4b3a" font-size="3.5" '
        f'font-family="sans-serif">N</text>'
    )
    if not rotation:
        return arrow
    return (
        f'<g transform="rotate({_num(rotation)} {_num(x_value)} {_num(y_value + 4)})">'
        f"{arrow}</g>"
    )


def _layer_open(identifier: str, label: str) -> str:
    return (
        f'<g inkscape:groupmode="layer" id="{_xml(identifier)}" '
        f'inkscape:label="{_xml(label)}">'
    )


def _layer_id(label: str, index: int) -> str:
    cleaned = "".join(character if character.isalnum() else "_" for character in label).strip("_")
    return f"layer_{index + 1}_{cleaned or 'layer'}"


def _clip_segment(start, end, rect):
    x0, y0 = start
    x1, y1 = end
    code0 = _code(x0, y0, rect)
    code1 = _code(x1, y1, rect)
    for _attempt in range(12):
        if code0 == 0 and code1 == 0:
            return (x0, y0), (x1, y1)
        if code0 & code1:
            return None
        code = code0 or code1
        if code & 8:
            if y1 == y0:
                return None
            x_value = x0 + (x1 - x0) * (rect[3] - y0) / (y1 - y0)
            y_value = rect[3]
        elif code & 4:
            if y1 == y0:
                return None
            x_value = x0 + (x1 - x0) * (rect[1] - y0) / (y1 - y0)
            y_value = rect[1]
        elif code & 2:
            if x1 == x0:
                return None
            y_value = y0 + (y1 - y0) * (rect[2] - x0) / (x1 - x0)
            x_value = rect[2]
        else:
            if x1 == x0:
                return None
            y_value = y0 + (y1 - y0) * (rect[0] - x0) / (x1 - x0)
            x_value = rect[0]
        if code == code0:
            x0, y0 = x_value, y_value
            code0 = _code(x0, y0, rect)
        else:
            x1, y1 = x_value, y_value
            code1 = _code(x1, y1, rect)
    return None


def _edge_intersection(start, end, edge: str, rect):
    x1, y1 = start
    x2, y2 = end
    if edge == "left" or edge == "right":
        edge_x = rect[0] if edge == "left" else rect[2]
        if x2 == x1:
            return edge_x, y1
        return edge_x, y1 + (y2 - y1) * (edge_x - x1) / (x2 - x1)
    edge_y = rect[1] if edge == "bottom" else rect[3]
    if y2 == y1:
        return x1, edge_y
    return x1 + (x2 - x1) * (edge_y - y1) / (y2 - y1), edge_y


def _inside(point, edge: str, rect) -> bool:
    if edge == "left":
        return point[0] >= rect[0]
    if edge == "right":
        return point[0] <= rect[2]
    if edge == "bottom":
        return point[1] >= rect[1]
    if edge == "top":
        return point[1] <= rect[3]
    raise ValueError(f"Unknown clip edge {edge!r}.")


def _code(x_value: float, y_value: float, rect) -> int:
    code = 0
    if x_value < rect[0]:
        code |= 1
    elif x_value > rect[2]:
        code |= 2
    if y_value < rect[1]:
        code |= 4
    elif y_value > rect[3]:
        code |= 8
    return code


def _same(first, second) -> bool:
    return abs(first[0] - second[0]) < 1e-8 and abs(first[1] - second[1]) < 1e-8


def _num(value: float) -> str:
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _xml(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _base64(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")
