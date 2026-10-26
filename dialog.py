"""Dialogs for a Hong Kong basemap and for CSDI datasets."""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .catalog import (
    BASEMAPS,
    CRS_CHOICES,
    LABEL_KINDS,
    LABEL_LANGUAGES,
    BasemapRequest,
    DatasetRequest,
    datasets_for_dialog,
    default_label_kind,
    project_crs_authid,
)

_DATASET_LIST_HEIGHT = 220


class BasemapDialog(QDialog):
    """Choose a basemap, coordinate system, and optional place-name labels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hong Kong Map Services")
        self.setMinimumWidth(480)
        self._basemap_buttons = []
        self._crs_buttons = []
        self._label_kind_buttons = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Add a Lands Department basemap and optional place-name labels "
            "as one layer group."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self._build_basemap_box())
        layout.addWidget(self._build_crs_box())
        layout.addWidget(self._build_label_box())

        self._project_crs = QCheckBox(self._project_crs_text())
        self._project_crs.setChecked(True)
        layout.addWidget(self._project_crs)
        layout.addWidget(self._button_box())

        self._labels.toggled.connect(self._set_label_options_enabled)
        for button in self._crs_buttons:
            button.toggled.connect(self._update_project_crs_text)
        for button in self._basemap_buttons:
            button.toggled.connect(self._on_basemap_toggled)
        self._set_label_options_enabled(self._labels.isChecked())

    def request(self) -> BasemapRequest:
        labels_on = self._labels.isChecked()
        language = self._language.currentData()
        return BasemapRequest(
            basemap=self._checked_key(self._basemap_buttons),
            crs=self._checked_key(self._crs_buttons),
            label_language=language if labels_on else None,
            label_kind=self._checked_key(self._label_kind_buttons) if labels_on else None,
            set_project_crs=self._project_crs.isChecked(),
        )

    def _build_basemap_box(self) -> QGroupBox:
        box = QGroupBox("Basemap")
        layout = QVBoxLayout(box)
        group = QButtonGroup(self)
        for key, label, tip in BASEMAPS:
            button = QRadioButton(label)
            button.setToolTip(tip)
            button.setProperty("choice", key)
            group.addButton(button)
            layout.addWidget(button)
            self._basemap_buttons.append(button)
        self._basemap_buttons[0].setChecked(True)
        return box

    def _build_crs_box(self) -> QGroupBox:
        box = QGroupBox("Coordinate system")
        layout = QVBoxLayout(box)
        group = QButtonGroup(self)
        for key, label, _authid in CRS_CHOICES:
            button = QRadioButton(label)
            button.setProperty("choice", key)
            group.addButton(button)
            layout.addWidget(button)
            self._crs_buttons.append(button)
        self._crs_buttons[0].setChecked(True)
        return box

    def _build_label_box(self) -> QGroupBox:
        box = QGroupBox("Labels")
        layout = QVBoxLayout(box)
        self._labels = QCheckBox("Place-name labels")
        self._labels.setChecked(True)
        layout.addWidget(self._labels)

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("Language"))
        self._language = QComboBox()
        for key, label in LABEL_LANGUAGES:
            self._language.addItem(label, key)
        language_row.addWidget(self._language, 1)
        layout.addLayout(language_row)

        kind_group = QButtonGroup(self)
        for key, label in LABEL_KINDS:
            button = QRadioButton(label)
            button.setProperty("choice", key)
            kind_group.addButton(button)
            layout.addWidget(button)
            self._label_kind_buttons.append(button)
        self._select_label_kind(default_label_kind(self._checked_key(self._basemap_buttons)))
        return box

    def _button_box(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox()
        add_button = buttons.addButton("Add to map", _button_role("AcceptRole"))
        add_button.setDefault(True)
        buttons.addButton("Close", _button_role("RejectRole"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        return buttons

    def _on_basemap_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._select_label_kind(default_label_kind(self._checked_key(self._basemap_buttons)))

    def _select_label_kind(self, kind: str) -> None:
        for button in self._label_kind_buttons:
            button.setChecked(button.property("choice") == kind)

    def _set_label_options_enabled(self, enabled: bool) -> None:
        self._language.setEnabled(enabled)
        for button in self._label_kind_buttons:
            button.setEnabled(enabled)

    def _update_project_crs_text(self) -> None:
        self._project_crs.setText(self._project_crs_text())

    def _project_crs_text(self) -> str:
        return f"Set project CRS to {project_crs_authid(self._checked_key(self._crs_buttons))}"

    @staticmethod
    def _checked_key(buttons) -> str:
        for button in buttons:
            if button.isChecked():
                return button.property("choice")
        raise RuntimeError("No option is selected.")


class DatasetDialog(QDialog):
    """Choose CSDI datasets without adding a basemap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hong Kong Map Services")
        self.setMinimumWidth(480)
        self._dataset_boxes = []

        layout = QVBoxLayout(self)
        intro = QLabel("Add selected CSDI datasets as one layer group.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self._build_language_box())
        layout.addWidget(self._build_dataset_box())

        buttons = QDialogButtonBox()
        self._add_button = buttons.addButton("Add to map", _button_role("AcceptRole"))
        self._add_button.setDefault(True)
        self._add_button.setEnabled(False)
        buttons.addButton("Close", _button_role("RejectRole"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for _dataset_id, checkbox, _row in self._dataset_boxes:
            checkbox.toggled.connect(self._update_add_button)

    def request(self) -> DatasetRequest:
        dataset_ids = tuple(
            dataset_id for dataset_id, box, _row in self._dataset_boxes if box.isChecked()
        )
        return DatasetRequest(
            name_language=self._language.currentData(),
            dataset_ids=dataset_ids,
        )

    def _build_language_box(self) -> QGroupBox:
        box = QGroupBox("Names")
        layout = QHBoxLayout(box)
        layout.addWidget(QLabel("Language"))
        self._language = QComboBox()
        for key, label in LABEL_LANGUAGES:
            self._language.addItem(label, key)
        layout.addWidget(self._language, 1)
        return box

    def _build_dataset_box(self) -> QGroupBox:
        box = QGroupBox("Datasets")
        layout = QVBoxLayout(box)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search datasets")
        self._search.setClearButtonEnabled(True)
        layout.addWidget(self._search)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        for dataset in datasets_for_dialog():
            checkbox = QCheckBox(dataset.title)
            checkbox.setToolTip(dataset.attribution)
            link = QLabel(f'<a href="{dataset.portal_url}">CSDI</a>')
            link.setTextFormat(_text_format("RichText"))
            link.setTextInteractionFlags(_text_flag("TextBrowserInteraction"))
            link.setToolTip(dataset.portal_url)
            link.linkActivated.connect(_open_portal)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(checkbox)
            row_layout.addWidget(link)
            row_layout.addStretch(1)
            content_layout.addWidget(row)
            self._dataset_boxes.append((dataset.id, checkbox, row))
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setMinimumHeight(_DATASET_LIST_HEIGHT)
        scroll.setMaximumHeight(_DATASET_LIST_HEIGHT)
        scroll.setHorizontalScrollBarPolicy(_scroll_policy("ScrollBarAlwaysOff"))
        layout.addWidget(scroll)
        self._search.textChanged.connect(self._filter_datasets)
        return box

    def _update_add_button(self, *_args) -> None:
        self._add_button.setEnabled(any(box.isChecked() for _dataset_id, box, _row in self._dataset_boxes))

    def _filter_datasets(self, text: str) -> None:
        query = text.casefold().strip()
        for _dataset_id, checkbox, row in self._dataset_boxes:
            row.setVisible(query in checkbox.text().casefold())


def _open_portal(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


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


def _scroll_policy(name: str):
    if hasattr(Qt, name):
        return getattr(Qt, name)
    return getattr(Qt.ScrollBarPolicy, name)
