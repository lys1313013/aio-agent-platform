"""Slash command data structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

ArgKind = Literal["str", "int", "cron", "uuid", "select"]


@dataclass
class CommandArg:
    name: str
    kind: ArgKind = "str"
    required: bool = False
    variadic: bool = False
    choices: list[str] | None = None
    hint: str | None = None


@dataclass
class Command:
    name: str
    handler: Callable[[CommandContext], CommandResult]
    group: str = "通用"
    desc: str = ""
    usage: str | None = None
    aliases: list[str] = field(default_factory=list)
    args: list[CommandArg] = field(default_factory=list)
    permission: str = "user"
    dynamic: bool = False
    hidden: bool = False

    @property
    def usage_text(self) -> str:
        if self.usage:
            return self.usage
        parts = [f"/{self.name}"]
        for a in self.args:
            if a.variadic:
                parts.append(f"<{a.name}...>")
            elif a.required:
                parts.append(f"<{a.name}>")
            else:
                parts.append(f"[{a.name}]")
        return " ".join(parts)


@dataclass
class CommandContext:
    user: Any
    user_id: str
    db: Any
    raw: str
    session_id: str | None = None
    session: Any | None = None
    args: dict[str, Any] = field(default_factory=dict)
    tool_executor: Any | None = None


@dataclass
class CommandResult:
    content: str
    session_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    persist: bool = True
