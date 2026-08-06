"""Observation recorder — async fire-and-forget collection of observability data.

Writes the three detail tables (llm_call_logs / tool_call_logs / agent_trace_logs)
and pre-aggregates into tool_usage_daily / performance_daily.  Calls only enqueue
into a bounded queue; a background loop batches and flushes every ~2s or every 500
rows.  Failures are logged, never raised, so observability can never block or break
the agent loop.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import structlog
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import (
    AgentTraceLog,
    LLMCallLog,
    PerformanceDaily,
    ToolCallLog,
    ToolUsageDaily,
)

logger = structlog.get_logger(__name__)

_FLUSH_INTERVAL = 2.0
_BATCH_SIZE = 500
_MAX_QUEUE = 20_000


@dataclass
class ObsContext:
    """Per-execution context propagated via contextvars to LLM/tool call sites."""

    trace_id: UUID
    session_id: UUID | None = None
    user_id: UUID | None = None
    tenant_id: UUID | None = None
    agent_id: UUID | None = None
    call_order: int = 0

    def next_call_order(self) -> int:
        self.call_order += 1
        return self.call_order


_obs_ctx: contextvars.ContextVar[ObsContext | None] = contextvars.ContextVar(
    "observation_context", default=None
)


def set_obs_context(ctx: ObsContext | None) -> None:
    """Set the observation context for the current task (agent loop)."""
    _obs_ctx.set(ctx)


def get_obs_context() -> ObsContext | None:
    """Get the current observation context, if any."""
    return _obs_ctx.get()


class Recorder:
    """Async batch writer for observability rows."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._batch: list[tuple[str, dict]] = []
        self._task: asyncio.Task | None = None
        self._last_overflow_log = 0.0

    # ---- lifecycle ----

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_loop())

    async def flush(self) -> None:
        if self._batch:
            batch, self._batch = self._batch, []
            await self._write_batch(batch)

    async def shutdown(self) -> None:
        await self.flush()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---- enqueue (non-blocking) ----

    def _enqueue(self, kind: str, payload: dict) -> None:
        try:
            self._queue.put_nowait((kind, payload))
        except asyncio.QueueFull:
            now = time.monotonic()
            if now - self._last_overflow_log > 300:
                self._last_overflow_log = now
                logger.warning(
                    "observation_queue_overflow",
                    kind=kind,
                    max_queue=_MAX_QUEUE,
                )

    def record_llm_call(self, **fields: object) -> None:
        ctx = get_obs_context()
        payload: dict = {
            "trace_id": ctx.trace_id if ctx else None,
            "session_id": ctx.session_id if ctx else None,
            "user_id": ctx.user_id if ctx else None,
            "tenant_id": ctx.tenant_id if ctx else None,
            "agent_id": ctx.agent_id if ctx else None,
            "call_order": ctx.next_call_order() if ctx else 0,
            "created_at": datetime.now(UTC),
            **fields,
        }
        self._enqueue("llm_call", payload)

    def record_tool_call(
        self,
        *,
        tool_name: str,
        exec_type: str,
        duration_ms: float,
        is_error: bool,
        error_type: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        arg_bytes: int | None = None,
        arg_chars: int | None = None,
        output_bytes: int | None = None,
        output_chars: int | None = None,
        est_injected_tokens: int | None = None,
        is_truncated: bool = False,
        is_concurrent: bool = False,
        call_order: int = 1,
        **fields: object,
    ) -> None:
        ctx = get_obs_context()
        payload: dict = {
            "tool_name": tool_name,
            "exec_type": exec_type,
            "duration_ms": int(duration_ms),
            "is_error": is_error,
            "error_type": error_type,
            "arg_bytes": arg_bytes,
            "arg_chars": arg_chars,
            "output_bytes": output_bytes,
            "output_chars": output_chars,
            "est_injected_tokens": est_injected_tokens,
            "is_truncated": is_truncated,
            "is_concurrent": is_concurrent,
            "call_order": call_order,
            "user_id": user_id if user_id else (str(ctx.user_id) if ctx and ctx.user_id else None),
            "session_id": session_id if session_id else (ctx.session_id if ctx else None),
            "trace_id": ctx.trace_id if ctx else None,
            "tenant_id": ctx.tenant_id if ctx else None,
            "agent_id": ctx.agent_id if ctx else None,
            "created_at": datetime.now(UTC),
            **fields,
        }
        self._enqueue("tool_call", payload)

    def record_trace(self, **fields: object) -> None:
        payload: dict = {"created_at": datetime.now(UTC), **fields}
        self._enqueue("trace", payload)

    # ---- flush loop ----

    async def _flush_loop(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=_FLUSH_INTERVAL)
            except TimeoutError:
                if self._batch:
                    batch, self._batch = self._batch, []
                    await self._write_batch(batch)
                continue
            self._batch.append(item)
            if len(self._batch) >= _BATCH_SIZE:
                batch, self._batch = self._batch, []
                await self._write_batch(batch)

    async def _write_batch(self, items: list[tuple[str, dict]]) -> None:
        if not items:
            return
        try:
            factory = get_session_factory()
            async with factory() as db:
                for kind, payload in items:
                    if kind == "llm_call":
                        await self._insert_llm_call(db, payload)
                    elif kind == "tool_call":
                        await self._insert_tool_call(db, payload)
                    elif kind == "trace":
                        await self._insert_trace(db, payload)
                await db.commit()
        except Exception:
            logger.exception("observation_write_failed", batch_size=len(items))

    # ---- writers ----

    async def _insert_llm_call(self, db, payload: dict) -> None:
        db.add(LLMCallLog(**payload))
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            return
        duration_ms = int(payload.get("duration_ms") or 0)
        ttft_ms = int(payload.get("ttft_ms") or 0)
        stmt = pg_insert(PerformanceDaily).values(
            tenant_id=tenant_id,
            date=date.today(),
            llm_call_count=1,
            llm_total_duration_ms=duration_ms,
            ttft_total_ms=ttft_ms,
            max_llm_duration_ms=duration_ms,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "date"],
            set_={
                "llm_call_count": PerformanceDaily.llm_call_count + 1,
                "llm_total_duration_ms": PerformanceDaily.llm_total_duration_ms
                + stmt.excluded.llm_total_duration_ms,
                "ttft_total_ms": PerformanceDaily.ttft_total_ms + stmt.excluded.ttft_total_ms,
                "max_llm_duration_ms": func.greatest(
                    PerformanceDaily.max_llm_duration_ms, stmt.excluded.max_llm_duration_ms
                ),
            },
        )
        await db.execute(stmt)

    async def _insert_tool_call(self, db, payload: dict) -> None:
        db.add(ToolCallLog(**payload))
        user_id = payload.get("user_id")
        tool_name = payload.get("tool_name")
        if not user_id or not tool_name:
            return
        is_error = bool(payload.get("is_error", False))
        duration_ms = int(payload.get("duration_ms") or 0)
        stmt = pg_insert(ToolUsageDaily).values(
            user_id=user_id,
            date=date.today(),
            tool_name=tool_name,
            request_count=1,
            success_count=0 if is_error else 1,
            error_count=1 if is_error else 0,
            total_duration_ms=duration_ms,
            max_duration_ms=duration_ms,
            total_injected_tokens=int(payload.get("est_injected_tokens") or 0),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "date", "tool_name"],
            set_={
                "request_count": ToolUsageDaily.request_count + 1,
                "success_count": ToolUsageDaily.success_count + stmt.excluded.success_count,
                "error_count": ToolUsageDaily.error_count + stmt.excluded.error_count,
                "total_duration_ms": ToolUsageDaily.total_duration_ms
                + stmt.excluded.total_duration_ms,
                "max_duration_ms": func.greatest(
                    ToolUsageDaily.max_duration_ms, stmt.excluded.max_duration_ms
                ),
                "total_injected_tokens": ToolUsageDaily.total_injected_tokens
                + stmt.excluded.total_injected_tokens,
            },
        )
        await db.execute(stmt)

    async def _insert_trace(self, db, payload: dict) -> None:
        db.add(AgentTraceLog(**payload))
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            return
        status = payload.get("status") or "completed"
        stmt = pg_insert(PerformanceDaily).values(
            tenant_id=tenant_id,
            date=date.today(),
            trace_count=1,
            success_count=1 if status == "completed" else 0,
            error_count=1 if status == "error" else 0,
            interrupted_count=1 if status == "interrupted" else 0,
            timeout_count=1 if status == "timeout" else 0,
            total_duration_ms=int(payload.get("duration_ms") or 0),
            total_tokens=int(payload.get("total_tokens") or 0),
            tool_call_count=int(payload.get("tool_call_count") or 0),
            compress_count=1 if payload.get("is_compressed") else 0,
            saved_tokens_total=int(payload.get("saved_tokens") or 0),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "date"],
            set_={
                "trace_count": PerformanceDaily.trace_count + 1,
                "success_count": PerformanceDaily.success_count + stmt.excluded.success_count,
                "error_count": PerformanceDaily.error_count + stmt.excluded.error_count,
                "interrupted_count": PerformanceDaily.interrupted_count
                + stmt.excluded.interrupted_count,
                "timeout_count": PerformanceDaily.timeout_count + stmt.excluded.timeout_count,
                "total_duration_ms": PerformanceDaily.total_duration_ms
                + stmt.excluded.total_duration_ms,
                "total_tokens": PerformanceDaily.total_tokens + stmt.excluded.total_tokens,
                "tool_call_count": PerformanceDaily.tool_call_count + stmt.excluded.tool_call_count,
                "compress_count": PerformanceDaily.compress_count + stmt.excluded.compress_count,
                "saved_tokens_total": PerformanceDaily.saved_tokens_total
                + stmt.excluded.saved_tokens_total,
            },
        )
        await db.execute(stmt)


_recorder: Recorder | None = None


def get_recorder() -> Recorder:
    """Get the process-global recorder (lazily created)."""
    global _recorder
    if _recorder is None:
        _recorder = Recorder()
    return _recorder


def reset_recorder() -> None:
    global _recorder
    _recorder = None
