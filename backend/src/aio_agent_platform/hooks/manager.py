"""Hook 调度引擎 — 作用域匹配、有界队列、worker 执行、日志批写。"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from aio_agent_platform.core.config import settings
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import Hook, HookExecution
from aio_agent_platform.hooks import dispatcher
from aio_agent_platform.hooks.masking import mask_payload
from aio_agent_platform.observation.recorder import get_obs_context

logger = structlog.get_logger(__name__)


def _is_missing_table(exc: BaseException) -> bool:
    """判断异常是否为"hooks 相关表不存在"（迁移未执行）。"""
    return isinstance(exc, ProgrammingError) and "does not exist" in str(exc)

REFRESH_INTERVAL = 30.0  # 定期重载 Hook 配置
_FLUSH_INTERVAL = 2.0
_LOG_BATCH_SIZE = 200
_MAX_LOG_QUEUE = 5000

# 递归防护：hook 动作执行期间置位，经 create_task 传播到嵌套的 Agent 执行
_hook_action_ctx: contextvars.ContextVar[int] = contextvars.ContextVar("hook_action_ctx", default=0)

# 高频事件默认不落日志（避免写放大），可配置开启
_LOG_SKIP_DEFAULT = {"PreToolUse", "PreCompact"}


@dataclass
class HookDef:
    """Hook 配置的进程内轻量表示（从 ORM Hook 行映射）。"""

    id: UUID
    name: str
    scope: str
    tenant_id: UUID | None
    agent_id: UUID | None
    event: str
    action_type: str
    config: dict
    timeout_ms: int
    retry_count: int

    @classmethod
    def from_row(cls, row: Hook) -> HookDef:
        return cls(
            id=row.id,
            name=row.name,
            scope=row.scope,
            tenant_id=row.tenant_id,
            agent_id=row.agent_id,
            event=row.event,
            action_type=row.action_type,
            config=row.config or {},
            timeout_ms=row.timeout_ms,
            retry_count=row.retry_count,
        )


class HookManager:
    """匹配已加载 Hook → 入有界队列 → worker 异步执行 → 批写触发日志。

    ``fire`` / ``fire_nowait`` 只入队不等待，Agent 主流程零阻塞；队列满则丢弃并告警。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[HookDef, dict, dict]] = asyncio.Queue(
            maxsize=settings.hook.queue_size
        )
        self._log_queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_LOG_QUEUE)
        self._log_batch: list = []
        self._hooks: list[HookDef] = []
        self._workers: list[asyncio.Task] = []
        self._refresh_task: asyncio.Task | None = None
        self._load_task: asyncio.Task | None = None
        self._log_task: asyncio.Task | None = None
        self._sandbox_mgr = None
        self._started = False

    # ---- lifecycle ----

    def start(self, sandbox_mgr=None) -> None:
        """启动 worker 池 + 配置刷新循环 + 日志批写（应用 lifespan 调用）。"""
        if self._started:
            return
        self._sandbox_mgr = sandbox_mgr
        self._started = True
        self._log_task = asyncio.create_task(self._log_flush_loop())
        for _ in range(settings.hook.concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop()))
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._load_task = asyncio.create_task(self._load_hooks())

    async def shutdown(self) -> None:
        """取消刷新与 worker，刷掉积压日志。"""
        self._started = False
        if self._refresh_task:
            self._refresh_task.cancel()
        for w in self._workers:
            w.cancel()
        self._workers = []
        await self._flush_log_queue()
        if self._log_task:
            self._log_task.cancel()

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            await self._load_hooks()

    async def _load_hooks(self) -> None:
        """从 DB 加载启用中的 Hook（失败静默，保留旧缓存）。"""
        try:
            factory = get_session_factory()
            async with factory() as db:
                rows = (
                    await db.execute(select(Hook).where(Hook.is_enabled.is_(True)))
                ).scalars().all()
            self._hooks = [HookDef.from_row(r) for r in rows]
        except Exception as e:
            if _is_missing_table(e):
                # 迁移未执行（hooks 表不存在）是暂态，启动不受阻，提示后继续
                logger.warning(
                    "hook_table_missing",
                    hint="hooks/hook_executions 表不存在，请先执行: alembic upgrade head",
                )
            else:
                logger.exception("hook_load_failed")

    # ---- entry points ----

    async def fire(self, event: str, *, data: dict | None = None, **kwargs) -> None:
        """异步入口：入队匹配的 Hook，立即返回（不等待执行）。"""
        self._fire(event, data=data, **kwargs)

    def fire_nowait(self, event: str, *, data: dict | None = None, **kwargs) -> None:
        """同步入口（供同步调用点使用），同样只入队。"""
        self._fire(event, data=data, **kwargs)

    def _fire(self, event: str, *, data: dict | None = None, **kwargs) -> None:
        if not settings.hook.enabled or not self._started:
            return
        if _hook_action_ctx.get() > 0:  # 递归防护：hook 动作链内不再触发
            return
        ctx = self._build_ctx(kwargs)
        matched = 0
        for hook in self._hooks:
            if hook.event != event or not self._matches(hook, ctx):
                continue
            payload = self._build_payload(hook, event, ctx, data)
            try:
                self._queue.put_nowait((hook, payload, ctx))
                matched += 1
            except asyncio.QueueFull:
                logger.warning("hook_queue_overflow", event=event)
                break
        if matched:
            logger.debug("hook_fired", hook_event=event, matched=matched)

    # ---- context / payload ----

    def _build_ctx(self, kwargs: dict) -> dict:
        """显式 kwargs 优先，其余回退 ObsContext（后台任务可能缺失）。"""
        obs = get_obs_context()

        def _fallback(key: str) -> str | None:
            val = getattr(obs, key, None) if obs is not None else None
            return str(val) if val is not None else None

        return {
            "trace_id": kwargs.get("trace_id") or _fallback("trace_id"),
            "session_id": kwargs.get("session_id") or _fallback("session_id"),
            "user_id": kwargs.get("user_id") or _fallback("user_id"),
            "tenant_id": kwargs.get("tenant_id") or _fallback("tenant_id"),
            "agent_id": kwargs.get("agent_id") or _fallback("agent_id"),
            "model": kwargs.get("model"),
            "workspace_id": kwargs.get("workspace_id"),
            "workspace_slug": kwargs.get("workspace_slug"),
        }

    def _matches(self, hook: HookDef, ctx: dict) -> bool:
        if hook.scope == "global":
            return True
        if hook.scope == "tenant":
            return bool(ctx.get("tenant_id")) and hook.tenant_id == UUID(ctx["tenant_id"])
        if hook.scope == "agent":
            return bool(ctx.get("agent_id")) and hook.agent_id == UUID(ctx["agent_id"])
        return False

    def _build_payload(self, hook: HookDef, event: str, ctx: dict, data: dict | None) -> dict:
        payload = {
            "event": event,
            "event_id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "trace_id": ctx.get("trace_id"),
            "session_id": ctx.get("session_id"),
            "user_id": ctx.get("user_id"),
            "tenant_id": ctx.get("tenant_id"),
            "agent_id": ctx.get("agent_id"),
            "model": ctx.get("model"),
            "hook": {"id": str(hook.id), "name": hook.name, "scope": hook.scope},
            "data": data or {},
        }
        return mask_payload(payload)

    # ---- worker ----

    async def _worker_loop(self) -> None:
        while True:
            hook, payload, ctx = await self._queue.get()
            token = _hook_action_ctx.set(_hook_action_ctx.get() + 1)
            try:
                result = await dispatcher.execute(
                    hook, payload, sandbox_mgr=self._sandbox_mgr, ctx=ctx
                )
            except Exception as e:  # 防御：dispatcher 已兜底，此处绝不抛出
                logger.exception("hook_worker_crash", hook_id=str(hook.id))
                from aio_agent_platform.hooks.dispatcher import ActionResult

                result = ActionResult(status="failed", error=str(e))
            finally:
                _hook_action_ctx.reset(token)
            self._enqueue_log(hook, payload, ctx, result)

    # ---- logging ----

    def _should_log(self, event: str) -> bool:
        if event in _LOG_SKIP_DEFAULT and not settings.hook.log_pre_events:
            return False
        return True

    def _enqueue_log(self, hook: HookDef, payload: dict, ctx: dict, result) -> None:
        if not self._should_log(payload.get("event", "")):
            return
        try:
            self._log_queue.put_nowait((hook, payload, ctx, result))
        except asyncio.QueueFull:
            logger.warning("hook_log_queue_overflow")

    async def _log_flush_loop(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(self._log_queue.get(), timeout=_FLUSH_INTERVAL)
            except TimeoutError:
                await self._flush_log_queue()
                continue
            self._log_batch.append(item)
            if len(self._log_batch) >= _LOG_BATCH_SIZE:
                await self._flush_log_queue()

    async def _flush_log_queue(self) -> None:
        if not self._log_batch:
            return
        batch, self._log_batch = self._log_batch, []
        try:
            factory = get_session_factory()
            async with factory() as db:
                for hook, payload, ctx, result in batch:
                    db.add(HookExecution(**self._log_fields(hook, payload, ctx, result)))
                await db.commit()
        except Exception as e:
            if _is_missing_table(e):
                logger.warning("hook_log_table_missing", hint="请先执行: alembic upgrade head")
            else:
                logger.exception("hook_log_write_failed", batch_size=len(batch))

    @staticmethod
    def _log_fields(hook: HookDef, payload: dict, ctx: dict, result) -> dict:
        p = payload.get
        target = None
        if hook.action_type == "webhook":
            target = _mask_url((hook.config or {}).get("url", "")) or None
        elif hook.action_type == "sandbox_command":
            target = "sandbox_command"
        return {
            "hook_id": hook.id,
            "event": p("event", ""),
            "scope": hook.scope,
            "trace_id": _coerce_uuid(p("trace_id")),
            "session_id": _coerce_uuid(p("session_id")),
            "user_id": _coerce_uuid(p("user_id")),
            "tenant_id": _coerce_uuid(p("tenant_id")),
            "agent_id": _coerce_uuid(p("agent_id")),
            "action_type": hook.action_type,
            "target": target,
            "status": result.status,
            "duration_ms": result.duration_ms,
            "http_status": result.http_status,
            "exit_code": result.exit_code,
            "error": (result.error or "")[:2000] if result.error else None,
            "response_preview": (result.response_preview or "")[:1000] if result.response_preview else None,
        }


def _coerce_uuid(value) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _mask_url(url: str) -> str:
    """去掉 URL 查询串（可能含 token），防日志泄露。"""
    if not url:
        return url
    return url.split("?", 1)[0] + "?***" if "?" in url else url


_manager: HookManager | None = None


def get_hook_manager() -> HookManager:
    """进程级单例（应用 lifespan 初始化）。"""
    global _manager
    if _manager is None:
        _manager = HookManager()
    return _manager


def reset_hook_manager() -> None:
    """重置单例（测试用）。"""
    global _manager
    _manager = None
