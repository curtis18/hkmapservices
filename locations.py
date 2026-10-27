"""Lands Department location search.

This module does not import QGIS. Coordinates in the response are HK1980 Grid:
x is easting and y is northing.
"""

from __future__ import annotations

from urllib.parse import quote


API_URL = "https://www.map.gov.hk/gs/api/v1.0.0/locationSearch"


def search_url(query: str) -> str:
    """Return the location search request for one place name."""
    text = query.strip()
    if not text:
        raise ValueError("Enter a place, address, or facility name.")
    return API_URL + "?q=" + quote(text)


def parse_results(payload) -> list:
    """Read every match from a location search response."""
    if not isinstance(payload, list):
        raise ValueError("The location service did not return a list.")
    results = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            easting = float(item["x"])
            northing = float(item["y"])
        except (KeyError, TypeError, ValueError):
            continue
        results.append(
            {
                "name_en": _text(item.get("nameEN")),
                "name_zh": _text(item.get("nameZH")),
                "address_en": _text(item.get("addressEN")),
                "address_zh": _text(item.get("addressZH")),
                "district_en": _text(item.get("districtEN")),
                "district_zh": _text(item.get("districtZH")),
                "easting": easting,
                "northing": northing,
            }
        )
    return results


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()
