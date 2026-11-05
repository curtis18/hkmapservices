"""Local proxy that makes Lands Department vector tiles usable in QGIS.

QGIS rejects an ArcGIS vector-tile service when its advertised tilemap
URL returns 404. These services do that. The proxy returns the same service
description with the tilemap entry removed, and forwards tile requests to
Lands Department. Upstream requests follow the QGIS proxy settings.
"""

from __future__ import annotations

import base64
import gzip
import http.client
import json
import socket
import ssl
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .catalog import API_ROOT, vector_service_path


_HOST = "127.0.0.1"
_FIRST_PORT = 47880
_TILE_TIMEOUT = 20
_JSON_TIMEOUT = 25
_POOL_LIMIT = 4
_server = None
_proxy = None
_stopping = False
_generation = 0
_lock = threading.Lock()
_pool = []
_pool_lock = threading.Lock()
_json_cache = {}
_json_lock = threading.Lock()


@dataclass(frozen=True)
class _Proxy:
    host: str
    port: int
    user: str = ""
    password: str = ""
    socks: bool = False

    @property
    def key(self) -> tuple:
        return (self.host, self.port, self.user, self.password, self.socks)


class _ProxyServer(ThreadingHTTPServer):
    """Do not share the port with another QGIS process.

    On Windows SO_REUSEADDR lets two servers bind the same port, so QGIS can
    silently reach the wrong helper instance.
    """

    allow_reuse_address = False
    daemon_threads = True


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
    global _server, _proxy, _stopping, _generation
    snapshot = _proxy_snapshot()
    with _lock:
        _stopping = False
        if _proxy != snapshot:
            _proxy = snapshot
            _generation += 1
            _close_pool()
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
    global _server, _proxy, _stopping, _generation
    with _lock:
        _stopping = True
        _generation += 1
        server = _server
        _server = None
        _proxy = None
    _close_pool()
    with _json_lock:
        _json_cache.clear()
    if server is not None:
        server.shutdown()
        server.server_close()


def loopback_proxy_warning() -> str | None:
    """Explain when QGIS would send the local proxy through another proxy."""
    try:
        # Imported here so unit tests can load this module without QGIS.
        from .qgisproxy import proxy_endpoint
    except ImportError:
        return None
    endpoint = proxy_endpoint(f"http://{_HOST}/")
    if endpoint is None:
        return None
    host = endpoint["host"]
    return (
        f"QGIS is sending the local vector tile proxy through {host}:{endpoint['port']}. "
        f"Add http://{_HOST} to Settings → Options → Network → "
        "No proxy for (URLs starting with), then reload the basemap."
    )


def _bind():
    last_error = None
    for port in range(_FIRST_PORT, _FIRST_PORT + 20):
        try:
            server = _ProxyServer((_HOST, port), _ProxyHandler)
        except OSError as exc:
            last_error = exc
            continue
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
    for attempt in (1, 2):
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
        except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError) as exc:
            conn.close()
            if attempt == 1:
                continue
            raise URLError(exc)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            conn.close()
            raise URLError(exc)
        _return_connection(conn, reusable and status < 400)
        if status >= 400:
            raise HTTPError(url, status, "tile request failed", hdrs=None, fp=None)
        if encoding.lower() == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        return body, content_type.split(";", 1)[0]
    raise URLError("The tile request failed.")


def _proxy_snapshot():
    """Resolve QGIS's proxy for the upstream service on the main thread."""
    try:
        # Imported here so unit tests can load this module without QGIS.
        from .qgisproxy import proxy_endpoint
    except ImportError:
        return None
    endpoint = proxy_endpoint(API_ROOT + "/")
    if endpoint is None:
        return None
    socks = endpoint["socks"]
    port = endpoint["port"] or (1080 if socks else 8080)
    return _Proxy(
        host=endpoint["host"],
        port=port,
        user=endpoint["user"],
        password=endpoint["password"],
        socks=socks,
    )


def _authorization_value(user: str, password: str) -> str:
    if not user and not password:
        return ""
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _connect_request(host: str, port: int, authorization: str = "") -> bytes:
    lines = [
        f"CONNECT {host}:{port} HTTP/1.1",
        f"Host: {host}:{port}",
        "Proxy-Connection: Keep-Alive",
    ]
    if authorization:
        lines.append(f"Proxy-Authorization: {authorization}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def _connect_status(header: bytes) -> int:
    line = header.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise OSError("The proxy returned an invalid response.")
    try:
        return int(parts[1])
    except ValueError as exc:
        raise OSError("The proxy returned an invalid response.") from exc


class _TunneledHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection opened through an HTTP CONNECT or SOCKS5 proxy."""

    def __init__(self, proxy: _Proxy, host: str, port: int, timeout: int):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._proxy = proxy

    def connect(self) -> None:
        raw = socket.create_connection((self._proxy.host, self._proxy.port), self.timeout)
        try:
            if self._proxy.socks:
                _socks5_handshake(raw, self._proxy, self.host, self.port)
            else:
                _http_connect(raw, self._proxy, self.host, self.port)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _http_connect(sock, proxy: _Proxy, host: str, port: int) -> None:
    authorization = _authorization_value(proxy.user, proxy.password)
    sock.sendall(_connect_request(host, port, authorization))
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("The proxy closed the connection.")
        header += chunk
        if len(header) > 65536:
            raise OSError("The proxy returned an oversized response.")
    status = _connect_status(header)
    if status != 200:
        raise OSError(f"The proxy refused the connection (HTTP {status}).")


def _socks5_handshake(sock, proxy: _Proxy, host: str, port: int) -> None:
    authenticated = bool(proxy.user or proxy.password)
    sock.sendall(b"\x05\x02\x00\x02" if authenticated else b"\x05\x01\x00")
    reply = _recv_exact(sock, 2)
    if reply[0] != 5:
        raise OSError("The SOCKS5 proxy returned an invalid response.")
    method = reply[1]
    if method == 2:
        user = proxy.user.encode("utf-8")
        password = proxy.password.encode("utf-8")
        if len(user) > 255 or len(password) > 255:
            raise OSError("The SOCKS5 proxy username or password is too long.")
        sock.sendall(
            b"\x01"
            + bytes((len(user),))
            + user
            + bytes((len(password),))
            + password
        )
        auth_reply = _recv_exact(sock, 2)
        if auth_reply[0] != 1 or auth_reply[1] != 0:
            raise OSError("The SOCKS5 proxy rejected the username or password.")
    elif method != 0:
        raise OSError("The SOCKS5 proxy did not accept a supported authentication method.")
    host_bytes = host.encode("idna")
    if len(host_bytes) > 255:
        raise OSError("The upstream host name is too long.")
    sock.sendall(
        b"\x05\x01\x00\x03"
        + bytes((len(host_bytes),))
        + host_bytes
        + port.to_bytes(2, "big")
    )
    reply = _recv_exact(sock, 4)
    if reply[0] != 5:
        raise OSError("The SOCKS5 proxy returned an invalid response.")
    if reply[1] != 0:
        raise OSError(f"The SOCKS5 proxy could not connect (code {reply[1]}).")
    address_type = reply[3]
    if address_type == 1:
        _recv_exact(sock, 4)
    elif address_type == 3:
        _recv_exact(sock, _recv_exact(sock, 1)[0])
    elif address_type == 4:
        _recv_exact(sock, 16)
    else:
        raise OSError("The SOCKS5 proxy returned an invalid address.")
    _recv_exact(sock, 2)


def _recv_exact(sock, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("The proxy closed the connection.")
        data += chunk
    return data


def _borrow_connection(host: str, port: int, timeout: int):
    proxy = _proxy
    key = proxy.key if proxy is not None else None
    with _pool_lock:
        while _pool:
            conn = _pool.pop()
            if (
                getattr(conn, "host", None) == host
                and getattr(conn, "port", None) == port
                and getattr(conn, "_hk_proxy", None) == key
            ):
                conn.timeout = timeout
                sock = getattr(conn, "sock", None)
                if sock is not None:
                    sock.settimeout(timeout)
                _checkout(conn)
                return conn
            conn.close()
    conn = _open_upstream(proxy, host, port, timeout)
    conn._hk_proxy = key
    _checkout(conn)
    return conn


def _open_upstream(proxy, host: str, port: int, timeout: int):
    if proxy is None:
        return http.client.HTTPSConnection(host, port, timeout=timeout)
    return _TunneledHTTPSConnection(proxy, host, port, timeout)


def _checkout(conn) -> None:
    conn._hk_checked_out = True
    conn._hk_generation = _generation


def _return_connection(conn, reusable: bool) -> None:
    """Close a finished connection on this handler thread when the server is stopping."""
    stale = getattr(conn, "_hk_generation", None) != _generation
    conn._hk_checked_out = False
    if _stopping or stale or not reusable:
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
