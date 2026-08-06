"""Agent / confirmation / workspace / model commands."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aio_agent_platform.core.confirmation import confirmation_manager
from aio_agent_platform.db.models import Agent, LLMModel, Workspace
from aio_agent_platform.interface.routes.agents import _agent_visible_to

from ..models import CommandArg, CommandContext, CommandResult
from ..registry import command

# ---- Agents ----


@command("agents", group="智能体", desc="列出我可用的智能体")
async def cmd_agents(ctx: CommandContext) -> CommandResult:
    result = await ctx.db.execute(
        select(Agent)
        .where(Agent.is_active, _agent_visible_to(ctx.user))
        .order_by(Agent.created_at)
    )
    agents = list(result.scalars().all())
    if not agents:
        return CommandResult(content="暂无可用智能体。")
    lines = ["**可用智能体：**", ""]
    for a in agents:
        marker = " ← 当前" if ctx.session and ctx.session.agent_id == a.id else ""
        lines.append(f"- `{a.name}` — {a.description or '无描述'}{marker}")
    lines.append("")
    lines.append("切换：`/agent <名称>`")
    return CommandResult(content="\n".join(lines))


@command(
    "agent",
    group="智能体",
    desc="切换当前会话绑定的智能体",
    args=[CommandArg(name="name", required=True, hint="智能体名称")],
)
async def cmd_agent(ctx: CommandContext) -> CommandResult:
    if ctx.session is None:
        return CommandResult(content="当前没有会话，请先开始对话。")
    name = ctx.args["name"]
    agent = await ctx.db.scalar(
        select(Agent)
        .where(Agent.name == name, Agent.is_active, _agent_visible_to(ctx.user))
    )
    if agent is None:
        return CommandResult(content=f"智能体 `{name}` 不存在。\n输入 /agents 查看可用智能体。")
    ctx.session.agent_id = agent.id
    await ctx.db.flush()
    return CommandResult(
        content=f"✅ 已切换到智能体「{agent.name}」。",
        data={"agent_id": str(agent.id)},
    )


# ---- Confirmations ----


@command(
    "approve",
    group="确认",
    desc="通过待确认操作（等效于点击确认卡片按钮）",
    args=[CommandArg(name="id", required=False, hint="确认 ID（缺省列出待确认项）")],
)
async def cmd_approve(ctx: CommandContext) -> CommandResult:
    return await _resolve_confirmation(ctx, "approved")


@command(
    "deny",
    group="确认",
    desc="拒绝待确认操作",
    args=[CommandArg(name="id", required=False, hint="确认 ID（缺省列出待确认项）")],
)
async def cmd_deny(ctx: CommandContext) -> CommandResult:
    return await _resolve_confirmation(ctx, "rejected")


async def _resolve_confirmation(ctx: CommandContext, status: str) -> CommandResult:
    cid = ctx.args.get("id")
    if not cid:
        if not ctx.session_id:
            return CommandResult(content="当前会话没有待确认项。")
        pending = confirmation_manager.get_pending(ctx.session_id)
        if not pending:
            return CommandResult(content="当前会话没有待确认项。")
        lines = ["**待确认操作：**", ""]
        for c in pending:
            lines.append(f"- `{c.id}` · {c.question}")
        lines.append("")
        lines.append("用 /approve <id> 或 /deny <id> 处理。")
        return CommandResult(content="\n".join(lines))

    ok = confirmation_manager.resolve_confirmation(cid, {"status": status})
    if not ok:
        return CommandResult(content="确认项不存在或已处理。")
    verb = "通过" if status == "approved" else "拒绝"
    return CommandResult(content=f"✅ 已{verb}确认项。")


# ---- Workspaces ----


@command(
    "ws",
    group="工作区",
    desc="管理工作区",
    usage="/ws <list|use> [slug]",
    args=[
        CommandArg(name="action", required=True, choices=["list", "use"], hint="list / use"),
        CommandArg(name="slug", required=False, hint="工作区 slug"),
    ],
)
async def cmd_ws(ctx: CommandContext) -> CommandResult:
    action = ctx.args["action"]
    if action == "list":
        return await _ws_list(ctx)
    return await _ws_use(ctx)


async def _ws_list(ctx: CommandContext) -> CommandResult:
    result = await ctx.db.execute(
        select(Workspace)
        .where(Workspace.user_id == UUID(ctx.user_id))
        .order_by(Workspace.is_default.desc(), Workspace.created_at)
    )
    workspaces = list(result.scalars().all())
    if not workspaces:
        return CommandResult(content="暂无工作区。")
    lines = ["**工作区：**", ""]
    for w in workspaces:
        marker = " ← 当前" if ctx.session and ctx.session.workspace_id == w.id else ""
        default = "（默认）" if w.is_default else ""
        lines.append(f"- `{w.slug}` · {w.name}{default}{marker}")
    lines.append("")
    lines.append("切换：`/ws use <slug>`")
    return CommandResult(content="\n".join(lines))


async def _ws_use(ctx: CommandContext) -> CommandResult:
    slug = ctx.args.get("slug")
    if not slug:
        return CommandResult(content="缺少工作区 slug。\n`/ws list` 查看，`/ws use <slug>` 切换。")
    ws = await ctx.db.scalar(
        select(Workspace).where(
            Workspace.user_id == UUID(ctx.user_id), Workspace.slug == slug
        )
    )
    if ws is None:
        return CommandResult(content=f"工作区 `{slug}` 不存在。")
    if ctx.session is None:
        return CommandResult(content="当前没有会话，请先开始对话。")
    ctx.session.workspace_id = ws.id
    await ctx.db.flush()
    return CommandResult(
        content=f"✅ 已切换到工作区「{ws.name}」。",
        data={"workspace_id": str(ws.id)},
    )


# ---- Model ----


@command("model", group="模型", desc="查看当前会话使用的模型与可用模型")
async def cmd_model(ctx: CommandContext) -> CommandResult:
    result = await ctx.db.execute(
        select(LLMModel)
        .options(selectinload(LLMModel.provider))
        .where(LLMModel.is_active)
        .order_by(LLMModel.is_default.desc(), LLMModel.name)
    )
    models = list(result.scalars().all())

    current = None
    if ctx.session is not None and ctx.session.agent_id:
        agent = await ctx.db.get(Agent, ctx.session.agent_id)
        if agent is not None and agent.model_id:
            current = await ctx.db.get(LLMModel, agent.model_id)

    if not models:
        return CommandResult(content="暂无可用模型，请联系管理员配置。")

    lines = ["**可用模型：**", ""]
    for m in models:
        marker = " ← 当前" if current and m.id == current.id else ""
        default = "（默认）" if m.is_default else ""
        provider_name = m.provider.name if m.provider else "?"
        lines.append(f"- `{m.name}` — {provider_name}{default}{marker}")
    if current is None:
        lines.append("")
        lines.append("当前会话使用系统默认模型。")
    return CommandResult(content="\n".join(lines))
