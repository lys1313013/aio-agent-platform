"""Session CRUD routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db import Session
from aio_agent_platform.db.connection import get_db

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ---- Schemas ----


class SessionCreate(BaseModel):
    title: str | None = None
    agent_id: UUID | None = None
    workspace_id: UUID | None = None


class SessionUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)


class SessionOut(BaseModel):
    id: UUID
    title: str | None
    agent_id: UUID | None = None
    workspace_id: UUID | None = None
    is_pinned: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AttachmentInfo(BaseModel):
    """Image attachment metadata stored alongside a user message."""

    key: str
    url: str
    mime: str
    size: int
    filename: str


class FileAttachmentInfo(BaseModel):
    """File attachment metadata stored alongside a user message."""

    file_id: str
    filename: str
    mime: str
    size: int
    workspace_path: str


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str | None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    token_usage: dict | None = None
    attachments: list[AttachmentInfo] | None = None
    file_attachments: list[FileAttachmentInfo] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionDetailOut(SessionOut):
    messages: list[MessageOut] = []


# ---- Routes ----


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    req: SessionCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """Create a new conversation session, auto-bound to user's default workspace."""
    from aio_agent_platform.workspaces.service import WorkspaceService

    default_ws = await WorkspaceService.get_or_create_default(db=db, user_id=user.id)
    workspace_id = req.workspace_id or default_ws.id

    session = Session(
        user_id=user.id,
        agent_id=req.agent_id,
        workspace_id=workspace_id,
        title=req.title or "New Chat",
    )
    db.add(session)
    await db.flush()
    return session


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Session]:
    """List all sessions for the current user, optionally filtered by agent."""
    query = select(Session).where(Session.user_id == user.id)
    if agent_id is not None:
        query = query.where(Session.agent_id == agent_id)
    query = query.order_by(Session.updated_at.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{session_id}", response_model=SessionDetailOut)
async def get_session(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """Get a session with its message history."""
    result = await db.execute(
        select(Session)
        .where(Session.id == session_id, Session.user_id == user.id)
        .options(selectinload(Session.messages))
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: UUID,
    req: SessionUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """Rename a session."""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    session.title = req.title
    await db.flush()
    await db.refresh(session)
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a session and all its messages."""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.delete(session)
