"""CSV helpers for HK1980 Grid and WGS84 coordinate tables.

This module does not import QGIS. The coordinate calculation lives in coords.py
and uses PROJ through QGIS.
"""

from __future__ import annotations

import csv
import re
from decimal import Decimal, ROUND_HALF_UP


MAX_POINTS = 10000

DIRECTIONS = (
    ("hkgrid", "HK1980 Grid to WGS84 geographic"),
    ("wgsgeog", "WGS84 geographic to HK1980 Grid"),
)

CSV_HEADERS = ("Point ID", "Northing (m)", "Easting (m)", "Latitude", "Longitude", "Remarks")
_CSV_FIELDS = ("point_id", "northing", "easting", "latitude", "longitude", "remarks")

TEMPLATE_RECORD = {
    "point_id": "A1",
    "northing": "819069.800",
    "easting": "836694.050",
    "latitude": "",
    "longitude": "",
    "remarks": "",
}

_COORDINATE_TEXT = re.compile(r"^[0-9]*\.?[0-9]*$")
_COORDINATE_FIELDS = ("northing", "easting", "latitude", "longitude")

_HEADER_FIELDS = {
    "point id": "point_id",
    "pointid": "point_id",
    "id": "point_id",
    "easting": "easting",
    "easting (m)": "easting",
    "e": "easting",
    "northing": "northing",
    "northing (m)": "northing",
    "n": "northing",
    "latitude": "latitude",
    "latitude (ddd.dddd)": "latitude",
    "lat": "latitude",
    "longitude": "longitude",
    "longitude (ddd.dddd)": "longitude",
    "long": "longitude",
    "lon": "longitude",
    "remarks": "remarks",
    "remark": "remarks",
}


def normalize_record(raw: dict) -> dict:
    """Map a spreadsheet row onto point id, northing, easting, latitude, and longitude."""
    record = {
        "point_id": "",
        "northing": "",
        "easting": "",
        "latitude": "",
        "longitude": "",
        "remarks": "",
    }
    for key, value in raw.items():
        field = _HEADER_FIELDS.get(str(key).strip().casefold())
        if field is None:
            continue
        record[field] = "" if value is None else str(value).strip()
    return record


def read_csv_records(path: str) -> list:
    """Read a CSV file into normalized coordinate records."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("The CSV file has no header row.")
        return [normalize_record(row) for row in reader]


def write_csv_records(path: str, records: list) -> None:
    """Write coordinate rows to a CSV file."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADERS)
        for record in records:
            writer.writerow([record.get(field, "") for field in _CSV_FIELDS])


def is_coordinate_text(text: str) -> bool:
    """Return whether a cell is empty or made of digits and at most one dot."""
    return _COORDINATE_TEXT.fullmatch(str(text).strip()) is not None


def sanitize_record(record: dict) -> tuple:
    """Clear coordinate cells that contain anything other than digits and one dot."""
    cleaned = dict(record)
    skipped = 0
    for field in _COORDINATE_FIELDS:
        text = str(cleaned.get(field, "")).strip()
        if is_coordinate_text(text):
            cleaned[field] = text
            continue
        cleaned[field] = ""
        skipped += 1
    return cleaned, skipped


def sanitize_records(records: list) -> tuple:
    """Return cleaned rows and how many coordinate cells were rejected."""
    cleaned = []
    skipped = 0
    for record in records:
        row, count = sanitize_record(record)
        cleaned.append(row)
        skipped += count
    return cleaned, skipped


def format_number(value: float, places: int) -> str:
    return f"{value:.{places}f}"


def format_rounded(value: float, places: int) -> str:
    """Format a number to a fixed number of places, rounding halves away from zero."""
    if places < 0:
        raise ValueError("Decimal places cannot be negative.")
    quantizer = Decimal(1).scaleb(-places)
    rounded = Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP)
    return f"{rounded:.{places}f}"


def format_dms(value: float) -> str:
    """Format a signed decimal degree as degrees, minutes, and seconds."""
    prefix = "-" if value < 0 else ""
    absolute = abs(value)
    degrees = int(absolute)
    minutes_float = (absolute - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 2)
    if seconds >= 60:
        seconds = 0.0
        minutes += 1
    if minutes >= 60:
        minutes = 0
        degrees += 1
    return f"{prefix}{degrees}\u00b0 {minutes:02d}' {seconds:05.2f}\""
