"""Layer and group choices shared by the swipe and split views."""

from __future__ import annotations

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsProject

from .comparemath import clamp_opacity, clamp_position, normalize_direction, normalize_opacity_side


class CompareSettings:
    """The choices currently shown in a compare dock."""

    def __init__(
        self,
        left_key: str = "",
        right_key: str = "",
        direction: str = "vertical",
        position: int = 50,
        opacity: float = 1.0,
        opacity_side: str = "both",
    ):
        self.left_key = left_key
        self.right_key = right_key
        self.direction = normalize_direction(direction)
        self.position = clamp_position(position)
        self.opacity = clamp_opacity(opacity)
        self.opacity_side = normalize_opacity_side(opacity_side)

    def copy(self) -> "CompareSettings":
        return CompareSettings(
            self.left_key,
            self.right_key,
            self.direction,
            self.position,
            self.opacity,
            self.opacity_side,
        )


class MapSide:
    """One layer, or every layer in one group, in legend order."""

    def __init__(self, key: str, label: str, layer_ids: list):
        self.key = key
        self.label = label
        self.layer_ids = list(layer_ids)

    def layers(self, project=None) -> list:
        project = project or QgsProject.instance()
        found = []
        for layer_id in self.layer_ids:
            layer = project.mapLayer(layer_id)
            if layer is not None and _layer_alive(layer):
                found.append(layer)
        return found


class OpacityGuard:
    """Apply one opacity, and put the previous values back later."""

    def __init__(self):
        self._saved = {}

    def apply(self, layers, opacity: float) -> None:
        self.restore()
        opacity = clamp_opacity(opacity)
        seen = set()
        for layer in layers:
            if not _layer_alive(layer) or layer.id() in seen:
                continue
            seen.add(layer.id())
            self._saved[layer.id()] = layer.opacity()
            layer.setOpacity(opacity)
            layer.triggerRepaint()

    def restore(self) -> None:
        project = QgsProject.instance()
        for layer_id, opacity in self._saved.items():
            layer = project.mapLayer(layer_id)
            if layer is not None and _layer_alive(layer):
                layer.setOpacity(opacity)
                layer.triggerRepaint()
        self._saved = {}


class CanvasOverride:
    """Temporarily choose which layers the main map draws, then follow the legend again."""

    def __init__(self, iface):
        self._iface = iface
        self._saved = None
        self._wanted = []
        self._watching = False
        self._applying = False

    def show(self, layers) -> None:
        """Draw only these layers on the main map until restore."""
        if not self._begin():
            return
        self._wanted = _live_layers(layers)
        self._push()

    def show_without(self, removed) -> None:
        """Keep the map as it was, except hide these layers. The compare view draws them."""
        if not self._begin():
            return
        removed_ids = {layer.id() for layer in removed if _layer_alive(layer)}
        self._wanted = [layer for layer in self._saved if layer.id() not in removed_ids]
        self._push()

    def _begin(self) -> bool:
        canvas = self._canvas()
        if canvas is None:
            return False
        if self._saved is None:
            self._saved = _live_layers(canvas.layers())
            self._watch(True)
        return True

    def restore(self) -> None:
        self._watch(False)
        self._wanted = []
        canvas = self._canvas()
        saved = self._saved
        self._saved = None
        if canvas is None:
            return
        bridge = _canvas_bridge(self._iface)
        if bridge is not None and hasattr(bridge, "setCanvasLayers"):
            bridge.setCanvasLayers()
        elif saved is not None:
            canvas.setLayers(_live_layers(saved))
        canvas.refresh()

    def _keep(self, *_args) -> None:
        if self._applying or not self._wanted:
            return
        self._push()

    def _push(self) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        self._applying = True
        try:
            canvas.setLayers(_live_layers(self._wanted))
            canvas.refresh()
        finally:
            self._applying = False

    def _watch(self, enabled: bool) -> None:
        root = QgsProject.instance().layerTreeRoot()
        if enabled and not self._watching:
            root.visibilityChanged.connect(self._keep)
            root.layerOrderChanged.connect(self._keep)
            self._watching = True
            return
        if not enabled and self._watching:
            _disconnect(root.visibilityChanged, self._keep)
            _disconnect(root.layerOrderChanged, self._keep)
            self._watching = False

    def _canvas(self):
        canvas = self._iface.mapCanvas() if self._iface is not None else None
        return canvas


class CompareView:
    """Operations both compare docks call. Subclasses draw the maps."""

    def __init__(self, iface):
        self.iface = iface
        self._settings = CompareSettings()
        self._active = False
        self._opacity = OpacityGuard()
        self._position_listener = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def settings(self) -> CompareSettings:
        return self._settings

    def set_position_listener(self, listener) -> None:
        self._position_listener = listener

    def start(self, settings: CompareSettings) -> bool:
        self._settings = self._accepted(settings)
        if not self._ready():
            if self._active:
                self.stop()
            return False
        self._active = True
        self._show()
        return self._active

    def stop(self) -> None:
        if not self._active:
            self._opacity.restore()
            return
        self._active = False
        self._opacity.restore()
        self._hide()

    def set_direction(self, direction: str) -> None:
        self._settings.direction = normalize_direction(direction)
        if self._active:
            self._show()

    def set_position(self, position: int) -> None:
        self._settings.position = clamp_position(position)
        self._notify_position()
        if self._active:
            self._on_position()

    def swap(self) -> None:
        self._settings.left_key, self._settings.right_key = self._settings.right_key, self._settings.left_key
        if self._active:
            self._show()

    def set_opacity(self, opacity: float) -> None:
        self._settings.opacity = clamp_opacity(opacity)
        if self._active:
            self._apply_opacity()
            self._on_opacity()

    def set_opacity_side(self, side: str) -> None:
        self._settings.opacity_side = normalize_opacity_side(side)
        if self._active:
            self._apply_opacity()
            self._on_opacity()

    def reload(self) -> bool:
        if not self._active:
            return True
        return self.start(self._settings)

    def _accepted(self, settings: CompareSettings) -> CompareSettings:
        return settings.copy()

    def _ready(self) -> bool:
        return bool(self._layers(self._settings.left_key) and self._layers(self._settings.right_key))

    def _layers(self, key: str) -> list:
        for side in collect_sides():
            if side.key == key:
                return side.layers()
        return []

    def _compared_layers(self) -> list:
        return _unique(self._layers(self._settings.left_key) + self._layers(self._settings.right_key))

    def _opacity_layers(self) -> list:
        side = self._settings.opacity_side
        chosen = []
        if side == "left" or side == "both":
            chosen.extend(self._layers(self._settings.left_key))
        if side == "right" or side == "both":
            chosen.extend(self._layers(self._settings.right_key))
        if side != "left" and side != "right" and side != "both":
            raise ValueError(f"Unknown opacity side {side!r}.")
        return _unique(chosen)

    def _apply_opacity(self) -> None:
        self._opacity.apply(self._opacity_layers(), self._settings.opacity)

    def _notify_position(self) -> None:
        if self._position_listener is not None:
            self._position_listener(self._settings.position)

    def _show(self) -> None:
        raise NotImplementedError

    def _hide(self) -> None:
        raise NotImplementedError

    def _on_position(self) -> None:
        self._show()

    def _on_opacity(self) -> None:
        return None


def collect_sides(project=None) -> list:
    """List layers and groups from the top of the legend downward."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    sides = []

    def walk(node, prefix: str) -> None:
        for child in node.children():
            if isinstance(child, QgsLayerTreeGroup):
                layer_ids = _group_layer_ids(child)
                if layer_ids:
                    sides.append(MapSide(_group_key(child, root), f"Group: {prefix}{child.name()}", layer_ids))
                walk(child, prefix + child.name() + " / ")
            elif isinstance(child, QgsLayerTreeLayer):
                layer = child.layer()
                if layer is None or not _layer_alive(layer):
                    continue
                sides.append(MapSide(f"layer:{layer.id()}", prefix + layer.name(), [layer.id()]))

    walk(root, "")
    return sides


def _group_layer_ids(group) -> list:
    layer_ids = []
    for node in group.findLayers():
        layer = node.layer()
        if layer is not None and _layer_alive(layer):
            layer_ids.append(layer.id())
    return layer_ids


def _group_key(group, root) -> str:
    names = []
    node = group
    while node is not None and node != root:
        names.append(node.name())
        node = node.parent()
    names.reverse()
    return "group:" + "/".join(names)


def _canvas_bridge(iface):
    finder = getattr(iface, "layerTreeCanvasBridge", None)
    if not callable(finder):
        return None
    return finder()


def _live_layers(layers) -> list:
    return [layer for layer in layers if _layer_alive(layer)]


def _unique(layers) -> list:
    found = []
    seen = set()
    for layer in layers:
        if not _layer_alive(layer) or layer.id() in seen:
            continue
        seen.add(layer.id())
        found.append(layer)
    return found


def _layer_alive(layer) -> bool:
    try:
        return layer is not None and layer.isValid()
    except RuntimeError:
        return False


def _disconnect(signal, slot) -> None:
    try:
        signal.disconnect(slot)
    except TypeError:
        return
