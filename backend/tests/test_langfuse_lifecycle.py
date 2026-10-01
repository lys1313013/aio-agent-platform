"""Langfuse network cleanup must not block the application's event loop."""

import asyncio
import threading
from unittest.mock import Mock

from aio_agent_platform.observation import client as observation


async def test_shutdown_drains_sdk_off_event_loop(monkeypatch):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    event_loop_thread = threading.get_ident()
    shutdown_threads = []

    def shutdown():
        shutdown_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        # The finite fallback also makes a synchronous regression fail safely.
        release.wait(timeout=2)

    client = Mock(shutdown=Mock(side_effect=shutdown))
    monkeypatch.setattr(observation, "_client", client)
    task = asyncio.create_task(observation.shutdown_langfuse())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert not task.done(), "the event loop stalled until SDK shutdown finished"
        assert len(shutdown_threads) == 1
        assert shutdown_threads[0] != event_loop_thread
        assert observation.get_langfuse_client() is None
    finally:
        release.set()
        await task

    await observation.shutdown_langfuse()
    client.shutdown.assert_called_once_with()
    client.flush.assert_not_called()  # SDK shutdown already flushes its queues.


async def test_shutdown_failure_is_logged_and_client_released(monkeypatch):
    client = Mock(shutdown=Mock(side_effect=RuntimeError("export unavailable")))
    logger = Mock()
    monkeypatch.setattr(observation, "_client", client)
    monkeypatch.setattr(observation, "logger", logger)

    await observation.shutdown_langfuse()

    logger.exception.assert_called_once_with("langfuse_shutdown_failed")
    assert observation.get_langfuse_client() is None
