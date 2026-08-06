"""Slash command registry — process-wide singleton + @command decorator."""

from __future__ import annotations

from typing import Any

from .models import Command

# Command permissions are coarse-grained; user/admin derived from User.role.
_ADMIN_ROLES = {"admin", "superadmin"}


def _allowed(user: Any, permission: str) -> bool:
    if permission == "admin":
        return getattr(user, "role", "user") in _ADMIN_ROLES
    return True


class CommandRegistry:
    def __init__(self) -> None:
        # name/alias -> Command (aliases point to the same Command object).
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        """Deduplicated list of unique commands (aliases share an object)."""
        return list({id(c): c for c in self._commands.values()}.values())

    def list_for(self, user: Any) -> list[Command]:
        return [c for c in self.all() if not c.hidden and _allowed(user, c.permission)]


registry = CommandRegistry()


def command(
    name: str,
    *,
    group: str = "通用",
    desc: str = "",
    usage: str | None = None,
    aliases: list[str] | None = None,
    args: list | None = None,
    permission: str = "user",
    hidden: bool = False,
):
    """Register an async handler function as a slash command.

    The decorated function receives a ``CommandContext`` and returns a
    ``CommandResult``. Importing the handler module triggers registration.
    """

    def deco(fn):
        cmd = Command(
            name=name,
            handler=fn,
            group=group,
            desc=desc,
            usage=usage,
            aliases=list(aliases or []),
            args=list(args or []),
            permission=permission,
            hidden=hidden,
        )
        registry.register(cmd)
        return fn

    return deco
