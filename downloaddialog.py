"""Choose a URL column and a folder, then download the files in the background."""

from __future__ import annotations

from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .downloader import DownloadWorker, database_fields, network_proxy, read_urls
from .downloadmath import split_addresses
from .status import apply_status, prepare_status
from .unzipfiles import list_zip_files, unzip_folder


_TITLE = "Download Files"


class DownloadDialog(QDialog):
    """Read a URL column from a GeoPackage or a File Geodatabase."""

    def __init__(self, iface_object=None, parent=None):
        super().__init__(parent)
        self._iface = iface_object
        self._running = False
        self._running_kind = None
        self._stop = [False]
        self._close_after = False
        self._thread = None
        self._worker = None
        self._fields = {}
        self._rejected = 0
        self._bar_widget = None
        self._bar_label = None
        self.setWindowTitle(_TITLE)
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Download the files named by a URL column in a GeoPackage or a File Geodatabase. "
            "A complete file in the folder is left alone. An incomplete file is never changed: "
            "each transfer is written to a .part name and moved into place when it finishes. "
            "A .part file from an earlier run continues when the server accepts a byte range, "
            "and starts again when it does not. "
            "Unzip extracts every zip in the save folder, moves its files beside the zip, "
            "replaces a file that is already there, and leaves the zip files. "
            "Unzip cannot be cancelled once it starts, and an existing folder with the same name "
            "as one inside a zip is not changed."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._source = QLineEdit()
        self._source.setReadOnly(True)
        self._source.setPlaceholderText("GeoPackage or File Geodatabase")
        source_row = QHBoxLayout()
        source_row.addWidget(self._source, 1)
        browse_gpkg = QPushButton("GeoPackage")
        browse_gpkg.clicked.connect(self._browse_gpkg)
        browse_gdb = QPushButton("File Geodatabase")
        browse_gdb.clicked.connect(self._browse_gdb)
        source_row.addWidget(browse_gpkg)
        source_row.addWidget(browse_gdb)
        layout.addLayout(source_row)

        self._layer = QComboBox()
        self._column = QComboBox()
        self._layer.currentTextChanged.connect(self._fill_columns)
        layout.addWidget(self._layer)
        layout.addWidget(self._column)

        self._folder = QLineEdit()
        self._folder.setReadOnly(True)
        self._folder.setPlaceholderText("Save folder")
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._folder, 1)
        browse_folder = QPushButton("Folder")
        browse_folder.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_folder)
        layout.addLayout(folder_row)

        buttons = QHBoxLayout()
        self._download = QPushButton("Download")
        self._download.clicked.connect(self._start)
        self._unzip = QPushButton("Unzip")
        self._unzip.clicked.connect(self._start_unzip)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._cancel_download)
        self._cancel.setEnabled(False)
        minimize = QPushButton("Minimize")
        minimize.clicked.connect(self._minimize)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(self._download)
        buttons.addWidget(self._unzip)
        buttons.addWidget(self._cancel)
        buttons.addWidget(minimize)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._busy_buttons = (browse_gpkg, browse_gdb, browse_folder, self._layer, self._column)

        self._status = QLabel("")
        layout.addWidget(prepare_status(self._status))
        self._source.textChanged.connect(self._refresh_download)
        self._folder.textChanged.connect(self._refresh_download)
        self._column.currentTextChanged.connect(self._refresh_download)
        self._refresh_download()

    def reject(self) -> None:
        if self._running and self._running_kind == "unzip":
            self._show_status("Unzip must finish before closing. Use Minimize to keep working.", "warn")
            return
        if self._running:
            self._stop[0] = True
            self._close_after = True
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._running and self._running_kind == "unzip":
            self._show_status("Unzip must finish before closing. Use Minimize to keep working.", "warn")
            event.ignore()
            return
        if self._running:
            self._stop[0] = True
            self._close_after = True
            event.ignore()
            return
        self._remove_bar()
        super().closeEvent(event)

    def shutdown(self, timeout_ms: int = 10000) -> None:
        """Stop a download, or wait for an unzip, before the plugin drops this dialog."""
        self._close_after = False
        thread = self._thread
        if self._running_kind == "unzip":
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait()
            self._running = False
            self._running_kind = None
            self._remove_bar()
            return
        self._stop[0] = True
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(timeout_ms)
        self._running = False
        self._running_kind = None
        self._remove_bar()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._remove_bar()

    def _browse_gpkg(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, "GeoPackage", self._source.text(), "GeoPackage (*.gpkg)")
        if path:
            self._open_source(path)

    def _browse_gdb(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "File Geodatabase", self._source.text())
        if path:
            self._open_source(path)

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Save folder", self._folder.text())
        if path:
            self._folder.setText(path)

    def _open_source(self, path: str) -> None:
        try:
            self._fields = database_fields(path)
        except Exception as exc:
            self._show_status(f"The database could not be read: {exc}", "error")
            return
        if not self._fields:
            self._show_status("The database has no readable layers.", "error")
            return
        self._source.setText(path)
        self._layer.blockSignals(True)
        self._layer.clear()
        self._layer.addItems(list(self._fields))
        self._layer.blockSignals(False)
        self._fill_columns()

    def _fill_columns(self, *_args) -> None:
        names = self._fields.get(self._layer.currentText(), [])
        self._column.blockSignals(True)
        self._column.clear()
        self._column.addItems(names)
        self._column.blockSignals(False)
        self._refresh_download()

    def _start(self) -> None:
        if self._running or (self._thread is not None and self._thread.isRunning()):
            return
        column = self._column.currentText()
        try:
            values = read_urls(self._source.text().strip(), self._layer.currentText(), column)
        except Exception as exc:
            self._show_status(f"The URL column could not be read: {exc}", "error")
            return
        urls, rejected = split_addresses(values)
        if not urls:
            self._show_status(f"{column} has no web addresses. Choose a URL column.", "error")
            return
        try:
            proxies = [network_proxy(url) for url in urls]
        except Exception as exc:
            self._show_status(f"The network proxy could not be read: {exc}", "error")
            return
        self._rejected = rejected
        self._stop[0] = False
        self._running = True
        self._running_kind = "download"
        self._set_busy(True)
        self._show_status(f"Downloading 1 of {len(urls)}", "ok")
        worker = DownloadWorker(urls, self._folder.text().strip(), proxies, self._stop)
        self._start_worker(worker, self._on_download_finished)

    def _start_unzip(self) -> None:
        if self._running or (self._thread is not None and self._thread.isRunning()):
            return
        folder = self._folder.text().strip()
        if not folder:
            return
        try:
            names = list_zip_files(folder)
        except OSError as exc:
            self._show_status(f"The folder could not be read: {exc}", "error")
            return
        if not names:
            self._show_status("The folder has no zip files.", "error")
            return
        self._rejected = 0
        self._running = True
        self._running_kind = "unzip"
        self._set_busy(True)
        self._show_status(f"Unzipping 1 of {len(names)}: {names[0]}", "ok")
        self._start_worker(UnzipWorker(folder), self._on_finished)

    def _start_worker(self, worker, finished_slot) -> None:
        """Move one worker to its own thread and connect the usual signals."""
        self._worker = worker
        self._thread = QThread(self)
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._show_status)
        worker.finished.connect(finished_slot)
        worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.start()

    def _on_download_finished(self, text: str, kind: str) -> None:
        if self._rejected:
            text = f"{text} {self._rejected} cells were not web addresses."
            if kind == "ok":
                kind = "warn"
        self._on_finished(text, kind)

    def _on_finished(self, text: str, kind: str) -> None:
        self._running = False
        self._running_kind = None
        self._set_busy(False)
        self._show_status(text, kind)
        if self._close_after:
            self._close_after = False
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(5000)
            super().reject()

    def _cancel_download(self) -> None:
        if not self._running or self._running_kind != "download":
            return
        self._stop[0] = True
        self._cancel.setEnabled(False)

    def _minimize(self) -> None:
        self.hide()
        self._show_bar(self._status.text())

    def _restore(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self._remove_bar()

    def _refresh_download(self, *_args) -> None:
        ready = bool(self._source.text().strip() and self._column.currentText().strip() and self._folder.text().strip())
        self._download.setEnabled(ready and not self._running)
        self._unzip.setEnabled(bool(self._folder.text().strip()) and not self._running)

    def _set_busy(self, busy: bool) -> None:
        for button in self._busy_buttons:
            button.setEnabled(not busy)
        self._cancel.setEnabled(busy and self._running_kind == "download")
        self._refresh_download()

    def _show_status(self, text: str, kind: str) -> None:
        apply_status(self._status, text, kind)
        if self._bar_label is not None:
            self._bar_label.setText(text)

    def _show_bar(self, text: str) -> None:
        bar = self._status_bar()
        if bar is None or not hasattr(bar, "addPermanentWidget"):
            return
        if self._bar_widget is None:
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(6, 0, 6, 0)
            self._bar_label = QLabel(text)
            show = QPushButton("Show")
            show.clicked.connect(self._restore)
            row.addWidget(self._bar_label)
            row.addWidget(show)
            bar.addPermanentWidget(widget)
            self._bar_widget = widget
        self._bar_label.setText(text)

    def _remove_bar(self) -> None:
        widget = self._bar_widget
        if widget is None:
            return
        bar = self._status_bar()
        if bar is not None and hasattr(bar, "removeWidget"):
            bar.removeWidget(widget)
        widget.hide()
        widget.deleteLater()
        self._bar_widget = None
        self._bar_label = None

    def _status_bar(self):
        iface_object = self._iface
        if iface_object is None:
            return None
        finder = getattr(iface_object, "mainWindow", None)
        if not callable(finder):
            return None
        try:
            window = finder()
        except Exception:
            return None
        if window is None or not hasattr(window, "statusBar"):
            return None
        try:
            return window.statusBar()
        except Exception:
            return None


class UnzipWorker(QObject):
    """Extract one zip at a time on a background thread."""

    progress = pyqtSignal(str, str)
    finished = pyqtSignal(str, str)

    def __init__(self, folder: str):
        super().__init__()
        self._folder = folder

    def run(self) -> None:
        try:
            text, kind = unzip_folder(self._folder, self.progress.emit)
        except Exception as exc:
            self.finished.emit(str(exc), "error")
            return
        self.finished.emit(text, kind)
