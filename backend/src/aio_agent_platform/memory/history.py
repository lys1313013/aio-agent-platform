"""Immutable full snapshots and atomic, optimistic undo. Caller owns transaction."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm.exc import StaleDataError

from aio_agent_platform.db.models import Memory, MemoryChange, MemoryVersion
from aio_agent_platform.db.sanitize import sanitize_pg_text


def timestamp(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def snapshot(memory: Memory) -> dict:
    return {
        "id": str(memory.id),
        "user_id": str(memory.user_id),
        "tenant_id": str(memory.tenant_id) if memory.tenant_id else None,
        "agent_id": str(memory.agent_id) if memory.agent_id else None,
        "layer": memory.layer,
        "content": memory.content,
        "metadata": copy.deepcopy(memory.meta or {}),
        "version": memory.version or 1,
        "created_at": timestamp(memory.created_at),
        "updated_at": timestamp(memory.updated_at),
        "exists": True,
    }


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def lock_scopes(db, user_id: UUID, scopes: list[tuple[str | None, str]]) -> None:
    if db.bind.dialect.name != "postgresql":
        return
    await db.execute(select(func.set_config("app.current_user_id", str(user_id), True)))
    async with asyncio.timeout(20):
        for agent_id, layer in sorted(set(scopes), key=lambda scope: (str(scope[0]), scope[1])):
            key = f"memory:{user_id}:{agent_id}:{layer}"
            while not await db.scalar(
                select(func.pg_try_advisory_xact_lock(func.hashtextextended(key, 0)))
            ):
                await asyncio.sleep(0.05)


async def record_change(
    db,
    user_id: UUID,
    kind: str,
    before: dict,
    live: list[Memory],
    deleted: list[Memory] | None = None,
) -> MemoryChange:
    """Records every affected row as one atomic operation, including deletion tombstones."""
    change_id = uuid4()
    after = {}
    for memory in live:
        old = before.get(str(memory.id))
        if old:
            memory.version = old["version"] + 1
            memory.updated_at = datetime.now(UTC)
    try:
        await db.flush()
    except StaleDataError as exc:
        raise HTTPException(409, "记忆已发生变化，请刷新后重试") from exc
    for memory in live:
        after[str(memory.id)] = snapshot(memory)
    for memory in deleted or []:
        old = before[str(memory.id)]
        after[str(memory.id)] = {**old, "exists": False, "version": old["version"] + 1}
        await db.delete(memory)
    change = MemoryChange(
        id=change_id,
        user_id=user_id,
        kind=kind,
        before=sanitize_pg_text(before),
        after=sanitize_pg_text(after),
        created_at=datetime.now(UTC),
    )
    db.add(change)
    for memory_id, state in after.items():
        db.add(
            MemoryVersion(
                memory_id=UUID(memory_id),
                version=state["version"],
                user_id=user_id,
                change_id=change_id,
                kind=kind,
                snapshot=sanitize_pg_text(state),
            )
        )
    try:
        await db.flush()
    except StaleDataError as exc:
        raise HTTPException(409, "记忆已发生变化，请刷新后重试") from exc
    return change


async def locked_memories(db, user_id: UUID, states: dict) -> dict[str, Memory]:
    await lock_scopes(db, user_id, [(s.get("agent_id"), s["layer"]) for s in states.values()])
    rows = await db.scalars(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.id.in_([UUID(key) for key in states]))
        .order_by(Memory.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return {str(row.id): row for row in rows}


def matches(memory: Memory | None, state: dict) -> bool:
    return digest(snapshot(memory)) == digest(state) if memory else not state.get("exists", True)


def apply_snapshot(memory: Memory, state: dict) -> None:
    from aio_agent_platform.memory.service import MemoryService

    memory.content = state["content"]
    memory.layer = state["layer"]
    memory.agent_id = UUID(state["agent_id"]) if state.get("agent_id") else None
    memory.meta = copy.deepcopy(state.get("metadata") or {})
    memory.search_vec = MemoryService._tokenize(memory.content)


async def undo(db, user_id: UUID, change_id: UUID) -> MemoryChange:
    change = await db.scalar(
        select(MemoryChange)
        .where(MemoryChange.id == change_id, MemoryChange.user_id == user_id)
        .with_for_update()
    )
    if change is None:
        raise HTTPException(404, "修改记录不存在")
    if change.undone_by:
        return await db.scalar(
            select(MemoryChange).where(
                MemoryChange.id == change.undone_by, MemoryChange.user_id == user_id
            )
        )
    # Lock both old and new scopes, e.g. an edit that moved a memory to another agent.
    await lock_scopes(
        db,
        user_id,
        [
            (s.get("agent_id"), s["layer"])
            for s in [*change.before.values(), *change.after.values()]
        ],
    )
    current = await locked_memories(db, user_id, change.after)
    for key, state in change.after.items():
        latest = await db.scalar(
            select(func.max(MemoryVersion.version)).where(
                MemoryVersion.user_id == user_id, MemoryVersion.memory_id == UUID(key)
            )
        )
        if latest != state["version"] or not matches(current.get(key), state):
            raise HTTPException(409, "这些记忆在操作后又发生了变化，不能直接撤销；请先查看版本记录")
    live, deleted = [], []
    before = copy.deepcopy(change.after)
    for key, state in change.before.items():
        row = current.get(key)
        if state.get("exists", True):
            if row is None:
                row = Memory(
                    id=UUID(key),
                    user_id=user_id,
                    tenant_id=UUID(state["tenant_id"]),
                    created_at=datetime.fromisoformat(state["created_at"]),
                )
                db.add(row)
            apply_snapshot(row, state)
            live.append(row)
        elif row:
            deleted.append(row)
    # Undo creation: no before snapshot exists; remove its created rows.
    for key, row in current.items():
        if key not in change.before:
            deleted.append(row)
    reverted = await record_change(db, user_id, "undo", before, live, deleted)
    change.undone_by = reverted.id
    await db.flush()
    return reverted


async def restore(
    db, user_id: UUID, memory_id: UUID, version: int, expected_version: int
) -> MemoryChange:
    version_row = await db.scalar(
        select(MemoryVersion).where(
            MemoryVersion.memory_id == memory_id,
            MemoryVersion.user_id == user_id,
            MemoryVersion.version == version,
        )
    )
    if not version_row or not version_row.snapshot.get("exists", True):
        raise HTTPException(404, "可恢复的记忆版本不存在")
    row = await db.scalar(select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id))
    if row is None:
        raise HTTPException(409, "记忆已合并或删除，请从修改记录撤销对应操作")
    target = version_row.snapshot
    current = snapshot(row)
    await lock_scopes(db, user_id, [(s.get("agent_id"), s["layer"]) for s in (current, target)])
    row = (await locked_memories(db, user_id, {str(memory_id): current})).get(str(memory_id))
    if not row or row.version != expected_version:
        raise HTTPException(409, "记忆已更新，请刷新版本记录后再恢复")
    before = {str(row.id): snapshot(row)}
    apply_snapshot(row, target)
    return await record_change(db, user_id, "restore", before, [row])
