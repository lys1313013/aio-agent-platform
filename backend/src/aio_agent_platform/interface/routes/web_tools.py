"""Admin routes for web tool (web_search / web_fetch) runtime configuration.

Config is stored in the system_config table with `web_`-prefixed keys and
overrides env defaults — changes take effect within seconds, no restart.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import SystemConfig
from aio_agent_platform.tools.web.config import DB_KEY_PREFIX, DB_KEYS, web_config

router = APIRouter(tags=["web-tools"])

_SECRET_FIELDS = {"brave_api_key", "tavily_api_key", "firecrawl_api_key"}
_BOOL_FIELDS = {"enabled", "summary_enabled"}
_INT_FIELDS = {"cache_ttl_seconds", "fetch_max_chars"}


class WebToolConfigOut(BaseModel):
    enabled: bool
    search_provider: str
    searxng_url: str
    summary_enabled: bool
    cache_ttl_seconds: int
    fetch_max_chars: int
    has_brave_api_key: bool
    has_tavily_api_key: bool
    has_firecrawl_api_key: bool


class WebToolConfigUpdate(BaseModel):
    enabled: bool | None = None
    search_provider: Literal["auto", "duckduckgo", "brave", "tavily", "searxng"] | None = None
    brave_api_key: str | None = None
    tavily_api_key: str | None = None
    searxng_url: str | None = None
    firecrawl_api_key: str | None = None
    summary_enabled: bool | None = None
    cache_ttl_seconds: int | None = Field(None, ge=0, le=86400)
    fetch_max_chars: int | None = Field(None, ge=500, le=10000)


def _out(effective) -> dict:
    return {
        "enabled": effective.enabled,
        "search_provider": effective.search_provider,
        "searxng_url": effective.searxng_url,
        "summary_enabled": effective.summary_enabled,
        "cache_ttl_seconds": effective.cache_ttl_seconds,
        "fetch_max_chars": effective.fetch_max_chars,
        "has_brave_api_key": bool(effective.brave_api_key),
        "has_tavily_api_key": bool(effective.tavily_api_key),
        "has_firecrawl_api_key": bool(effective.firecrawl_api_key),
    }


@router.get("/api/admin/settings/web", response_model=WebToolConfigOut)
async def get_web_tool_config(_admin: AdminUser) -> dict:
    """Return the effective (DB-over-env) web tool config. Secrets are never returned."""
    return _out(await web_config.get())


@router.put("/api/admin/settings/web", response_model=WebToolConfigOut)
async def update_web_tool_config(
    req: WebToolConfigUpdate,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update web tool config. Omitted fields keep current values.

    For secret fields (API keys): a non-empty value replaces, an empty
    string clears, omission keeps the stored value.
    """
    updates = req.model_dump(exclude_none=True)

    for field, value in updates.items():
        if field not in DB_KEYS:
            continue
        key = f"{DB_KEY_PREFIX}{field}"
        if field in _BOOL_FIELDS:
            stored = "true" if value else "false"
        elif field in _INT_FIELDS:
            stored = str(value)
        else:
            stored = str(value).strip()

        result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
        row = result.scalar_one_or_none()
        if not stored:
            # Cleared — remove the DB row so env default applies again.
            if row:
                await db.delete(row)
            continue
        if row:
            row.value = stored
        else:
            db.add(SystemConfig(key=key, value=stored))

    await db.commit()
    web_config.invalidate()
    return _out(await web_config.get())
