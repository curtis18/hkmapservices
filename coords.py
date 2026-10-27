"""HK1980 Grid and WGS84 conversion through PROJ."""

from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)

from .config import HK1980, WGS84


_TRANSFORMS = {}
_PROJECT_CRS = None
_CRS_HOOKED = False


def transform_xy(point: QgsPointXY, source, destination) -> QgsPointXY:
    """Transform one point with the project coordinate context."""
    if isinstance(source, str):
        source = QgsCoordinateReferenceSystem(source)
    if isinstance(destination, str):
        destination = QgsCoordinateReferenceSystem(destination)
    transform = _cached_transform(source, destination)
    return transform.transform(point)


def _cached_transform(source, destination):
    """Reuse one transform until the project CRS changes."""
    _watch_project_crs()
    project = QgsProject.instance()
    current = project.crs().authid()
    global _PROJECT_CRS
    if current != _PROJECT_CRS:
        _TRANSFORMS.clear()
        _PROJECT_CRS = current
    key = (_crs_key(source), _crs_key(destination))
    transform = _TRANSFORMS.get(key)
    if transform is None:
        transform = QgsCoordinateTransform(source, destination, project)
        _TRANSFORMS[key] = transform
    return transform


def _crs_key(crs) -> str:
    authid = crs.authid()
    if authid:
        return authid
    return crs.toWkt()


def _watch_project_crs() -> None:
    global _CRS_HOOKED
    if _CRS_HOOKED:
        return
    QgsProject.instance().crsChanged.connect(_clear_transforms)
    _CRS_HOOKED = True


def _clear_transforms() -> None:
    global _PROJECT_CRS
    _TRANSFORMS.clear()
    _PROJECT_CRS = None


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
