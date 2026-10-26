"""QGIS plugin entry point for Hong Kong Map Services."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget, QMenu, QToolButton
from qgis.core import QgsProject

from .aboutdialog import AboutDialog
from .comparedock import CompareDock
from .convertdialog import ConvertDialog
from .cursordock import CursorDock
from .dialog import BasemapDialog, DatasetDialog
from .locationdialog import LocationDialog
from .layers import add_basemap, add_datasets, repair_grid_sources, shutdown_background_services
from .splitview import SplitView
from .swipeview import SwipeView


class HkMapServices:
    """Toolbar menu and Web submenu for basemaps and datasets."""

    def __init__(self, iface):
        self.iface = iface
        self.basemap_action = None
        self.dataset_action = None
        self.location_action = None
        self.convert_action = None
        self.cursor_action = None
        self.swipe_action = None
        self.about_action = None
        self._about_separator = None
        self.hold_action = None
        self.tool_button = None
        self.toolbar_action = None
        self.dialog = None
        self.cursor_dock = None
        self.swipe_view = None
        self.split_view = None
        self.compare_dock = None

    def initGui(self) -> None:
        icon = _load_icon("icon.png")
        window = self.iface.mainWindow()
        self.basemap_action = QAction(_load_icon("icon_basemap.png"), "Basemap", window)
        self.basemap_action.setObjectName("hkmapservicesBasemap")
        self.basemap_action.triggered.connect(self.run_basemap)
        self.dataset_action = QAction(_load_icon("icon_datasets.png"), "Datasets", window)
        self.dataset_action.setObjectName("hkmapservicesDatasets")
        self.dataset_action.triggered.connect(self.run_datasets)
        self.location_action = QAction(_load_icon("icon_location.png"), "Location Search", window)
        self.location_action.setObjectName("hkmapservicesLocation")
        self.location_action.triggered.connect(self.run_location)
        self.swipe_action = QAction(_load_icon("icon_swipe.png"), "Swipe and Split", window)
        self.swipe_action.setObjectName("hkmapservicesSwipe")
        self.swipe_action.triggered.connect(self.run_swipe)
        self.convert_action = QAction(_load_icon("icon_convert.png"), "Convert Coordinates", window)
        self.convert_action.setObjectName("hkmapservicesConvert")
        self.convert_action.triggered.connect(self.run_convert)
        self.cursor_action = QAction("Cursor Coordinates", window)
        self.cursor_action.setObjectName("hkmapservicesCursor")
        self.cursor_action.setCheckable(True)
        self.cursor_action.triggered.connect(self.toggle_cursor)
        self.about_action = QAction(_load_icon("icon_about.png"), "About", window)
        self.about_action.setObjectName("hkmapservicesAbout")
        self.about_action.triggered.connect(self.run_about)
        self.hold_action = QAction("Hold (Ctrl+Shift+H)", window)
        self.hold_action.setObjectName("hkmapservicesHold")
        self.hold_action.setCheckable(True)
        self.iface.registerMainWindowAction(self.hold_action, "Ctrl+Shift+H")
        self.hold_action.toggled.connect(self._on_hold_toggled)

        for action in self._ordered_actions():
            self.iface.addPluginToWebMenu("&Hong Kong Map Services", action)
        _set_web_menu_icon(self.iface, icon)
        self._add_about_to_web_menu()

        self.tool_button = QToolButton(window)
        self.tool_button.setIcon(icon)
        self.tool_button.setToolTip("Hong Kong Map Services")
        self.tool_button.setPopupMode(_popup_mode("InstantPopup"))
        menu = QMenu(self.tool_button)
        for action in self._ordered_actions():
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self.about_action)
        self.tool_button.setMenu(menu)
        self.toolbar_action = self.iface.addToolBarWidget(self.tool_button)

        QgsProject.instance().readProject.connect(self._on_project_read)
        repair_grid_sources()

    def unload(self) -> None:
        project = QgsProject.instance()
        try:
            project.readProject.disconnect(self._on_project_read)
        except TypeError:
            pass
        for action in self._ordered_actions():
            if action is not None:
                self.iface.removePluginWebMenu("&Hong Kong Map Services", action)
        self.basemap_action = None
        self.dataset_action = None
        self.location_action = None
        self.convert_action = None
        self.cursor_action = None
        self.swipe_action = None
        self._remove_about_from_web_menu()
        self.about_action = None
        if self.hold_action is not None:
            try:
                self.hold_action.toggled.disconnect(self._on_hold_toggled)
            except TypeError:
                pass
            self.iface.unregisterMainWindowAction(self.hold_action)
            self.hold_action = None
        if self.compare_dock is not None:
            self.compare_dock.shutdown()
            self.iface.removeDockWidget(self.compare_dock)
            self.compare_dock = None
            self.swipe_view = None
            self.split_view = None
        if self.cursor_dock is not None:
            try:
                self.cursor_dock.visibilityChanged.disconnect(self._sync_cursor_action)
            except TypeError:
                pass
            self.cursor_dock.unload()
            self.iface.removeDockWidget(self.cursor_dock)
            self.cursor_dock = None
        if self.toolbar_action is not None:
            self.iface.removeToolBarIcon(self.toolbar_action)
            self.toolbar_action = None
        self.tool_button = None
        self.dialog = None
        shutdown_background_services()

    def run_basemap(self) -> None:
        self.dialog = BasemapDialog(self.iface.mainWindow())
        if _accepted(self.dialog):
            add_basemap(self.iface, self.dialog.request())

    def run_datasets(self) -> None:
        self.dialog = DatasetDialog(self.iface.mainWindow())
        if _accepted(self.dialog):
            add_datasets(self.iface, self.dialog.request())

    def run_convert(self) -> None:
        self.dialog = ConvertDialog(self.iface.mainWindow())
        self.dialog.show()

    def run_location(self) -> None:
        self.dialog = LocationDialog(self.iface)
        self.dialog.show()

    def run_about(self) -> None:
        self.dialog = AboutDialog(self.iface.mainWindow())
        _accepted(self.dialog)

    def run_swipe(self) -> None:
        self._ensure_compare()
        self.compare_dock.show()
        self._place_compare_dock()
        self.compare_dock.raise_()

    def toggle_cursor(self, checked: bool) -> None:
        self._ensure_cursor_dock()
        self.cursor_dock.setVisible(bool(checked))

    def _on_hold_toggled(self, _held: bool) -> None:
        self._ensure_cursor_dock()
        self.cursor_dock.show()

    def _ensure_compare(self) -> None:
        if self.compare_dock is not None:
            return
        window = self.iface.mainWindow()
        self.swipe_view = SwipeView(self.iface)
        self.split_view = SplitView(self.iface)
        self.compare_dock = CompareDock(self.swipe_view, self.split_view, window)
        self.swipe_view.set_position_listener(self.compare_dock.show_position)
        self.split_view.set_position_listener(self.compare_dock.show_position)
        self.iface.addDockWidget(_dock_area("RightDockWidgetArea"), self.compare_dock)
        self.compare_dock.setVisible(False)

    def _place_compare_dock(self) -> None:
        """Keep Swipe and Split at the top of the right-hand dock area."""
        window = self.iface.mainWindow()
        dock = self.compare_dock
        if window is None or dock is None or not hasattr(window, "splitDockWidget"):
            return
        area = _dock_area("RightDockWidgetArea")
        others = [
            child
            for child in window.findChildren(QDockWidget)
            if child is not dock and child.isVisible() and window.dockWidgetArea(child) == area
        ]
        if not others:
            return
        window.splitDockWidget(dock, others[0], _orientation("Vertical"))

    def _ensure_cursor_dock(self) -> None:
        if self.cursor_dock is not None:
            return
        self.cursor_dock = CursorDock(self.iface)
        self.cursor_dock.bind_hold(self.hold_action)
        self.iface.addDockWidget(_dock_area("RightDockWidgetArea"), self.cursor_dock)
        self.cursor_dock.visibilityChanged.connect(self._sync_cursor_action)

    def _ordered_actions(self) -> tuple:
        return (
            self.basemap_action,
            self.dataset_action,
            self.location_action,
            self.swipe_action,
            self.convert_action,
            self.cursor_action,
        )

    def _sync_cursor_action(self, visible: bool) -> None:
        if self.cursor_action is None:
            return
        self.cursor_action.blockSignals(True)
        self.cursor_action.setChecked(bool(visible))
        self.cursor_action.blockSignals(False)

    def _add_about_to_web_menu(self) -> None:
        menu = _plugin_submenu(self.iface)
        if menu is None:
            self.iface.addPluginToWebMenu("&Hong Kong Map Services", self.about_action)
            return
        self._about_separator = menu.addSeparator()
        menu.addAction(self.about_action)

    def _remove_about_from_web_menu(self) -> None:
        menu = _plugin_submenu(self.iface)
        if menu is not None and self._about_separator is not None:
            menu.removeAction(self._about_separator)
        self._about_separator = None
        if self.about_action is not None:
            self.iface.removePluginWebMenu("&Hong Kong Map Services", self.about_action)

    def _on_project_read(self, *_args) -> None:
        repair_grid_sources()


def _accepted(dialog) -> bool:
    result = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
    return bool(result)


def _popup_mode(name: str):
    if hasattr(QToolButton, name):
        return getattr(QToolButton, name)
    return getattr(QToolButton.ToolButtonPopupMode, name)


def _dock_area(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.DockWidgetArea, name)


def _orientation(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.Orientation, name)


def _load_icon(filename: str) -> QIcon:
    return QIcon(os.path.join(os.path.dirname(__file__), filename))


def _plugin_submenu(iface):
    web_menu = getattr(iface, "webMenu", None)
    if not callable(web_menu):
        return None
    menu_bar = web_menu()
    if menu_bar is None:
        return None
    for action in menu_bar.actions():
        menu = action.menu()
        if menu is None:
            continue
        if menu.title().replace("&", "") == "Hong Kong Map Services":
            return menu
    return None


def _set_web_menu_icon(iface, icon: QIcon) -> None:
    menu = _plugin_submenu(iface)
    if menu is None:
        return
    menu.menuAction().setIcon(icon)
    menu.setIcon(icon)
