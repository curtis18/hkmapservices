"""Local proxy that makes Lands Department vector tiles usable in QGIS.

QGIS 3.34 rejects an ArcGIS vector-tile service when its advertised tilemap
URL returns 404. These services do that. The proxy returns the same service
description with the tilemap entry removed, and forwards tile requests to
Lands Department.
"""

from __future__ import annotations

import gzip
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .catalog import API_ROOT, vector_service_path


_HOST = "127.0.0.1"
_FIRST_PORT = 47880
_TILE_TIMEOUT = 20
_JSON_TIMEOUT = 60
_POOL_LIMIT = 4
_server = None
_lock = threading.Lock()
_pool = []
_pool_lock = threading.Lock()
_json_cache = {}
_json_lock = threading.Lock()


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if not _allowed(parts.path):
            self.send_error(404)
            return
        upstream = API_ROOT + parts.path
        if parts.query:
            upstream = upstream + "?" + parts.query
        try:
            body, content_type = _fetch(upstream)
        except (HTTPError, URLError, TimeoutError, OSError):
            self.send_error(502)
            return
        if _is_service_description(parts.path):
            body, content_type = _without_tilemap(body)
        self._send(200, content_type, body)

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def proxy_base_url() -> str:
    """Start the proxy if needed and return its base URL."""
    global _server
    with _lock:
        if _server is not None:
            return _server.base_url
        server, port = _bind()
        thread = threading.Thread(
            target=server.serve_forever,
            name="hkmapservices-vector-proxy",
            daemon=True,
        )
        thread.start()
        server.base_url = f"http://{_HOST}:{port}"
        _server = server
        return server.base_url


def stop_proxy() -> None:
    """Stop the proxy when the plugin is unloaded."""
    global _server
    with _lock:
        server = _server
        _server = None
    _close_pool()
    with _json_lock:
        _json_cache.clear()
    if server is not None:
        server.shutdown()
        server.server_close()


def _bind():
    last_error = None
    for port in range(_FIRST_PORT, _FIRST_PORT + 20):
        try:
            server = ThreadingHTTPServer((_HOST, port), _ProxyHandler)
        except OSError as exc:
            last_error = exc
            continue
        server.daemon_threads = True
        return server, port
    raise OSError(f"Could not start the vector tile proxy: {last_error}")


def _allowed(path: str) -> bool:
    return path.startswith("/vt/basemap/") or path.startswith("/vt/label/")


def _is_service_description(path: str) -> bool:
    for crs_key in ("HK80", "WGS84"):
        if path == vector_service_path("basemap", crs_key):
            return True
        for language in ("en", "tc", "sc"):
            if path == vector_service_path("label", crs_key, language):
                return True
    return False


def _without_tilemap(body: bytes) -> tuple:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, "application/json"
    if isinstance(payload, dict):
        payload.pop("tileMap", None)
    return json.dumps(payload).encode("utf-8"), "application/json"


def _fetch(url: str) -> tuple:
    parts = urlsplit(url)
    cacheable = _cacheable(parts.path)
    if cacheable:
        with _json_lock:
            cached = _json_cache.get(url)
        if cached is not None:
            return cached
    timeout = _JSON_TIMEOUT if cacheable else _TILE_TIMEOUT
    body, content_type = _fetch_upstream(url, timeout)
    if cacheable:
        with _json_lock:
            _json_cache[url] = (body, content_type)
    return body, content_type


def _cacheable(path: str) -> bool:
    return _is_service_description(path) or "/resources/styles/" in path


def _fetch_upstream(url: str, timeout: int) -> tuple:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise URLError(f"Unsupported tile URL {url}.")
    path = parts.path or "/"
    if parts.query:
        path = path + "?" + parts.query
    port = parts.port or 443
    last_error = None
    for _attempt in (1, 2):
        conn = _borrow_connection(parts.hostname, port, timeout)
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "Accept-Encoding": "identity",
                    "User-Agent": "hkmapservices",
                    "Connection": "keep-alive",
                    "Host": parts.hostname,
                },
            )
            response = conn.getresponse()
            body = response.read()
            content_type = response.getheader("Content-Type", "application/octet-stream")
            encoding = response.getheader("Content-Encoding", "")
            status = response.status
            reusable = (response.getheader("Connection") or "").lower() != "close"
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            conn.close()
            last_error = exc
            continue
        _return_connection(conn, reusable and status < 400)
        if status >= 400:
            raise HTTPError(url, status, "tile request failed", hdrs=None, fp=None)
        if encoding.lower() == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return body, content_type.split(";", 1)[0]
    raise URLError(last_error)


def _borrow_connection(host: str, port: int, timeout: int):
    with _pool_lock:
        while _pool:
            conn = _pool.pop()
            if getattr(conn, "host", None) == host and getattr(conn, "port", None) == port:
                conn.timeout = timeout
                sock = getattr(conn, "sock", None)
                if sock is not None:
                    sock.settimeout(timeout)
                return conn
            conn.close()
    return http.client.HTTPSConnection(host, port, timeout=timeout)


def _return_connection(conn, reusable: bool) -> None:
    if not reusable:
        conn.close()
        return
    with _pool_lock:
        if len(_pool) < _POOL_LIMIT:
            _pool.append(conn)
            return
    conn.close()


def _close_pool() -> None:
    with _pool_lock:
        connections = list(_pool)
        _pool.clear()
    for conn in connections:
        conn.close()
