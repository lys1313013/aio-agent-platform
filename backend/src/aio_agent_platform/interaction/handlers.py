"""AskUserQuestion tool handler — FALLBACK ONLY.

NOTE: AskUserQuestion 的实际处理逻辑在 AgentLoop._run_ask_user_flow() 中，
由 agent loop 直接拦截处理，不走 tool_executor。

原因：如果在 tool_executor handler 中阻塞等待用户响应，会导致死锁——
event_queue 中的 confirmation_required 事件无法被 SSE generator drain，
因为 agent_loop 被 handler 阻塞，不会 yield。

此 handler 仅作为 fallback 注册，防止 agent loop 未拦截时出现未知错误。
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def handle_ask_user_question(
    arguments: dict,
    user_id: str,
    session_id: str,
    **kwargs,
) -> str:
    """Fallback handler — should never be called in normal flow."""
    logger.warning(
        "ask_user_fallback_called",
        session_id=session_id,
        message="AskUserQuestion handler called outside agent loop interception — this is unexpected",
    )
    return (
        "Error: AskUserQuestion must be handled by the agent loop directly. "
        "This fallback handler should not be reached."
    )


# Handler registry for easy registration
INTERACTION_HANDLERS = {
    "AskUserQuestion": handle_ask_user_question,
}
