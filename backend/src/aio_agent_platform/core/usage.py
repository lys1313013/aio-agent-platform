"""LLM token usage recording into token_usage_daily."""

import asyncio
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import TokenUsageDaily

logger = structlog.get_logger()

_background_tasks: set[asyncio.Task] = set()


def record_llm_usage(user_id: UUID, model: str, usage: dict | None) -> None:
    """Fire-and-forget: accumulate one LLM call's usage into token_usage_daily."""
    if not usage:
        return
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or prompt + completion
    if prompt == 0 and completion == 0 and total == 0:
        return
    cache_read = int(usage.get("cache_read_tokens") or 0)
    cache_creation = int(usage.get("cache_creation_tokens") or 0)

    task = asyncio.create_task(
        _upsert(user_id, model, prompt, completion, total, cache_read, cache_creation)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _upsert(
    user_id: UUID,
    model: str,
    prompt: int,
    completion: int,
    total: int,
    cache_read: int,
    cache_creation: int,
) -> None:
    today = datetime.now().date()
    try:
        factory = get_session_factory()
        async with factory() as db:
            stmt = pg_insert(TokenUsageDaily).values(
                user_id=user_id,
                date=today,
                model=model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                request_count=1,
                cost_usd=0,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "date", "model"],
                set_={
                    "prompt_tokens": TokenUsageDaily.prompt_tokens + stmt.excluded.prompt_tokens,
                    "completion_tokens": TokenUsageDaily.completion_tokens
                    + stmt.excluded.completion_tokens,
                    "total_tokens": TokenUsageDaily.total_tokens + stmt.excluded.total_tokens,
                    "request_count": TokenUsageDaily.request_count + 1,
                    "cache_read_tokens": TokenUsageDaily.cache_read_tokens
                    + stmt.excluded.cache_read_tokens,
                    "cache_creation_tokens": TokenUsageDaily.cache_creation_tokens
                    + stmt.excluded.cache_creation_tokens,
                },
            )
            await db.execute(stmt)
            await db.commit()
    except Exception:
        logger.exception("token_usage_record_failed", user_id=str(user_id), model=model)
