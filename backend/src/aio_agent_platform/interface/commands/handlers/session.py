"""Session management commands."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from aio_agent_platform.db import Session

from ..models import CommandArg, CommandContext, CommandResult
from ..registry import command


@command("new", aliases=["reset", "clear"], group="会话", desc="清空当前上下文，开启新会话")
async def cmd_new(ctx: CommandContext) -> CommandResult:
    session = Session(user_id=UUID(ctx.user_id), title="新对话")
    ctx.db.add(session)
    await ctx.db.flush()
    await ctx.db.refresh(session)
    return CommandResult(
        content="✅ 已开启新会话。",
        session_id=str(session.id),
        data={"new_session_id": str(session.id)},
    )


@command("sessions", group="会话", desc="列出我的历史会话")
async def cmd_sessions(ctx: CommandContext) -> CommandResult:
    result = await ctx.db.execute(
        select(Session)
        .where(Session.user_id == UUID(ctx.user_id))
        .order_by(Session.updated_at.desc())
        .limit(20)
    )
    sessions = list(result.scalars().all())
    if not sessions:
        return CommandResult(content="暂无历史会话，发送 /new 开启新会话。")
    lines = ["**最近会话：**", ""]
    for s in sessions:
        title = (s.title or "未命名会话")[:40]
        lines.append(f"- `{s.id}` · {title}")
    return CommandResult(content="\n".join(lines))


@command(
    "rename",
    group="会话",
    desc="重命名当前会话",
    args=[CommandArg(name="title", required=True, variadic=True, hint="新标题")],
)
async def cmd_rename(ctx: CommandContext) -> CommandResult:
    if ctx.session is None:
        return CommandResult(content="当前没有可重命名的会话，请先开始对话。")
    title = ctx.args["title"].strip()
    if not title:
        return CommandResult(content="标题不能为空。")
    ctx.session.title = title
    await ctx.db.flush()
    return CommandResult(
        content=f"✅ 已重命名为「{title}」",
        data={"session_id": str(ctx.session.id), "title": title},
    )


@command(
    "delete",
    group="会话",
    desc="删除指定会话",
    args=[CommandArg(name="id", kind="uuid", required=True, hint="会话 ID")],
)
async def cmd_delete(ctx: CommandContext) -> CommandResult:
    session = await ctx.db.get(Session, UUID(ctx.args["id"]))
    if session is None or session.user_id != UUID(ctx.user_id):
        return CommandResult(content="会话不存在。")
    await ctx.db.delete(session)
    return CommandResult(
        content=f"✅ 已删除会话 `{ctx.args['id']}`。",
        data={"deleted_session_id": ctx.args["id"]},
    )
