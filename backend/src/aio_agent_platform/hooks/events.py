"""Hook 事件注册表 — 事件名、说明、负载字段，供管理端事件字典与表单使用。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventDef:
    """单个 Hook 事件定义。"""

    name: str
    description: str
    payload_fields: tuple[tuple[str, str, bool], ...] = ()
    # payload_fields 元素: (字段名, 说明, 是否可能含敏感值需脱敏)


EVENTS: dict[str, EventDef] = {
    e.name: e
    for e in [
        EventDef(
            "SessionStart",
            "会话开始（Agent 收到用户输入开始执行）",
            (("user_input", "用户输入（截断500）", True),),
        ),
        EventDef(
            "SessionEnd",
            "会话结束（正常完成 / 超迭代 / 异常终止）",
            (
                ("status", "completed/timeout/error", False),
                ("duration_ms", "端到端耗时", False),
                ("iteration_count", "LLM 轮数", False),
                ("total_tokens", "累计 token", False),
            ),
        ),
        EventDef(
            "PreToolUse",
            "工具调用前",
            (("tool_name", "工具名", False), ("arguments", "工具参数", True)),
        ),
        EventDef(
            "PostToolUse",
            "工具调用后",
            (
                ("tool_name", "工具名", False),
                ("success", "是否成功", False),
                ("duration_ms", "耗时", False),
                ("output_excerpt", "输出摘要（截断500）", True),
            ),
        ),
        EventDef(
            "PreCompact",
            "上下文压缩前",
            (("tokens_before", "压缩前 token", False),),
        ),
        EventDef(
            "PostCompact",
            "上下文压缩后",
            (
                ("tokens_before", "压缩前 token", False),
                ("tokens_after", "压缩后 token", False),
                ("saved_tokens", "节省 token", False),
            ),
        ),
        EventDef(
            "Notification",
            "异常事件（权限拒绝 / 上下文告警 / 超迭代 / 工具失败）",
            (
                ("level", "permission/error/warning", False),
                ("category", "异常分类", False),
                ("message", "说明", True),
            ),
        ),
    ]
}
