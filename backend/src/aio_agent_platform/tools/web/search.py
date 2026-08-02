"""web_search tool — pluggable search providers with auto-detection."""

from __future__ import annotations

import asyncio
from typing import Protocol, TypedDict

import httpx

from aio_agent_platform.core.config import WebSettings
from aio_agent_platform.tools.web.cache import TTLCache
from aio_agent_platform.tools.web.config import WebConfigService

_DISABLED_MSG = "Error: web tools are disabled by the administrator (Web 工具设置页可开启)."


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[SearchResult]: ...


class DuckDuckGoProvider:
    name = "duckduckgo"

    def __init__(self, timeout: int) -> None:
        self._timeout = timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        return await asyncio.wait_for(asyncio.to_thread(self._search_sync, query, limit), self._timeout)

    @staticmethod
    def _search_sync(query: str, limit: int) -> list[SearchResult]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            hits = ddgs.text(query, max_results=limit)
        return [
            SearchResult(
                title=h.get("title", ""),
                url=h.get("href", ""),
                snippet=h.get("body", ""),
            )
            for h in hits
        ]


class BraveProvider:
    name = "brave"

    def __init__(self, api_key: str, timeout: int) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": limit},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("description", ""),
            )
            for r in data.get("web", {}).get("results", [])
        ]


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, timeout: int) -> None:
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": limit},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])
        ]


class SearXNGProvider:
    name = "searxng"

    def __init__(self, base_url: str, timeout: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/search",
                params={"q": query, "format": "json"},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
            )
            for r in data.get("results", [])[:limit]
        ]


class SearchRouter:
    """Pick a provider from runtime config, run searches, cache results."""

    def __init__(self, config: WebConfigService) -> None:
        self._config = config
        self._cache = TTLCache()

    @staticmethod
    def _build_provider(s: WebSettings) -> SearchProvider:
        timeout = s.fetch_timeout_seconds
        choice = s.search_provider

        if choice == "auto":
            if s.brave_api_key:
                choice = "brave"
            elif s.tavily_api_key:
                choice = "tavily"
            elif s.searxng_url:
                choice = "searxng"
            else:
                choice = "duckduckgo"

        if choice == "brave":
            if not s.brave_api_key:
                raise _ProviderConfigError("brave", "在 Web 工具设置页填写 Brave API Key")
            return BraveProvider(s.brave_api_key, timeout)
        if choice == "tavily":
            if not s.tavily_api_key:
                raise _ProviderConfigError("tavily", "在 Web 工具设置页填写 Tavily API Key")
            return TavilyProvider(s.tavily_api_key, timeout)
        if choice == "searxng":
            if not s.searxng_url:
                raise _ProviderConfigError("searxng", "在 Web 工具设置页填写 SearXNG URL")
            return SearXNGProvider(s.searxng_url, timeout)
        return DuckDuckGoProvider(timeout)

    async def handle(self, args: dict, *_unused) -> str:
        s = await self._config.get()
        if not s.enabled:
            return _DISABLED_MSG

        query = str(args.get("query", "")).strip()
        if not query:
            return "Error: missing required parameter 'query'."
        limit = int(args.get("limit") or 5)
        limit = max(1, min(limit, 10))

        try:
            provider = self._build_provider(s)
        except _ProviderConfigError as e:
            return f"Error: {e}"

        cache_key = f"search:{provider.name}:{query}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            results = await provider.search(query, limit)
        except Exception as e:
            return (
                f"Error: search provider '{provider.name}' failed: {e}. "
                "Check the provider configuration in the Web 工具设置页; "
                "duckduckgo is a key-free fallback."
            )

        if not results:
            output = f'No results found for "{query}".'
        else:
            lines = [f'Search results for "{query}" (provider: {provider.name}):', ""]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}")
                lines.append(f"   {r['url']}")
                if r["snippet"]:
                    lines.append(f"   {r['snippet']}")
                lines.append("")
            output = "\n".join(lines).rstrip()

        self._cache.set(cache_key, output, s.cache_ttl_seconds)
        return output


class _ProviderConfigError(Exception):
    def __init__(self, provider: str, hint: str) -> None:
        super().__init__(f"search provider '{provider}' is selected but not configured: {hint}")
