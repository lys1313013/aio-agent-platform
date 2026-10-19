"""Streaming consumers may switch tasks without moving the execution context."""

import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.execution_scope import bind_execution_scope, execution_tenant_id
from aio_agent_platform.observation.recorder import get_obs_context, set_obs_context


@pytest.mark.parametrize("finish", ["exhaust", "close", "cancel", "error"])
async def test_stream_context_survives_task_switch_and_restores_caller(monkeypatch, finish):
    user, tenant, agent, parent_tenant, parent_agent = [uuid4() for _ in range(5)]
    entered = asyncio.Event()
    closed = asyncio.Event()
    observation = Mock()
    caller_observation = get_obs_context()

    def assert_scope():
        assert execution_tenant_id(user) == tenant
        assert current_agent_id.get() == str(agent)
        assert get_obs_context() is observation

    async def run(*args):
        producer = asyncio.current_task()
        set_obs_context(observation)
        try:
            assert_scope()
            yield "first"
            assert_scope()
            assert asyncio.current_task() is producer
            # Tasks spawned by execution must inherit its own scope.
            async def child():
                assert_scope()
            await asyncio.create_task(child())
            entered.set()
            if finish == "cancel":
                await asyncio.Event().wait()
            if finish == "error":
                raise RuntimeError("provider failed")
            yield "second"
        finally:
            assert asyncio.current_task() is producer
            assert_scope()
            closed.set()
            set_obs_context(None)

    loop = AgentLoop(provider=Mock(), tool_executor=Mock(), execution_tenant_id=tenant)
    loop.execution_agent_id = agent
    monkeypatch.setattr(loop, "_run", run)
    with bind_execution_scope(user, parent_tenant, agent_id=parent_agent):
        iterator = loop.run("test", user, uuid4(), [], [])
        assert await asyncio.create_task(anext(iterator)) == "first"
        assert not entered.is_set()  # No prefetch while the consumer is paused.
        assert execution_tenant_id(user) == parent_tenant
        assert current_agent_id.get() == str(parent_agent)
        assert get_obs_context() is caller_observation
        try:
            if finish == "exhaust":
                assert [event async for event in iterator] == ["second"]
            elif finish == "close":
                await asyncio.create_task(iterator.aclose())
            elif finish == "cancel":
                next_event = asyncio.create_task(anext(iterator))
                await asyncio.wait_for(entered.wait(), timeout=2)
                next_event.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await next_event
            else:
                with pytest.raises(RuntimeError, match="provider failed"):
                    await anext(iterator)
        finally:
            await iterator.aclose()
        assert closed.is_set()
        assert execution_tenant_id(user) == parent_tenant
        assert current_agent_id.get() == str(parent_agent)
        assert get_obs_context() is caller_observation
