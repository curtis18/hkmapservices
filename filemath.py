"""File names and geometry kinds for database conversion.

This module does not import QGIS.
"""

from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_UP


_COORDINATE_NAMES = ("Northing", "Easting", "Latitude", "Longitude")


def geometry_kind(type_name: str) -> str | None:
    """Return point, line, or polygon. Other kinds are not converted."""
    text = str(type_name).casefold().replace(" ", "").replace("_", "")
    if text in ("", "nogeometry", "none", "null", "unknown", "geometrycollection"):
        return None
    if "polygon" in text:
        return "polygon"
    if "line" in text:
        return "line"
    if "point" in text:
        return "point"
    return None


def normalize_outputs(outputs) -> tuple:
    """Return GeoPackage and File Geodatabase outputs, in the order given."""
    chosen = []
    for item in outputs:
        if item == "gpkg" or item == "fgdb":
            if item not in chosen:
                chosen.append(item)
            continue
        raise ValueError(f"Unknown file output {item!r}.")
    if not chosen:
        raise ValueError("Choose GeoPackage or File Geodatabase.")
    return tuple(chosen)


def discover_sources(path: str) -> list:
    """Return one GeoPackage, one File Geodatabase, or each geodatabase in a folder."""
    if not path or not str(path).strip():
        return []
    source = os.path.abspath(path)
    if os.path.isfile(source) and source.lower().endswith(".gpkg"):
        return [source]
    if os.path.isdir(source) and source.lower().endswith(".gdb"):
        return [source]
    if not os.path.isdir(source):
        return []
    found = []
    for name in sorted(os.listdir(source)):
        child = os.path.join(source, name)
        if os.path.isdir(child) and name.lower().endswith(".gdb") and not is_working_name(name):
            found.append(child)
    return found


def is_working_name(name: str) -> bool:
    """Return whether a folder is a conversion work folder, not a source database."""
    lowered = str(name or "").casefold()
    if not lowered.startswith("hkmap-"):
        return False
    return ".previous" in lowered or ".building" in lowered


def is_geometry_table(type_name: str) -> bool:
    """Return whether a layer type is a table with no geometry."""
    text = str(type_name or "").casefold().replace(" ", "").replace("_", "")
    return text in ("", "nogeometry", "none", "null")


def measure_names(kind: str, add_coordinates: bool, add_measures: bool) -> tuple:
    """Return the columns selected for a point, line, or polygon."""
    if kind not in ("point", "line", "polygon"):
        return ()
    names = []
    if add_coordinates:
        names.extend(_COORDINATE_NAMES)
    if add_measures and kind == "line":
        names.append("Length")
    elif add_measures and kind == "polygon":
        names.extend(("Length", "Area"))
    return tuple(names)


def field_targets(existing_names, wanted) -> tuple:
    """Reuse a field of the same name, ignoring case, and list names still to add."""
    folded = {}
    for name in existing_names:
        folded.setdefault(str(name).casefold(), str(name))
    added = []
    targets = {}
    for name in wanted:
        match = folded.get(name.casefold())
        if match is None:
            added.append(name)
            targets[name] = name
            folded[name.casefold()] = name
        else:
            targets[name] = match
    return tuple(added), targets


def round_places(value, places: int) -> float:
    """Round half up to the selected number of decimal places."""
    count = min(8, max(0, int(places)))
    quantizer = Decimal("1") if count == 0 else Decimal("0." + ("0" * (count - 1)) + "1")
    rounded = Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP)
    return float(rounded)


def source_stem(path: str) -> str:
    """Return the database name without its extension."""
    base = os.path.basename(os.path.normpath(path))
    lowered = base.lower()
    if lowered.endswith(".gpkg"):
        base = base[:-5]
    elif lowered.endswith(".gdb"):
        base = base[:-4]
    cleaned = "".join("_" if character in '<>:"/\\|?*' else character for character in base).strip().rstrip(".")
    return cleaned or "export"
