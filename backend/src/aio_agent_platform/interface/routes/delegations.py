"""Delegation history routes — query delegation records."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import ExecutionUser
from aio_agent_platform.core.execution_scope import ExecutionAccessError, require_agent_tenant
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent, Delegation, Session, User

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
    user: ExecutionUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """Page through an authorized session's delegations (default 50, maximum 200)."""
    await _require_session_access(db, session_id, user)

    result = await db.execute(
        select(Delegation)
        .where(Delegation.parent_session_id == session_id, Delegation.user_id == user.id)
        .order_by(Delegation.created_at, Delegation.id)
        .limit(limit).offset(offset)
    )
    delegations = result.scalars().all()
    names = await _agent_names(db, delegations)
    return [_delegation_to_dict(d, names) for d in delegations]


@router.get("/{delegation_id}", response_model=DelegationOut)
async def get_delegation(
    delegation_id: UUID,
    user: ExecutionUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a single delegation by ID (owner only)."""
    result = await db.execute(
        select(Delegation).where(Delegation.id == delegation_id, Delegation.user_id == user.id)
    )
    delegation = result.scalar_one_or_none()
    if not delegation:
        raise HTTPException(status_code=404, detail="委派记录不存在")

    await _require_session_access(db, delegation.parent_session_id, user)

    return _delegation_to_dict(delegation, await _agent_names(db, [delegation]))


# ---- Helpers ----


async def _require_session_access(db: AsyncSession, session_id: UUID, user: User) -> None:
    session = await db.scalar(select(Session).where(
        Session.id == session_id, Session.user_id == user.id,
    ))
    if session is None:
        raise HTTPException(404, "会话不存在")
    if session.source == "room":
        from aio_agent_platform.rooms.service import owned_room
        await owned_room(db, session.id, user)
        return
    # Historical unbound sessions do not carry a reliable tenant. Never infer
    # their ownership from the user's mutable UI selection.
    if session.agent_id is None:
        raise HTTPException(403, "无法确定历史会话的执行租户")
    try:
        await require_agent_tenant(db, user.id, session.agent_id)
    except ExecutionAccessError as error:
        raise HTTPException(403, str(error)) from error


async def _agent_names(db: AsyncSession, delegations: list[Delegation]) -> dict[UUID, str]:
    ids = {agent_id for d in delegations for agent_id in (d.parent_agent_id, d.child_agent_id)}
    if not ids:
        return {}
    rows = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(ids)))
    return dict(rows.all())


def _delegation_to_dict(d: Delegation, names: dict[UUID, str]) -> dict:
    return {
        "id": d.id,
        "parent_session_id": d.parent_session_id,
        "parent_agent_id": d.parent_agent_id,
        "parent_agent_name": names.get(d.parent_agent_id),
        "child_agent_id": d.child_agent_id,
        "child_agent_name": names.get(d.child_agent_id),
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
