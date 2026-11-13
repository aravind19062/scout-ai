"""SSRF protection for outbound fetches.

Every URL (including every redirect hop) is checked: http/https only, no embedded credentials,
standard ports only, and *all* resolved addresses must be public (not private, loopback,
link-local, multicast, reserved or unspecified). Residual risk: DNS rebinding between our check
and the HTTP client's own resolution; see docs/SECURITY.md.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.errors import UnsafeURLError

ALLOWED_PORTS = {None, 80, 443, 8080, 8443}
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata"}


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url_syntax(url: str) -> tuple[str, int | None]:
    if len(url) > 2048:
        raise UnsafeURLError("URL too long")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"Scheme not allowed: {parts.scheme!r}")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in URL are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL has no host")
    if host in BLOCKED_HOSTNAMES or host.endswith((".localhost", ".internal", ".local")):
        raise UnsafeURLError(f"Host not allowed: {host}")
    try:
        port = parts.port
    except ValueError as e:
        raise UnsafeURLError("Invalid port") from e
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError(f"Port not allowed: {port}")
    return host, port


async def assert_public_url(url: str, allow_private: bool = False) -> None:
    host, port = check_url_syntax(url)
    if allow_private:
        return
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        addrs = [host]
    else:
        loop = asyncio.get_running_loop()
        try:
            infos = await asyncio.wait_for(
                loop.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM), timeout=5
            )
        except (TimeoutError, socket.gaierror) as e:
            raise UnsafeURLError(f"Could not resolve host: {host}") from e
        addrs = list({i[4][0] for i in infos})
    if not addrs or not all(_ip_is_public(a) for a in addrs):
        raise UnsafeURLError(f"Host resolves to a non-public address: {host}")
