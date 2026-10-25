"""SSRF protection for outbound web requests.

Validates DNS answers and pins the actual connection to a validated address.
The transport validates every request, including each redirect hop, while
preserving the original HTTP Host and TLS server name.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

# Cloud metadata endpoints are blocked by the default public-only policy.
# 169.254.0.0/16 is also covered by the link-local check.
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


class SSRFError(ValueError):
    """Raised when a URL targets a non-public address."""


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # Shared-address space (100.64.0.0/10) is neither private nor reserved,
    # but is not globally routable and must not be reachable by public fetches.
    return ip.is_global and not (
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


async def resolve_allowed_ips(
    url: str, *, allow_private: bool = False, allowlist: str = "",
) -> list[str]:
    """Resolve once and validate all answers; callers must connect to these IPs.

    Private destinations are opt-in for administrator-configured webhooks only.
    An allowlist entry grants that exact hostname access to private addresses.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"only http/https URLs are allowed, got: {url}")

    host = parsed.hostname
    if not host:
        raise SSRFError(f"URL has no host: {url}")

    host = host.rstrip(".").lower()
    allowed_hosts = {entry.strip().rstrip(".").lower() for entry in allowlist.split(",") if entry.strip()}
    private_allowed = allow_private or host in allowed_hosts
    if host in _BLOCKED_HOSTS and not private_allowed:
        raise SSRFError(f"URL host is blocked: {host}")

    # Literal IP — skip DNS resolution.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not private_allowed and not _is_public_ip(host):
            raise SSRFError(f"URL resolves to a private/internal address and is blocked: {url}")
        return [host]

    try:
        ips = await _resolve_host(host)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve host: {host}") from e

    if not ips:
        raise SSRFError(f"cannot resolve host: {host}")

    for ip in ips:
        if not private_allowed and not _is_public_ip(ip):
            raise SSRFError(f"URL resolves to a private/internal address and is blocked: {url}")

    # Normalize to literal addresses even for allowlisted/private destinations:
    # the transport must never hand a hostname to its connection resolver.
    try:
        return list(dict.fromkeys(str(ipaddress.ip_address(ip)) for ip in ips))
    except ValueError as exc:
        raise SSRFError(f"invalid DNS address for host: {host}") from exc


async def assert_public_url(url: str) -> None:
    """Preflight validation only; use PinnedAsyncTransport for network requests."""
    await resolve_allowed_ips(url)


class PinnedAsyncTransport(httpx.AsyncBaseTransport):
    """Connect to validated literal IPs without a second hostname lookup.

    Each original origin has its own connection pool. HTTPS connections must
    not be shared by different hostnames merely because they share an IP, as
    their certificate validation and SNI belong to different origins.
    """

    def __init__(
        self, *, allow_private: bool = False, allowlist: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._allow_private = allow_private
        self._allowlist = allowlist
        self._transport = transport
        self._pools: dict[tuple[str, str, int | None, str], httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        connect_timeout = request.extensions.get("timeout", {}).get("connect")
        try:
            async with asyncio.timeout(connect_timeout):
                ips = await resolve_allowed_ips(
                    str(request.url), allow_private=self._allow_private, allowlist=self._allowlist,
                )
        except TimeoutError as exc:
            raise httpx.ConnectTimeout("DNS resolution timed out", request=request) from exc

        # Only connect errors may try another validated IP; requests that have
        # reached a server are never replayed (important for webhook POSTs).
        for index, ip in enumerate(ips):
            key = (request.url.scheme, request.url.host, request.url.port, ip)
            transport = self._transport
            if transport is None:
                if key not in self._pools:
                    self._pools[key] = httpx.AsyncHTTPTransport(trust_env=False)
                transport = self._pools[key]
            headers = request.headers.copy()
            headers["Host"] = request.url.netloc.decode("ascii")
            pinned = httpx.Request(
                request.method, request.url.copy_with(host=ip), headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": request.url.raw_host.decode("ascii")},
            )
            try:
                return await transport.handle_async_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(ips) - 1:
                    raise
        raise SSRFError("no validated destination addresses")

    async def aclose(self) -> None:
        transports = [self._transport] if self._transport is not None else list(self._pools.values())
        try:
            for transport in transports:
                await transport.aclose()
        finally:
            self._pools.clear()


async def ssrf_check_hook(request: httpx.Request) -> None:
    """Legacy preflight hook; it does not replace connection-level IP pinning."""
    await assert_public_url(str(request.url))
