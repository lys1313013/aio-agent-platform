"""Durable Web tasks with leased workers and independent, cursor-based subscribers.

Text deltas are committed in batches; tool boundaries commit before the producer
can execute its next step. An expired worker is never automatically re-executed.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from fastapi import HTTPException
from sqlalchemy import select, text

from aio_agent_platform.core.chat_history import _merge_file_changes
from aio_agent_platform.core.task_scope import call_key, child_tasks, completed_calls
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import ChatRun, ChatRunEvent, Message, Session
from aio_agent_platform.db.sanitize import sanitize_pg_text

logger = structlog.get_logger()
OWNER = str(uuid4())
LEASE_SECONDS = 30
_workers: dict[UUID, asyncio.Task] = {}
_worker_users: dict[UUID, UUID] = {}


def now() -> datetime:
    return datetime.now(UTC)


async def user_scope(db, user_id: UUID) -> None:
    # Every new transaction needs its own RLS context, including background jobs.
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": str(user_id)})


def snapshot_event(snapshot: dict, event: dict) -> dict:
    state = copy.deepcopy(snapshot)
    kind = event.get("type")
    if kind == "thinking":
        chunks = state.setdefault("reasoning", [])
        if not chunks or state.get("last_type") != "thinking":
            chunks.append({"id": f"thinking-{len(chunks)}", "content": ""})
        chunks[-1]["content"] += event.get("content", "")
    elif kind == "text_delta":
        state["content"] = state.get("content", "") + event.get("content", "")
    elif kind == "text":
        state["content"] = event.get("content", "")
    elif kind == "tool_call":
        state.setdefault("tool_calls", []).append({k: event[k] for k in ("id", "name", "arguments")})
    elif kind == "tool_result":
        for call in state.get("tool_calls", []):
            if call["id"] == event.get("tool_call_id"):
                call["result"] = {"status": event.get("status"), "preview": event.get("preview", "")}
    elif kind == "confirmation_required":
        for call in state.get("tool_calls", []):
            if call["name"] == "AskUserQuestion" and not call.get("confirmation"):
                call["confirmation"] = dict(event)
                break
    elif kind == "confirmation_resolved":
        for call in state.get("tool_calls", []):
            confirmation = call.get("confirmation") or {}
            if confirmation.get("confirmation_id") == event.get("confirmation_id"):
                confirmation["resolved"] = dict(event)
    elif kind == "delegation_start":
        for call in state.get("tool_calls", []):
            if call["id"] == event.get("tool_call_id"):
                call["delegation"] = {
                    **event, "status": "running", "thinking": "", "toolCalls": [], "result": "",
                }
    elif str(kind).startswith("delegation_"):
        for call in state.get("tool_calls", []):
            delegation = call.get("delegation")
            if not delegation or delegation.get("delegation_id") != event.get("delegation_id"):
                continue
            if kind == "delegation_thinking":
                delegation["thinking"] += event.get("content", "")
            elif kind == "delegation_text_delta":
                delegation["result"] += event.get("content", "")
            elif kind == "delegation_tool_call":
                delegation["toolCalls"].append({k: event.get(k) for k in ("id", "name", "arguments")})
            elif kind == "delegation_tool_result":
                for child in delegation["toolCalls"]:
                    if child["id"] == event.get("tool_call_id"):
                        child["result"] = {"status": event.get("status"), "preview": event.get("preview", "")}
            elif kind == "delegation_end":
                delegation.update(status=event.get("status", "completed"),
                                  duration_ms=event.get("duration_ms", 0), error=event.get("error"))
                delegation["result"] = delegation.get("result") or event.get("result_preview", "")
    elif kind == "file_changes":
        state["file_changes"] = _merge_file_changes(state.get("file_changes", []), event.get("file_changes", []))
    elif kind == "done":
        state["completed"] = True
        for key in ("content", "tool_calls", "reasoning", "file_changes"):
            state[key] = event.get(key) or ([] if key != "content" else "")
    if kind not in {"ui_action_heartbeat", "delegation_heartbeat"}:
        state["last_type"] = kind
    return state


def uncertain_tools(run: ChatRun) -> list[str]:
    # Even failed external calls can have side effects. Resume requires all calls
    # to have a durable successful result; do not infer safety from tool names.
    return [call.get("name", "unknown") for call in run.snapshot.get("tool_calls", [])
            if not call.get("result") or call["result"].get("status") != "ok"]


def describe(run: ChatRun) -> dict:
    uncertain = uncertain_tools(run)
    return {"id": str(run.id), "session_id": str(run.session_id), "status": run.status,
            "assistant_message_id": str(run.assistant_message_id),
            "last_sequence": run.last_sequence, "stop_requested": run.stop_requested,
            "can_resume": run.status in {"interrupted", "failed", "stopped"} and not uncertain,
            "uncertain_tools": uncertain}


async def save_snapshot(db, run: ChatRun) -> None:
    message = await db.scalar(select(Message).where(Message.id == run.assistant_message_id, Message.user_id == run.user_id))
    if message:
        snapshot = copy.deepcopy(run.snapshot)
        if run.status != "completed":
            for call in snapshot.get("tool_calls", []):
                if not call.get("result"):
                    call["result"] = {"status": "unknown", "preview": "任务中断前未保存工具结果，请核对实际执行结果，不要直接重试。"}
                delegation = call.get("delegation")
                if delegation and delegation.get("status") == "running":
                    delegation["status"] = "failed"
                    delegation["error"] = "任务已中断，执行结果待核对"
                confirmation = call.get("confirmation")
                if confirmation and not confirmation.get("resolved"):
                    confirmation["resolved"] = {"status": "cancelled"}
        for key in ("content", "tool_calls", "reasoning", "file_changes"):
            setattr(message, key, sanitize_pg_text(snapshot.get(key) or ("" if key == "content" else None)))


async def expire_run(db, run: ChatRun) -> None:
    heartbeat = run.heartbeat_at
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    if run.status == "running" and heartbeat < now() - timedelta(seconds=LEASE_SECONDS):
        run.status = "completed" if run.snapshot.get("completed") else "interrupted"
        await save_snapshot(db, run)
        await db.flush()


async def latest(db, user_id: UUID, session_id: UUID) -> ChatRun | None:
    await user_scope(db, user_id)
    session = await db.scalar(select(Session).where(Session.id == session_id, Session.user_id == user_id, Session.source != "room"))
    if not session:
        raise HTTPException(404, "Session not found")
    run = await db.scalar(select(ChatRun).where(ChatRun.session_id == session_id, ChatRun.user_id == user_id)
                          .order_by(ChatRun.created_at.desc()).limit(1).with_for_update())
    if run:
        await expire_run(db, run)
    await db.commit()
    return run


async def owned(db, user_id: UUID, run_id: UUID, *, lock: bool = False) -> ChatRun:
    await user_scope(db, user_id)
    query = select(ChatRun).where(ChatRun.id == run_id, ChatRun.user_id == user_id)
    if lock:
        query = query.with_for_update()
    run = await db.scalar(query)
    if not run:
        raise HTTPException(404, "Task not found")
    return run


async def reserve(db, user_id: UUID, session_id: UUID, request: dict, resumed_from: UUID | None = None) -> ChatRun:
    await user_scope(db, user_id)
    session = await db.scalar(select(Session).where(Session.id == session_id, Session.user_id == user_id).with_for_update())
    if not session:
        raise HTTPException(404, "Session not found")
    active = await db.scalar(select(ChatRun).where(ChatRun.session_id == session_id, ChatRun.user_id == user_id,
                                                 ChatRun.status == "running").with_for_update())
    if active:
        await expire_run(db, active)
        if active.status == "running":
            raise HTTPException(409, "该会话已有后台任务，请连接现有任务或先停止任务")
    if resumed_from:
        prior = await owned(db, user_id, resumed_from, lock=True)
        await expire_run(db, prior)
        if prior.session_id != session_id or not describe(prior)["can_resume"]:
            raise HTTPException(409, "任务不可安全续跑：请先核对没有成功结果的工具操作")
        # Only the latest turn may be resumed; concurrent/double clicks cannot
        # fork the same continuation, even after a very fast worker finishes.
        last = await db.scalar(select(ChatRun.id).where(ChatRun.session_id == session_id, ChatRun.user_id == user_id)
                               .order_by(ChatRun.created_at.desc()).limit(1))
        if last != prior.id:
            raise HTTPException(409, "该任务之后已有新一轮对话，请刷新会话")
        cached = dict(prior.request.get("completed_calls", {}))
        for call in prior.snapshot.get("tool_calls", []):
            if call.get("result", {}).get("status") == "ok":
                cached[call_key(call["name"], call.get("arguments", {}))] = call["result"]
        request = {**request, "completed_calls": cached}
    run = ChatRun(id=uuid4(), session_id=session_id, user_id=user_id, assistant_message_id=uuid4(),
                  owner=OWNER, status="running", request=request, snapshot={}, last_sequence=0,
                  heartbeat_at=now(), created_at=now(), resumed_from=resumed_from, stop_requested=False)
    db.add(run)
    await db.flush()
    return run


async def append(run_id: UUID, user_id: UUID, events: list[dict]) -> None:
    async with get_session_factory()() as db:
        run = await owned(db, user_id, run_id, lock=True)
        if run.status != "running" or run.owner != OWNER:
            raise asyncio.CancelledError()
        for event in events:
            run.last_sequence += 1
            payload = sanitize_pg_text(event)
            run.snapshot = snapshot_event(run.snapshot, payload)
            db.add(ChatRunEvent(run_id=run_id, user_id=user_id, sequence=run.last_sequence, payload=payload))
        await db.commit()


async def finish(run_id: UUID, user_id: UUID, status: str) -> None:
    async with get_session_factory()() as db:
        run = await owned(db, user_id, run_id, lock=True)
        if run.status == "running" and run.owner == OWNER:
            run.status = status
            await save_snapshot(db, run)
            await db.commit()


async def heartbeat(run_id: UUID, user_id: UUID, worker: asyncio.Task) -> None:
    try:
        while True:
            await asyncio.sleep(1)
            async with get_session_factory()() as db:
                run = await owned(db, user_id, run_id, lock=True)
                if run.status != "running" or run.owner != OWNER or run.stop_requested:
                    if not worker.cancelling():
                        worker.cancel()
                    return
                run.heartbeat_at = now()
                await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("chat_run_heartbeat_failed", run_id=str(run_id))
        if not worker.cancelling():
            worker.cancel()  # Fail closed if we cannot prove ownership.


async def produce(run: ChatRun, source: AsyncIterator[str]) -> None:
    token = current_user_id.set(str(run.user_id))
    children: set[asyncio.Task] = set()
    child_token = child_tasks.set(children)
    completed_token = completed_calls.set(run.request.get("completed_calls", {}))
    beat = asyncio.create_task(heartbeat(run.id, run.user_id, asyncio.current_task()))
    pending: list[dict] = []
    last_flush = time.monotonic()
    outcome = "interrupted"
    try:
        async for chunk in source:
            if not chunk.startswith("data: "):
                continue
            event = json.loads(chunk[6:].strip())
            if event.get("type") in {"ui_action_heartbeat", "delegation_heartbeat"}:
                continue
            pending.append(event)
            if (event.get("type") not in {"thinking", "text_delta"}
                    or time.monotonic() - last_flush >= 0.1 or len(pending) >= 64):
                await append(run.id, run.user_id, pending)
                pending.clear()
                last_flush = time.monotonic()
            if event.get("type") == "done":
                # Commit terminal state before clients can enqueue the next turn.
                outcome = "completed"
            elif event.get("type") == "error":
                outcome = "failed"
        if pending:
            await append(run.id, run.user_id, pending)
            pending.clear()
    except asyncio.CancelledError:
        pass
    except Exception:
        outcome = "failed"
        logger.exception("chat_run_failed", run_id=str(run.id))
    finally:
        for child in list(children):
            child.cancel()
        if children:
            await asyncio.gather(*list(children), return_exceptions=True)
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
        with suppress(Exception):
            await source.aclose()
        try:
            if pending:
                await append(run.id, run.user_id, pending)
            async with get_session_factory()() as db:
                current = await owned(db, run.user_id, run.id)
                if current.stop_requested and outcome != "completed":
                    outcome = "stopped"
            await finish(run.id, run.user_id, outcome)
        except (Exception, asyncio.CancelledError):
            logger.exception("chat_run_finalize_failed", run_id=str(run.id))
        child_tasks.reset(child_token)
        completed_calls.reset(completed_token)
        current_user_id.reset(token)


def start(run: ChatRun, source: AsyncIterator[str]) -> None:
    task = asyncio.create_task(produce(run, source), name=f"web-chat-{run.id}")
    _workers[run.id] = task
    _worker_users[run.id] = run.user_id

    def remove_worker(_):
        _workers.pop(run.id, None)
        _worker_users.pop(run.id, None)

    task.add_done_callback(remove_worker)


async def subscribe(run_id: UUID, user_id: UUID, after: int = 0, *, replay: bool = True) -> AsyncIterator[str]:
    # Read-only subscriber. Closing this generator does not touch the worker.
    async with get_session_factory()() as db:
        run = await owned(db, user_id, run_id)
        replay_until = run.last_sequence if replay else 0
        info = describe(run)
    yield sse({"type": "run", **info})
    cursor = after
    while True:
        async with get_session_factory()() as db:
            run = await owned(db, user_id, run_id, lock=True)
            await expire_run(db, run)
            rows = list((await db.scalars(select(ChatRunEvent).where(
                ChatRunEvent.run_id == run_id, ChatRunEvent.user_id == user_id,
                ChatRunEvent.sequence > cursor).order_by(ChatRunEvent.sequence).limit(256))).all())
            info = describe(run)
            await db.commit()
        for row in rows:
            cursor = row.sequence
            # done is withheld until producer finalization so queue flushing
            # cannot race the active-session uniqueness constraint.
            if row.payload.get("type") == "done" and info["status"] == "running":
                cursor -= 1
                break
            yield sse({**row.payload, "run_id": str(run_id), "sequence": cursor,
                       "replay": cursor <= replay_until})
        if info["status"] != "running" and cursor >= info["last_sequence"]:
            yield sse({"type": "run_status", **info})
            return
        if not rows:
            yield ": ping\n\n"
        await asyncio.sleep(0.2)


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stop(db, user_id: UUID, run_id: UUID) -> dict:
    run = await owned(db, user_id, run_id, lock=True)
    await expire_run(db, run)
    if run.status == "running":
        run.stop_requested = True
    await db.commit()
    task = _workers.get(run_id)
    if task:
        if not task.cancelling():
            task.cancel()
        with suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 10)
        if task.cancelled():
            # Cancellation before the coroutine's first instruction cannot run
            # its finally block; the explicit stopper must persist the outcome.
            await finish(run_id, user_id, "stopped")
    # Other worker processes observe stop_requested through their heartbeat.
    for _ in range(50):
        async with get_session_factory()() as fresh:
            run = await owned(fresh, user_id, run_id)
            if run.status != "running":
                return describe(run)
        await asyncio.sleep(0.2)
    raise HTTPException(409, "任务仍在停止，请稍后重试；尚未开始新任务")


async def shutdown() -> None:
    jobs = [(run_id, _worker_users[run_id], task) for run_id, task in _workers.items()]
    for _, _, task in jobs:
        if not task.cancelling():
            task.cancel()
    if jobs:
        await asyncio.gather(*(task for _, _, task in jobs), return_exceptions=True)
    for run_id, user_id, task in jobs:
        if task.cancelled():
            await finish(run_id, user_id, "interrupted")
