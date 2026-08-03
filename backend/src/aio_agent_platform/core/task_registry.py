"""在跑任务的 Redis 注册表。

渠道（飞书等）触发的 AgentLoop 完全跑在后端，没有浏览器 SSE 连接，
宠物 widget 无法通过前端事件感知。这里维护 user_id → 任务的 Redis 哈希，
由渠道 pipeline 写入、宠物路由轮询读出。多 worker / 多副本部署共享。

Redis 不可用时所有操作静默降级（宠物是附属系统，不能拖垮对话链路）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from uuid import UUID

import redis.asyncio as aioredis
import structlog

from aio_agent_platform.core.config import settings

logger = structlog.get_logger()

TASK_TTL_SECONDS = 30 * 60
LABEL_MAX_LEN = 30
_KEY_PREFIX = "aio:pet_tasks:"


@dataclass
class RunningTask:
    session_id: str
    label: str
    source: str  # 渠道类型: feishu/dingtalk/wecom
    # 渠道会话标识 {channel_id}:{chat_id}:{external_id}，同一渠道聊天 /new 换 session 后仍同 key
    chat_key: str = ""
    tool: str | None = None
    started_at: float = field(default_factory=time.time)

    def dumps(self) -> str:
        return json.dumps(
            {
                "session_id": self.session_id,
                "label": self.label,
                "source": self.source,
                "chat_key": self.chat_key,
                "tool": self.tool,
                "started_at": self.started_at,
            },
            ensure_ascii=False,
        )

    @classmethod
    def loads(cls, raw: str) -> RunningTask | None:
        try:
            data = json.loads(raw)
            return cls(
                session_id=str(data["session_id"]),
                label=str(data["label"]),
                source=str(data["source"]),
                chat_key=str(data.get("chat_key") or ""),
                tool=data.get("tool"),
                started_at=float(data.get("started_at") or time.time()),
            )
        except (KeyError, TypeError, ValueError):
            return None


_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis.from_url(settings.redis.url, decode_responses=True)
    return _client


def _key(user_id: UUID) -> str:
    return f"{_KEY_PREFIX}{user_id}"


async def task_started(
    user_id: UUID, session_id: UUID, label: str, source: str, chat_key: str = ""
) -> None:
    label = " ".join(label.split())[:LABEL_MAX_LEN]
    task = RunningTask(session_id=str(session_id), label=label, source=source, chat_key=chat_key)
    try:
        pipe = _redis().pipeline()
        pipe.hset(_key(user_id), str(session_id), task.dumps())
        pipe.expire(_key(user_id), TASK_TTL_SECONDS)
        await pipe.execute()
    except Exception:
        logger.warning("task_registry_write_failed", op="start", user_id=str(user_id))


async def task_tool(user_id: UUID, session_id: UUID, tool: str) -> None:
    try:
        raw = await _redis().hget(_key(user_id), str(session_id))
        if raw is None:
            return
        task = RunningTask.loads(raw)
        if task is None:
            return
        task.tool = tool
        pipe = _redis().pipeline()
        pipe.hset(_key(user_id), str(session_id), task.dumps())
        pipe.expire(_key(user_id), TASK_TTL_SECONDS)
        await pipe.execute()
    except Exception:
        logger.warning("task_registry_write_failed", op="tool", user_id=str(user_id))


async def task_finished(user_id: UUID, session_id: UUID) -> None:
    try:
        await _redis().hdel(_key(user_id), str(session_id))
    except Exception:
        logger.warning("task_registry_write_failed", op="finish", user_id=str(user_id))


async def list_tasks(user_id: UUID) -> list[RunningTask]:
    try:
        raw = await _redis().hgetall(_key(user_id))
    except Exception:
        logger.warning("task_registry_read_failed", user_id=str(user_id))
        return []
    now = time.time()
    tasks: list[RunningTask] = []
    stale_fields: list[str] = []
    for field_name, value in raw.items():
        task = RunningTask.loads(value)
        if task is None or now - task.started_at > TASK_TTL_SECONDS:
            stale_fields.append(field_name)
            continue
        tasks.append(task)
    if stale_fields:
        try:
            await _redis().hdel(_key(user_id), *stale_fields)
        except Exception:
            pass
    return tasks
