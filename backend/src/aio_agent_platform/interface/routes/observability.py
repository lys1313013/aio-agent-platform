"""Observability routes: /api/observability (admin).

Queries the observability detail/pre-aggregated tables written by
``observation/recorder.py``.  All endpoints require an admin/superadmin role.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import (
    Agent,
    AgentTraceLog,
    LLMCallLog,
    Session,
    Tenant,
    ToolCallLog,
    User,
)

router = APIRouter(prefix="/api/observability", tags=["observability"])


def _require_admin(user: User) -> None:
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )


# ---- Time helpers ----


def _resolve_range(
    window: str = "24h",
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[datetime, datetime]:
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start and end must be provided together",
        )
    if start is not None and end is not None:
        start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
        end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
        if start >= end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start must be earlier than end",
            )
        return start, end
    now = datetime.now(UTC)
    if window == "1h":
        return now - timedelta(hours=1), now
    if window == "7d":
        return now - timedelta(days=7), now
    return now - timedelta(hours=24), now


def _bucket_expr(column, window: str):
    trunc = "minute" if window == "1h" else "day" if window == "7d" else "hour"
    # 先转 UTC 墙钟再截断，保证 bucket 边界与 _fill_series 的 UTC 对齐一致
    return func.timezone("UTC", func.date_trunc(trunc, func.timezone("UTC", column)))


def _bucket_step(window: str) -> timedelta:
    if window == "1h":
        return timedelta(minutes=1)
    if window == "7d":
        return timedelta(days=1)
    return timedelta(hours=1)


def _fill_series(
    start: datetime,
    end: datetime,
    step: timedelta,
    data: dict[datetime, float],
) -> list[dict]:
    def align(dt: datetime) -> datetime:
        # 对齐到 bucket 边界（UTC），与 date_trunc 返回的键保持一致
        dt = dt.astimezone(UTC)
        if step == timedelta(days=1):
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if step == timedelta(minutes=1):
            return dt.replace(second=0, microsecond=0)
        return dt.replace(minute=0, second=0, microsecond=0)

    points: list[dict] = []
    t = align(start)
    end_key = align(end)
    while t <= end_key:
        points.append({"ts": t.isoformat(), "value": data.get(t, 0.0)})
        t += step
    return points


# ---- Schemas ----


class OverviewCards(BaseModel):
    llm_requests: int
    tool_requests: int
    llm_error_rate: float
    tool_error_rate: float
    avg_ttft_ms: float | None
    p95_latency_ms: float | None
    total_tokens: int
    context_util_p95: float | None
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cache_hit_rate: float


class OverviewOut(BaseModel):
    cards: OverviewCards
    series: dict[str, list[dict]]


class TraceItem(BaseModel):
    trace_id: UUID
    session_id: UUID | None
    agent_id: UUID | None
    status: str
    iteration_count: int
    tool_call_count: int
    total_tokens: int
    duration_ms: int | None
    created_at: datetime
    session_title: str | None = None


class TracePage(BaseModel):
    items: list[TraceItem]
    total: int


class DistributionItem(BaseModel):
    key: str
    label: str
    request_count: int = 0
    total_tokens: int = 0
    error_count: int = 0
    avg_duration_ms: float | None = None


class ToolRankItem(BaseModel):
    tool_name: str
    request_count: int
    error_count: int
    error_rate: float
    avg_duration_ms: float | None
    p95_duration_ms: float | None
    total_injected_tokens: int


class ToolTrendPoint(BaseModel):
    ts: str
    request_count: int
    error_count: int


class QualityOut(BaseModel):
    trace_count: int
    success_count: int
    error_count: int
    interrupted_count: int
    avg_duration_ms: float | None
    avg_tokens_per_trace: float | None
    avg_llm_calls: float | None
    avg_tool_calls: float | None
    compress_count: int
    saved_tokens: int
    daily: list[dict]


# ---- Overview ----


@router.get("/overview", response_model=OverviewOut)
async def get_overview(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    window: str = Query(default="24h", pattern="^(1h|24h|7d)$"),
    start_at: datetime | None = Query(default=None, alias="start"),
    end_at: datetime | None = Query(default=None, alias="end"),
) -> OverviewOut:
    _require_admin(user)
    start, end = _resolve_range(window, start_at, end_at)

    def _in_range(col):
        return col >= start, col <= end

    # Cards
    llm_total = await db.scalar(
        select(func.count()).where(*_in_range(LLMCallLog.created_at))
    ) or 0
    llm_failed = await db.scalar(
        select(func.count()).where(
            *_in_range(LLMCallLog.created_at),
            LLMCallLog.final_status == "failed",
        )
    ) or 0
    tool_total = await db.scalar(
        select(func.count()).where(*_in_range(ToolCallLog.created_at))
    ) or 0
    tool_failed = await db.scalar(
        select(func.count()).where(
            *_in_range(ToolCallLog.created_at),
            ToolCallLog.is_error.is_(True),
        )
    ) or 0
    avg_ttft = await db.scalar(
        select(func.avg(LLMCallLog.ttft_ms)).where(*_in_range(LLMCallLog.created_at))
    )
    p95_latency = await db.scalar(
        select(func.percentile_cont(0.95).within_group(LLMCallLog.duration_ms)).where(
            *_in_range(LLMCallLog.created_at),
            LLMCallLog.duration_ms.is_not(None),
        )
    )
    total_tokens = await db.scalar(
        select(func.coalesce(func.sum(LLMCallLog.total_tokens), 0)).where(
            *_in_range(LLMCallLog.created_at)
        )
    )
    prompt_tokens = await db.scalar(
        select(func.coalesce(func.sum(LLMCallLog.prompt_tokens), 0)).where(
            *_in_range(LLMCallLog.created_at)
        )
    )
    completion_tokens = await db.scalar(
        select(func.coalesce(func.sum(LLMCallLog.completion_tokens), 0)).where(
            *_in_range(LLMCallLog.created_at)
        )
    )
    cache_read_tokens = await db.scalar(
        select(func.coalesce(func.sum(LLMCallLog.cache_read_tokens), 0)).where(
            *_in_range(LLMCallLog.created_at)
        )
    )
    ctx_p95 = await db.scalar(
        select(
            func.percentile_cont(0.95).within_group(LLMCallLog.context_utilization)
        ).where(
            *_in_range(LLMCallLog.created_at),
            LLMCallLog.context_utilization.is_not(None),
        )
    )

    cards = OverviewCards(
        llm_requests=llm_total,
        tool_requests=tool_total,
        llm_error_rate=round(llm_failed / llm_total * 100, 2) if llm_total else 0.0,
        tool_error_rate=round(tool_failed / tool_total * 100, 2) if tool_total else 0.0,
        avg_ttft_ms=round(avg_ttft, 1) if avg_ttft is not None else None,
        p95_latency_ms=round(p95_latency, 1) if p95_latency is not None else None,
        total_tokens=total_tokens,
        context_util_p95=round(ctx_p95, 4) if ctx_p95 is not None else None,
        prompt_tokens=int(prompt_tokens),
        completion_tokens=int(completion_tokens),
        cache_read_tokens=int(cache_read_tokens),
        cache_hit_rate=round(cache_read_tokens / prompt_tokens * 100, 2) if prompt_tokens else 0.0,
    )

    step = _bucket_step(window)

    # Series: per-bucket aggregation
    llm_bucket = _bucket_expr(LLMCallLog.created_at, window)
    llm_rows = (
        await db.execute(
            select(llm_bucket, func.count()).where(*_in_range(LLMCallLog.created_at))
            .group_by(llm_bucket).order_by(llm_bucket)
        )
    ).all()
    llm_counts = {r[0]: float(r[1]) for r in llm_rows}

    llm_fail_rows = (
        await db.execute(
            select(llm_bucket, func.count()).where(
                *_in_range(LLMCallLog.created_at),
                LLMCallLog.final_status == "failed",
            ).group_by(llm_bucket).order_by(llm_bucket)
        )
    ).all()
    llm_fail = {r[0]: float(r[1]) for r in llm_fail_rows}

    token_rows = (
        await db.execute(
            select(llm_bucket, func.coalesce(func.sum(LLMCallLog.total_tokens), 0))
            .where(*_in_range(LLMCallLog.created_at)).group_by(llm_bucket).order_by(llm_bucket)
        )
    ).all()
    token_map = {r[0]: float(r[1]) for r in token_rows}

    tool_bucket = _bucket_expr(ToolCallLog.created_at, window)
    tool_rows = (
        await db.execute(
            select(tool_bucket, func.count()).where(*_in_range(ToolCallLog.created_at))
            .group_by(tool_bucket).order_by(tool_bucket)
        )
    ).all()
    tool_counts = {r[0]: float(r[1]) for r in tool_rows}

    tool_fail_rows = (
        await db.execute(
            select(tool_bucket, func.count()).where(
                *_in_range(ToolCallLog.created_at),
                ToolCallLog.is_error.is_(True),
            ).group_by(tool_bucket).order_by(tool_bucket)
        )
    ).all()
    tool_fail = {r[0]: float(r[1]) for r in tool_fail_rows}

    return OverviewOut(
        cards=cards,
        series={
            "llm_requests": _fill_series(start, end, step, llm_counts),
            "llm_error_rate": _fill_series(
                start,
                end,
                step,
                {
                    t: round(llm_fail.get(t, 0) / c * 100, 2)
                    for t, c in llm_counts.items()
                },
            ),
            "tool_requests": _fill_series(start, end, step, tool_counts),
            "tool_error_rate": _fill_series(
                start,
                end,
                step,
                {
                    t: round(tool_fail.get(t, 0) / c * 100, 2)
                    for t, c in tool_counts.items()
                },
            ),
            "tokens": _fill_series(start, end, step, token_map),
        },
    )


# ---- Traces ----


@router.get("/traces", response_model=TracePage)
async def list_traces(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
    window: str = Query(default="24h", pattern="^(1h|24h|7d)$"),
    start_at: datetime | None = Query(default=None, alias="start"),
    end_at: datetime | None = Query(default=None, alias="end"),
) -> TracePage:
    _require_admin(user)
    start, end = _resolve_range(window, start_at, end_at)

    conds = [
        AgentTraceLog.created_at >= start,
        AgentTraceLog.created_at <= end,
    ]
    if status_filter:
        conds.append(AgentTraceLog.status == status_filter)
    if agent_id:
        conds.append(AgentTraceLog.agent_id == agent_id)
    if session_id:
        conds.append(AgentTraceLog.session_id == session_id)

    total = await db.scalar(
        select(func.count()).select_from(AgentTraceLog).where(*conds)
    ) or 0

    stmt = (
        select(AgentTraceLog, Session.title)
        .outerjoin(Session, Session.id == AgentTraceLog.session_id)
        .where(*conds)
        .order_by(AgentTraceLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()

    return TracePage(
        items=[
            TraceItem(
                trace_id=r[0].trace_id,
                session_id=r[0].session_id,
                agent_id=r[0].agent_id,
                status=r[0].status,
                iteration_count=r[0].iteration_count,
                tool_call_count=r[0].tool_call_count,
                total_tokens=r[0].total_tokens,
                duration_ms=r[0].duration_ms,
                created_at=r[0].created_at,
                session_title=r[1],
            )
            for r in rows
        ],
        total=total,
    )


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_admin(user)
    trace = await db.scalar(
        select(AgentTraceLog).where(AgentTraceLog.trace_id == trace_id)
    )
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    llm_calls = (
        (await db.execute(
            select(LLMCallLog).where(LLMCallLog.trace_id == trace_id)
            .order_by(LLMCallLog.call_order)
        )).scalars().all()
    )
    tool_calls = (
        (await db.execute(
            select(ToolCallLog).where(ToolCallLog.trace_id == trace_id)
            .order_by(ToolCallLog.call_order)
        )).scalars().all()
    )

    def _as_dict(obj) -> dict:
        d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        # The drawer needs timestamps and relationship ids to explain a trace.
        # Only the internal row primary key is presentation noise.
        d.pop("id", None)
        return d

    return {
        "trace": _as_dict(trace),
        "llm_calls": [_as_dict(c) for c in llm_calls],
        "tool_calls": [_as_dict(t) for t in tool_calls],
    }


# ---- Stats ----


@router.get("/stats", response_model=list[DistributionItem])
async def get_stats(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    by: Literal["model", "agent", "user", "tenant"] = Query(default="model"),
    metric: Literal["tokens", "duration", "error", "count"] = Query(default="tokens"),
    window: str = Query(default="7d", pattern="^(1h|24h|7d)$"),
    start_at: datetime | None = Query(default=None, alias="start"),
    end_at: datetime | None = Query(default=None, alias="end"),
) -> list[DistributionItem]:
    _require_admin(user)
    start, end = _resolve_range(window, start_at, end_at)

    if by == "model":
        rows = (
            await db.execute(
                select(
                    LLMCallLog.model,
                    func.count(),
                    func.coalesce(func.sum(LLMCallLog.total_tokens), 0),
                    func.coalesce(
                        func.sum(
                            case((LLMCallLog.final_status == "failed", 1), else_=0)
                        ), 0,
                    ),
                    func.avg(LLMCallLog.duration_ms),
                )
                .where(
                    LLMCallLog.created_at >= start,
                    LLMCallLog.created_at <= end,
                    LLMCallLog.model.is_not(None),
                )
                .group_by(LLMCallLog.model)
            )
        ).all()
        items = [
            DistributionItem(
                key=r[0],
                label=r[0],
                request_count=int(r[1]),
                total_tokens=int(r[2]),
                error_count=int(r[3]),
                avg_duration_ms=round(r[4], 1) if r[4] is not None else None,
            )
            for r in rows
        ]
        return _sort_distribution(items, metric)

    # agent / user / tenant 按 trace 聚合
    dim_col = {
        "agent": AgentTraceLog.agent_id,
        "user": AgentTraceLog.user_id,
        "tenant": AgentTraceLog.tenant_id,
    }[by]

    rows = (
        await db.execute(
            select(
                dim_col,
                func.count(),
                func.coalesce(func.sum(AgentTraceLog.total_tokens), 0),
                func.coalesce(
                    func.sum(
                        case((AgentTraceLog.status == "error", 1), else_=0)
                    ), 0,
                ),
                func.avg(AgentTraceLog.duration_ms),
            )
            .where(
                AgentTraceLog.created_at >= start,
                AgentTraceLog.created_at <= end,
                dim_col.is_not(None),
            )
            .group_by(dim_col)
        )
    ).all()

    keys = [r[0] for r in rows]
    labels: dict = {}
    if keys:
        id_col = {
            "agent": Agent.id,
            "user": User.id,
            "tenant": Tenant.id,
        }[by]
        name_col = {
            "agent": Agent.name,
            "user": User.username,
            "tenant": Tenant.name,
        }[by]
        label_rows = (
            await db.execute(
                select(id_col, name_col).where(id_col.in_(keys))
            )
        ).all()
        labels = {r[0]: r[1] for r in label_rows}

    items = [
        DistributionItem(
            key=str(r[0]),
            label=labels.get(r[0], str(r[0])[:8]),
            request_count=int(r[1]),
            total_tokens=int(r[2]),
            error_count=int(r[3]),
            avg_duration_ms=round(r[4], 1) if r[4] is not None else None,
        )
        for r in rows
    ]
    return _sort_distribution(items, metric)


def _sort_distribution(items: list[DistributionItem], metric: str) -> list[DistributionItem]:
    if metric == "count":
        items.sort(key=lambda i: i.request_count, reverse=True)
    elif metric == "error":
        items.sort(key=lambda i: i.error_count, reverse=True)
    elif metric == "duration":
        items.sort(key=lambda i: i.avg_duration_ms or 0, reverse=True)
    else:
        items.sort(key=lambda i: i.total_tokens, reverse=True)
    return items


# ---- Tool ranking & trend ----


@router.get("/tool-ranking", response_model=list[ToolRankItem])
async def get_tool_ranking(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    metric: Literal["duration", "error", "count", "tokens"] = Query(default="count"),
    top: int = Query(default=20, ge=1, le=100),
    window: str = Query(default="7d", pattern="^(1h|24h|7d)$"),
    start_at: datetime | None = Query(default=None, alias="start"),
    end_at: datetime | None = Query(default=None, alias="end"),
) -> list[ToolRankItem]:
    _require_admin(user)
    start, end = _resolve_range(window, start_at, end_at)
    conds = [
        ToolCallLog.created_at >= start,
        ToolCallLog.created_at <= end,
        ToolCallLog.tool_name.is_not(None),
    ]

    rows = (
        await db.execute(
            select(
                ToolCallLog.tool_name,
                func.count(),
                func.coalesce(
                    func.sum(case((ToolCallLog.is_error.is_(True), 1), else_=0)), 0,
                ),
                func.avg(ToolCallLog.duration_ms),
                func.percentile_cont(0.95).within_group(ToolCallLog.duration_ms),
                func.coalesce(func.sum(ToolCallLog.est_injected_tokens), 0),
            )
            .where(*conds)
            .group_by(ToolCallLog.tool_name)
        )
    ).all()

    items: list[ToolRankItem] = []
    for r in rows:
        total = int(r[1])
        err = int(r[2])
        items.append(
            ToolRankItem(
                tool_name=r[0],
                request_count=total,
                error_count=err,
                error_rate=round(err / total * 100, 2) if total else 0.0,
                avg_duration_ms=round(r[3], 1) if r[3] is not None else None,
                p95_duration_ms=round(r[4], 1) if r[4] is not None else None,
                total_injected_tokens=int(r[5]),
            )
        )

    if metric == "error":
        items.sort(key=lambda i: i.error_rate, reverse=True)
    elif metric == "duration":
        items.sort(key=lambda i: i.avg_duration_ms or 0, reverse=True)
    elif metric == "tokens":
        items.sort(key=lambda i: i.total_injected_tokens, reverse=True)
    else:
        items.sort(key=lambda i: i.request_count, reverse=True)
    return items[:top]


@router.get("/tools/{tool}/trend", response_model=list[ToolTrendPoint])
async def get_tool_trend(
    tool: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    granularity: Literal["minute", "hour", "day"] = Query(default="hour"),
    window: str = Query(default="24h", pattern="^(1h|24h|7d)$"),
    start_at: datetime | None = Query(default=None, alias="start"),
    end_at: datetime | None = Query(default=None, alias="end"),
) -> list[ToolTrendPoint]:
    _require_admin(user)
    s, e = _resolve_range(window, start_at, end_at)
    # Use UTC wall-clock buckets consistently with the overview endpoint.
    bucket = func.timezone(
        "UTC",
        func.date_trunc(
            granularity,
            func.timezone("UTC", ToolCallLog.created_at),
        ),
    )

    rows = (
        await db.execute(
            select(
                bucket,
                func.count(),
                func.coalesce(
                    func.sum(case((ToolCallLog.is_error.is_(True), 1), else_=0)), 0,
                ),
            )
            .where(
                ToolCallLog.tool_name == tool,
                ToolCallLog.created_at >= s,
                ToolCallLog.created_at <= e,
            )
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    count_map = {r[0]: float(r[1]) for r in rows}
    error_map = {r[0]: float(r[2]) for r in rows}
    step = {
        "minute": timedelta(minutes=1),
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
    }[granularity]
    count_series = _fill_series(s, e, step, count_map)
    error_series = _fill_series(s, e, step, error_map)
    return [
        ToolTrendPoint(
            ts=point["ts"],
            request_count=int(point["value"]),
            error_count=int(error_series[index]["value"]),
        )
        for index, point in enumerate(count_series)
    ]


# ---- Quality ----


@router.get("/quality", response_model=QualityOut)
async def get_quality(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    window: str = Query(default="7d", pattern="^(1h|24h|7d)$"),
    start_at: datetime | None = Query(default=None, alias="start"),
    end_at: datetime | None = Query(default=None, alias="end"),
) -> QualityOut:
    _require_admin(user)
    start, end = _resolve_range(window, start_at, end_at)

    row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(case((AgentTraceLog.status == "completed", 1), else_=0)), 0,
                ),
                func.coalesce(
                    func.sum(case((AgentTraceLog.status == "error", 1), else_=0)), 0,
                ),
                func.coalesce(
                    func.sum(case((AgentTraceLog.status == "interrupted", 1), else_=0)), 0,
                ),
                func.avg(AgentTraceLog.duration_ms),
                func.coalesce(func.sum(AgentTraceLog.total_tokens), 0),
                func.coalesce(func.sum(AgentTraceLog.iteration_count), 0),
                func.coalesce(func.sum(AgentTraceLog.tool_call_count), 0),
                func.coalesce(
                    func.sum(case((AgentTraceLog.is_compressed.is_(True), 1), else_=0)), 0,
                ),
                func.coalesce(func.sum(AgentTraceLog.saved_tokens), 0),
            )
            .select_from(AgentTraceLog)
            .where(AgentTraceLog.created_at >= start, AgentTraceLog.created_at <= end)
        )
    ).one()
    count, completed, errors, interrupted, avg_dur, total_tok, llm_calls, tool_calls, comp, saved = row

    bucket_expr = _bucket_expr(AgentTraceLog.created_at, window)
    daily_rows = (
        await db.execute(
            select(
                bucket_expr,
                func.count(),
                func.coalesce(
                    func.sum(case((AgentTraceLog.status == "completed", 1), else_=0)), 0,
                ),
                func.coalesce(func.sum(AgentTraceLog.total_tokens), 0),
                func.avg(AgentTraceLog.duration_ms),
                func.coalesce(
                    func.sum(case((AgentTraceLog.is_compressed.is_(True), 1), else_=0)), 0,
                ),
                func.coalesce(func.sum(AgentTraceLog.saved_tokens), 0),
            )
            .where(AgentTraceLog.created_at >= start, AgentTraceLog.created_at <= end)
            .group_by(bucket_expr)
            .order_by(bucket_expr)
        )
    ).all()

    return QualityOut(
        trace_count=int(count),
        success_count=int(completed),
        error_count=int(errors),
        interrupted_count=int(interrupted),
        avg_duration_ms=round(avg_dur, 1) if avg_dur is not None else None,
        avg_tokens_per_trace=round(total_tok / count, 1) if count else None,
        avg_llm_calls=round(llm_calls / count, 2) if count else None,
        avg_tool_calls=round(tool_calls / count, 2) if count else None,
        compress_count=int(comp),
        saved_tokens=int(saved),
        daily=[
            {
                "ts": r[0].isoformat(),
                "trace_count": int(r[1]),
                "success_count": int(r[2]),
                "total_tokens": int(r[3]),
                "avg_duration_ms": round(r[4], 1) if r[4] is not None else None,
                "compress_count": int(r[5]),
                "saved_tokens": int(r[6]),
            }
            for r in daily_rows
        ],
    )
