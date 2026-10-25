"""Connection-level SSRF regressions; all sockets and DNS are simulated."""

from unittest.mock import AsyncMock

import httpx
import pytest

from aio_agent_platform.tools.web import ssrf
from aio_agent_platform.tools.web.ssrf import PinnedAsyncTransport, SSRFError


class HTTPStream:
    """Minimal network stream consumed by the real HTTPX/httpcore transport."""

    def __init__(self, response=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"):
        self.response = response
        self.written = b""
        self.server_name = None
        self.closed = False

    async def read(self, max_bytes, timeout=None):
        data, self.response = self.response[:max_bytes], self.response[max_bytes:]
        return data

    async def write(self, data, timeout=None):
        self.written += data

    async def aclose(self):
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.server_name = server_hostname
        assert ssl_context.check_hostname
        return self

    def get_extra_info(self, info):
        return None


async def test_rebinding_does_not_change_actual_tcp_destination(monkeypatch):
    """The real TCP transport receives the checked IP, never the attack domain."""
    resolver = AsyncMock(side_effect=[["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(ssrf, "_resolve_host", resolver)
    stream = HTTPStream()
    connections = []

    async def connect_tcp(self, host, port, **kwargs):
        connections.append((host, port))
        return stream

    monkeypatch.setattr("httpcore._backends.auto.AutoBackend.connect_tcp", connect_tcp)
    # A system proxy must not bypass the checked direct connection.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9999")
    async with httpx.AsyncClient(transport=PinnedAsyncTransport(), trust_env=False) as client:
        response = await client.get("https://rebind.example:8443/path?q=1")
        assert response.text == "ok"
        assert response.url == httpx.URL("https://rebind.example:8443/path?q=1")
        assert resolver.await_count == 1
        assert connections == [("93.184.216.34", 8443)]
        assert stream.server_name == "rebind.example"
        assert b"Host: rebind.example:8443\r\n" in stream.written
        assert b"GET /path?q=1 HTTP/1.1\r\n" in stream.written
        # A later request rechecks DNS even if a public pooled connection exists.
        with pytest.raises(SSRFError):
            await client.get("https://rebind.example:8443/again")
        assert len(connections) == 1
    assert stream.closed


async def test_redirect_resolves_and_blocks_private_destination(monkeypatch):
    resolver = AsyncMock(side_effect=[["93.184.216.34"], ["10.0.0.1"]])
    monkeypatch.setattr(ssrf, "_resolve_host", resolver)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://internal.example/admin"})

    transport = PinnedAsyncTransport(transport=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=True) as client:
        with pytest.raises(SSRFError):
            await client.get("https://public.example/")
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert resolver.await_count == 2


async def test_relative_redirect_keeps_original_origin_and_pins_again(monkeypatch):
    resolver = AsyncMock(side_effect=[["93.184.216.34"], ["1.1.1.1"]])
    monkeypatch.setattr(ssrf, "_resolve_host", resolver)
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "/next"})
        return httpx.Response(200, text="redirected")

    transport = PinnedAsyncTransport(transport=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=True) as client:
        response = await client.get("https://public.example/start")
    assert response.url == httpx.URL("https://public.example/next")
    assert [r.url.host for r in requests] == ["93.184.216.34", "1.1.1.1"]
    assert [r.headers["host"] for r in requests] == ["public.example", "public.example"]


async def test_https_origins_sharing_ip_use_distinct_tls_connections(monkeypatch):
    monkeypatch.setattr(ssrf, "_resolve_host", AsyncMock(return_value=["93.184.216.34"]))
    streams = []

    async def connect_tcp(self, host, port, **kwargs):
        stream = HTTPStream()
        streams.append(stream)
        return stream

    monkeypatch.setattr("httpcore._backends.auto.AutoBackend.connect_tcp", connect_tcp)
    async with httpx.AsyncClient(transport=PinnedAsyncTransport(), trust_env=False) as client:
        await client.get("https://first.example/")
        await client.get("https://second.example/")
    assert [s.server_name for s in streams] == ["first.example", "second.example"]
    assert all(s.closed for s in streams)


@pytest.mark.parametrize("policy", [{"allow_private": True}, {"allowlist": "allowed.example"}])
async def test_explicit_hook_private_policy_still_pins_address(monkeypatch, policy):
    resolver = AsyncMock(return_value=["127.0.0.1"])
    monkeypatch.setattr(ssrf, "_resolve_host", resolver)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200)

    transport = PinnedAsyncTransport(**policy, transport=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        await client.post("http://allowed.example:8100/hook", content=b"signed body")
    assert requests[0].url.host == "127.0.0.1"
    assert requests[0].headers["host"] == "allowed.example:8100"
    assert requests[0].content == b"signed body"
    assert resolver.await_count == 1


async def test_allowlist_does_not_grant_other_hosts_private_access(monkeypatch):
    monkeypatch.setattr(ssrf, "_resolve_host", AsyncMock(return_value=["127.0.0.1"]))
    responder = AsyncMock()
    transport = PinnedAsyncTransport(
        allowlist="allowed.example", transport=httpx.MockTransport(responder),
    )
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(SSRFError):
            await client.get("http://other.example/")
    responder.assert_not_awaited()


async def test_fallback_only_uses_checked_ips(monkeypatch):
    monkeypatch.setattr(ssrf, "_resolve_host", AsyncMock(return_value=["93.184.216.34", "1.1.1.1"]))
    hosts = []

    def respond(request):
        hosts.append(request.url.host)
        if len(hosts) == 1:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200)

    transport = PinnedAsyncTransport(transport=httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        assert (await client.get("https://public.example/")).status_code == 200
    assert hosts == ["93.184.216.34", "1.1.1.1"]


async def test_web_fetch_uses_pinned_transport(monkeypatch):
    from aio_agent_platform.core.config import WebSettings
    from aio_agent_platform.tools.web.fetch import WebFetcher

    resolver = AsyncMock(return_value=["93.184.216.34"])
    monkeypatch.setattr(ssrf, "_resolve_host", resolver)
    hosts = []
    body = b"<html><body>Public article</body></html>"
    stream = HTTPStream(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
        + str(len(body)).encode() + b"\r\n\r\n" + body,
    )

    async def connect_tcp(self, host, port, **kwargs):
        hosts.append(host)
        return stream

    monkeypatch.setattr("httpcore._backends.auto.AutoBackend.connect_tcp", connect_tcp)
    fetcher = WebFetcher(AsyncMock())
    result = await fetcher._fetch("https://article.example/", WebSettings())
    assert result == "Public article"
    assert hosts == ["93.184.216.34"]
    assert resolver.await_count == 1


async def test_hook_delivery_uses_pinned_transport(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from aio_agent_platform.core.config import settings
    from aio_agent_platform.hooks import dispatcher

    monkeypatch.setattr(settings.hook, "allow_private_urls", False)
    monkeypatch.setattr(settings.hook, "url_allowlist", "")
    resolver = AsyncMock(side_effect=[["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(ssrf, "_resolve_host", resolver)
    stream = HTTPStream()
    hosts = []

    async def connect_tcp(self, host, port, **kwargs):
        hosts.append(host)
        return stream

    monkeypatch.setattr("httpcore._backends.auto.AutoBackend.connect_tcp", connect_tcp)
    hook = SimpleNamespace(
        id=uuid4(), action_type="webhook", timeout_ms=5000, retry_count=0,
        config={"url": "https://hooks.example/receive", "sign": True, "secret": "test-key"},
    )
    result = await dispatcher.execute(hook, {"event": "PostToolUse"})
    assert result.status == "success"
    assert hosts == ["93.184.216.34"]
    assert resolver.await_count == 1
    assert b"Host: hooks.example\r\n" in stream.written
    assert b"X-Hook-Signature:" in stream.written
