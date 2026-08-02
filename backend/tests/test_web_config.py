"""WebConfigService / overlay / TTLCache tests."""

import time

from aio_agent_platform.core.config import WebSettings
from aio_agent_platform.tools.web.cache import TTLCache
from aio_agent_platform.tools.web.config import WebConfigService, overlay

# ---- overlay ----


def test_overlay_empty_db_returns_env():
    env = WebSettings(brave_api_key="env-key", search_provider="brave")
    merged = overlay(env, {})
    assert merged.brave_api_key == "env-key"
    assert merged.search_provider == "brave"


def test_overlay_db_overrides_env():
    env = WebSettings(brave_api_key="env-key", search_provider="auto", cache_ttl_seconds=900)
    merged = overlay(
        env,
        {
            "search_provider": "tavily",
            "tavily_api_key": "db-key",
            "enabled": "false",
            "summary_enabled": "true",
            "cache_ttl_seconds": "60",
            "fetch_max_chars": "5000",
        },
    )
    assert merged.search_provider == "tavily"
    assert merged.tavily_api_key == "db-key"
    assert merged.brave_api_key == "env-key"  # untouched
    assert merged.enabled is False
    assert merged.summary_enabled is True
    assert merged.cache_ttl_seconds == 60
    assert merged.fetch_max_chars == 5000


def test_overlay_ignores_empty_and_garbage_values():
    env = WebSettings(cache_ttl_seconds=900)
    merged = overlay(
        env,
        {
            "brave_api_key": "",          # empty = not set
            "cache_ttl_seconds": "abc",   # not an int
            "enabled": "maybe",           # not a bool
            "unknown_key": "x",           # not a known field
        },
    )
    assert merged.brave_api_key == ""
    assert merged.cache_ttl_seconds == 900
    assert merged.enabled is True


def test_overlay_invalid_enum_falls_back_to_env():
    env = WebSettings(search_provider="auto")
    merged = overlay(env, {"search_provider": "not-a-provider"})
    assert merged.search_provider == "auto"


# ---- WebConfigService caching ----


async def test_service_caches_load(monkeypatch):
    service = WebConfigService(ttl_seconds=60)
    calls = 0

    async def fake_load():
        nonlocal calls
        calls += 1
        return WebSettings(search_provider="duckduckgo")

    monkeypatch.setattr(service, "_load", fake_load)
    await service.get()
    await service.get()
    assert calls == 1


async def test_service_invalidate_forces_reload(monkeypatch):
    service = WebConfigService(ttl_seconds=60)
    calls = 0

    async def fake_load():
        nonlocal calls
        calls += 1
        return WebSettings()

    monkeypatch.setattr(service, "_load", fake_load)
    await service.get()
    service.invalidate()
    await service.get()
    assert calls == 2


# ---- TTLCache (per-entry ttl) ----


def test_ttl_cache_expiry():
    cache = TTLCache()
    cache.set("a", "1", ttl_seconds=60)
    assert cache.get("a") == "1"

    cache.set("b", "2", ttl_seconds=0)  # disabled → not stored
    assert cache.get("b") is None


def test_ttl_cache_evicts_oldest_when_full():
    cache = TTLCache(maxsize=2)
    cache.set("a", "1", 60)
    cache.set("b", "2", 60)
    cache.set("c", "3", 60)
    assert cache.get("a") is None
    assert cache.get("c") == "3"


def test_ttl_cache_expired_entry_removed(monkeypatch):
    cache = TTLCache()
    cache.set("a", "1", ttl_seconds=60)
    # simulate passage of time past expiry
    key_expiry, _ = cache._store["a"]
    monkeypatch.setattr(time, "monotonic", lambda: key_expiry + 1)
    assert cache.get("a") is None
    assert "a" not in cache._store
