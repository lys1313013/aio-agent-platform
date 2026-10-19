"""Per-run notification decisions; only the cron executor performs delivery."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

CRON_RESPOND_TOOL_NAME = "cron_respond"
NOTIFY_CHANNEL_TOOL_NAME = "notify_channel"  # Legacy prompts may still call this alias.
SILENT_REPLY_TOKEN = "NO_REPLY"

CRON_RESPOND_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": CRON_RESPOND_TOOL_NAME,
        "description": (
            "登记定时任务的执行摘要和 IM 通知决策，不立即发送。"
            "按定时任务运行协议与任务约定选择 notify；"
            "notify=false 必须提供静默原因 reason，summary 始终保存在后台。"
            "notify=true 可另填 notification_text，省略时使用 summary。"
            "邮件等任务操作应先完成，再上报；工具成功仅代表已登记，不能据此声称消息已送达。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "notify": {"type": "boolean", "description": "本次是否发送 IM 通知"},
                "summary": {"type": "string", "description": "保存在后台的完整执行摘要"},
                "notification_text": {"type": "string", "description": "需要通知时的消息正文"},
                "reason": {"type": "string", "description": "notify=false 时必填的静默原因"},
            },
            "required": ["notify", "summary"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class CronResponse:
    notify: bool
    summary: str
    notification_text: str | None = None
    reason: str | None = None


@dataclass
class CronNotifyContext:
    job_id: str
    user_id: str
    session_id: str
    response: CronResponse | None = None
    legacy_text: str | None = None
    active: bool = True


current_cron_notify_ctx: contextvars.ContextVar[CronNotifyContext | None] = (
    contextvars.ContextVar("current_cron_notify_ctx", default=None)
)


def _context(user_id: str, session_id: str) -> CronNotifyContext | None:
    ctx = current_cron_notify_ctx.get()
    if ctx is None or not ctx.active or ctx.user_id != user_id or ctx.session_id != session_id:
        return None
    return ctx


async def handle_cron_respond(args: dict, user_id: str, session_id: str, **kwargs) -> str:
    ctx = _context(user_id, session_id)
    if ctx is None:
        return "Error: 当前执行环境不支持定时任务结果上报。"
    notify = args.get("notify")
    summary = args.get("summary")
    reason = args.get("reason")
    text = args.get("notification_text")
    if not isinstance(notify, bool):
        return "Error: notify 必须是布尔值。"
    if not isinstance(summary, str) or not summary.strip() or summary.strip() == SILENT_REPLY_TOKEN:
        return "Error: summary 必须是非空的执行摘要，不能是 NO_REPLY。"
    if not notify and (not isinstance(reason, str) or not reason.strip()):
        return "Error: 静默时必须提供 reason。"
    if text is not None and (not isinstance(text, str) or not text.strip()):
        return "Error: notification_text 必须是非空文本。"
    if notify and text is not None and text.strip() == SILENT_REPLY_TOKEN:
        return "Error: 不通知时请使用 notify=false，不能将 NO_REPLY 作为通知正文。"
    ctx.response = CronResponse(
        notify=notify,
        summary=summary.strip(),
        notification_text=text.strip() if text is not None else None,
        reason=reason.strip() if isinstance(reason, str) else None,
    )
    return "已记录执行摘要和通知决策，尚未发送通知。若结果无误，请结束本轮；最终文字不会覆盖已登记的决策。"


async def handle_notify_channel(args: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Queue a legacy notification; never send before the final decision is known."""
    ctx = _context(user_id, session_id)
    if ctx is None:
        return "Error: 当前执行环境不支持渠道通知。"
    text = args.get("text")
    if not isinstance(text, str) or not text.strip() or text.strip() == SILENT_REPLY_TOKEN:
        return "Error: 请提供非空通知正文；静默请使用 cron_respond(notify=false)。"
    # Kept separate so a structured decision or final NO_REPLY can override it.
    ctx.legacy_text = text.strip()
    return "通知请求已登记，尚未发送。请使用 cron_respond 上报最终摘要和通知决策。"
