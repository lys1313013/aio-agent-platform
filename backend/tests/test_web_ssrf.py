"""SSRF protection tests — table-driven, no real network for blocked cases."""

import pytest

from aio_agent_platform.tools.web.ssrf import SSRFError, assert_public_url

# ---- scheme / format validation ----


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "http://",
        "not-a-url",
    ],
)
async def test_rejects_bad_scheme_or_format(url):
    with pytest.raises(SSRFError):
        await assert_public_url(url)


# ---- literal private/blocked IPs ----


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://127.0.0.2/",
        "http://10.0.0.5/",
        "http://10.255.255.1/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://192.168.1.1/",
        "http://169.254.1.1/",
        "http://169.254.169.254/latest/meta-data",  # AWS metadata
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",  # multicast
        "http://192.0.0.8/",  # reserved (IANA)
    ],
)
async def test_rejects_private_literal_ips(url):
    with pytest.raises(SSRFError):
        await assert_public_url(url)


# ---- public literal IPs pass without DNS ----


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/",
        "https://1.1.1.1/path?q=1",
        "https://[2606:4700:4700::1111]/",
    ],
)
async def test_allows_public_literal_ips(url):
    await assert_public_url(url)


# ---- hostname resolution ----


async def test_rejects_localhost():
    with pytest.raises(SSRFError):
        await assert_public_url("http://localhost:8100/api")


async def test_rejects_blocked_metadata_hostname(monkeypatch):
    async def fake_resolve(host):
        return ["169.254.169.254"]

    monkeypatch.setattr(
        "aio_agent_platform.tools.web.ssrf._resolve_host", fake_resolve
    )
    with pytest.raises(SSRFError):
        await assert_public_url("http://evil.example.com/")


async def test_rejects_when_any_resolved_ip_is_private(monkeypatch):
    async def fake_resolve(host):
        return ["8.8.8.8", "192.168.0.1"]  # one bad apple → reject

    monkeypatch.setattr(
        "aio_agent_platform.tools.web.ssrf._resolve_host", fake_resolve
    )
    with pytest.raises(SSRFError):
        await assert_public_url("http://evil.example.com/")


async def test_allows_public_hostname(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34"]

    monkeypatch.setattr(
        "aio_agent_platform.tools.web.ssrf._resolve_host", fake_resolve
    )
    await assert_public_url("https://example.com/")


async def test_rejects_unresolvable_host(monkeypatch):
    import socket

    async def fake_resolve(host):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(
        "aio_agent_platform.tools.web.ssrf._resolve_host", fake_resolve
    )
    with pytest.raises(SSRFError):
        await assert_public_url("http://nonexistent.invalid/")
