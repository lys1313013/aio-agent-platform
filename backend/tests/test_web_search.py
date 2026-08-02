"""web_search tests — provider selection, formatting, cache, error semantics."""

import pytest

from aio_agent_platform.core.config import WebSettings
from aio_agent_platform.tools.web.search import (
    BraveProvider,
    DuckDuckGoProvider,
    SearchRouter,
    SearXNGProvider,
    TavilyProvider,
    _ProviderConfigError,
)


class FakeConfig:
    def __init__(self, settings: WebSettings):
        self._settings = settings

    async def get(self) -> WebSettings:
        return self._settings


class FakeProvider:
    name = "fake"

    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error
        self.calls = 0

    async def search(self, query, limit):
        self.calls += 1
        if self._error:
            raise self._error
        return self._results[:limit]


SAMPLE = [
    {"title": "Result One", "url": "https://a.com/1", "snippet": "first snippet"},
    {"title": "Result Two", "url": "https://b.com/2", "snippet": "second snippet"},
]


def _router(provider, monkeypatch=None, **overrides) -> SearchRouter:
    overrides.setdefault("cache_ttl_seconds", 0)
    router = SearchRouter(FakeConfig(WebSettings(**overrides)))
    if provider is not None:
        # staticmethod on the class — patch with a plain function taking settings
        router._build_provider = lambda s: provider
    return router


# ---- provider selection (static _build_provider) ----


def test_auto_defaults_to_duckduckgo():
    p = SearchRouter._build_provider(WebSettings(search_provider="auto"))
    assert isinstance(p, DuckDuckGoProvider)


def test_auto_prefers_brave_when_key_set():
    p = SearchRouter._build_provider(WebSettings(search_provider="auto", brave_api_key="k"))
    assert isinstance(p, BraveProvider)


def test_auto_prefers_searxng_over_duckduckgo():
    p = SearchRouter._build_provider(
        WebSettings(search_provider="auto", searxng_url="http://searxng.internal:8888")
    )
    assert isinstance(p, SearXNGProvider)


def test_explicit_provider_without_key_raises():
    with pytest.raises(_ProviderConfigError):
        SearchRouter._build_provider(WebSettings(search_provider="brave", brave_api_key=""))


def test_tavily_selected_when_configured():
    p = SearchRouter._build_provider(WebSettings(search_provider="tavily", tavily_api_key="k"))
    assert isinstance(p, TavilyProvider)


def test_searxng_selected_when_configured():
    p = SearchRouter._build_provider(
        WebSettings(search_provider="searxng", searxng_url="http://searxng.internal:8888")
    )
    assert isinstance(p, SearXNGProvider)


def test_searxng_without_url_raises():
    with pytest.raises(_ProviderConfigError):
        SearchRouter._build_provider(WebSettings(search_provider="searxng", searxng_url=""))


# ---- SearXNGProvider response parsing ----


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload, captured, **_kwargs):
        self._payload = payload
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url, params=None, headers=None):
        self._captured["url"] = url
        self._captured["params"] = params
        return _FakeResponse(self._payload)


async def test_searxng_search_parses_results(monkeypatch):
    payload = {
        "results": [
            {"title": "T1", "url": "https://a.com", "content": "snippet one"},
            {"title": "T2", "url": "https://b.com", "content": "snippet two"},
            {"title": "T3", "url": "https://c.com", "content": "snippet three"},
        ]
    }
    captured = {}
    monkeypatch.setattr(
        "aio_agent_platform.tools.web.search.httpx.AsyncClient",
        lambda **kw: _FakeClient(payload, captured, **kw),
    )
    provider = SearXNGProvider("http://searxng.internal:8888/", timeout=30)
    results = await provider.search("redis", limit=2)

    assert captured["url"] == "http://searxng.internal:8888/search"
    assert captured["params"]["q"] == "redis"
    assert captured["params"]["format"] == "json"
    assert results == [
        {"title": "T1", "url": "https://a.com", "snippet": "snippet one"},
        {"title": "T2", "url": "https://b.com", "snippet": "snippet two"},
    ]


# ---- handle() behavior ----


async def test_handle_missing_query():
    out = await _router(FakeProvider()).handle({})
    assert out.startswith("Error:") and "query" in out


async def test_handle_disabled_by_config():
    out = await _router(FakeProvider(), enabled=False).handle({"query": "x"})
    assert "disabled by the administrator" in out


async def test_handle_config_error_returned_not_raised():
    router = SearchRouter(FakeConfig(WebSettings(search_provider="tavily", tavily_api_key="")))
    out = await router.handle({"query": "test"})
    assert out.startswith("Error: search provider 'tavily'")


async def test_handle_formats_results():
    out = await _router(FakeProvider(SAMPLE)).handle({"query": "redis 8"})
    assert 'Search results for "redis 8" (provider: fake)' in out
    assert "1. Result One" in out
    assert "https://a.com/1" in out
    assert "first snippet" in out
    assert "2. Result Two" in out


async def test_handle_no_results():
    out = await _router(FakeProvider([])).handle({"query": "zzz"})
    assert 'No results found for "zzz"' in out


async def test_handle_provider_failure_returns_guidance():
    provider = FakeProvider(error=RuntimeError("HTTP 401"))
    out = await _router(provider).handle({"query": "x"})
    assert "search provider 'fake' failed: HTTP 401" in out
    assert "duckduckgo" in out


@pytest.mark.parametrize("limit,expected_calls_arg", [(0, 5), (99, 10), (3, 3)])
async def test_handle_clamps_limit(limit, expected_calls_arg):
    provider = FakeProvider(SAMPLE * 10)
    router = _router(provider)
    captured = {}

    orig_search = provider.search

    async def spy(query, limit):
        captured["limit"] = limit
        return await orig_search(query, limit)

    provider.search = spy
    await router.handle({"query": "x", "limit": limit})
    assert captured["limit"] == expected_calls_arg


async def test_handle_caches_results():
    provider = FakeProvider(SAMPLE)
    router = _router(provider, cache_ttl_seconds=900)
    await router.handle({"query": "redis"})
    out2 = await router.handle({"query": "redis"})
    assert provider.calls == 1
    assert "Result One" in out2
