"""Memory tool handlers for ToolExecutor._execute_direct() dispatch."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import Session
from aio_agent_platform.memory.reconciliation import reconcile_memory
from aio_agent_platform.memory.service import MemoryService

_LAYER_LABELS = {"L1": "常驻上下文", "L2": "长期记忆", "L3": "情景记忆"}


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
    for i, (mem, score) in enumerate(results, 1):
        layer_label = _LAYER_LABELS.get(mem.layer, mem.layer)
        created = mem.created_at.strftime("%Y-%m-%d") if mem.created_at else "unknown"
        parts.append(
            f"{i}. [{mem.layer}/{layer_label}] (relevance: {score:.2f})\n"
            f"   {mem.content}\n"
            f"   Created: {created}"
        )
    return "\n\n".join(parts)


async def handle_memory_write(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Handle memory_write tool call — save a new memory."""
    layer = arguments.get("layer", "")
    content = arguments.get("content", "")
    tags = arguments.get("tags", [])

    if not layer or layer not in ("L1", "L2", "L3"):
        return "Error: layer must be L1, L2, or L3"
    if not content:
        return "Error: content is required"

    uid = UUID(user_id)
    scope = arguments.get("scope", "agent")
    if scope not in ("agent", "user"):
        return "Error: scope must be agent or user"
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

    layer_label = _LAYER_LABELS.get(layer, layer)
    if action in {"updated", "merged", "skipped"}:
        return (
            f"Memory {action}: existing {layer} ({layer_label}) entry reused "
            f"(same fact already present), id: {memory.id}"
        )
    return f"Memory saved to {layer} ({layer_label}), id: {memory.id}"


# Registry mapping tool_name -> handler function
MEMORY_HANDLERS: dict[str, Callable] = {
    "memory_read": handle_memory_read,
    "memory_write": handle_memory_write,
}
