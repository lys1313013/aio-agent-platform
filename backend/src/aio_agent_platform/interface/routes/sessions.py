"""Session CRUD routes."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.core import task_event_log, task_registry
from aio_agent_platform.db import Session
from aio_agent_platform.db.connection import get_db

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ---- Schemas ----


class SessionCreate(BaseModel):
    title: str | None = None
    agent_id: UUID | None = None
    workspace_id: UUID | None = None


class SessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    agent_id: UUID | None = None


class SessionOut(BaseModel):
    id: UUID
    title: str | None
    agent_id: UUID | None = None
    workspace_id: UUID | None = None
    is_pinned: bool
    is_archived: bool
    source: str = "chat"
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
    """File attachment metadata stored alongside a workspace message."""

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


class SessionStatusOut(BaseModel):
    """会话当前处理状态 —— 用于前端进入历史会话时判断是否需要重新连接 SSE。"""

    session_id: str
    is_running: bool
    label: str | None = None
    tool: str | None = None
    source: str | None = None
    started_at: float | None = None


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


@router.get("/{session_id}/status", response_model=SessionStatusOut)
async def get_session_status(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionStatusOut:
    """查询会话当前是否有 AgentLoop / 渠道任务在跑。

    后端不持久化「处理中」状态，只读 Redis 在跑任务注册表。Web 端发送消息时
    没有注册任务，因此 web 会话会返回 is_running=false；从飞书/企微等渠道
    触发的会话会返回 running=true，前端据此显示「重新连接」入口。
    """
    result = await db.execute(
        select(Session.id).where(Session.id == session_id, Session.user_id == user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Session not found")

    task = await task_registry.get_task(user.id, session_id)
    if task is None:
        return SessionStatusOut(session_id=str(session_id), is_running=False)
    return SessionStatusOut(
        session_id=str(session_id),
        is_running=True,
        label=task.label or None,
        tool=task.tool,
        source=task.source or None,
        started_at=task.started_at,
    )


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line (matches chat.py convention)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/{session_id}/events")
async def session_events_stream(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE 回放渠道任务在跑期间的完整 Agent 事件（连接即回放 + 实时尾随）。

    与 ``/{session_id}/status`` 配套：status 告诉前端该会话有渠道任务在跑，这里把
    任务跑过的 thinking/tool_call/tool_result/text_delta/text/done 事件按序下发。
    事件由 channel pipeline 实时写入 Redis Stream（``core.task_event_log``）。
    Redis 不可用时流为空，前端收到 EOF 以 ``closed`` 收尾。
    """
    result = await db.execute(
        select(Session.id).where(Session.id == session_id, Session.user_id == user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Session not found")

    async def event_generator():
        last_id = "0"
        events = await task_event_log.read_events(user.id, session_id)
        for stream_id, ev in events:
            last_id = stream_id
            yield _sse_event(ev)
        if events and events[-1][1].get("type") in ("done", "error"):
            return
        if not events:
            # 无事件且任务已不在跑（Redis 不可用或任务刚结束）：无事可回放
            if await task_registry.get_task(user.id, session_id) is None:
                return

        # 实时尾随：等待 done/error 事件，超时兜底防止连接泄漏
        deadline = time.monotonic() + task_event_log.TASK_TTL_SECONDS
        while time.monotonic() < deadline:
            tail = await task_event_log.tail_events(user.id, session_id, last_id)
            if tail is None:
                yield ": ping\n\n"
                continue
            last_id, ev = tail
            yield _sse_event(ev)
            if ev.get("type") in ("done", "error"):
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: UUID,
    req: SessionUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Session:
    """Update a session (rename title and/or rebind agent)."""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if req.title is not None:
        session.title = req.title
    if req.agent_id is not None:
        session.agent_id = req.agent_id
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
