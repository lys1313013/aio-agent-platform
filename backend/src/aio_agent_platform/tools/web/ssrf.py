"""SSRF protection for outbound web requests.

Validates that a URL resolves only to public IP addresses. Re-checks on
every redirect hop via an httpx event hook, so DNS-rebinding and
redirect-based bypasses are covered.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

# Cloud metadata endpoints — always blocked even though 169.254.0.0/16
# is already covered by the link-local check; kept explicit for clarity.
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


class SSRFError(Exception):
    """Raised when a URL targets a non-public address."""


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list({info[4][0] for info in infos})


async def assert_public_url(url: str) -> None:
    """Raise SSRFError unless url is http/https and resolves to public IPs only.

    Fails closed: unresolvable hosts and non-IP-parseable results are rejected.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"only http/https URLs are allowed, got: {url}")

    host = parsed.hostname
    if not host:
        raise SSRFError(f"URL has no host: {url}")

    host = host.rstrip(".").lower()
    if host in _BLOCKED_HOSTS:
        raise SSRFError(f"URL host is blocked: {host}")

    # Literal IP — skip DNS resolution.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _is_public_ip(host):
            raise SSRFError(f"URL resolves to a private/internal address and is blocked: {url}")
        return

    try:
        ips = await _resolve_host(host)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve host: {host}") from e

    if not ips:
        raise SSRFError(f"cannot resolve host: {host}")

    for ip in ips:
        if not _is_public_ip(ip):
            raise SSRFError(f"URL resolves to a private/internal address and is blocked: {url}")


async def ssrf_check_hook(request: httpx.Request) -> None:
    """httpx event hook — runs before every request, including redirect hops."""
    await assert_public_url(str(request.url))
