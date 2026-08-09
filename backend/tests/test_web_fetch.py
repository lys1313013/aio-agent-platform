"""web_fetch tests — extraction, truncation, error semantics (no real network)."""

from aio_agent_platform.core.config import WebSettings
from aio_agent_platform.tools.web.fetch import WebFetcher

ARTICLE_HTML = """
<html><head><title>Test Article</title></head>
<body>
<nav>Home | About | Contact</nav>
<article>
<h1>Redis 8 发布</h1>
<p>Redis 8 introduces major performance improvements and new data structures
for vector search workloads. The release also improves memory efficiency.</p>
<p>According to the announcement, throughput is up to 2x higher than Redis 7
in common benchmark scenarios, and the new query engine is now GA.</p>
</article>
<footer>Copyright 2026</footer>
<script>console.log('tracker')</script>
</body></html>
"""


class FakeConfig:
    def __init__(self, settings: WebSettings):
        self._settings = settings

    async def get(self) -> WebSettings:
        return self._settings


def _fetcher(**overrides) -> WebFetcher:
    return WebFetcher(FakeConfig(WebSettings(cache_ttl_seconds=0, **overrides)))


# ---- extraction ----


def test_extract_readability_returns_markdown_body():
    text = WebFetcher._extract(ARTICLE_HTML)
    assert "Redis 8" in text
    assert "performance improvements" in text
    assert "tracker" not in text  # script removed


def test_extract_fallback_on_garbage():
    text = WebFetcher._extract("<div>hello world, this is plain text content</div>")
    assert "hello world" in text


def test_extract_empty_page():
    assert WebFetcher._extract("") == "(no readable content extracted)"


# ---- handle() argument / SSRF validation ----


async def test_handle_missing_url():
    out = await _fetcher().handle({})
    assert out.startswith("Error:") and "url" in out


async def test_handle_disabled_by_config():
    out = await _fetcher(enabled=False).handle({"url": "http://example.com"})
    assert "disabled by the administrator" in out


async def test_handle_rejects_non_http_scheme():
    out = await _fetcher().handle({"url": "file:///etc/passwd"})
    assert "only http/https" in out


async def test_handle_rejects_private_ip():
    out = await _fetcher().handle({"url": "http://192.168.1.1/internal"})
    assert "private/internal address" in out


async def test_handle_rejects_metadata_endpoint():
    out = await _fetcher().handle({"url": "http://169.254.169.254/latest/meta-data"})
    assert "blocked" in out


# ---- truncation & formatting (mock _fetch to avoid network) ----


async def test_handle_truncates_to_max_chars(monkeypatch):
    fetcher = _fetcher()
    body = "x" * 5000

    async def fake_fetch(url, s):
        return body

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    out = await fetcher.handle({"url": "http://example.com", "max_chars": 1000})
    assert "truncated, 5000 chars total" in out
    assert out.startswith("# Content from http://example.com")
    assert len(out) < 1200


async def test_handle_max_chars_clamped_to_settings(monkeypatch):
    fetcher = _fetcher(fetch_max_chars=2000)
    body = "x" * 5000

    async def fake_fetch(url, s):
        return body

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    # request 9000 chars but settings cap is 2000
    out = await fetcher.handle({"url": "http://example.com", "max_chars": 9000})
    assert "truncated, 5000 chars total" in out
    assert len(out) < 2200


async def test_handle_propagates_fetch_error(monkeypatch):
    fetcher = _fetcher()

    async def fake_fetch(url, s):
        return "Error: HTTP 403 Forbidden from http://example.com (site may block automated access)"

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    out = await fetcher.handle({"url": "http://example.com"})
    assert out.startswith("Error: HTTP 403")


async def test_handle_caches_success(monkeypatch):
    fetcher = WebFetcher(FakeConfig(WebSettings(cache_ttl_seconds=900)))
    calls = 0

    async def fake_fetch(url, s):
        nonlocal calls
        calls += 1
        return "cached body content"

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    # public literal IP skips DNS
    await fetcher.handle({"url": "http://8.8.8.8/"})
    await fetcher.handle({"url": "http://8.8.8.8/"})
    assert calls == 1


async def test_cache_disabled_when_ttl_zero(monkeypatch):
    fetcher = _fetcher()  # cache_ttl_seconds=0
    calls = 0

    async def fake_fetch(url, s):
        nonlocal calls
        calls += 1
        return "some body"

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    await fetcher.handle({"url": "http://8.8.8.8/"})
    await fetcher.handle({"url": "http://8.8.8.8/"})
    assert calls == 2


# ---- Firecrawl fallback ----


async def test_firecrawl_fallback_used_when_extraction_poor(monkeypatch):
    fetcher = _fetcher(firecrawl_api_key="fc-key")

    async def fake_get(client, url, s):
        return "<html><body></body></html>", "text/html"

    async def fake_firecrawl(url, s):
        return "firecrawl extracted content " * 20

    monkeypatch.setattr(fetcher, "_get_with_size_limit", fake_get)
    monkeypatch.setattr(fetcher, "_extract", lambda html: "tiny")
    monkeypatch.setattr(fetcher, "_fetch_via_firecrawl", fake_firecrawl)

    out = await fetcher._fetch("http://example.com", WebSettings(firecrawl_api_key="fc-key"))
    assert out.startswith("firecrawl extracted content")


async def test_firecrawl_fallback_skipped_without_key(monkeypatch):
    fetcher = _fetcher()  # no firecrawl key

    async def fake_get(client, url, s):
        return "<html><body></body></html>", "text/html"

    monkeypatch.setattr(fetcher, "_get_with_size_limit", fake_get)
    monkeypatch.setattr(fetcher, "_extract", lambda html: "tiny")

    out = await fetcher._fetch("http://example.com", WebSettings())
    assert out == "tiny"


async def test_firecrawl_failure_keeps_local_extraction(monkeypatch):
    fetcher = _fetcher(firecrawl_api_key="fc-key")

    async def fake_get(client, url, s):
        return "<html/>", "text/html"

    async def fake_firecrawl(url, s):
        return None  # API failed

    monkeypatch.setattr(fetcher, "_get_with_size_limit", fake_get)
    monkeypatch.setattr(fetcher, "_extract", lambda html: "tiny local text")
    monkeypatch.setattr(fetcher, "_fetch_via_firecrawl", fake_firecrawl)

    out = await fetcher._fetch("http://example.com", WebSettings(firecrawl_api_key="fc-key"))
    assert out == "tiny local text"


# ---- LLM summary ----


async def test_summary_replaces_truncation_when_enabled(monkeypatch):
    fetcher = _fetcher(summary_enabled=True)
    body = "x" * 5000

    async def fake_fetch(url, s):
        return body

    async def fake_summarize(text, max_chars, tenant_id):
        return "页面核心内容摘要"

    async def fake_execute(*args, **kwargs):
        class FakeResult:
            def scalar_one_or_none(self):
                return "00000000-0000-0000-0000-000000000001"
        return FakeResult()

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, *args, **kwargs):
            return await fake_execute(*args, **kwargs)

    class FakeFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    monkeypatch.setattr(
        "aio_agent_platform.tools.web.summarize.summarize_content", fake_summarize
    )
    monkeypatch.setattr(
        "aio_agent_platform.tools.web.fetch.get_session_factory", FakeFactory
    )

    out = await fetcher.handle({"url": "http://example.com", "max_chars": 1000}, user_id="some-user-id")
    assert "页面核心内容摘要" in out
    assert "summarized by LLM" in out
    assert "truncated" not in out


async def test_summary_falls_back_to_truncation_on_failure(monkeypatch):
    fetcher = _fetcher(summary_enabled=True)
    body = "x" * 5000

    async def fake_fetch(url, s):
        return body

    async def fake_summarize(text, max_chars):
        return None  # no model configured / API error

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    monkeypatch.setattr(
        "aio_agent_platform.tools.web.summarize.summarize_content", fake_summarize
    )

    out = await fetcher.handle({"url": "http://example.com", "max_chars": 1000})
    assert "truncated, 5000 chars total" in out


async def test_summary_disabled_by_default(monkeypatch):
    fetcher = _fetcher()  # summary_enabled defaults to False
    body = "x" * 5000

    async def fake_fetch(url, s):
        return body

    monkeypatch.setattr(fetcher, "_fetch", fake_fetch)
    out = await fetcher.handle({"url": "http://example.com", "max_chars": 1000})
    assert "truncated, 5000 chars total" in out
    assert "summarized" not in out
