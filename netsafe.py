"""Outbound HTTP that can only reach public internet addresses (SSRF guard).

Every connection re-resolves the hostname and refuses loopback, private, link-local
and other non-global addresses, including after redirects, so a feed or article URL
can never be used to probe the machine or LAN the bot runs on.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit


def _public_address_info(host: str, port: int | None) -> list[tuple]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Could not resolve URL hostname.") from exc
    if not results or any(not ipaddress.ip_address(result[4][0]).is_global for result in results):
        raise ValueError("URL must resolve only to public IP addresses.")
    return results


def _connect_public_socket(
    host: str, port: int | None, timeout: float | None, source_address: tuple | None
) -> socket.socket:
    last_error = None
    for family, socktype, proto, _canonname, sockaddr in _public_address_info(host, port):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error:
        raise last_error
    raise ValueError("URL hostname has no public addresses.")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        if self._tunnel_host:
            raise ValueError("HTTP tunnels are not allowed.")
        self.sock = _connect_public_socket(
            self.host, self.port, self.timeout, self.source_address
        )


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        if self._tunnel_host:
            raise ValueError("HTTPS tunnels are not allowed.")
        sock = _connect_public_socket(self.host, self.port, self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _validate_public_http_url(url: str) -> None:
    """Reject non-HTTP(S), credential-bearing, and non-public destinations."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must use HTTP or HTTPS and include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed.")
    _public_address_info(parsed.hostname, parsed.port)


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _PublicOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_url_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PublicHTTPHandler(),
        _PublicHTTPSHandler(),
        _PublicOnlyRedirectHandler(),
    )
