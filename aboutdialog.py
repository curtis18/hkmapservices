"""About dialog for Hong Kong Map Services."""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QFontDatabase, QIcon
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .compat import enum_member


_SOURCE_URL = "https://github.com/curtis18/hkmapservices"
_TRACKER_URL = "https://github.com/curtis18/hkmapservices/issues"


class AboutDialog(QDialog):
    """Plugin name, developer, source links, and the GNU license."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Hong Kong Map Services")
        self.setMinimumWidth(552)
        metadata = _read_metadata()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 8)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._icon = QLabel()
        self._icon.setPixmap(_load_icon("icon_about.png").pixmap(64, 64))
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        name = QLabel(metadata.get("name", "Hong Kong Map Services"))
        name_font = name.font()
        name_font.setBold(True)
        name_font.setPointSize(name_font.pointSize() + 6)
        name.setFont(name_font)
        name.setAlignment(_alignment("AlignCenter"))
        version = QLabel(f"Version: {metadata.get('version', '')}")
        version.setAlignment(_alignment("AlignCenter"))
        titles.addWidget(name)
        titles.addWidget(version)
        balance = QWidget()
        balance.setFixedWidth(self._icon.sizeHint().width())
        header.addWidget(self._icon, 0, _alignment("AlignVCenter"))
        header.addLayout(titles, 1)
        header.addWidget(balance)
        layout.addLayout(header)

        self._tabs = QTabWidget()
        self._info = _info_label()
        info_page = QWidget()
        info_layout = QVBoxLayout(info_page)
        info_layout.addWidget(self._info)
        info_layout.addStretch(1)
        self._tabs.addTab(info_page, "Info")

        self._license = QPlainTextEdit()
        self._license.setReadOnly(True)
        self._license.setFont(_license_font())
        self._license.setPlainText(_read_license())
        self._tabs.addTab(self._license, "License")
        layout.addWidget(self._tabs, 1)

        buttons = QDialogButtonBox()
        buttons.addButton("Close", _button_role("RejectRole"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def _info_label() -> QLabel:
    label = QLabel(
        "Adds Lands Department imagery, topographic, and vector basemaps, with place-name labels. "
        "Adds selected CSDI datasets. Search a place, convert HK1980 Grid and WGS84 coordinates, "
        "and read both under the map cursor. Compare layers or groups with Swipe and Split.<br><br>"
        "<b>Developer:</b> Curtis Yung<br>"
        f'<b>Issue Tracker:</b> <a href="{_TRACKER_URL}">GitHub</a><br>'
        f'<b>Source Code:</b> <a href="{_SOURCE_URL}">GitHub</a>'
    )
    label.setWordWrap(True)
    label.setTextFormat(_text_format("RichText"))
    label.setTextInteractionFlags(_text_flag("TextBrowserInteraction"))
    label.setOpenExternalLinks(True)
    return label


def _read_metadata() -> dict:
    values = {}
    path = os.path.join(os.path.dirname(__file__), "metadata.txt")
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if "=" not in line or line.startswith("["):
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _read_license() -> str:
    path = os.path.join(os.path.dirname(__file__), "LICENSE")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    heading = "GNU GENERAL PUBLIC LICENSE"
    start = text.find(heading)
    if start < 0:
        raise ValueError("The GNU license heading is missing.")
    line_start = text.rfind("\n", 0, start) + 1
    return text[line_start:]


def _license_font() -> QFont:
    """Fixed-width font so the license's own spaces center the heading."""
    if "Consolas" in _font_families():
        font = QFont("Consolas")
    else:
        font = QFontDatabase.systemFont(_fixed_font())
    font.setFixedPitch(True)
    font.setStyleHint(_font_style("Monospace"))
    return font


def _font_families():
    """Qt 6 lists families on the class. Qt 5 lists them on an object."""
    families = getattr(QFontDatabase, "families")
    try:
        return families()
    except TypeError:
        return QFontDatabase().families()


def _fixed_font():
    return enum_member(QFontDatabase, "SystemFont", "FixedFont")


def _font_style(name: str):
    if hasattr(QFont, name):
        return getattr(QFont, name)
    return getattr(QFont.StyleHint, name)


def _alignment(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.AlignmentFlag, name)


def _load_icon(filename: str) -> QIcon:
    return QIcon(os.path.join(os.path.dirname(__file__), filename))


def _text_format(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.TextFormat, name)


def _text_flag(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.TextInteractionFlag, name)


def _button_role(name: str):
    if hasattr(QDialogButtonBox, name):
        return getattr(QDialogButtonBox, name)
    return getattr(QDialogButtonBox.ButtonRole, name)
