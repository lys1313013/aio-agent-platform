"""Usage analytics routes: /api/analytics."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent, Message, Session, TokenUsageDaily, User

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

Scope = Literal["mine", "global"]


# ---- Schemas ----


class SummaryOut(BaseModel):
    sessions: int
    messages: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int
    active_users: int | None = None  # global scope only
    prev_sessions: int
    prev_messages: int
    prev_total_tokens: int
    prev_request_count: int


class TrendPoint(BaseModel):
    date: date
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    sessions: int


class DistributionItem(BaseModel):
    key: str
    label: str
    sessions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0


class DetailItem(BaseModel):
    date: date
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    request_count: int


class DetailPage(BaseModel):
    items: list[DetailItem]
    total: int


# ---- Helpers ----


def _resolve_range(start: date | None, end: date | None) -> tuple[date, date]:
    today = date.today()
    return (start or today, end or today)


def _check_scope(scope: Scope, user: User) -> UUID | None:
    """Return the user_id filter for 'mine', None (no filter) for 'global'."""
    if scope == "global":
        if user.role not in {"admin", "superadmin"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required for global scope",
            )
        return None
    return user.id


def _usage_filter(uid: UUID | None, start: date, end: date):
    conds = [TokenUsageDaily.date >= start, TokenUsageDaily.date <= end]
    if uid is not None:
        conds.append(TokenUsageDaily.user_id == uid)
    return conds


def _session_filter(uid: UUID | None, start: date, end: date):
    conds = [
        func.date(Session.created_at) >= start,
        func.date(Session.created_at) <= end,
    ]
    if uid is not None:
        conds.append(Session.user_id == uid)
    return conds


def _message_filter(uid: UUID | None, start: date, end: date):
    conds = [
        func.date(Message.created_at) >= start,
        func.date(Message.created_at) <= end,
    ]
    if uid is not None:
        conds.append(Message.user_id == uid)
    return conds


async def _sum_usage(db: AsyncSession, uid: UUID | None, start: date, end: date):
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(TokenUsageDaily.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsageDaily.completion_tokens), 0),
                func.coalesce(func.sum(TokenUsageDaily.total_tokens), 0),
                func.coalesce(func.sum(TokenUsageDaily.request_count), 0),
            ).where(*_usage_filter(uid, start, end))
        )
    ).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])


# ---- Routes ----


@router.get("/summary", response_model=SummaryOut)
async def get_summary(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    scope: Scope = Query(default="mine"),
) -> SummaryOut:
    uid = _check_scope(scope, user)
    start, end = _resolve_range(start, end)
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    prompt, completion, total, requests = await _sum_usage(db, uid, start, end)
    _, _, prev_total, prev_requests = await _sum_usage(db, uid, prev_start, prev_end)

    sessions = await db.scalar(
        select(func.count(Session.id)).where(*_session_filter(uid, start, end))
    )
    prev_sessions = await db.scalar(
        select(func.count(Session.id)).where(*_session_filter(uid, prev_start, prev_end))
    )
    messages = await db.scalar(
        select(func.count(Message.id)).where(*_message_filter(uid, start, end))
    )
    prev_messages = await db.scalar(
        select(func.count(Message.id)).where(*_message_filter(uid, prev_start, prev_end))
    )

    active_users = None
    if uid is None:
        active_users = await db.scalar(
            select(func.count(func.distinct(Session.user_id))).where(
                *_session_filter(None, start, end)
            )
        )

    return SummaryOut(
        sessions=sessions or 0,
        messages=messages or 0,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        request_count=requests,
        active_users=active_users,
        prev_sessions=prev_sessions or 0,
        prev_messages=prev_messages or 0,
        prev_total_tokens=prev_total,
        prev_request_count=prev_requests,
    )


@router.get("/trend", response_model=list[TrendPoint])
async def get_trend(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    scope: Scope = Query(default="mine"),
) -> list[TrendPoint]:
    uid = _check_scope(scope, user)
    start, end = _resolve_range(start, end)

    usage_rows = (
        await db.execute(
            select(
                TokenUsageDaily.date,
                func.coalesce(func.sum(TokenUsageDaily.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsageDaily.completion_tokens), 0),
                func.coalesce(func.sum(TokenUsageDaily.total_tokens), 0),
            )
            .where(*_usage_filter(uid, start, end))
            .group_by(TokenUsageDaily.date)
        )
    ).all()
    usage_by_date = {r[0]: r for r in usage_rows}

    session_rows = (
        await db.execute(
            select(func.date(Session.created_at), func.count(Session.id))
            .where(*_session_filter(uid, start, end))
            .group_by(func.date(Session.created_at))
        )
    ).all()
    sessions_by_date = {r[0]: int(r[1]) for r in session_rows}

    points: list[TrendPoint] = []
    day = start
    while day <= end:
        u = usage_by_date.get(day)
        points.append(
            TrendPoint(
                date=day,
                prompt_tokens=int(u[1]) if u else 0,
                completion_tokens=int(u[2]) if u else 0,
                total_tokens=int(u[3]) if u else 0,
                sessions=sessions_by_date.get(day, 0),
            )
        )
        day += timedelta(days=1)
    return points


@router.get("/distribution", response_model=list[DistributionItem])
async def get_distribution(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    by: Literal["model", "agent", "user"] = Query(default="model"),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    scope: Scope = Query(default="mine"),
) -> list[DistributionItem]:
    uid = _check_scope(scope, user)
    start, end = _resolve_range(start, end)

    if by == "model":
        rows = (
            await db.execute(
                select(
                    TokenUsageDaily.model,
                    func.sum(TokenUsageDaily.prompt_tokens),
                    func.sum(TokenUsageDaily.completion_tokens),
                    func.sum(TokenUsageDaily.total_tokens),
                    func.sum(TokenUsageDaily.request_count),
                )
                .where(*_usage_filter(uid, start, end))
                .group_by(TokenUsageDaily.model)
                .order_by(func.sum(TokenUsageDaily.total_tokens).desc())
            )
        ).all()
        return [
            DistributionItem(
                key=r[0],
                label=r[0],
                prompt_tokens=int(r[1] or 0),
                completion_tokens=int(r[2] or 0),
                total_tokens=int(r[3] or 0),
                request_count=int(r[4] or 0),
            )
            for r in rows
        ]

    if by == "agent":
        rows = (
            await db.execute(
                select(Session.agent_id, func.count(Session.id))
                .where(*_session_filter(uid, start, end))
                .group_by(Session.agent_id)
                .order_by(func.count(Session.id).desc())
            )
        ).all()
        agent_ids = [r[0] for r in rows if r[0] is not None]
        names: dict[UUID, str] = {}
        if agent_ids:
            name_rows = (
                await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
            ).all()
            names = {r[0]: r[1] for r in name_rows}
        return [
            DistributionItem(
                key=str(r[0]) if r[0] else "none",
                label=names.get(r[0], "未绑定智能体") if r[0] else "未绑定智能体",
                sessions=int(r[1]),
            )
            for r in rows
        ]

    # by == "user": global scope only
    if uid is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User distribution requires global scope",
        )
    rows = (
        await db.execute(
            select(
                TokenUsageDaily.user_id,
                func.sum(TokenUsageDaily.prompt_tokens),
                func.sum(TokenUsageDaily.completion_tokens),
                func.sum(TokenUsageDaily.total_tokens),
                func.sum(TokenUsageDaily.request_count),
            )
            .where(*_usage_filter(None, start, end))
            .group_by(TokenUsageDaily.user_id)
            .order_by(func.sum(TokenUsageDaily.total_tokens).desc())
            .limit(20)
        )
    ).all()
    user_ids = [r[0] for r in rows]
    usernames: dict[UUID, str] = {}
    if user_ids:
        name_rows = (
            await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        ).all()
        usernames = {r[0]: r[1] for r in name_rows}
    return [
        DistributionItem(
            key=str(r[0]),
            label=usernames.get(r[0], str(r[0])[:8]),
            prompt_tokens=int(r[1] or 0),
            completion_tokens=int(r[2] or 0),
            total_tokens=int(r[3] or 0),
            request_count=int(r[4] or 0),
        )
        for r in rows
    ]


@router.get("/detail", response_model=DetailPage)
async def get_detail(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    scope: Scope = Query(default="mine"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> DetailPage:
    uid = _check_scope(scope, user)
    start, end = _resolve_range(start, end)

    conds = _usage_filter(uid, start, end)
    total = await db.scalar(
        select(func.count()).select_from(TokenUsageDaily).where(*conds)
    )
    rows = (
        await db.execute(
            select(TokenUsageDaily)
            .where(*conds)
            .order_by(TokenUsageDaily.date.desc(), TokenUsageDaily.model)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return DetailPage(
        items=[
            DetailItem(
                date=r.date if isinstance(r.date, date) else r.date.date(),
                model=r.model,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                total_tokens=r.total_tokens,
                request_count=r.request_count,
            )
            for r in rows
        ],
        total=total or 0,
    )
