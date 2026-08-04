"""渠道任务完整 Agent 事件日志（Redis Stream）。

channel pipeline 跑 AgentLoop 时把每个事件（thinking/tool_call/tool_result/
text_delta/text/done）实时写入按 ``user_id:session_id`` 分片的 Redis Stream，
Web 端「重新连接」通过回放端点从头读流 + 实时尾随，让聊天页能看到渠道任务的
完整执行过程（与 Web 端聊天 SSE 事件形状一致）。

Redis 不可用时所有操作静默降级（与 task_registry 一致）：事件不落盘，
回放端点读到的为空流。
"""

from __future__ import annotations

import json
from typing import cast
from uuid import UUID

import redis.asyncio as aioredis
import structlog

from aio_agent_platform.core.config import settings

logger = structlog.get_logger()

TASK_TTL_SECONDS = 30 * 60  # 与 task_registry.TASK_TTL_SECONDS 一致
MAXLEN = 2000  # 单会话事件条数上限（近似）
_KEY_PREFIX = "aio:task_events:"
_FIELD = "data"


_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.Redis.from_url(settings.redis.url, decode_responses=True)
    return _client


def _stream_key(user_id: UUID, session_id: UUID) -> str:
    return f"{_KEY_PREFIX}{user_id}:{session_id}"


async def log_event(user_id: UUID, session_id: UUID, event: dict) -> None:
    """追加一条事件到会话流，续期 TTL。Redis 不可用时静默丢弃。"""
    try:
        client = _redis()
        await client.xadd(
            _stream_key(user_id, session_id),
            {_FIELD: json.dumps(event, ensure_ascii=False)},
            maxlen=MAXLEN,
            approximate=True,
        )
        await client.expire(_stream_key(user_id, session_id), TASK_TTL_SECONDS)
    except Exception:
        logger.warning("task_event_log_write_failed", session_id=str(session_id))


async def read_events(user_id: UUID, session_id: UUID) -> list[tuple[str, dict]]:
    """全量读出会话事件，返回 [(stream_id, event), ...]。"""
    try:
        raw = await _redis().xrange(_stream_key(user_id, session_id), min="-", max="+")
    except Exception:
        logger.warning("task_event_log_read_failed", session_id=str(session_id))
        return []
    events: list[tuple[str, dict]] = []
    for stream_id, fields in raw:
        try:
            events.append((stream_id, json.loads(fields.get(_FIELD) or "{}")))
        except json.JSONDecodeError:
            continue
    return events


async def tail_events(
    user_id: UUID,
    session_id: UUID,
    after_id: str,
    timeout_ms: int = 8000,
) -> tuple[str, dict] | None:
    """阻塞等待 after_id 之后的新事件，超时返回 None。

    ``after_id`` 传最后一条已消费的 stream id（"0" 表示从未消费）。
    """
    try:
        result = await _redis().xread(
            streams={_stream_key(user_id, session_id): after_id},
            count=1,
            block=timeout_ms,
        )
    except Exception:
        logger.warning("task_event_log_tail_failed", session_id=str(session_id))
        return None
    if not result:
        return None
    # redis xread 返回 [[stream_name, [(id, {field: value})]]]，类型桩不精确，显式转换。
    parsed = cast(
        "list[tuple[bytes | str, list[tuple[bytes | str, dict[bytes | str, bytes | str]]]]]",
        result,
    )
    for _stream_name, entries in parsed:
        if not entries:
            continue
        stream_id, fields = entries[0]
        sid = stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)
        try:
            return sid, json.loads((fields or {}).get(_FIELD) or "{}")
        except json.JSONDecodeError:
            return sid, {}
    return None
