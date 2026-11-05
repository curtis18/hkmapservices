"""Export the open map to one SVG for Adobe Illustrator."""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .status import apply_status, prepare_status
from .svgexport import export_map_svg


_TITLE = "Export SVG"


class SvgDialog(QDialog):
    """Write the visible map view as one A3 SVG."""

    def __init__(self, iface_object, parent=None):
        super().__init__(parent or iface_object.mainWindow())
        self._iface = iface_object
        self._running = False
        self._stop = False
        self._closing = False
        self._unload = False
        self._closed = False
        self.setWindowTitle(_TITLE)
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "This SVG is the current map view on an A3 page. Each visible point, line, and "
            "polygon layer becomes its own layer. The basemap is one picture."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._scale = QCheckBox("Include scale bar")
        self._scale.setChecked(True)
        self._north = QCheckBox("Include north arrow")
        self._north.setChecked(True)
        layout.addWidget(self._scale)
        layout.addWidget(self._north)

        buttons = QHBoxLayout()
        export = QPushButton("Export SVG")
        export.clicked.connect(self._export)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(export)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._busy_buttons = (export, self._scale, self._north)

        self._status = QLabel("")
        layout.addWidget(prepare_status(self._status))

    def reject(self) -> None:
        if self._running:
            self._stop = True
            self._closing = True
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._running and not self._unload:
            self._stop = True
            self._closing = True
            event.ignore()
            return
        if self._running:
            self._stop = True
            self.hide()
        self._closed = True
        super().closeEvent(event)

    def shutdown(self) -> None:
        """Stop a running export before the plugin drops this dialog."""
        self._stop = True
        self._unload = True
        self.hide()
        if self._running:
            self._closing = True

    def _export(self) -> None:
        if self._running:
            return
        path, _filter = QFileDialog.getSaveFileName(self, "Export SVG", "", "SVG (*.svg)")
        if not path:
            return
        canvas = self._iface.mapCanvas() if hasattr(self._iface, "mapCanvas") else None
        self._stop = False
        self._running = True
        self._set_busy(True)
        self._show_status("Processing...", "ok")
        QApplication.processEvents()
        try:
            text, kind = export_map_svg(
                canvas,
                path,
                self._scale.isChecked(),
                self._north.isChecked(),
                self._pulse,
            )
        except Exception as exc:
            text, kind = str(exc) or "The SVG could not be exported.", "error"
        finally:
            self._running = False
            self._set_busy(False)
        if not self._closed:
            self._show_status(text, kind)
        self._stop = False
        if self._closing and not self._closed:
            self._closing = False
            self.reject()

    def _pulse(self) -> bool:
        QApplication.processEvents()
        return not self._stop

    def _set_busy(self, busy: bool) -> None:
        for button in self._busy_buttons:
            button.setEnabled(not busy)

    def _show_status(self, text: str, kind: str) -> None:
        apply_status(self._status, text, kind)
