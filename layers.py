"""Create Hong Kong basemap, label, and dataset layers."""

from __future__ import annotations

import os

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsCsException,
    QgsDataSourceUri,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsVectorTileLayer,
)
from qgis.PyQt.QtGui import QColor, QFont

from .config import HK1980, WEB_MERCATOR
from .catalog import (
    CSDI_CREDIT,
    DATASET_GROUP_TITLE,
    HK_EXTENT_2326,
    MAP_CREDIT,
    SERVICE_EXTENT,
    BasemapRequest,
    basemap_credits,
    dataset_by_id,
    datasets_bottom_to_top,
    group_title,
    grid_xml_name,
    label_kind_name,
    language_name,
    project_crs_authid,
    raster_url,
    raster_zoom,
    vector_service_path,
    vector_style_url,
    vector_zoom,
    write_grid_files,
)
from .vectorproxy import proxy_base_url, stop_proxy


def grid_directory() -> str:
    path = os.path.join(QgsApplication.qgisSettingsDirPath(), "hkmapservices")
    os.makedirs(path, exist_ok=True)
    return path


def repair_grid_sources() -> None:
    """Point saved HK80 layers at the current XML. Start the proxy only for vector tiles."""
    directory = grid_directory()
    paths = write_grid_files(directory)
    project = QgsProject.instance()
    layers = list(project.mapLayers().values())
    base_url = ""
    if any(layer.customProperty("hkmapservices/vector_path") for layer in layers):
        base_url = proxy_base_url()
    for layer in layers:
        key = layer.customProperty("hkmapservices/grid_xml")
        if key:
            path = paths.get(str(key))
            if not path:
                path = os.path.join(directory, grid_xml_name_from_key(str(key)))
            if os.path.normcase(layer.source()) != os.path.normcase(path):
                layer.setDataSource(path, layer.name(), "gdal")
            continue
        service_path = layer.customProperty("hkmapservices/vector_path")
        if service_path and base_url and base_url not in str(layer.source()):
            style_url = str(layer.customProperty("hkmapservices/vector_style") or "")
            uri = _vector_tile_uri(base_url + str(service_path), style_url)
            layer.setDataSource(uri, layer.name(), "arcgisvectortileservice")


def shutdown_background_services() -> None:
    stop_proxy()


def add_basemap(iface, request: BasemapRequest) -> bool:
    """Add one basemap group, with optional place-name labels and no datasets."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    group = root.insertGroup(0, group_title(request))
    errors = []
    added = []

    basemap = _make_basemap(request)
    if basemap.isValid():
        added.append(basemap)
    else:
        errors.append(f"{basemap.name()}: {_layer_error(basemap)}")

    if request.label_language:
        labels = _make_labels(request)
        if labels.isValid():
            added.append(labels)
        else:
            errors.append(f"{labels.name()}: {_layer_error(labels)}")

    if not added:
        root.removeChildNode(group)
        _report(iface, added, errors, "")
        return False

    _commit_group(project, group, added)
    if request.set_project_crs:
        project.setCrs(QgsCoordinateReferenceSystem(project_crs_authid(request.crs)))
    _zoom_to_hong_kong(iface)
    _report(iface, added, errors, group.name())
    return True


def add_datasets(iface, request) -> bool:
    """Add the checked datasets in their own group, without a basemap."""
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    chosen = datasets_bottom_to_top(request.dataset_ids)
    if not chosen:
        _report(iface, [], ["Select at least one dataset."], "")
        return False

    group = root.insertGroup(0, DATASET_GROUP_TITLE)
    errors = []
    added = []
    for dataset in chosen:
        layer = _make_dataset(dataset, request.name_language)
        if layer.isValid():
            added.append(layer)
        else:
            errors.append(f"{layer.name()}: {_layer_error(layer)}")

    if not added:
        root.removeChildNode(group)
        _report(iface, added, errors, "")
        return False

    _commit_group(project, group, added)
    _report(iface, added, errors, group.name())
    return True


def _commit_group(project, group, layers) -> None:
    for layer in layers:
        project.addMapLayer(layer, False)
        group.insertLayer(0, layer)
    group.setExpanded(True)


def make_basemap_layer(kind: str, crs_key: str, grid_dir: str | None = None):
    """Build one basemap layer without adding it to the project."""
    request = BasemapRequest(kind, crs_key, None, None, False)
    if grid_dir is None and crs_key == "HK80" and kind != "vector":
        grid_dir = grid_directory()
    return _make_basemap(request, grid_dir)


def make_label_layer(label_kind: str, crs_key: str, language: str = "tc", grid_dir: str | None = None):
    """Build one label layer without adding it to the project."""
    basemap = "vector" if label_kind == "vector" else "imagery"
    request = BasemapRequest(basemap, crs_key, language, label_kind, False)
    if grid_dir is None and crs_key == "HK80" and label_kind == "raster":
        grid_dir = grid_directory()
    return _make_labels(request, grid_dir)


def make_dataset_layer(dataset_id: str, language: str = "tc"):
    """Build one dataset layer without adding it to the project."""
    return _make_dataset(dataset_by_id(dataset_id), language)


def _make_basemap(request: BasemapRequest, grid_dir: str | None = None):
    title = _basemap_title(request.basemap, request.crs)
    if request.basemap == "vector":
        layer = _vector_tile_layer(
            "basemap",
            request.crs,
            None,
            title,
        )
        _set_credits(layer, basemap_credits("vector"))
    elif request.basemap in ("imagery", "topographic"):
        layer = _raster_layer(request.basemap, request.crs, None, title, grid_dir)
        _set_credits(layer, basemap_credits(request.basemap))
    else:
        raise ValueError(f"Unknown basemap {request.basemap!r}.")
    layer.setCustomProperty("hkmapservices/role", "basemap")
    _allow_overzoom(layer)
    return layer


def _make_labels(request: BasemapRequest, grid_dir: str | None = None):
    language = request.label_language
    kind = request.label_kind
    title = (
        f"Hong Kong Labels — {label_kind_name(kind)} — "
        f"{language_name(language)} ({_crs_short(request.crs)})"
    )
    if kind == "vector":
        layer = _vector_tile_layer("label", request.crs, language, title)
    elif kind == "raster":
        layer = _raster_layer("label", request.crs, language, title, grid_dir)
    else:
        raise ValueError(f"Unknown label type {kind!r}.")
    _set_credits(layer, (MAP_CREDIT,))
    layer.setCustomProperty("hkmapservices/role", "label")
    _allow_overzoom(layer)
    return layer


def _allow_overzoom(layer) -> None:
    """Keep the last published tiles visible when the canvas zooms in closer."""
    layer.setScaleBasedVisibility(False)


def _raster_layer(kind: str, crs_key: str, language: str | None, title: str, grid_dir: str | None):
    if crs_key == "WGS84":
        zmin, zmax = raster_zoom(kind, crs_key)
        uri = _xyz_uri(raster_url(kind, crs_key, language), zmin, zmax, WEB_MERCATOR)
        layer = QgsRasterLayer(uri, title, "wms")
        _limit_extent(layer, crs_key)
        return layer

    if crs_key != "HK80":
        raise ValueError(f"Unknown coordinate system {crs_key!r}.")

    directory = grid_dir or grid_directory()
    paths = write_grid_files(directory)
    filename = grid_xml_name(kind, language)
    path = paths[filename[:-4]]
    layer = QgsRasterLayer(path, title, "gdal")
    layer.setCustomProperty("hkmapservices/grid_xml", filename[:-4])
    _limit_extent(layer, crs_key)
    return layer


def _vector_tile_layer(kind: str, crs_key: str, language: str | None, title: str):
    service_path = vector_service_path(kind, crs_key, language)
    style_url = vector_style_url(kind, crs_key, language)
    layer = _open_vector_tile(service_path, style_url, title)
    if not layer.isValid():
        layer = _open_vector_tile(service_path, style_url, title)
    return layer


def _open_vector_tile(service_path: str, style_url: str, title: str):
    layer = QgsVectorTileLayer(
        _vector_tile_uri(proxy_base_url() + service_path, style_url),
        title,
    )
    layer.setCustomProperty("hkmapservices/vector_path", service_path)
    layer.setCustomProperty("hkmapservices/vector_style", style_url)
    if layer.isValid():
        _load_vector_style(layer)
    return layer


def _vector_tile_uri(service_url: str, style_url: str) -> str:
    zmin, zmax = vector_zoom()
    uri = QgsDataSourceUri()
    uri.setParam("type", "xyz")
    uri.setParam("serviceType", "arcgis")
    uri.setParam("url", service_url)
    uri.setParam("styleUrl", style_url)
    uri.setParam("zmin", str(zmin))
    uri.setParam("zmax", str(zmax))
    return _as_text(uri.encodedUri())


def _make_dataset(dataset, language: str):
    layer = _open_dataset(dataset, language)
    if not layer.isValid():
        layer = _open_dataset(dataset, language)
    return layer


def _open_dataset(dataset, language: str):
    uri = QgsDataSourceUri()
    uri.setParam("url", dataset.url)
    uri.setParam("crs", dataset.crs)
    layer = QgsVectorLayer(_vector_uri(uri), dataset.title, "arcgisfeatureserver")
    layer.setCustomProperty("hkmapservices/role", "dataset")
    layer.setCustomProperty("hkmapservices/dataset", dataset.id)
    if not layer.isValid():
        return layer
    _style_dataset(layer, dataset, language)
    _set_credits(layer, (CSDI_CREDIT,))
    return layer


def _style_dataset(layer, dataset, language: str) -> None:
    name_field = dataset.name_field(language)
    if dataset.geometry == "polygon":
        symbol = QgsFillSymbol.createSimple(
            {
                "color": "0,0,0,0",
                "outline_color": dataset.color,
                "outline_width": "0.7",
                "style": "no",
                "outline_style": "solid",
            }
        )
    elif dataset.geometry == "line":
        symbol = QgsLineSymbol.createSimple(
            {
                "color": dataset.color,
                "width": "0.7",
            }
        )
    elif dataset.geometry == "point":
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": dataset.marker,
                "color": dataset.color,
                "outline_color": "0,0,0,255",
                "outline_width": "0.3",
                "size": "3.2",
            }
        )
    else:
        raise ValueError(f"Unknown geometry {dataset.geometry!r}.")
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _label_layer(layer, name_field, dataset.geometry)
    _set_map_tip(layer, dataset, language, name_field)


def _label_layer(layer, field_name: str, geometry: str) -> None:
    settings = QgsPalLayerSettings()
    if hasattr(settings, "setFieldName"):
        settings.setFieldName(field_name)
    else:
        settings.fieldName = field_name
    if hasattr(settings, "setIsExpression"):
        settings.setIsExpression(False)
    else:
        settings.isExpression = False
    settings.placement = _label_placement(geometry)

    text_format = QgsTextFormat()
    font = QFont("Microsoft JhengHei")
    font.setPointSize(9)
    text_format.setFont(font)
    text_format.setSize(9)
    text_format.setColor(QColor(32, 32, 32))
    buffer_settings = QgsTextBufferSettings()
    buffer_settings.setEnabled(True)
    buffer_settings.setSize(0.8)
    buffer_settings.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer_settings)
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


def _label_placement(geometry: str):
    if geometry == "point":
        names = ("AroundPoint",)
    elif geometry == "line":
        names = ("Line", "Horizontal")
    elif geometry == "polygon":
        names = ("Horizontal",)
    else:
        raise ValueError(f"Unknown geometry {geometry!r}.")
    for name in names:
        if hasattr(QgsPalLayerSettings, name):
            return getattr(QgsPalLayerSettings, name)
        placement_enum = getattr(QgsPalLayerSettings, "Placement", None)
        if placement_enum is not None and hasattr(placement_enum, name):
            return getattr(placement_enum, name)
        label_placement = getattr(Qgis, "LabelPlacement", None)
        if label_placement is not None and hasattr(label_placement, name):
            return getattr(label_placement, name)
    raise RuntimeError(f"No label placement named {names[0]}.")


def _set_map_tip(layer, dataset, language: str, name_field: str) -> None:
    lines = [f'<b>[% "{name_field}" %]</b>']
    address = dataset.address_field(language)
    district = dataset.district_field(language)
    if address:
        lines.append(f'[% "{address}" %]')
    if district:
        lines.append(f'[% "{district}" %]')
    for field_name in dataset.tip_fields:
        lines.append(f'[% "{field_name}" %]')
    html = "<br/>".join(lines)
    if hasattr(layer, "setMapTipTemplate"):
        layer.setMapTipTemplate(html)
    layer.setDisplayExpression(f'"{name_field}"')


def _set_credits(layer, lines: tuple) -> None:
    metadata = layer.metadata()
    metadata.setRights(list(lines))
    metadata.setAbstract(" ".join(lines))
    layer.setMetadata(metadata)


def _limit_extent(layer, crs_key: str) -> None:
    if not hasattr(layer, "setExtent"):
        return
    layer.setExtent(QgsRectangle(*SERVICE_EXTENT[crs_key]))


def _zoom_to_hong_kong(iface) -> None:
    if iface is None:
        return
    project = QgsProject.instance()
    rectangle = QgsRectangle(*HK_EXTENT_2326)
    transform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem(HK1980),
        project.crs(),
        project,
    )
    try:
        rectangle = transform.transformBoundingBox(rectangle)
    except QgsCsException:
        return
    iface.mapCanvas().setExtent(rectangle)
    iface.mapCanvas().refresh()


def _report(iface, added, errors, group_name: str) -> None:
    if iface is None:
        return
    bar = iface.messageBar()
    if errors and added:
        failed = "; ".join(errors)
        bar.pushWarning(
            "Hong Kong Map Services",
            f"Added {group_name}. Could not add {failed}",
        )
        return
    if errors:
        bar.pushCritical("Hong Kong Map Services", "; ".join(errors))
        return
    bar.pushSuccess("Hong Kong Map Services", f"Added {group_name}")


def _xyz_uri(url: str, zmin: int, zmax: int, crs_authid: str) -> str:
    encoded = url.replace("%", "%25").replace("&", "%26").replace("=", "%3D")
    return f"type=xyz&url={encoded}&zmin={zmin}&zmax={zmax}&crs={crs_authid}"


def _vector_uri(uri: QgsDataSourceUri) -> str:
    text = uri.uri(False)
    if isinstance(text, str):
        return text
    return _as_text(text)


def _as_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return bytes(value).decode("utf-8")


def _load_vector_style(layer) -> None:
    try:
        layer.loadDefaultStyle()
    except Exception:
        # The tile layer remains usable if the style document cannot be read.
        return


def _layer_error(layer) -> str:
    error = layer.error()
    summary = error.summary() if error is not None else ""
    return summary or "layer is not valid"


def _basemap_title(kind: str, crs_key: str) -> str:
    names = {
        "topographic": "Hong Kong Topographic",
        "imagery": "Hong Kong Imagery",
        "vector": "Hong Kong Vector Map",
    }
    try:
        name = names[kind]
    except KeyError as exc:
        raise ValueError(f"Unknown basemap {kind!r}.") from exc
    return f"{name} ({_crs_short(crs_key)})"


def _crs_short(crs_key: str) -> str:
    if crs_key == "HK80":
        return "HK1980"
    if crs_key == "WGS84":
        return "WGS84"
    raise ValueError(f"Unknown coordinate system {crs_key!r}.")


def grid_xml_name_from_key(key: str) -> str:
    return f"{key}.xml"
