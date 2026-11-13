"""Download files named by a URL column, using the QGIS network proxy."""

from __future__ import annotations

import os
import time
import zipfile

from osgeo import gdal
from qgis.PyQt.QtCore import QEventLoop, QObject, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkReply,
    QNetworkRequest,
)
from qgis.core import QgsProviderRegistry, QgsVectorLayer

from .compat import enum_member
from .qgisproxy import proxy_endpoint
from .downloadmath import (
    FolderIndex,
    discard_part,
    discard_sidecar,
    filename_from_disposition,
    finished_message,
    is_valid_url,
    part_is_ours,
    part_is_stale,
    part_path,
    publish_part,
    range_start,
    range_total,
    read_sidecar,
    ssl_failure_message,
    stopped_message,
    unique_filename,
    write_sidecar,
)


_IDLE_TIMEOUT_SECONDS = 60.0
_ZIP_SCAN_BYTES = 64 * 1024 * 1024


class DownloadWorker(QObject):
    """Save one URL at a time on a background thread."""

    progress = pyqtSignal(str, str)
    finished = pyqtSignal(str, str)

    def __init__(self, urls: list, folder: str, proxies: list, stop: list):
        super().__init__()
        self._urls = list(urls)
        self._folder = folder
        self._proxies = [_copy_proxy(proxy) for proxy in proxies]
        self._stop = stop
        self._names = set()

    def run(self) -> None:
        try:
            self._download_all()
        except Exception as exc:
            self.finished.emit(str(exc), "error")

    def _download_all(self) -> None:
        manager = QNetworkAccessManager()
        saved = 0
        skipped = 0
        failed = 0
        total = len(self._urls)
        folder_index = FolderIndex(self._folder)
        tls_failures = []
        for index, url in enumerate(self._urls, start=1):
            if self._stop[0]:
                self.finished.emit(stopped_message(saved, skipped), "warn")
                return
            if not is_valid_url(url):
                failed += 1
                continue
            manager.setProxy(self._proxies[index - 1] if index - 1 < len(self._proxies) else _empty_proxy())
            name = filename_from_disposition("", url)
            self.progress.emit(f"Downloading {index} of {total}: {name}", "ok")
            outcome = _save_url(
                manager,
                url,
                self._folder,
                self._stop,
                self.progress,
                index,
                total,
                self._names,
                folder_index,
                failures=tls_failures,
            )
            if outcome == "stopped":
                self.finished.emit(stopped_message(saved, skipped), "warn")
                return
            if outcome == "tls_failed":
                failed_url, ssl_errors = tls_failures[-1] if tls_failures else (url, [])
                remaining = total - index
                self.finished.emit(ssl_failure_message(failed_url, ssl_errors, remaining), "error")
                return
            if outcome == "saved":
                saved += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                failed += 1
        self.finished.emit(*finished_message(saved, skipped, failed, total))


def database_fields(path: str) -> dict:
    """Return field names for each layer in a GeoPackage or File Geodatabase."""
    found = {}
    for name in _layer_names(path):
        layer = QgsVectorLayer(f"{path}|layername={name}", name, "ogr")
        if not layer.isValid():
            continue
        found[name] = [field.name() for field in layer.fields()]
        layer = None
    return found


def read_urls(path: str, layer_name: str, column: str) -> list:
    """Return the text values in one column."""
    layer = QgsVectorLayer(f"{path}|layername={layer_name}", layer_name, "ogr")
    if not layer.isValid():
        return []
    values = []
    for feature in layer.getFeatures():
        value = feature[column]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            values.append(text)
    layer = None
    return values


def network_proxy(sample: str = "https://www.example.com/") -> QNetworkProxy:
    """Copy the proxy QGIS would use for this address, including its username and password."""
    endpoint = proxy_endpoint(sample)
    if endpoint is None:
        return _empty_proxy()
    return QNetworkProxy(
        endpoint["type"],
        endpoint["host"],
        endpoint["port"],
        endpoint["user"],
        endpoint["password"],
    )


def _empty_proxy() -> QNetworkProxy:
    return QNetworkProxy(enum_member(QNetworkProxy, "ProxyType", "NoProxy"))


def _copy_proxy(proxy: QNetworkProxy) -> QNetworkProxy:
    return QNetworkProxy(proxy.type(), proxy.hostName(), int(proxy.port()), proxy.user(), proxy.password())


def _layer_names(path: str) -> list:
    metadata = QgsProviderRegistry.instance().providerMetadata("ogr")
    names = []
    if metadata is not None and hasattr(metadata, "querySublayers"):
        try:
            names = [detail.name() for detail in metadata.querySublayers(path) if detail.name()]
        except Exception:
            names = []
    if names:
        return names
    layer = QgsVectorLayer(path, "layer", "ogr")
    if layer.isValid() and layer.name():
        names = [layer.name()]
    layer = None
    return names


def _save_url(
    manager,
    url: str,
    folder: str,
    stop: list,
    progress,
    index: int,
    total: int,
    names: set,
    folder_index=None,
    failures=None,
) -> str:
    if folder_index is None:
        folder_index = FolderIndex(folder)
    probe = _exchange(manager, url, None, None, stop, headers_only=True)
    if stop[0]:
        return "stopped"
    if probe.get("tls_error"):
        _record_tls_failure(failures, url, probe.get("ssl_errors"))
        return "tls_failed"
    if probe["error"]:
        probe = {"length": -1, "ranges": False, "disposition": "", "etag": ""}
    base = filename_from_disposition(probe["disposition"], url)
    name, action = _download_plan(
        folder,
        base,
        names,
        folder_index,
        probe["length"],
        probe["etag"],
        url,
        stop,
    )
    if stop[0]:
        return "stopped"
    progress.emit(f"Downloading {index} of {total}: {name}", "ok")
    if action == "skip":
        return "skipped"
    final_path = os.path.join(folder, name)
    if action == "recover":
        if not publish_part(part_path(folder, name), final_path):
            return "failed"
        discard_sidecar(folder, name)
        folder_index.add(name)
        return "saved"
    part = part_path(folder, name)
    header = None
    mode = "wb"
    offset = None
    if action == "resume":
        offset = os.path.getsize(part) if os.path.isfile(part) else 0
        if offset:
            header = f"bytes={offset}-"
            mode = "ab"
    write_sidecar(folder, name, url, probe["etag"], probe["length"])
    try:
        saved = _exchange(
            manager,
            url,
            header,
            part,
            stop,
            mode=mode,
            range_from=offset,
            if_range=probe["etag"] if offset else "",
        )
    except OSError as exc:
        progress.emit(f"Could not write {name}: {exc}", "error")
        return "failed"
    if stop[0]:
        return "stopped"
    if saved["mismatch"] or saved["code"] == 416:
        progress.emit(f"Restarting {name}: the server resumed at a different point.", "warn")
        try:
            saved = _exchange(manager, url, None, part, stop, mode="wb")
        except OSError as exc:
            progress.emit(f"Could not write {name}: {exc}", "error")
            return "failed"
        if stop[0]:
            return "stopped"
    if saved.get("tls_error"):
        if not os.path.isfile(part) or os.path.getsize(part) == 0:
            discard_part(folder, name)
        _record_tls_failure(failures, url, saved.get("ssl_errors"))
        return "tls_failed"
    if saved["timed_out"]:
        progress.emit(f"Download timed out: {name}", "warn")
        return "failed"
    if saved["error"] or not os.path.isfile(part):
        if not os.path.isfile(part) or os.path.getsize(part) == 0:
            discard_part(folder, name)
        return "failed"
    expected = saved["full"] if saved["full"] >= 0 else probe["length"]
    if not _download_ok(part, name, expected, stop):
        if stop[0]:
            return "stopped"
        discard_part(folder, name)
        return "failed"
    if not publish_part(part, final_path):
        return "failed"
    discard_sidecar(folder, name)
    folder_index.add(name)
    return "saved"


def _record_tls_failure(failures, url: str, ssl_errors) -> None:
    """Keep the address and SSL details for the one message that stops the batch."""
    if failures is not None:
        failures.append((str(url), list(ssl_errors or [])))


def _download_plan(
    folder: str,
    base: str,
    names: set,
    folder_index,
    expected: int,
    etag: str,
    url: str,
    stop,
) -> tuple:
    """Choose the final name and whether to skip, recover, resume, restart, or start.

    A file already in the folder is never extended or overwritten. Only a
    ``.part`` file with a matching sidecar from this plugin can be resumed.
    """
    name = unique_filename(base, names)
    existing = folder_index.find(name)
    if existing is not None:
        if _file_complete(os.path.join(folder, existing), existing, expected, stop):
            _discard_completed_part(folder, existing, url)
            return existing, "skip"
        while True:
            candidate = unique_filename(base, names)
            if _free_for_download(folder_index, candidate):
                name = candidate
                break
    part = folder_index.find(name + ".part")
    if part is not None:
        actual = part[: -len(".part")]
        record = read_sidecar(folder, actual)
        if not part_is_ours(record, url):
            while True:
                candidate = unique_filename(base, names)
                if _free_for_download(folder_index, candidate):
                    return candidate, "start"
        if part_is_stale(record, etag, expected):
            return actual, "restart"
        if _file_complete(os.path.join(folder, part), actual, expected, stop):
            return actual, "recover"
        return actual, "resume"
    return name, "start"


def _free_for_download(folder_index, name: str) -> bool:
    return (
        folder_index.find(name) is None
        and folder_index.find(name + ".part") is None
        and folder_index.find(name + ".part.json") is None
    )


def _discard_completed_part(folder: str, name: str, url: str) -> None:
    """Remove our partial copy after the final file is found to be complete."""
    if part_is_ours(read_sidecar(folder, name), url):
        discard_part(folder, name)


def _exchange(
    manager,
    url: str,
    range_header,
    path,
    stop: list,
    mode: str = "wb",
    headers_only: bool = False,
    range_from=None,
    if_range: str = "",
) -> dict:
    handle = None
    if path is not None:
        handle = open(path, mode)
    request = QNetworkRequest(QUrl(url))
    _allow_redirects(request)
    request.setRawHeader(b"Accept-Encoding", b"identity")
    if range_header:
        request.setRawHeader(b"Range", range_header.encode("ascii"))
    if if_range and not str(if_range).startswith("W/"):
        request.setRawHeader(b"If-Range", str(if_range).encode("latin-1", "replace"))
    reply = manager.head(request) if headers_only else manager.get(request)
    loop = QEventLoop()
    state = {
        "code": None,
        "rewritten": mode == "wb",
        "mismatch": False,
        "timed_out": False,
        "ssl_errors": [],
        "last": time.monotonic(),
    }

    def responded() -> int:
        code = reply.attribute(_status_attribute())
        state["code"] = code
        return code

    def content_range() -> str:
        return bytes(reply.rawHeader(b"Content-Range")).decode("ascii", "replace")

    def trim(size: int) -> None:
        if handle is None:
            return
        try:
            handle.flush()
            handle.truncate(size)
        except OSError:
            pass

    def wrong_start(start) -> bool:
        if range_from is None:
            return False
        if start == range_from:
            return False
        state["mismatch"] = True
        trim(range_from)
        reply.abort()
        return True

    def on_meta() -> None:
        state["last"] = time.monotonic()
        code = responded()
        if handle is None or mode != "ab":
            return
        if code == 200 and not state["rewritten"]:
            handle.seek(0)
            handle.truncate(0)
            state["rewritten"] = True
        elif code == 206:
            wrong_start(range_start(content_range()))

    def on_ssl_errors(errors) -> None:
        state["last"] = time.monotonic()
        state["ssl_errors"] = [error.errorString() for error in errors]

    def on_ready() -> None:
        state["last"] = time.monotonic()
        if stop[0]:
            reply.abort()
            return
        if state["mismatch"]:
            return
        if handle is None:
            return
        code = responded()
        if mode == "ab":
            if code == 200 and not state["rewritten"]:
                handle.seek(0)
                handle.truncate(0)
                state["rewritten"] = True
            elif code == 206 and wrong_start(range_start(content_range())):
                return
        data = bytes(reply.readAll())
        if data:
            handle.write(data)

    def on_tick() -> None:
        if stop[0]:
            reply.abort()
            return
        if time.monotonic() - state["last"] >= _IDLE_TIMEOUT_SECONDS:
            state["timed_out"] = True
            reply.abort()

    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(on_tick)
    reply.metaDataChanged.connect(on_meta)
    reply.readyRead.connect(on_ready)
    reply.sslErrors.connect(on_ssl_errors)
    reply.finished.connect(loop.quit)
    timer.start()
    try:
        _exec_loop(loop)
    finally:
        timer.stop()
        if handle is not None and not handle.closed:
            rest = bytes(reply.readAll())
            if rest and not stop[0] and not state["mismatch"] and not state["timed_out"]:
                handle.write(rest)
            handle.close()
    result = {
        "error": _failed(reply, stop) or state["mismatch"] or state["timed_out"],
        "length": _header_int(reply, b"Content-Length"),
        "full": _full_length(reply),
        "ranges": b"bytes" in bytes(reply.rawHeader(b"Accept-Ranges")).lower(),
        "disposition": bytes(reply.rawHeader(b"Content-Disposition")).decode("utf-8", "replace"),
        "etag": bytes(reply.rawHeader(b"ETag")).decode("latin-1", "replace"),
        "range_start": range_start(content_range()),
        "code": state["code"],
        "mismatch": state["mismatch"],
        "timed_out": state["timed_out"],
        "ssl_errors": list(state["ssl_errors"]),
        "tls_error": bool(state["ssl_errors"])
        or reply.error() == enum_member(QNetworkReply, "NetworkError", "SslHandshakeFailedError"),
    }
    if not headers_only and state["code"] not in (200, 206, None):
        result["error"] = True
    reply.deleteLater()
    return result


def _failed(reply, stop: list) -> bool:
    if stop[0]:
        return True
    no_error = enum_member(QNetworkReply, "NetworkError", "NoError")
    return reply.error() != no_error


def _file_complete(path: str, name: str, expected: int, stop=None) -> bool:
    lowered = name.casefold()
    if lowered.endswith(".zip"):
        return _zip_ok(path, stop)
    if lowered.endswith(".tif") or lowered.endswith(".tiff"):
        return _tiff_ok(path, stop)
    if expected is None or expected < 0:
        return False
    return os.path.isfile(path) and os.path.getsize(path) == expected


def _download_ok(path: str, name: str, expected: int, stop=None) -> bool:
    lowered = name.casefold()
    if lowered.endswith(".zip") or lowered.endswith(".tif") or lowered.endswith(".tiff"):
        return _file_complete(path, name, expected, stop)
    if expected is None or expected < 0:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    return os.path.isfile(path) and os.path.getsize(path) == expected


def _full_length(reply) -> int:
    span = bytes(reply.rawHeader(b"Content-Range")).decode("ascii", "replace")
    total = range_total(span)
    if total is not None:
        return total
    code = reply.attribute(_status_attribute())
    if code == 206:
        return -1
    return _header_int(reply, b"Content-Length")


def _stopped(stop) -> bool:
    return bool(stop and stop[0])


def _zip_ok(path: str, stop=None) -> bool:
    """Check the archive structure and decompress a bounded amount of its data."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if not members:
                return False
            scanned = 0
            for info in members:
                if _stopped(stop):
                    return False
                with archive.open(info) as handle:
                    while scanned < _ZIP_SCAN_BYTES:
                        chunk = handle.read(min(1024 * 1024, _ZIP_SCAN_BYTES - scanned))
                        if not chunk:
                            break
                        scanned += len(chunk)
                        if _stopped(stop):
                            return False
                    if scanned >= _ZIP_SCAN_BYTES:
                        return True
            return True
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return False


def _tiff_ok(path: str, stop=None) -> bool:
    """Open the TIFF and read a small window near the end of each band."""
    try:
        dataset = gdal.Open(path)
        if dataset is None or dataset.RasterCount < 1:
            return False
        for index in range(1, dataset.RasterCount + 1):
            if _stopped(stop):
                return False
            band = dataset.GetRasterBand(index)
            width = max(1, min(256, band.XSize))
            height = max(1, min(256, band.YSize))
            window = band.ReadRaster(band.XSize - width, band.YSize - height, width, height)
            if window is None:
                return False
        return True
    except Exception:
        return False
    finally:
        dataset = None


def _header_int(reply, name: bytes) -> int:
    text = bytes(reply.rawHeader(name)).decode("ascii", "replace").strip()
    if not text:
        return -1
    try:
        return int(text)
    except ValueError:
        return -1


def _allow_redirects(request) -> None:
    follow = getattr(QNetworkRequest, "FollowRedirectsAttribute", None)
    if follow is not None:
        request.setAttribute(follow, True)
    policy_attribute = getattr(QNetworkRequest, "RedirectPolicyAttribute", None)
    policy = getattr(QNetworkRequest, "NoLessSafeRedirectPolicy", None)
    redirect_policy = getattr(QNetworkRequest, "RedirectPolicy", None)
    if policy is None and redirect_policy is not None:
        policy = getattr(redirect_policy, "NoLessSafeRedirectPolicy", None)
    if policy_attribute is not None and policy is not None:
        request.setAttribute(policy_attribute, policy)


def _status_attribute():
    return enum_member(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")


def _exec_loop(loop: QEventLoop) -> None:
    method = getattr(loop, "exec", None)
    if method is None:
        method = loop.exec_
    method()


