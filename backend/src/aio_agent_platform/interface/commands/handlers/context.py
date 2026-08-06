"""Context / usage / export commands."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from aio_agent_platform.core.chat import load_conversation_history, resolve_model
from aio_agent_platform.core.config import settings
from aio_agent_platform.core.context import (
    ContextBudget,
    estimate_messages_tokens,
    estimate_tokens,
    generate_summary,
)
from aio_agent_platform.db.models import Agent, Message, TokenUsageDaily
from aio_agent_platform.llm import create_provider

from ..models import CommandArg, CommandContext, CommandResult
from ..registry import command

_EXPORT_MAX_CHARS = 20_000


async def _session_model_id(ctx: CommandContext) -> UUID | None:
    """Session-level model override, falling back to the bound agent's model."""
    if ctx.session is not None and ctx.session.model_id:
        return ctx.session.model_id
    if ctx.session is not None and ctx.session.agent_id:
        agent = await ctx.db.get(Agent, ctx.session.agent_id)
        return agent.model_id if agent else None
    return None


async def _history_messages(ctx: CommandContext) -> tuple[list, str | None]:
    if not ctx.session_id:
        return [], None
    return await load_conversation_history(ctx.db, UUID(ctx.session_id), limit=None)


@command("context", aliases=["ctx"], group="上下文", desc="显示上下文占用：token 估算与预算占比")
async def cmd_context(ctx: CommandContext) -> CommandResult:
    if not ctx.session_id:
        return CommandResult(content="当前没有会话，请先开始对话。")
    budget = ContextBudget.from_settings()
    history, context_summary = await _history_messages(ctx)
    est = estimate_messages_tokens(history)
    total_msgs = await ctx.db.scalar(
        select(func.count(Message.id)).where(Message.session_id == UUID(ctx.session_id))
    )
    pct = est / budget.usable * 100 if budget.usable else 0
    lines = [
        "**上下文占用：**",
        "",
        f"- 可用窗口：{budget.usable:,} tokens（总 {budget.total_window:,} - 预留 {budget.reserve_output:,}）",
        f"- 历史估算：{est:,} tokens（{pct:.0f}%）",
        f"- 压缩触发线：{budget.trigger_at:,} tokens",
        f"- 会话消息数：{total_msgs or 0} 条（含命令结果）",
        f"- 已有摘要：{len(context_summary or ''):,} 字符",
    ]
    if pct >= budget.compress_threshold * 100:
        lines.append("")
        lines.append("⚠️ 已接近预算，建议运行 /compact 压缩上下文。")
    return CommandResult(content="\n".join(lines))


@command("compact", group="上下文", desc="立即压缩上下文，生成摘要释放 token")
async def cmd_compact(ctx: CommandContext) -> CommandResult:
    if ctx.session is None or not ctx.session_id:
        return CommandResult(content="当前没有会话，请先开始对话。")
    history, _ = await _history_messages(ctx)
    if not history:
        return CommandResult(content="会话还没有可压缩的内容。")
    before = estimate_messages_tokens(history)

    model = await resolve_model(ctx.db, await _session_model_id(ctx))
    provider = create_provider(
        provider=model.provider.provider_type,
        model=model.model_name,
        base_url=model.provider.base_url,
        api_key=model.provider.api_key_encrypted,
        temperature=settings.llm.temperature,
    )
    summary = await generate_summary(history[-20:], provider)
    if not summary:
        return CommandResult(content="摘要生成失败，请稍后重试。")

    ctx.session.context_summary = summary
    await ctx.db.flush()
    after = estimate_tokens(summary)
    return CommandResult(
        content=(
            f"✅ 已生成上下文摘要（{len(summary):,} 字符）。\n\n"
            f"- 压缩前历史估算：{before:,} tokens\n"
            f"- 摘要估算：{after:,} tokens\n"
            f"- 下次对话将基于摘要继续。"
        )
    )


@command("usage", group="上下文", desc="显示会话 token 用量与消息统计")
async def cmd_usage(ctx: CommandContext) -> CommandResult:
    if not ctx.session_id:
        return CommandResult(content="当前没有会话，请先开始对话。")
    sid = UUID(ctx.session_id)

    rows = (
        await ctx.db.execute(
            select(Message.role, func.count(Message.id))
            .where(Message.session_id == sid)
            .group_by(Message.role)
        )
    ).all()
    by_role = dict(rows)
    total_msgs = sum(by_role.values())

    history, _ = await _history_messages(ctx)
    est = estimate_messages_tokens(history)

    since = datetime.now().date() - timedelta(days=6)
    prompt_t, comp_t, total_t, reqs = (
        await ctx.db.execute(
            select(
                func.sum(TokenUsageDaily.prompt_tokens),
                func.sum(TokenUsageDaily.completion_tokens),
                func.sum(TokenUsageDaily.total_tokens),
                func.sum(TokenUsageDaily.request_count),
            ).where(
                TokenUsageDaily.user_id == UUID(ctx.user_id),
                TokenUsageDaily.date >= since,
            )
        )
    ).one()

    lines = [
        "**会话统计：**",
        "",
        f"- 消息总数：{total_msgs}（用户 {by_role.get('user', 0)} / 助手 {by_role.get('assistant', 0)} / 工具 {by_role.get('tool', 0)} / 系统 {by_role.get('system', 0)}）",
        f"- 历史 token 估算：{est:,}",
        "",
        f"**近 7 天 LLM 用量（{since.isoformat()} 起）：**",
        f"- 请求次数：{reqs or 0}",
        f"- Prompt：{prompt_t or 0:,} tokens",
        f"- 生成：{comp_t or 0:,} tokens",
        f"- 合计：{total_t or 0:,} tokens",
    ]
    return CommandResult(content="\n".join(lines))


@command(
    "export",
    group="会话",
    desc="导出会话历史",
    args=[
        CommandArg(name="format", choices=["markdown", "json"], required=False, hint="导出格式（缺省 markdown）")
    ],
)
async def cmd_export(ctx: CommandContext) -> CommandResult:
    if not ctx.session_id:
        return CommandResult(content="当前没有会话，请先开始对话。")
    sid = UUID(ctx.session_id)
    messages = (
        await ctx.db.execute(
            select(Message).where(Message.session_id == sid).order_by(Message.created_at)
        )
    ).scalars().all()
    if not messages:
        return CommandResult(content="会话暂无消息。")

    fmt = ctx.args.get("format") or "markdown"
    if fmt == "json":
        payload = [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "tool_calls": m.tool_calls,
            }
            for m in messages
        ]
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        role_names = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
        parts: list[str] = []
        for m in messages:
            parts.append(f"### {role_names.get(m.role, m.role)}")
            if m.tool_calls:
                names = [
                    tc.get("name") if isinstance(tc, dict) else "?"
                    for tc in (m.tool_calls or [])
                ]
                parts.append(f"调用工具：{', '.join(str(n) for n in names)}")
            if m.content and m.content.strip():
                parts.append(m.content.strip())
            parts.append("")
        content = "\n".join(parts).strip()

    if len(content) > _EXPORT_MAX_CHARS:
        content = content[:_EXPORT_MAX_CHARS] + (
            f"\n\n...（已截断，本会话共 {len(messages)} 条消息）"
        )
    return CommandResult(content=content)
