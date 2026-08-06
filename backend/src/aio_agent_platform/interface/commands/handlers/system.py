"""Help / identity / run-control commands."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from aio_agent_platform.db import Session
from aio_agent_platform.db.models import Memory

from ..dispatcher import dynamic_commands
from ..models import Command, CommandContext, CommandResult
from ..registry import command, registry


@command("help", group="帮助", desc="显示命令帮助摘要，按分组列出")
async def cmd_help(ctx: CommandContext) -> CommandResult:
    cmds = registry.list_for(ctx.user)
    dyn = await dynamic_commands(ctx.db, ctx.user_id)
    known = {c.name for c in cmds}
    cmds = [*cmds, *[d for d in dyn if d.name not in known]]

    groups: dict[str, list[Command]] = {}
    for c in cmds:
        groups.setdefault(c.group, []).append(c)

    order = [
        "帮助", "会话", "技能", "记忆", "知识", "定时任务",
        "智能体", "确认", "工作区", "模型", "运行", "通用",
    ]
    lines = ["## 可用命令", ""]
    for g in order:
        if g not in groups:
            continue
        lines.append(f"### {g}")
        for c in sorted(groups[g], key=lambda x: x.name):
            lines.append(f"- `{c.usage_text}` — {c.desc}")
        lines.append("")
    lines.append("> 输入 /commands 查看全部命令，输入 /help 查看此帮助。")
    return CommandResult(content="\n".join(lines))


@command("commands", group="帮助", desc="列出全部可用命令（含动态生成的技能命令）")
async def cmd_commands(ctx: CommandContext) -> CommandResult:
    cmds = registry.list_for(ctx.user)
    dyn = await dynamic_commands(ctx.db, ctx.user_id)
    known = {c.name for c in cmds}
    cmds = [*cmds, *[d for d in dyn if d.name not in known]]

    lines = ["## 全部命令", "", "| 命令 | 说明 | 参数 |", "|---|---|---|"]
    for c in sorted(cmds, key=lambda x: (x.group, x.name)):
        args_text = ", ".join(
            f"{a.name}({'必填' if a.required else '可选'})" for a in c.args
        ) or "—"
        marker = "🔹 " if c.dynamic else ""
        lines.append(f"| {marker}`{c.usage_text}` | {c.desc} | {args_text} |")
    return CommandResult(content="\n".join(lines))


@command("whoami", group="帮助", desc="显示当前用户 ID、租户、会话信息")
async def cmd_whoami(ctx: CommandContext) -> CommandResult:
    uid = UUID(ctx.user_id)
    session_count = await ctx.db.scalar(
        select(func.count(Session.id)).where(Session.user_id == uid)
    )
    memory_count = await ctx.db.scalar(
        select(func.count(Memory.id)).where(Memory.user_id == uid)
    )
    return CommandResult(
        content=(
            f"**当前用户**：{ctx.user.username}\n"
            f"**用户 ID**：`{ctx.user_id}`\n"
            f"**角色**：{ctx.user.role}\n"
            f"**租户 ID**：`{ctx.user.tenant_id}`\n"
            f"**会话数**：{session_count or 0}\n"
            f"**长期记忆数**：{memory_count or 0}"
        )
    )


@command("stop", group="运行", desc="中断当前正在生成的回复")
async def cmd_stop(ctx: CommandContext) -> CommandResult:
    # The frontend aborts its SSE connection to actually cancel the stream;
    # this command returns a confirmation marker.
    return CommandResult(content="✅ 已停止生成。")
