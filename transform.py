"""CSV helpers for HK1980 Grid and WGS84 coordinate tables.

This module does not import QGIS. The coordinate calculation lives in coords.py
and uses PROJ through QGIS.
"""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
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

COORDINATE_PATTERN = r"^(?:\d+(?:\.\d*)?|\.\d+)?$"
_COORDINATE_TEXT = re.compile(COORDINATE_PATTERN)
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

# Frequent characters used when both Big5 and GB18030 can decode the same bytes.
_COMMON_CJK = frozenset(
    "的一是不了在人有我他这个们中来上大为和国地到以说时要就出会也你对生能而子那得于着下自之年过发后作里用道行所然家种事成方多经么去法学如都同现当没动面起看定天分还进好小部其些主样理心她本前开但因只从想实"
    "的一是不了在人有我他這個們中來上大為和國地到以說時要就出會也你對生能而子那得於著下自之年過發後作裡用道行所然家種事成方多經麼去法學如都同現當沒動面起看定天分還進好小部其些主樣理心她本前開但因只從想實"
    "香港九龍新界觀塘荃灣沙田大埔元朗屯門西貢離島油尖旺深水埗黃大仙葵青灣仔备注備註說明名称名稱地址深圳广州廣州观塘"
)


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


def read_csv_records(path: str, limit: int | None = None) -> list:
    """Read a CSV file into normalized coordinate records."""
    with open(path, "rb") as handle:
        payload = handle.read()
    reader = csv.DictReader(io.StringIO(decode_csv_text(payload), newline=""))
    if reader.fieldnames is None:
        raise ValueError("The CSV file has no header row.")
    records = []
    for row in reader:
        records.append(normalize_record(row))
        if limit is not None and len(records) >= limit:
            break
    return records


def decode_csv_text(payload: bytes) -> str:
    """Decode CSV bytes as UTF-8, UTF-16 with a BOM, Big5, or GB18030."""
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeError:
        pass
    choices = []
    for name in ("big5hkscs", "gb18030"):
        try:
            text = payload.decode(name)
        except UnicodeError:
            continue
        if text not in choices:
            choices.append(text)
    if not choices:
        raise ValueError("The CSV encoding could not be read. Save the file as UTF-8 and try again.")
    if len(choices) == 1:
        return choices[0]
    return max(choices, key=_csv_text_score)


def _csv_text_score(text: str) -> tuple:
    lines = text.splitlines()
    header = lines[0] if lines else ""
    names = next(csv.reader([header]), [])
    known = sum(1 for name in names if str(name).strip().casefold() in _HEADER_FIELDS)
    ordinary = sum(1 for char in text if char in _COMMON_CJK)
    return (known, ordinary)


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


def preferred_pair(direction: str, record: dict) -> str | None:
    """Use the selected coordinate pair, or the other pair when that one is incomplete."""
    if direction != "hkgrid" and direction != "wgsgeog":
        raise ValueError(f"Unknown direction {direction!r}.")
    if _pair_ready(direction, record):
        return direction
    other = "wgsgeog" if direction == "hkgrid" else "hkgrid"
    if _pair_ready(other, record):
        return other
    return None


def _pair_ready(direction: str, record: dict) -> bool:
    if direction == "hkgrid":
        fields = ("northing", "easting")
    elif direction == "wgsgeog":
        fields = ("latitude", "longitude")
    else:
        raise ValueError(f"Unknown direction {direction!r}.")
    return all(_is_number(record.get(field, "")) for field in fields)


def _is_number(text) -> bool:
    cleaned = str(text).strip()
    if cleaned == "" or not is_coordinate_text(cleaned):
        return False
    try:
        float(cleaned)
    except ValueError:
        return False
    return True


def publish_file(path: str, write) -> bool:
    """Write beside the destination and replace it only after that write succeeds."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp = None
    try:
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(prefix="hkmap-", suffix=".xlsx", dir=directory)
        os.close(handle)
        os.remove(temp)
        if not write(temp):
            return False
        os.replace(temp, path)
        return True
    except OSError:
        return False
    finally:
        if temp and os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass


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
