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

    task = asyncio.create_task(_upsert(user_id, model, prompt, completion, total))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _upsert(user_id: UUID, model: str, prompt: int, completion: int, total: int) -> None:
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
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "date", "model"],
                set_={
                    "prompt_tokens": TokenUsageDaily.prompt_tokens + stmt.excluded.prompt_tokens,
                    "completion_tokens": TokenUsageDaily.completion_tokens
                    + stmt.excluded.completion_tokens,
                    "total_tokens": TokenUsageDaily.total_tokens + stmt.excluded.total_tokens,
                    "request_count": TokenUsageDaily.request_count + 1,
                },
            )
            await db.execute(stmt)
            await db.commit()
    except Exception:
        logger.exception("token_usage_record_failed", user_id=str(user_id), model=model)
