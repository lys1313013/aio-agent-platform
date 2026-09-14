"""Serialized, conservative reconciliation for automatic memory writes."""

from __future__ import annotations

import asyncio
import json
import re
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Memory
from aio_agent_platform.memory.service import (
    MemoryService,
    _merge_meta,
    create_default_provider_for_user,
    memory_scope,
)

logger = structlog.get_logger()


async def reconcile_memory(
    db: AsyncSession,
    user_id: UUID,
    layer: str,
    content: str,
    meta: dict | None = None,
    agent_id: UUID | None = None,
) -> tuple[Memory, str]:
    """Caller owns commit/rollback. Only automatic writes use this path.

    The transaction lock covers candidate selection, model decision and write,
    including the empty-scope case where row locks cannot prevent duplicates.
    """
    content = content.strip()
    if not content or layer not in {"L1", "L2", "L3"}:
        raise ValueError("Memory requires non-empty content and a valid layer")
    await db.execute(select(func.set_config("app.current_user_id", str(user_id), True)))
    lock_key = f"memory:{user_id}:{agent_id}:{layer}"
    async with asyncio.timeout(20):
        while not await db.scalar(
            select(func.pg_try_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
        ):
            await asyncio.sleep(0.05)

    scope = (
        Memory.user_id == user_id,
        memory_scope(Memory, agent_id),
        Memory.layer == layer,
    )
    exact = await db.scalar(
        select(Memory)
        .where(*scope, Memory.content == content)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if exact is not None:
        exact.meta = _merge_meta(exact.meta or {}, meta or {})
        await db.flush()
        await db.refresh(exact)
        return exact, "skipped"

    # Episodic summaries keep their existing text-based policy.
    if layer == "L3":
        return await MemoryService.create_or_update_memory(
            db, user_id, layer, content, meta=meta, agent_id=agent_id
        )

    # Relevance plus recency improves recall for differently phrased facts.
    relevant = await db.scalars(
        select(Memory)
        .where(*scope)
        .order_by(
            func.similarity(Memory.search_vec, MemoryService._tokenize(content)).desc().nullslast(),
            Memory.updated_at.desc(),
        )
        .limit(12)
        .execution_options(populate_existing=True)
    )
    recent = await db.scalars(
        select(Memory)
        .where(*scope)
        .order_by(
            Memory.updated_at.desc(),
            Memory.id,
        )
        .limit(12)
        .execution_options(populate_existing=True)
    )
    candidates = {str(m.id): m for m in [*relevant.all(), *recent.all()]}
    decision = await _decide(user_id, layer, content, candidates) if candidates else None
    if decision is not None and decision["action"] != "create":
        existing = candidates[decision["memory_id"]]
        action = decision["action"]
        merged_meta = _merge_meta(existing.meta or {}, meta or {})
        if action != "skip":
            # Bounded audit trail; never accept history supplied by the model.
            history = list((existing.meta or {}).get("revisions", []))[-9:]
            history.append(
                {
                    "content": existing.content,
                    "updated_at": existing.updated_at.isoformat() if existing.updated_at else None,
                    "source_session": (existing.meta or {}).get("source_session"),
                    "action": action,
                }
            )
            merged_meta["revisions"] = history
            existing.content = decision["content"]
            existing.search_vec = MemoryService._tokenize(existing.content)
        existing.meta = merged_meta
        await db.flush()
        await db.refresh(existing)
        return existing, {"skip": "skipped", "merge": "merged", "update": "updated"}[action]

    # Failure must not turn a lexical resemblance into a destructive overwrite.
    memory = await MemoryService.create_memory(
        db, user_id, layer, content, meta=meta, agent_id=agent_id
    )
    return memory, "created"


async def _decide(user_id: UUID, layer: str, content: str, candidates: dict[str, Memory]):
    from aio_agent_platform.core.prompt import _env
    from aio_agent_platform.llm import LLMMessage

    try:
        async with asyncio.timeout(12):
            provider = await create_default_provider_for_user(user_id)
            if provider is None:
                return None
            response = await provider.complete(
                messages=[
                    LLMMessage(
                        role="system", content=_env.get_template("memory_reconcile.j2").render()
                    ),
                    LLMMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "layer": layer,
                                "incoming": content,
                                "existing": [
                                    {"id": key, "content": value.content}
                                    for key, value in candidates.items()
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ],
                max_tokens=2000,
            )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (response.content or "").strip())
        decision = json.loads(raw)
        if not isinstance(decision, dict) or decision.get("action") not in {
            "create",
            "skip",
            "merge",
            "update",
        }:
            raise ValueError("Invalid memory decision")
        if decision["action"] != "create":
            if (
                not isinstance(decision.get("memory_id"), str)
                or decision["memory_id"] not in candidates
            ):
                raise ValueError("Memory decision target outside candidate scope")
            if decision["action"] != "skip":
                if not isinstance(decision.get("content"), str) or not decision["content"].strip():
                    raise ValueError("Memory decision missing content")
                decision["content"] = decision["content"].strip()
        return decision
    except Exception as exc:
        logger.warning("memory_reconciliation_fallback", error_type=type(exc).__name__)
        return None
