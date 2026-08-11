"""Daily memory routes — per-day consolidated memory records.

Registered BEFORE memories_router: both live under /api/memories, and the
generic /{memory_id} route would otherwise swallow the /daily prefix.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import DailyMemory
from aio_agent_platform.memory.daily import DailyMemoryService

router = APIRouter(prefix="/api/memories/daily", tags=["memories"])


class DailyMemoryOut(BaseModel):
    id: UUID
    date: date
    content: str
    highlights: list = Field(default_factory=list)
    source_session_ids: list = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[DailyMemoryOut])
async def list_daily_memories(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    day: date | None = Query(default=None, alias="date", description="精确查某一天"),
    start: date | None = Query(default=None, description="范围起始(含)"),
    end: date | None = Query(default=None, description="范围结束(含)"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[DailyMemory]:
    """Query daily memories by exact date or date range (default: recent 30 days)."""
    if day is not None:
        memory = await DailyMemoryService.get_by_date(db, user.id, day)
        return [memory] if memory else []
    if start and end and start > end:
        raise HTTPException(status_code=422, detail="start must be <= end")
    return await DailyMemoryService.list_range(
        db, user.id, start=start, end=end, limit=limit, offset=offset
    )


@router.post("/{day}/regenerate", response_model=DailyMemoryOut)
async def regenerate_daily_memory(
    day: date,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DailyMemory:
    """Re-run LLM consolidation for a specific day (uses its own DB session)."""
    memory = await DailyMemoryService.consolidate_day(user.id, day)
    if memory is None:
        raise HTTPException(
            status_code=404,
            detail="该日期没有可合并的会话内容,或合并未产生有效记录",
        )
    return memory


@router.delete("/{day}", status_code=204)
async def delete_daily_memory(
    day: date,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a day's record (L3 source data is untouched; can regenerate)."""
    deleted = await DailyMemoryService.delete_by_date(db, user.id, day)
    if not deleted:
        raise HTTPException(status_code=404, detail="Daily memory not found")
