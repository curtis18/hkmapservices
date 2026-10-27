"""Lands Department and CSDI service definitions.

This module does not import QGIS so the URL and tile-grid tests can run
with a normal Python.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from xml.sax.saxutils import escape

from .config import (
    AERIAL_CREDIT,
    API_ROOT,
    CSDI_CREDIT,
    CRS_CODES,
    FEATURE_ROOT,
    GEOPORTAL,
    HK1980,
    MAP_CREDIT,
    WEB_MERCATOR,
)

# Vector-tile metadata uses a 512 px cell. The raster PNG for that same
# z/x/y index is 256 px, so the ground size of one raster tile is the
# vector cell size and each raster pixel is twice as large.
HK80_ORIGIN_X = -4786700.0
HK80_ORIGIN_Y = 8353100.0
HK80_LEVEL0_RESOLUTION = 78271.516964
VECTOR_TILE_PIXELS = 512
RASTER_TILE_PIXELS = 256
HK80_LEVEL0_SPAN = HK80_LEVEL0_RESOLUTION * VECTOR_TILE_PIXELS
RASTER_TILE_LEVEL = 20

SATELLITE_CREDITS = (
    "Guangdong-Hong Kong-Macao Greater Bay Area Satellite Image 2024 (Satellite Images from Landsat)",
    "Pan-Pearl River Delta Satellite Image 2016 (Satellite Images from Landsat)",
    "Globe Satellite Image 2001 (Satellite Images from MODIS)",
)

HK_EXTENT_2326 = (799174.0, 799838.0, 869854.0, 847618.0)
# Published fullExtent of the HK80 and WGS84 vector tile services.
SERVICE_EXTENT = {
    "HK80": (738917.014349, 747706.779830, 918916.834456, 900486.669346),
    "WGS84": (12605411.265376, 2471486.560629, 12799000.496211, 2637221.483205),
}

BASEMAPS = (
    ("imagery", "Imagery Map", "Aerial photograph and satellite image PNG tiles"),
    ("topographic", "Topographic Map", "Vector map drawn as raster PNG tiles"),
    ("vector", "Vector Map", "Vector tiles with the Lands Department style"),
)

CRS_CHOICES = (
    ("WGS84", f"WGS 84 Web Mercator ({WEB_MERCATOR})", WEB_MERCATOR),
    ("HK80", f"Hong Kong 1980 Grid ({HK1980})", HK1980),
)

LABEL_KINDS = (
    ("raster", "Raster labels"),
    ("vector", "Vector labels"),
)

GEOMETRY_ORDER = ("polygon", "line", "point")
DATASET_GROUP_TITLE = "Hong Kong datasets"

LABEL_LANGUAGES = (
    ("tc", "Traditional Chinese"),
    ("en", "English"),
    ("sc", "Simplified Chinese"),
)


@dataclass(frozen=True)
class Dataset:
    """One CSDI feature layer the dialog can add."""

    id: str
    title: str
    service: str
    crs: str
    geometry: str
    marker: str
    color: str
    name_fields: dict
    address_fields: dict
    district_fields: dict
    attribution: str
    tip_fields: tuple = ()

    @property
    def url(self) -> str:
        return f"{FEATURE_ROOT}/{self.service}/FeatureServer/0"

    @property
    def portal_url(self) -> str:
        return f"{GEOPORTAL}?lang=en&datasetId={self.service}"

    def name_field(self, language: str) -> str:
        return _field(self.name_fields, language)

    def address_field(self, language: str) -> str | None:
        return _optional_field(self.address_fields, language)

    def district_field(self, language: str) -> str | None:
        return _optional_field(self.district_fields, language)


_DATASET_FIELDS = {
    "id",
    "title",
    "service",
    "crs",
    "geometry",
    "marker",
    "color",
    "name_fields",
    "address_fields",
    "district_fields",
}
_DATASET_OPTIONAL = {"tip_fields"}
_FIELD_LANGUAGES = ("en", "tc", "sc")


def load_datasets(path: str | None = None) -> tuple:
    """Read datasets.json and return Dataset objects in file order."""
    path = path or os.path.join(os.path.dirname(__file__), "datasets.json")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("datasets.json must be a list.")
    datasets = tuple(_dataset_from_json(item) for item in payload)
    ids = [dataset.id for dataset in datasets]
    if len(ids) != len(set(ids)):
        raise ValueError("Dataset ids must be unique.")
    return datasets


def _dataset_from_json(item) -> Dataset:
    if not isinstance(item, dict):
        raise ValueError("Each dataset must be an object.")
    unknown = set(item) - _DATASET_FIELDS - _DATASET_OPTIONAL
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown dataset fields: {names}.")
    missing = _DATASET_FIELDS - set(item)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Dataset is missing {names}.")
    crs = _text(item, "crs")
    if crs not in CRS_CODES:
        raise ValueError(f"Unknown dataset CRS {crs!r}.")
    geometry = _text(item, "geometry")
    if geometry not in GEOMETRY_ORDER:
        raise ValueError(f"Unknown geometry {geometry!r}.")
    tip_fields = item.get("tip_fields", [])
    if not isinstance(tip_fields, list) or not all(isinstance(field, str) for field in tip_fields):
        raise ValueError("tip_fields must be a list of field names.")
    return Dataset(
        id=_text(item, "id"),
        title=_text(item, "title"),
        service=_text(item, "service"),
        crs=crs,
        geometry=geometry,
        marker=_text(item, "marker", allow_empty=True),
        color=_text(item, "color"),
        name_fields=_language_fields(item["name_fields"], required=True),
        address_fields=_language_fields(item["address_fields"], required=False),
        district_fields=_language_fields(item["district_fields"], required=False),
        attribution=CSDI_CREDIT,
        tip_fields=tuple(tip_fields),
    )


def _text(item: dict, key: str, allow_empty: bool = False) -> str:
    value = item[key]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"Dataset {key} must be text.")
    return value


def _language_fields(fields, required: bool) -> dict:
    if not isinstance(fields, dict):
        raise ValueError("Field names must be an object.")
    unknown = set(fields) - set(_FIELD_LANGUAGES)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"Unknown field language {names}.")
    if required and any(language not in fields for language in _FIELD_LANGUAGES):
        raise ValueError("Name fields need en, tc, and sc.")
    cleaned = {}
    for language, field in fields.items():
        if not isinstance(field, str) or not field.strip():
            raise ValueError(f"Field name for {language} must be text.")
        cleaned[language] = field
    return cleaned


DATASETS = load_datasets()
_DATASETS_BY_ID = {dataset.id: dataset for dataset in DATASETS}


@dataclass(frozen=True)
class BasemapRequest:
    """Choices collected by the basemap dialog."""

    basemap: str
    crs: str
    label_language: str | None
    label_kind: str | None
    set_project_crs: bool


@dataclass(frozen=True)
class DatasetRequest:
    """Choices collected by the dataset dialog."""

    name_language: str
    dataset_ids: tuple


def dataset_by_id(dataset_id: str) -> Dataset:
    try:
        return _DATASETS_BY_ID[dataset_id]
    except KeyError as exc:
        known = ", ".join(dataset.id for dataset in DATASETS)
        raise ValueError(f"Unknown dataset {dataset_id!r}. Expected {known}.") from exc


def project_crs_authid(crs_key: str) -> str:
    for key, _label, authid in CRS_CHOICES:
        if key == crs_key:
            return authid
    raise ValueError(f"Unknown coordinate system {crs_key!r}.")


def crs_label(crs_key: str) -> str:
    for key, label, _authid in CRS_CHOICES:
        if key == crs_key:
            return label
    raise ValueError(f"Unknown coordinate system {crs_key!r}.")


def raster_zoom(kind: str, crs_key: str) -> tuple:
    """Return inclusive min and max XYZ zoom for a raster service."""
    if kind == "topographic":
        return (10, 20)
    if kind == "imagery":
        if crs_key == "HK80":
            return (7, 20)
        if crs_key == "WGS84":
            return (0, 20)
        raise ValueError(f"Unknown coordinate system {crs_key!r}.")
    if kind == "label":
        if crs_key == "HK80":
            return (8, 20)
        if crs_key == "WGS84":
            return (0, 20)
        raise ValueError(f"Unknown coordinate system {crs_key!r}.")
    raise ValueError(f"Unknown raster kind {kind!r}.")


def raster_url(kind: str, crs_key: str, language: str | None = None) -> str:
    _check_crs(crs_key)
    if kind == "topographic":
        return f"{API_ROOT}/xyz/basemap/{crs_key}/{{z}}/{{x}}/{{y}}.png"
    if kind == "imagery":
        return f"{API_ROOT}/xyz/imagery/{crs_key}/{{z}}/{{x}}/{{y}}.png"
    if kind == "label":
        _check_language(language)
        return f"{API_ROOT}/xyz/label/hk/{language}/{crs_key}/{{z}}/{{x}}/{{y}}.png"
    raise ValueError(f"Unknown raster kind {kind!r}.")


def vector_service_path(kind: str, crs_key: str, language: str | None = None) -> str:
    """Path under the Map API root, used by the local vector-tile proxy."""
    _check_crs(crs_key)
    if kind == "basemap":
        return f"/vt/basemap/{crs_key}"
    if kind == "label":
        _check_language(language)
        return f"/vt/label/hk/{language}/{crs_key}"
    raise ValueError(f"Unknown vector kind {kind!r}.")


def vector_service_url(kind: str, crs_key: str, language: str | None = None) -> str:
    return API_ROOT + vector_service_path(kind, crs_key, language)


def vector_style_url(kind: str, crs_key: str, language: str | None = None) -> str:
    return vector_service_url(kind, crs_key, language) + "/resources/styles/root.json"


def vector_zoom() -> tuple:
    return (9, 15)


def hk80_tile_span(zoom: int) -> float:
    if zoom < 0:
        raise ValueError(f"Zoom must be zero or greater, got {zoom}.")
    return HK80_LEVEL0_SPAN / float(2**zoom)


def hk80_tile_index(easting: float, northing: float, zoom: int) -> tuple:
    """Return the HK80 raster/vector tile column and row for a grid coordinate."""
    span = hk80_tile_span(zoom)
    column = int((easting - HK80_ORIGIN_X) // span)
    row = int((HK80_ORIGIN_Y - northing) // span)
    return (column, row)


def hk80_raster_xml(url: str) -> str:
    """GDAL TMS description for one HK80 raster URL.

    TileLevel 0 is a single tile anchored at the Lands Department origin.
    Higher levels split that extent in half, which matches z/x/y indexes
    such as 11/287/385 for central Hong Kong.
    """
    lower_x = HK80_ORIGIN_X + HK80_LEVEL0_SPAN
    lower_y = HK80_ORIGIN_Y - HK80_LEVEL0_SPAN
    gdal_url = (
        url.replace("{z}", "${z}")
        .replace("{x}", "${x}")
        .replace("{y}", "${y}")
    )
    safe_url = escape(gdal_url, {"'": "&apos;", '"': "&quot;"})
    return (
        "<GDAL_WMS>\n"
        "  <Service name=\"TMS\">\n"
        f"    <ServerUrl>{safe_url}</ServerUrl>\n"
        "  </Service>\n"
        "  <DataWindow>\n"
        f"    <UpperLeftX>{_coord(HK80_ORIGIN_X)}</UpperLeftX>\n"
        f"    <UpperLeftY>{_coord(HK80_ORIGIN_Y)}</UpperLeftY>\n"
        f"    <LowerRightX>{_coord(lower_x)}</LowerRightX>\n"
        f"    <LowerRightY>{_coord(lower_y)}</LowerRightY>\n"
        f"    <TileLevel>{RASTER_TILE_LEVEL}</TileLevel>\n"
        "    <TileCountX>1</TileCountX>\n"
        "    <TileCountY>1</TileCountY>\n"
        "    <YOrigin>top</YOrigin>\n"
        "  </DataWindow>\n"
        f"  <Projection>{HK1980}</Projection>\n"
        f"  <BlockSizeX>{RASTER_TILE_PIXELS}</BlockSizeX>\n"
        f"  <BlockSizeY>{RASTER_TILE_PIXELS}</BlockSizeY>\n"
        "  <BandsCount>4</BandsCount>\n"
        "  <DataType>Byte</DataType>\n"
        "  <ZeroBlockHttpCodes>204,404</ZeroBlockHttpCodes>\n"
        "  <MaxConnections>4</MaxConnections>\n"
        "</GDAL_WMS>\n"
    )


def grid_xml_name(kind: str, language: str | None = None) -> str:
    if kind == "label":
        _check_language(language)
        return f"hk80_label_{language}.xml"
    if kind in ("topographic", "imagery"):
        return f"hk80_{kind}.xml"
    raise ValueError(f"Unknown HK80 raster kind {kind!r}.")


def write_grid_files(directory: str) -> dict:
    """Write the HK80 GDAL XML files and return their paths by key."""
    os.makedirs(directory, exist_ok=True)
    written = {}
    jobs = (("topographic", None), ("imagery", None))
    jobs += tuple(("label", language) for language, _label in LABEL_LANGUAGES)
    for kind, language in jobs:
        filename = grid_xml_name(kind, language)
        path = os.path.join(directory, filename)
        url = raster_url(kind, "HK80", language)
        text = hk80_raster_xml(url)
        if not _grid_file_current(path, text):
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        key = filename[:-4]
        written[key] = path
    return written


def _grid_file_current(path: str, text: str) -> bool:
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read() == text


def basemap_credits(kind: str) -> tuple:
    if kind == "imagery":
        return (MAP_CREDIT, AERIAL_CREDIT) + SATELLITE_CREDITS
    if kind in ("topographic", "vector"):
        return (MAP_CREDIT,)
    raise ValueError(f"Unknown basemap {kind!r}.")


def language_name(language: str) -> str:
    for key, label in LABEL_LANGUAGES:
        if key == language:
            return label
    raise ValueError(f"Unknown label language {language!r}.")


def label_kind_name(kind: str) -> str:
    for key, label in LABEL_KINDS:
        if key == kind:
            return label
    raise ValueError(f"Unknown label type {kind!r}.")


def default_label_kind(basemap: str) -> str:
    """Raster labels follow the PNG basemaps. Vector labels follow the vector map."""
    if basemap in ("imagery", "topographic"):
        return "raster"
    if basemap == "vector":
        return "vector"
    raise ValueError(f"Unknown basemap {basemap!r}.")


def dataset_draw_rank(geometry: str) -> int:
    try:
        return GEOMETRY_ORDER.index(geometry)
    except ValueError as exc:
        raise ValueError(f"Unknown geometry {geometry!r}.") from exc


def datasets_for_dialog() -> tuple:
    """Return datasets in the order shown in the dataset dialog."""
    return tuple(sorted(DATASETS, key=lambda dataset: dataset.title.casefold()))


def datasets_bottom_to_top(dataset_ids) -> tuple:
    """Return datasets from the bottom of the draw order to the top.

    Polygons are under lines, and lines are under points. Within one
    geometry, a later catalog entry draws above an earlier one.
    """
    catalog_index = {dataset.id: index for index, dataset in enumerate(DATASETS)}
    chosen = [dataset_by_id(dataset_id) for dataset_id in dataset_ids]
    chosen.sort(key=lambda dataset: (dataset_draw_rank(dataset.geometry), catalog_index[dataset.id]))
    return tuple(chosen)


def group_title(request: BasemapRequest) -> str:
    crs_name = "HK1980" if request.crs == "HK80" else "WGS84"
    if request.crs not in ("HK80", "WGS84"):
        raise ValueError(f"Unknown coordinate system {request.crs!r}.")
    names = {key: label for key, label, _tip in BASEMAPS}
    try:
        basemap_name = names[request.basemap]
    except KeyError as exc:
        raise ValueError(f"Unknown basemap {request.basemap!r}.") from exc
    if request.label_language:
        kind_name = label_kind_name(request.label_kind)
        return (
            f"Hong Kong — {crs_name} — {basemap_name} — "
            f"{kind_name} — {language_name(request.label_language)}"
        )
    return f"Hong Kong — {crs_name} — {basemap_name}"


def _check_crs(crs_key: str) -> None:
    if crs_key not in ("HK80", "WGS84"):
        raise ValueError(f"Unknown coordinate system {crs_key!r}.")


def _check_language(language: str | None) -> None:
    if language not in ("en", "tc", "sc"):
        raise ValueError(f"Unknown label language {language!r}.")


def _field(fields: dict, language: str) -> str:
    _check_language(language)
    try:
        return fields[language]
    except KeyError as exc:
        raise ValueError(f"No name field for language {language!r}.") from exc


def _optional_field(fields: dict, language: str) -> str | None:
    _check_language(language)
    return fields.get(language)


def _coord(value: float) -> str:
    return f"{value:.6f}"
