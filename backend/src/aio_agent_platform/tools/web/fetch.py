"""web_fetch tool — fetch a URL and extract readable content as markdown."""

from __future__ import annotations

import re

import httpx
from markdownify import markdownify as to_markdown
from readability import Document

from aio_agent_platform.core.config import WebSettings
from aio_agent_platform.tools.web.cache import TTLCache
from aio_agent_platform.tools.web.config import WebConfigService
from aio_agent_platform.tools.web.ssrf import SSRFError, assert_public_url, ssrf_check_hook

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

_DISABLED_MSG = "Error: web tools are disabled by the administrator (Web 工具设置页可开启)."


class WebFetcher:
    def __init__(self, config: WebConfigService) -> None:
        self._config = config
        self._cache = TTLCache()

    async def handle(self, args: dict, *_unused) -> str:
        s = await self._config.get()
        if not s.enabled:
            return _DISABLED_MSG

        url = str(args.get("url", "")).strip()
        max_chars = int(args.get("max_chars") or s.fetch_max_chars)
        max_chars = max(500, min(max_chars, s.fetch_max_chars))

        if not url:
            return "Error: missing required parameter 'url'."

        try:
            await assert_public_url(url)
        except SSRFError as e:
            return f"Error: {e}"

        cache_key = f"fetch:{url}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            body = cached
        else:
            result = await self._fetch(url, s)
            if result.startswith("Error:"):
                return result
            body = result
            self._cache.set(cache_key, body, s.cache_ttl_seconds)

        if len(body) > max_chars:
            summarized = False
            if s.summary_enabled:
                from aio_agent_platform.tools.web.summarize import summarize_content

                summary = await summarize_content(body, max_chars)
                if summary:
                    body = summary + "\n\n[summarized by LLM from a longer page]"
                    summarized = True
            if not summarized:
                body = body[:max_chars] + f"\n\n... [truncated, {len(body)} chars total]"

        return f"# Content from {url}\n\n{body}"

    async def _fetch(self, url: str, s: WebSettings) -> str:
        timeout = httpx.Timeout(s.fetch_timeout_seconds, connect=10.0)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                max_redirects=s.fetch_max_redirects,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                event_hooks={"request": [ssrf_check_hook]},
            ) as client:
                html, content_type = await self._get_with_size_limit(client, url, s)
        except SSRFError as e:
            return f"Error: {e}"
        except _HTTPStatusError as e:
            return f"Error: {e}"
        except httpx.TimeoutException:
            return f"Error: request timed out after {s.fetch_timeout_seconds}s: {url}"
        except httpx.TooManyRedirects:
            return f"Error: too many redirects (max {s.fetch_max_redirects}): {url}"
        except httpx.HTTPError as e:
            return f"Error: request failed: {e}"

        if not content_type.startswith(("text/html", "application/xhtml")):
            return (
                f"Error: unsupported content type '{content_type}' at {url}. "
                "web_fetch only extracts HTML pages."
            )

        text = self._extract(html)

        # Local extraction got almost nothing (JS-rendered shell, heavy
        # anti-bot page) — fall back to Firecrawl if configured.
        if len(text) < 100 and s.firecrawl_api_key:
            firecrawl_text = await self._fetch_via_firecrawl(url, s)
            if firecrawl_text and len(firecrawl_text) > len(text):
                return firecrawl_text

        return text

    async def _get_with_size_limit(
        self, client: httpx.AsyncClient, url: str, s: WebSettings
    ) -> tuple[str, str]:
        limit = s.fetch_max_response_bytes
        async with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                hint = " (site may block automated access)" if resp.status_code == 403 else ""
                raise _HTTPStatusError(
                    f"HTTP {resp.status_code} {resp.reason_phrase} from {url}{hint}"
                )
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            encoding = resp.charset_encoding or "utf-8"
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes(65536):
                size += len(chunk)
                if size > limit:
                    chunks.append(chunk[: max(0, len(chunk) - (size - limit))])
                    break
                chunks.append(chunk)
        raw = b"".join(chunks)
        return raw.decode(encoding, errors="replace"), content_type

    async def _fetch_via_firecrawl(self, url: str, s: WebSettings) -> str | None:
        """Scrape via Firecrawl API (real browser rendering + bot circumvention)."""
        try:
            async with httpx.AsyncClient(timeout=s.fetch_timeout_seconds) as client:
                resp = await client.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    json={"url": url, "formats": ["markdown"]},
                    headers={
                        "Authorization": f"Bearer {s.firecrawl_api_key}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            markdown = (data.get("data") or {}).get("markdown") or ""
            return markdown.strip() or None
        except Exception:
            return None

    @staticmethod
    def _extract(html: str) -> str:
        """Readability → markdown; fall back to crude tag stripping."""
        try:
            doc = Document(html)
            content_html = doc.summary()
            text = to_markdown(content_html, heading_style="ATX", strip=["img"])
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if len(text) >= 100:
                return text
        except Exception:
            pass

        cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
        text = _TAG_RE.sub(" ", cleaned)
        text = re.sub(r"\s+", " ", text).strip()
        return text or "(no readable content extracted)"


class _HTTPStatusError(Exception):
    pass
