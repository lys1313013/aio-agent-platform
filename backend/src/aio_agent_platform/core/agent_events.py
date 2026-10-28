"""Typed execution events and the compatibility codec for legacy consumers.

The execution loop uses these objects internally. ``AgentLoop.run`` encodes
them at its public boundary so existing CLI/API consumers retain their wire
contract. Consumers accepting legacy strings decode them here, never by
reimplementing the colon-delimited format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


class AgentEvent:
    """Base for the execution loop's non-terminal events."""


@dataclass(frozen=True, slots=True)
class TextEvent(AgentEvent):
    kind: Literal["text_delta", "reasoning_delta", "reasoning"]
    content: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent(AgentEvent):
    call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True, slots=True)
class ToolResultEvent(AgentEvent):
    call_id: str
    name: str
    status: str
    preview: object


@dataclass(frozen=True, slots=True)
class FileChangesEvent(AgentEvent):
    changes: list[dict]


@dataclass(frozen=True, slots=True)
class ControlEvent(AgentEvent):
    kind: Literal["confirmation_flow", "ui_action", "delegation_heartbeat"]
    call_id: str = ""
    status: str = ""


def encode_agent_event(event: AgentEvent) -> str:
    """Preserve the public AgentLoop event contract byte-for-byte."""
    if isinstance(event, TextEvent):
        return f"{event.kind}:{event.content}"
    if isinstance(event, ToolCallEvent):
        return f"tool_call:{event.call_id}:{event.name}:{json.dumps(event.arguments, ensure_ascii=False)}"
    if isinstance(event, ToolResultEvent):
        return f"tool_result:{event.call_id}:{event.name}:{event.status}:{json.dumps(event.preview, ensure_ascii=False)}"
    if isinstance(event, FileChangesEvent):
        return f"file_changes:{json.dumps(event.changes, ensure_ascii=False)}"
    if isinstance(event, ControlEvent):
        if event.kind == "delegation_heartbeat":
            return f"{event.kind}:{event.status}"
        return f"{event.kind}:{event.call_id}:{event.status}"
    raise TypeError(f"Unsupported agent event: {type(event).__name__}")


def _json_or(raw: str, fallback: object) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def decode_agent_event(event: AgentEvent | str) -> AgentEvent | None:
    """Decode legacy events; unknown control extensions remain ignorable.

    Malformed legacy JSON retains the historic argument/result fallbacks.
    Invalid file-change payloads are ignored instead of breaking persistence.
    """
    if isinstance(event, AgentEvent):
        return event
    kind, separator, content = event.partition(":")
    if not separator:
        return None
    if kind in {"text_delta", "reasoning_delta", "reasoning"}:
        return TextEvent(kind, content)
    if kind == "tool_call":
        parts = content.split(":", 2)
        parts += [""] * (3 - len(parts))
        arguments = _json_or(parts[2], {})
        return ToolCallEvent(parts[0], parts[1], arguments if isinstance(arguments, dict) else {})
    if kind == "tool_result":
        parts = content.split(":", 3)
        parts += [""] * (4 - len(parts))
        return ToolResultEvent(parts[0], parts[1], parts[2], _json_or(parts[3], parts[3]))
    if kind == "file_changes":
        changes = _json_or(content, None)
        if isinstance(changes, list) and all(isinstance(item, dict) for item in changes):
            return FileChangesEvent(changes)
    if kind in {"confirmation_flow", "ui_action"}:
        call_id, _, status = content.partition(":")
        return ControlEvent(kind, call_id, status)
    if kind == "delegation_heartbeat":
        return ControlEvent(kind, status=content)
    return None


def event_payload(event: AgentEvent) -> dict | None:
    """Project an event to the existing browser/recording payload contract."""
    if isinstance(event, TextEvent):
        if event.kind == "reasoning":
            return None  # Completed reasoning is persisted once, never streamed twice.
        return {"type": "thinking" if event.kind == "reasoning_delta" else event.kind,
                "content": event.content}
    if isinstance(event, ToolCallEvent):
        return {"type": "tool_call", "id": event.call_id, "name": event.name,
                "arguments": event.arguments}
    if isinstance(event, ToolResultEvent):
        return {"type": "tool_result", "tool_call_id": event.call_id, "name": event.name,
                "status": event.status, "preview": event.preview}
    if isinstance(event, FileChangesEvent):
        return {"type": "file_changes", "file_changes": event.changes}
    return None
