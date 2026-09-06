"""Tests for the shared Redis Pub/Sub task-event broker."""

from __future__ import annotations

import asyncio

import pytest

from aio_agent_platform.core import task_events


class _FailingPubSub:
    def __init__(self) -> None:
        self.closed = False

    async def subscribe(self, _channel: str) -> None:
        return None

    async def listen(self):
        if False:
            yield None
        raise ConnectionError("redis disconnected")

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self) -> None:
        self.pubsubs: list[_FailingPubSub] = []
        self.closed = False

    def pubsub(self) -> _FailingPubSub:
        pubsub = _FailingPubSub()
        self.pubsubs.append(pubsub)
        return pubsub

    async def aclose(self) -> None:
        self.closed = True


async def test_failed_pubsub_is_closed_before_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken subscription must return its dedicated connection to the pool."""
    client = _FakeRedis()

    monkeypatch.setattr(task_events.aioredis.Redis, "from_url", lambda *args, **kwargs: client)

    async def stop_after_retries(_delay: float) -> None:
        # sleep 位于两轮订阅之间；到这里时本轮连接必须已经归还。
        assert client.pubsubs[-1].closed is True
        if len(client.pubsubs) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(task_events.asyncio, "sleep", stop_after_retries)

    broker = task_events.TaskEventBroker()
    with pytest.raises(asyncio.CancelledError):
        await broker._listen_loop()

    assert len(client.pubsubs) == 3
    assert all(pubsub.closed for pubsub in client.pubsubs)
    assert client.closed is True
