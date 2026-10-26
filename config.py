"""Shared service addresses and coordinate system codes.

This module does not import QGIS.
"""

from __future__ import annotations


HK1980 = "EPSG:2326"
WGS84 = "EPSG:4326"
WEB_MERCATOR = "EPSG:3857"

API_ROOT = "https://mapapi.geodata.gov.hk/gs/api/v1.0.0"
FEATURE_ROOT = "https://portal.csdi.gov.hk/server/rest/services/common"
GEOPORTAL = "https://portal.csdi.gov.hk/geoportal/"

MAP_CREDIT = "Map from Lands Department"
AERIAL_CREDIT = "Aerial Photograph from Lands Department"
CSDI_CREDIT = "Data from CSDI"

CRS_CODES = (HK1980, WGS84, WEB_MERCATOR)
