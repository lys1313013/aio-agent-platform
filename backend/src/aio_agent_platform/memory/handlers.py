"""Memory tool handlers for ToolExecutor._execute_direct() dispatch."""

from __future__ import annotations

import json
from collections.abc import Callable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import Memory, Session
from aio_agent_platform.memory.presentation import format_memory, memory_reference
from aio_agent_platform.memory.reconciliation import reconcile_memory
from aio_agent_platform.memory.service import MemoryService, memory_scope


async def _set_rls_context(db, user_id: str) -> None:
    """Set PostgreSQL RLS context using set_config (supports parameterized queries)."""
    await db.execute(select(func.set_config("app.current_user_id", user_id, True)))


async def _active_agent(db, user_id: UUID, session_id: str) -> UUID | None:
    # Delegation switches the ContextVar; channels can resolve the saved session.
    active = current_agent_id.get()
    if active:
        return UUID(active)
    return await db.scalar(select(Session.agent_id).where(
        Session.id == UUID(session_id), Session.user_id == user_id,
    ))


async def handle_memory_read(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Handle memory_read tool call — search memories by relevance."""
    query = arguments.get("query", "")
    layer = arguments.get("layer")
    top_k = min(arguments.get("top_k", 5), 20)

    if not query:
        return "Error: query parameter is required"

    layers = [layer] if layer else ["L1", "L2", "L3"]
    uid = UUID(user_id)

    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        agent_id = await _active_agent(db, uid, session_id)
        results = await MemoryService.search_memories(
            db, uid, query, layers=layers, top_k=top_k, agent_id=agent_id
        )
        await db.commit()

    if not results:
        return "No relevant memories found."

    parts = [f"Found {len(results)} relevant memories:\n"]
    for mem, _score in results:
        parts.append("- " + format_memory(mem))
    return "\n\n".join(parts)


async def handle_memory_write(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Handle memory_write tool call — save a new memory."""
    layer = arguments.get("layer", "")
    content = arguments.get("content", "")
    tags = arguments.get("tags", [])

    if not layer or layer not in ("L1", "L2", "L3"):
        raise ValueError("layer must be L1, L2, or L3")
    if not content:
        raise ValueError("content is required")

    uid = UUID(user_id)
    scope = arguments.get("scope", "agent")
    if scope not in ("agent", "user"):
        raise ValueError("scope must be agent or user")
    meta = {"tags": tags, "source": "agent_tool", "source_session": session_id}

    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        agent_id = await _active_agent(db, uid, session_id) if scope == "agent" else None
        memory, action = await reconcile_memory(
            db, uid, layer, content, meta=meta, agent_id=agent_id
        )
        await db.commit()

    return json.dumps({"action": action, "memory": memory_reference(memory)}, ensure_ascii=False)


async def _mutate_memory(arguments: dict, user_id: str, session_id: str, *, delete: bool) -> str:
    try:
        memory_id = UUID(arguments.get("memory_id", ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("memory_id must be an exact UUID from memory_read or the prompt") from exc
    version = arguments.get("expected_version")
    if type(version) is not int or version < 1:
        raise ValueError("expected_version must be a positive integer from the current memory")
    content = arguments.get("content")
    if not delete and (not isinstance(content, str) or not content.strip()):
        raise ValueError("content must be non-empty; use memory_delete to remove an entry")

    uid = UUID(user_id)
    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        agent_id = await _active_agent(db, uid, session_id)
        # Serialize check and mutation, including ownership/scope changes.
        memory = await db.scalar(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id == uid,
                memory_scope(Memory, agent_id, include_shared=True),
            ).with_for_update().execution_options(populate_existing=True)
        )
        if memory is None:
            raise ValueError("Memory not found or outside the current user's/agent's scope")
        if memory.version != version:
            raise ValueError("Memory version conflict; reread with memory_read before retrying")
        before = memory_reference(memory)
        try:
            if delete:
                await MemoryService.delete_memory(db, memory_id, uid)
                result = {"action": "deleted", "memory": before}
            elif memory.content == content.strip():
                result = {"action": "skipped", "memory": before}
            else:
                memory = await MemoryService.update_memory(
                    db, memory_id, uid, content=content.strip(), expected_version=version,
                )
                result = {"action": "updated", "memory": memory_reference(memory), "before": before}
            await db.commit()
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc
    return json.dumps(result, ensure_ascii=False)


async def handle_memory_update(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    return await _mutate_memory(arguments, user_id, session_id, delete=False)


async def handle_memory_delete(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    return await _mutate_memory(arguments, user_id, session_id, delete=True)


# Registry mapping tool_name -> handler function
MEMORY_HANDLERS: dict[str, Callable] = {
    "memory_read": handle_memory_read,
    "memory_write": handle_memory_write,
    "memory_update": handle_memory_update,
    "memory_delete": handle_memory_delete,
}
