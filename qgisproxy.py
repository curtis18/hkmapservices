"""The proxy QGIS would use for one web address."""

from __future__ import annotations

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkProxy, QNetworkProxyQuery

from .compat import enum_member


def proxy_endpoint(url: str):
    """Return type, host, port, username, password, and SOCKS for one address."""
    try:
        factory = QgsNetworkAccessManager.instance().proxyFactory()
    except Exception:
        return None
    if factory is None:
        return None
    try:
        proxies = factory.queryProxy(QNetworkProxyQuery(QUrl(url)))
    except Exception:
        return None
    if not proxies:
        return None
    proxy = proxies[0]
    no_proxy = enum_member(QNetworkProxy, "ProxyType", "NoProxy")
    socks = enum_member(QNetworkProxy, "ProxyType", "Socks5Proxy")
    host = proxy.hostName().strip()
    if not host or proxy.type() == no_proxy:
        return None
    return {
        "type": proxy.type(),
        "host": host,
        "port": int(proxy.port()),
        "user": proxy.user(),
        "password": proxy.password(),
        "socks": proxy.type() == socks,
    }
