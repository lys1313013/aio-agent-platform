"""Shared event collection and message persistence for chat and scheduled runs."""

from __future__ import annotations

from collections.abc import AsyncIterable
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.agent_events import (
    AgentEvent,
    FileChangesEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
    decode_agent_event,
    event_payload,
)
from aio_agent_platform.db.models import Message
from aio_agent_platform.db.sanitize import sanitize_pg_text


def _append_reasoning_chunk(chunks: list[dict], content: str, tool_call_index: int | None = None) -> None:
    """Keep each ReAct reasoning step independently renderable after reload."""
    if content:
        chunk = {"id": f"thinking-{len(chunks)}", "content": content}
        if tool_call_index is not None:
            chunk["tool_call_index"] = tool_call_index
        chunks.append(chunk)


def restore_reasoning_order(reasoning: list[dict], events: list[dict]) -> list[dict]:
    """Recover old reasoning positions only when the retained event text matches."""
    chunks: list[dict] = []
    tool_count = 0
    for event in events:
        if event.get("type") == "tool_call":
            tool_count += 1
        elif event.get("type") == "thinking" and event.get("content"):
            if chunks and chunks[-1]["tool_call_index"] == tool_count:
                chunks[-1]["content"] += event["content"]
            else:
                _append_reasoning_chunk(chunks, event["content"], tool_count)
    if chunks and "".join(c["content"] for c in chunks) == "".join(c.get("content", "") for c in reasoning):
        return chunks
    return reasoning


def _merge_file_changes(current: list[dict], incoming: list[dict]) -> list[dict]:
    """Collapse repeated changes to the same path within one assistant turn."""
    merged = {item["path"]: item for item in current}
    for item in incoming:
        path = item.get("path")
        if not path:
            continue
        previous = merged.get(path)
        if previous is None:
            merged[path] = item
        elif previous["action"] == "created" and item["action"] == "deleted":
            merged.pop(path)
        elif previous["action"] == "created":
            merged[path] = {**item, "action": "created"}
        elif previous["action"] == "deleted" and item["action"] == "created":
            merged[path] = {**item, "action": "modified"}
        else:
            merged[path] = item
    return list(merged.values())


class ChatTurnRecorder:
    """Collect one turn independently of its trigger and transport.

    Live clients use ``process``; background callers use ``consume``. Both
    checkpoint the same message, including when rescuing an interrupted turn.
    """

    def __init__(self, session_id: UUID, user_id: UUID) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.message_id: UUID | None = None
        self.content = ""
        self.tool_calls: list[dict] = []
        self.reasoning: list[dict] = []
        self.file_changes: list[dict] = []

    def record(self, event: AgentStep | AgentEvent | str) -> dict | None:
        if isinstance(event, AgentStep):
            if event.done:
                self.content = event.final_output or self.content
            return None
        decoded = decode_agent_event(event)
        if decoded is None:
            return None
        if isinstance(decoded, TextEvent):
            if decoded.kind == "text_delta":
                self.content += decoded.content
            elif decoded.kind == "reasoning":
                _append_reasoning_chunk(self.reasoning, decoded.content, len(self.tool_calls))
        elif isinstance(decoded, ToolCallEvent):
            self.tool_calls.append({
                "id": decoded.call_id, "name": decoded.name, "arguments": decoded.arguments,
            })
        elif isinstance(decoded, ToolResultEvent):
            for call in self.tool_calls:
                if call["id"] == decoded.call_id:
                    call["result"] = {"status": decoded.status, "preview": decoded.preview}
                    break
        elif isinstance(decoded, FileChangesEvent):
            self.file_changes[:] = _merge_file_changes(self.file_changes, decoded.changes)
        return event_payload(decoded)

    async def process(self, event: AgentStep | AgentEvent | str, db: AsyncSession) -> dict | None:
        payload = self.record(event)
        if payload and payload["type"] in {"tool_call", "tool_result", "file_changes"}:
            await self.save(db)
        return payload

    async def save(
        self, db: AsyncSession, *, commit: bool = True, force: bool = False,
    ) -> UUID | None:
        if not (force or self.content or self.tool_calls or self.reasoning or self.file_changes):
            return self.message_id
        values = sanitize_pg_text({
            "content": self.content,
            "tool_calls": self.tool_calls or None,
            "reasoning": self.reasoning or None,
            "file_changes": self.file_changes or None,
        })
        message_id = self.message_id or uuid4()
        if self.message_id is None:
            db.add(Message(
                id=message_id, session_id=self.session_id, user_id=self.user_id,
                role="assistant", **values,
            ))
        else:
            await db.execute(update(Message).where(
                Message.id == message_id,
                Message.session_id == self.session_id,
                Message.user_id == self.user_id,
            ).values(**values))
        if commit:
            await db.commit()
        else:
            await db.flush()
        self.message_id = message_id
        return message_id

    async def consume(self, events: AsyncIterable[AgentStep | AgentEvent | str], db: AsyncSession) -> str:
        try:
            async for event in events:
                await self.process(event, db)
        finally:
            await self.save(db)
        return self.content

    async def rescue(self) -> None:
        """Persist partial state after the entry point's DB context has closed."""
        from aio_agent_platform.db.connection import current_user_id, get_session_factory

        token = current_user_id.set(str(self.user_id))
        try:
            async with get_session_factory()() as db:
                await self.save(db)
        finally:
            current_user_id.reset(token)
