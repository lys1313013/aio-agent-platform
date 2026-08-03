"""宠物任务事件的 Redis Pub/Sub 订阅端 broker。

`task_registry` 在任务生命周期事件（started/tool/finished）时发布到 Redis 频道
``aio:pet_task_events``，这里维护 user_id → SSE 连接队列的内存注册表，由共享的
pubsub 订阅后台任务按 user_id 过滤后写入各队列。SSE 端点通过 ``stream()`` 消费：
连接即快照（读 task_registry），再收增量事件，附带心跳哨兵。

Redis 不可用时：发布侧静默丢弃（task_registry），订阅侧监听循环带退避重连，
SSE 降级为「连接即快照、无增量」。宠物是附属系统，不拖垮对话链路。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis
import structlog

from aio_agent_platform.core import task_registry
from aio_agent_platform.core.config import settings
from aio_agent_platform.core.task_registry import _EVENT_CHANNEL

logger = structlog.get_logger()

HEARTBEAT_SECONDS = 25.0
_RECONNECT_DELAY_SECONDS = 1.0
_QUEUE_MAX = 128


def _serialize(task: task_registry.RunningTask) -> dict:
    return {
        "session_id": task.session_id,
        "label": task.label,
        "tool": task.tool,
        "source": task.source,
        "chat_key": task.chat_key,
        "agent_id": task.agent_id,
        "started_at": task.started_at,
    }


class TaskEventBroker:
    """进程内注册表 + 单条 Redis pubsub 订阅任务，向各 SSE 连接分发事件。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._listen_task: asyncio.Task | None = None

    async def _ensure_running(self) -> None:
        if self._listen_task is not None and not self._listen_task.done():
            return
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        client = aioredis.Redis.from_url(settings.redis.url, decode_responses=True)
        try:
            while True:
                try:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(_EVENT_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        self._dispatch(message["data"])
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("task_event_listen_failed", exc_info=True)
                    await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
        finally:
            await client.aclose()

    def _dispatch(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        user_id = str(data.get("user_id") or "")
        if not user_id:
            return
        queues = list(self._subscribers.get(user_id, ()))
        for q in queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                pass

    async def stream(self, user_id: UUID) -> AsyncIterator[dict | None]:
        """先订阅再读快照，随后排空增量事件；超过心跳间隔未收到事件产出 None（心跳哨兵）。"""
        key = str(user_id)
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        await self._ensure_running()
        self._subscribers.setdefault(key, set()).add(queue)
        try:
            tasks = await task_registry.list_tasks(user_id)
            yield {
                "type": "pet_task_snapshot",
                "tasks": [_serialize(t) for t in tasks],
            }
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield None
                    continue
                yield data
        finally:
            queues = self._subscribers.get(key)
            if queues is not None:
                queues.discard(queue)
                if not queues:
                    self._subscribers.pop(key, None)


broker = TaskEventBroker()
