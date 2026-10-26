"""HK1980 Grid and WGS84 conversion through PROJ."""

from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)

from .config import HK1980, WGS84


def transform_xy(point: QgsPointXY, source, destination) -> QgsPointXY:
    """Transform one point with the project coordinate context."""
    if isinstance(source, str):
        source = QgsCoordinateReferenceSystem(source)
    if isinstance(destination, str):
        destination = QgsCoordinateReferenceSystem(destination)
    transform = QgsCoordinateTransform(source, destination, QgsProject.instance())
    return transform.transform(point)


def convert_values(
    direction: str,
    easting: float | None = None,
    northing: float | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict:
    """Convert one point between HK1980 Grid and WGS84 geographic coordinates."""
    if direction == "hkgrid":
        if easting is None or northing is None:
            raise ValueError("HK1980 Grid needs a northing and an easting.")
        point = transform_xy(QgsPointXY(easting, northing), HK1980, WGS84)
        return {"latitude": point.y(), "longitude": point.x()}
    if direction == "wgsgeog":
        if latitude is None or longitude is None:
            raise ValueError("WGS84 geographic needs a latitude and a longitude.")
        point = transform_xy(QgsPointXY(longitude, latitude), WGS84, HK1980)
        return {"easting": point.x(), "northing": point.y()}
    raise ValueError(f"Unknown conversion direction {direction!r}.")
