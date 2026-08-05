"""notify_channel tool — lets a cron agent decide whether to push a result to the bound IM channel.

The cron executor sets ``current_cron_notify_ctx`` around the AgentLoop run and injects
the tool schema only when the job has a ``channel_id``. When the agent calls the tool,
the executor pushes the given text to the job owner's bound account. If the agent never
calls it, nothing is pushed — that is the "silent when there's nothing to report" mode.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

NOTIFY_CHANNEL_TOOL_NAME = "notify_channel"

NOTIFY_CHANNEL_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": NOTIFY_CHANNEL_TOOL_NAME,
        "description": (
            "将一条消息推送到用户绑定的 IM 渠道（如飞书），用于定时任务主动通知用户。"
            "只有当任务发现问题、需要用户关注或明确要求报告时才调用；"
            "如果一切正常、没有需要用户知晓的内容，不要调用本工具，直接结束。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要推送给用户的文本内容"},
            },
            "required": ["text"],
        },
    },
}


@dataclass
class CronNotifyContext:
    push_fn: Callable[[str], Awaitable[bool]]
    job_id: str
    channel_id: str | None


current_cron_notify_ctx: contextvars.ContextVar[CronNotifyContext | None] = (
    contextvars.ContextVar("current_cron_notify_ctx", default=None)
)


async def handle_notify_channel(
    args: dict,
    user_id: str,
    session_id: str,
    **kwargs,
) -> str:
    """Direct handler for ``notify_channel``."""
    ctx = current_cron_notify_ctx.get()
    if ctx is None:
        return "当前执行环境不支持渠道推送，无法通知用户。"

    text = (args.get("text") or "").strip()
    if not text:
        return "请提供要推送的文本内容（text 参数）。"

    try:
        ok = await ctx.push_fn(text)
    except Exception as exc:
        logger.warning(
            "cron_notify_channel_push_failed",
            job_id=ctx.job_id,
            channel_id=ctx.channel_id,
            error=str(exc),
        )
        return f"渠道通知失败：{exc}"

    if ok:
        return "已通过渠道通知用户。"
    return "渠道推送未成功（渠道未连接、未启用或未绑定账号），请检查渠道配置后再试。"
