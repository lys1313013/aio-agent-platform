"""Skills / memory / knowledge / portrait commands."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.models import Skill, UserProfile
from aio_agent_platform.memory.service import MemoryService

from ..dynamic import run_skill
from ..models import CommandArg, CommandContext, CommandResult
from ..registry import command

# ---- Skills ----


@command("skills", group="技能", desc="列出已安装技能")
async def cmd_skills(ctx: CommandContext) -> CommandResult:
    result = await ctx.db.execute(
        select(Skill)
        .where(Skill.user_id == UUID(ctx.user_id), Skill.is_active)
        .order_by(Skill.category, Skill.created_at)
    )
    skills = list(result.scalars().all())
    if not skills:
        return CommandResult(content="暂无技能。技能可在「技能管理」页面创建。")
    lines = ["**已安装技能：**", ""]
    for s in skills:
        lines.append(f"- `{s.name}` — {s.description or '无描述'}（{s.category}）")
    return CommandResult(content="\n".join(lines))


@command(
    "skill",
    group="技能",
    desc="运行指定技能，不经过模型规划",
    args=[
        CommandArg(name="name", required=True, hint="技能名称"),
        CommandArg(name="input", required=False, variadic=True, hint="交给技能的输入"),
    ],
)
async def cmd_skill(ctx: CommandContext) -> CommandResult:
    return await run_skill(ctx, ctx.args["name"], ctx.args.get("input"))


# ---- Memory ----


@command("memory", group="记忆", desc="查看当前用户长期记忆列表")
async def cmd_memory(ctx: CommandContext) -> CommandResult:
    memories = await MemoryService.list_memories(
        ctx.db, UUID(ctx.user_id), limit=50
    )
    if not memories:
        return CommandResult(content="暂无长期记忆，发送 /remember <内容> 写入一条。")
    lines = ["**长期记忆：**", ""]
    for m in memories:
        content = (m.content or "").replace("\n", " ")[:120]
        lines.append(f"- [`{m.id}` · {m.layer}] {content}")
    return CommandResult(content="\n".join(lines))


@command(
    "remember",
    group="记忆",
    desc="手动写入一条长期记忆",
    args=[CommandArg(name="content", required=True, variadic=True, hint="记忆内容")],
)
async def cmd_remember(ctx: CommandContext) -> CommandResult:
    content = ctx.args["content"].strip()
    if not content:
        return CommandResult(content="记忆内容不能为空。")
    await MemoryService.create_memory(
        ctx.db, UUID(ctx.user_id), layer="L2", content=content
    )
    return CommandResult(content=f"✅ 已写入长期记忆：{content[:80]}")


@command(
    "forget",
    group="记忆",
    desc="删除指定记忆",
    args=[CommandArg(name="id", kind="uuid", required=True, hint="记忆 ID")],
)
async def cmd_forget(ctx: CommandContext) -> CommandResult:
    deleted = await MemoryService.delete_memory(
        ctx.db, UUID(ctx.args["id"]), UUID(ctx.user_id)
    )
    if not deleted:
        return CommandResult(content="记忆不存在。")
    return CommandResult(content=f"✅ 已删除记忆 `{ctx.args['id']}`。")


# ---- Knowledge ----


@command(
    "knowledge",
    group="知识",
    desc="直接检索知识库并返回结果，不生成对话",
    args=[CommandArg(name="query", required=True, variadic=True, hint="检索问题")],
)
async def cmd_knowledge(ctx: CommandContext) -> CommandResult:
    from aio_agent_platform.knowledge.handlers import handle_knowledge_retrieval

    # The retrieval handler resolves datasets from current_agent_id; fall back to
    # the current session's agent when available.
    agent_id = ctx.session.agent_id if ctx.session is not None else None
    token = None
    try:
        if agent_id is not None:
            token = current_agent_id.set(str(agent_id))
        text = await handle_knowledge_retrieval(
            {"query": ctx.args["query"], "top_k": 5},
            user_id=ctx.user_id,
            session_id=ctx.session_id or "",
        )
    finally:
        if token is not None:
            current_agent_id.reset(token)
    return CommandResult(content=text)


# ---- Portrait ----


@command("portrait", group="知识", desc="查看 Agent 对当前用户的画像摘要")
async def cmd_portrait(ctx: CommandContext) -> CommandResult:
    profile = await ctx.db.scalar(
        select(UserProfile).where(UserProfile.user_id == UUID(ctx.user_id))
    )
    portrait = (profile.personal_portrait if profile else None) or "暂无画像数据。"
    return CommandResult(content=portrait)
