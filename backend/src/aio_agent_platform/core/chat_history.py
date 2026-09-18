"""Shared event collection and message persistence for chat and scheduled runs."""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.db.models import Message
from aio_agent_platform.db.sanitize import sanitize_pg_text


def _append_reasoning_chunk(chunks: list[dict], content: str) -> None:
    """Keep each ReAct reasoning step independently renderable after reload."""
    if content:
        chunks.append({"id": f"thinking-{len(chunks)}", "content": content})


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

    def record(self, event: AgentStep | str) -> dict | None:
        if isinstance(event, AgentStep):
            if event.done:
                self.content = event.final_output or self.content
            return None
        if event.startswith("text_delta:"):
            delta = event[len("text_delta:"):]
            self.content += delta
            return {"type": "text_delta", "content": delta}
        if event.startswith("reasoning_delta:"):
            return {"type": "thinking", "content": event[len("reasoning_delta:"):]}
        if event.startswith("reasoning:"):
            _append_reasoning_chunk(self.reasoning, event[len("reasoning:"):])
        elif event.startswith("tool_call:"):
            parts = event.split(":", 3)
            call_id = parts[1] if len(parts) > 1 else ""
            name = parts[2] if len(parts) > 2 else ""
            try:
                arguments = json.loads(parts[3]) if len(parts) > 3 else {}
            except json.JSONDecodeError:
                arguments = {}
            call = {"id": call_id, "name": name, "arguments": arguments}
            self.tool_calls.append(call)
            return {"type": "tool_call", **call}
        elif event.startswith("tool_result:"):
            parts = event.split(":", 4)
            call_id = parts[1] if len(parts) > 1 else ""
            name = parts[2] if len(parts) > 2 else ""
            status = parts[3] if len(parts) > 3 else ""
            try:
                preview = json.loads(parts[4]) if len(parts) > 4 else ""
            except json.JSONDecodeError:
                preview = parts[4] if len(parts) > 4 else ""
            result = {"status": status, "preview": preview}
            for call in self.tool_calls:
                if call["id"] == call_id:
                    call["result"] = result
                    break
            return {"type": "tool_result", "tool_call_id": call_id, "name": name, **result}
        elif event.startswith("file_changes:"):
            try:
                incoming = json.loads(event[len("file_changes:"):])
            except json.JSONDecodeError:
                return None
            self.file_changes[:] = _merge_file_changes(self.file_changes, incoming)
            return {"type": "file_changes", "file_changes": incoming}
        return None

    async def process(self, event: AgentStep | str, db: AsyncSession) -> dict | None:
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

    async def consume(self, events: AsyncIterable[AgentStep | str], db: AsyncSession) -> str:
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
