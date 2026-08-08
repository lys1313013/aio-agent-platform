"""Hook 机制 — 事件驱动的自动化动作（webhook / 沙箱命令）。

对外暴露：
- ``get_hook_manager()`` — 进程级 Hook 调度引擎（应用 lifespan 初始化后使用）
- ``EVENTS`` — 事件注册表（管理端事件字典数据源）
"""

from __future__ import annotations

from aio_agent_platform.hooks.events import EVENTS, EventDef
from aio_agent_platform.hooks.manager import (
    HookDef,
    HookManager,
    get_hook_manager,
    reset_hook_manager,
)

__all__ = [
    "EVENTS",
    "EventDef",
    "HookDef",
    "HookManager",
    "get_hook_manager",
    "reset_hook_manager",
]
