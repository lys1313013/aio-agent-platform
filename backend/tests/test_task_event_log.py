"""Unit tests for core.task_event_log (Redis Stream 事件日志)。"""

from __future__ import annotations

import uuid

import pytest

from aio_agent_platform.core import task_event_log


async def _skip_if_no_redis() -> None:
    try:
        await task_event_log._redis().ping()
    except Exception:
        pytest.skip("redis not reachable")


async def test_log_read_tail_roundtrip() -> None:
    await _skip_if_no_redis()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    client = task_event_log._redis()
    key = task_event_log._stream_key(user_id, session_id)
    await client.delete(key)

    await task_event_log.log_event(user_id, session_id, {"type": "thinking", "content": "hi"})
    await task_event_log.log_event(user_id, session_id, {"type": "done", "content": "ok"})

    events = await task_event_log.read_events(user_id, session_id)
    assert [e["type"] for _, e in events] == ["thinking", "done"]
    assert events[0][1]["content"] == "hi"

    # after_id = 第一条 id，tail 应取回第二条（done）
    tail = await task_event_log.tail_events(user_id, session_id, events[0][0], timeout_ms=1000)
    assert tail is not None
    assert tail[1]["type"] == "done"

    # 无新事件时 tail 超时返回 None
    stale = await task_event_log.tail_events(user_id, session_id, tail[0], timeout_ms=200)
    assert stale is None

    await client.delete(key)


async def test_log_event_degrades_when_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 不可用时 log_event 静默降级，read_events 返回空。"""
    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(task_event_log, "_redis", _boom)
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    await task_event_log.log_event(user_id, session_id, {"type": "thinking", "content": "x"})
    assert await task_event_log.read_events(user_id, session_id) == []
