"""Delegation history routes — query delegation records."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent, Delegation

router = APIRouter(prefix="/api/delegations", tags=["delegations"])


# ---- Schemas ----


class DelegationOut(BaseModel):
    id: UUID
    parent_session_id: UUID
    parent_agent_id: UUID
    parent_agent_name: str | None = None
    child_agent_id: UUID
    child_agent_name: str | None = None
    depth: int
    task: str
    context: str | None = None
    status: str
    result: str | None = None
    error: str | None = None
    token_usage: dict | None = None
    duration_ms: int = 0
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


# ---- Endpoints ----


@router.get("/sessions/{session_id}", response_model=list[DelegationOut])
async def get_session_delegations(
    session_id: UUID,
    _user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Get all delegations for a session."""
    result = await db.execute(
        select(Delegation)
        .where(Delegation.parent_session_id == session_id)
        .order_by(Delegation.created_at)
    )
    delegations = result.scalars().all()
    return [await _delegation_to_dict(db, d) for d in delegations]


@router.get("/{delegation_id}", response_model=DelegationOut)
async def get_delegation(
    delegation_id: UUID,
    _user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a single delegation by ID."""
    result = await db.execute(
        select(Delegation).where(Delegation.id == delegation_id)
    )
    delegation = result.scalar_one_or_none()
    if not delegation:
        raise HTTPException(status_code=404, detail="委派记录不存在")

    return await _delegation_to_dict(db, delegation)


# ---- Helpers ----


async def _delegation_to_dict(db: AsyncSession, d: Delegation) -> dict:
    """Convert delegation to dict with agent names resolved."""
    # Resolve agent names
    parent_name = None
    child_name = None

    result = await db.execute(
        select(Agent.name).where(Agent.id == d.parent_agent_id)
    )
    parent_name = result.scalar_one_or_none()

    result = await db.execute(
        select(Agent.name).where(Agent.id == d.child_agent_id)
    )
    child_name = result.scalar_one_or_none()

    return {
        "id": d.id,
        "parent_session_id": d.parent_session_id,
        "parent_agent_id": d.parent_agent_id,
        "parent_agent_name": parent_name,
        "child_agent_id": d.child_agent_id,
        "child_agent_name": child_name,
        "depth": d.depth,
        "task": d.task,
        "context": d.context,
        "status": d.status,
        "result": d.result,
        "error": d.error,
        "token_usage": d.token_usage,
        "duration_ms": d.duration_ms,
        "created_at": d.created_at,
        "completed_at": d.completed_at,
    }
