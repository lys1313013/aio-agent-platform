"""Slash command dispatcher — the unified interception entry point."""

from __future__ import annotations

import structlog

from . import dynamic
from .models import Command, CommandContext, CommandResult
from .parser import ParseError, parse_command, split_tokens
from .registry import _allowed, registry

logger = structlog.get_logger()


async def dispatch(ctx: CommandContext) -> CommandResult:
    """Resolve and run a command from raw text. Never raises for user input —
    unknown commands, permission and parse errors degrade to a CommandResult."""
    tokens = split_tokens(ctx.raw)
    if not tokens:
        return CommandResult(content="空命令，输入 /help 查看可用命令。")

    name = tokens[0].lstrip("/")
    cmd = registry.get(name)
    if cmd is not None:
        return await _run(ctx, cmd)

    # Dynamic skill command fallback.
    skill_cmd = None
    if ctx.db is not None:
        skill_cmd = await dynamic.find_skill_command(ctx.db, ctx.user_id, name)
    if skill_cmd is not None:
        return await _run(ctx, skill_cmd)

    return CommandResult(content=f"未知命令 `/{name}`，输入 /help 查看可用命令。")


async def _run(ctx: CommandContext, cmd: Command) -> CommandResult:
    if not _allowed(ctx.user, cmd.permission):
        return CommandResult(content=f"无权限执行 `{cmd.usage_text}`。")

    try:
        ctx.args = parse_command(ctx.raw, cmd)
    except ParseError as e:
        return CommandResult(
            content=f"**{e.message}**\n\n用法：`{e.usage}`\n\n输入 /help 查看命令说明。"
        )

    try:
        return await cmd.handler(ctx)
    except Exception:
        logger.exception("command_handler_failed", command=cmd.name, user_id=ctx.user_id)
        return CommandResult(content=f"执行 `/{cmd.name}` 时出错，请稍后重试。")


async def dynamic_commands(db, user_id: str) -> list[Command]:
    """Visible dynamic skill commands (for /commands and /api/commands)."""
    names = await dynamic.list_skill_names(db, user_id)
    cmds: list[Command] = []
    for name in names:
        cmd = await dynamic.find_skill_command(db, user_id, name)
        if cmd is not None:
            cmds.append(cmd)
    return cmds
