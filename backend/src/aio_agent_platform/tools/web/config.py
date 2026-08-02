"""Runtime web-tool configuration — system_config DB values override env defaults.

Admins edit config via /api/admin/settings/web; values land in the
system_config table with `web_`-prefixed keys. This service overlays them
on the env-driven WebSettings and caches the merged result briefly so
tool calls don't hit the DB every time.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from sqlalchemy import select

from aio_agent_platform.core.config import WebSettings, settings
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import SystemConfig

logger = structlog.get_logger()

DB_KEY_PREFIX = "web_"

DB_KEYS = (
    "enabled",
    "search_provider",
    "brave_api_key",
    "tavily_api_key",
    "searxng_url",
    "firecrawl_api_key",
    "summary_enabled",
    "cache_ttl_seconds",
    "fetch_max_chars",
)

_TRUE_FALSE = ("true", "false")


def overlay(env: WebSettings, db_values: dict[str, str]) -> WebSettings:
    """Merge DB values over env defaults. Empty DB strings mean 'not set'."""
    data = env.model_dump()

    def get_bool(field: str) -> None:
        v = db_values.get(field)
        if v in _TRUE_FALSE:
            data[field] = v == "true"

    def get_str(field: str) -> None:
        v = db_values.get(field)
        if v:
            data[field] = v

    def get_int(field: str) -> None:
        v = db_values.get(field)
        if v and v.lstrip("-").isdigit():
            data[field] = int(v)

    get_bool("enabled")
    get_str("search_provider")
    get_str("brave_api_key")
    get_str("tavily_api_key")
    get_str("searxng_url")
    get_str("firecrawl_api_key")
    get_bool("summary_enabled")
    get_int("cache_ttl_seconds")
    get_int("fetch_max_chars")

    try:
        return WebSettings(**data)
    except Exception:
        logger.warning("invalid web config in DB, falling back to env", db_values=db_values)
        return env


class WebConfigService:
    def __init__(self, ttl_seconds: float = 15.0) -> None:
        self._ttl = ttl_seconds
        self._cached: WebSettings | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._cached = None
        self._expires_at = 0.0

    async def get(self) -> WebSettings:
        if self._cached is not None and time.monotonic() < self._expires_at:
            return self._cached
        async with self._lock:
            if self._cached is not None and time.monotonic() < self._expires_at:
                return self._cached
            merged = await self._load()
            self._cached = merged
            self._expires_at = time.monotonic() + self._ttl
            return merged

    async def _load(self) -> WebSettings:
        env = settings.web
        try:
            factory = get_session_factory()
            async with factory() as db:
                result = await db.execute(
                    select(SystemConfig).where(
                        SystemConfig.key.in_([f"{DB_KEY_PREFIX}{k}" for k in DB_KEYS])
                    )
                )
                db_values = {
                    r.key[len(DB_KEY_PREFIX):]: r.value for r in result.scalars().all()
                }
        except Exception:
            logger.warning("web config DB load failed, using env defaults")
            return env
        return overlay(env, db_values)


web_config = WebConfigService()
