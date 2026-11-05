"""Tests for the vector-tile proxy helpers that do not need QGIS."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hkmapservices import vectorproxy


class VectorProxyTests(unittest.TestCase):
    def test_proxy_authorization_header(self) -> None:
        self.assertEqual(
            vectorproxy._authorization_value("user", "pass"),
            "Basic dXNlcjpwYXNz",
        )
        self.assertEqual(vectorproxy._authorization_value("", ""), "")

    def test_connect_request(self) -> None:
        request = vectorproxy._connect_request("example.test", 443, "Basic abc")
        self.assertEqual(
            request,
            (
                b"CONNECT example.test:443 HTTP/1.1\r\n"
                b"Host: example.test:443\r\n"
                b"Proxy-Connection: Keep-Alive\r\n"
                b"Proxy-Authorization: Basic abc\r\n\r\n"
            ),
        )
        self.assertNotIn(b"Proxy-Authorization", vectorproxy._connect_request("example.test", 443))

    def test_connect_status(self) -> None:
        self.assertEqual(
            vectorproxy._connect_status(b"HTTP/1.1 200 Connection established\r\n\r\n"),
            200,
        )
        self.assertEqual(
            vectorproxy._connect_status(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n"),
            407,
        )
        with self.assertRaises(OSError):
            vectorproxy._connect_status(b"not http\r\n\r\n")

    def test_plain_http_upstream_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            vectorproxy._fetch_upstream("http://example.test/tile", 1)

    def test_stop_closes_idle_connections_on_their_own_thread(self) -> None:
        class _Conn:
            def __init__(self):
                self.closed = False
                self._hk_checked_out = False
                self._hk_generation = vectorproxy._generation

            def close(self):
                self.closed = True

        idle = _Conn()
        busy = _Conn()
        busy._hk_checked_out = True
        vectorproxy._pool.append(idle)
        vectorproxy._stopping = True
        try:
            vectorproxy._close_pool()
            self.assertTrue(idle.closed)
            self.assertFalse(busy.closed)
            vectorproxy._return_connection(busy, True)
            self.assertTrue(busy.closed)
            self.assertNotIn(busy, vectorproxy._pool)
        finally:
            vectorproxy._stopping = False
            vectorproxy._pool.clear()

    def test_service_paths_are_limited(self) -> None:
        self.assertTrue(vectorproxy._allowed("/vt/basemap/WGS84"))
        self.assertTrue(vectorproxy._allowed("/vt/label/hk/tc/HK80"))
        self.assertFalse(vectorproxy._allowed("/other"))


if __name__ == "__main__":
    unittest.main()
