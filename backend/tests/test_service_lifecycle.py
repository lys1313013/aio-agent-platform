"""Resource ownership must survive partial startup, shutdown errors and cancellation."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI

from aio_agent_platform.channels import connection_manager
from aio_agent_platform.channels.adapter import InboundEvent
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
from aio_agent_platform.core.task_events import TaskEventBroker
from aio_agent_platform.cron_jobs import scheduler as scheduler_module
from aio_agent_platform.hooks.manager import HookManager
from aio_agent_platform.interface import bootstrap, lifecycle
from aio_agent_platform.observation.recorder import Recorder
from aio_agent_platform.tools.mcp import manager as mcp_module
from aio_agent_platform.tools.mcp.manager import MCPManager


@pytest.fixture
def services(monkeypatch):
    calls = []
    app = FastAPI()

    def sync(name):
        return Mock(side_effect=lambda *args, **kwargs: calls.append(name))

    def asynchronous(name):
        async def call(*args, **kwargs):
            calls.append(name)
        return AsyncMock(side_effect=call)

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace()

    sandbox = SimpleNamespace(start_periodic_sync=asynchronous("sandbox.start"), shutdown=asynchronous("sandbox.close"))
    mcp = SimpleNamespace(shutdown=asynchronous("mcp.close"))
    executor = SimpleNamespace(registry=object())
    remote = SimpleNamespace(initialize=asynchronous("remote.start"))
    recorder = SimpleNamespace(start=sync("recorder.start"), shutdown=asynchronous("recorder.close"))
    hooks = SimpleNamespace(start=sync("hooks.start"), shutdown=asynchronous("hooks.close"))
    scheduler = SimpleNamespace(start=asynchronous("scheduler.start"), shutdown=asynchronous("scheduler.close"), add_system_job=sync("scheduler.add"))
    channels = SimpleNamespace(start_all=asynchronous("channels.start"), stop_all=asynchronous("channels.close"))
    monkeypatch.setattr(lifecycle, "init_db", asynchronous("database.start"))
    monkeypatch.setattr(lifecycle, "close_db", asynchronous("database.close"))
    monkeypatch.setattr(lifecycle, "get_session_factory", lambda: factory)
    monkeypatch.setattr(lifecycle, "ObjectStorage", Mock())
    monkeypatch.setattr(lifecycle, "WorkspaceStorage", Mock())
    monkeypatch.setattr(lifecycle, "SandboxManager", lambda **kwargs: sandbox)
    monkeypatch.setattr(lifecycle, "create_tool_executor", lambda manager: executor)
    monkeypatch.setattr(lifecycle, "MCPManager", lambda: mcp)
    monkeypatch.setattr(lifecycle, "connect_mcp_servers", asynchronous("mcp.start"))
    monkeypatch.setattr(lifecycle, "RemoteToolManager", lambda registry: remote)
    monkeypatch.setattr(lifecycle, "RemoteToolExecutor", Mock())
    monkeypatch.setattr(lifecycle, "init_langfuse", sync("langfuse.start"))
    monkeypatch.setattr(lifecycle, "shutdown_langfuse", asynchronous("langfuse.close"))
    monkeypatch.setattr(lifecycle, "get_recorder", lambda: recorder)
    monkeypatch.setattr(lifecycle, "get_hook_manager", lambda: hooks)
    monkeypatch.setattr(lifecycle, "create_scheduler", lambda *args: scheduler)
    monkeypatch.setattr(lifecycle, "ChannelConnectionManager", lambda *args: channels)
    monkeypatch.setattr(lifecycle, "shutdown_chat_runs", asynchronous("chat.close"))
    monkeypatch.setattr(lifecycle, "shutdown_rooms", asynchronous("rooms.close"))
    monkeypatch.setattr(lifecycle, "shutdown_background_tasks", asynchronous("background.close"))
    monkeypatch.setattr(lifecycle, "broker", SimpleNamespace(shutdown=asynchronous("broker.close")))
    monkeypatch.setattr(scheduler_module, "_global_scheduler", None)
    monkeypatch.setattr(connection_manager, "_global_channel_manager", None)
    return SimpleNamespace(app=app, calls=calls, sandbox=sandbox, mcp=mcp, hooks=hooks, scheduler=scheduler, channels=channels)


def closed(services):
    return [name for name in services.calls if name.endswith(".close")]


async def test_normal_shutdown_follows_dependencies_and_clears_globals(services):
    async with lifecycle.lifespan(services.app):
        assert scheduler_module.get_global_scheduler() is services.scheduler
        assert connection_manager.get_global_channel_manager() is services.channels
        assert services.app.state.tool_executor is not None
    assert closed(services) == [
        "channels.close", "scheduler.close", "broker.close", "chat.close",
        "rooms.close", "background.close", "hooks.close", "mcp.close",
        "sandbox.close", "recorder.close", "langfuse.close", "database.close",
    ]
    assert scheduler_module.get_global_scheduler() is None
    assert connection_manager.get_global_channel_manager() is None
    assert not hasattr(services.app.state, "tool_executor")
    assert not hasattr(services.app.state, "scheduler")
    assert not hasattr(services.app.state, "channel_connection_manager")


async def test_partial_startup_releases_already_created_services(services):
    services.hooks.start.side_effect = RuntimeError("hook start failed")
    with pytest.raises(RuntimeError, match="hook start failed"):
        async with lifecycle.lifespan(services.app):
            pytest.fail("startup must fail")
    assert closed(services) == ["hooks.close", "mcp.close", "sandbox.close", "recorder.close", "langfuse.close", "database.close"]
    assert not hasattr(services.app.state, "sandbox_mgr")


async def test_database_start_failure_still_closes_its_engine(services, monkeypatch):
    monkeypatch.setattr(lifecycle, "init_db", AsyncMock(side_effect=RuntimeError("database setup failed")))
    with pytest.raises(RuntimeError, match="database setup failed"):
        async with lifecycle.lifespan(services.app):
            pytest.fail("startup must fail")
    assert closed(services) == ["database.close"]


async def test_shutdown_error_does_not_skip_other_cleanup(services):
    async def fail():
        services.calls.append("hooks.close")
        # Dependencies remain accessible until every active run is stopped.
        assert services.app.state.tool_executor is not None
        raise RuntimeError("hook shutdown failed")
    services.hooks.shutdown.side_effect = fail
    with pytest.raises(RuntimeError, match="hook shutdown failed"):
        async with lifecycle.lifespan(services.app):
            pass
    assert closed(services)[-6:] == ["hooks.close", "mcp.close", "sandbox.close", "recorder.close", "langfuse.close", "database.close"]
    assert not hasattr(services.app.state, "tool_executor")


async def test_optional_partial_starts_are_closed_immediately_and_once(services):
    services.scheduler.start.side_effect = RuntimeError("scheduler partial start")
    services.channels.start_all.side_effect = RuntimeError("channel partial start")
    async with lifecycle.lifespan(services.app):
        services.scheduler.shutdown.assert_awaited_once()
        services.channels.stop_all.assert_awaited_once()
        assert scheduler_module.get_global_scheduler() is None
        assert connection_manager.get_global_channel_manager() is None
    services.scheduler.shutdown.assert_awaited_once()
    services.channels.stop_all.assert_awaited_once()


async def test_startup_cancellation_cleans_resources_and_propagates(services, monkeypatch):
    entered = asyncio.Event()

    async def connecting(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(lifecycle, "connect_mcp_servers", connecting)

    async def serve():
        async with lifecycle.lifespan(services.app):
            pytest.fail("startup must be cancelled")

    task = asyncio.create_task(serve())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed(services) == ["mcp.close", "sandbox.close", "database.close"]


async def test_running_application_cancellation_still_closes_every_resource(services):
    entered = asyncio.Event()

    async def serve():
        async with lifecycle.lifespan(services.app):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(serve())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(closed(services)) == 12
    assert closed(services)[-1] == "database.close"


async def test_cancellation_during_shutdown_does_not_skip_remaining_resources(services):
    entered = asyncio.Event()

    async def stop():
        services.calls.append("channels.close")
        entered.set()
        await asyncio.Event().wait()

    services.channels.stop_all.side_effect = stop

    async def serve():
        async with lifecycle.lifespan(services.app):
            pass

    task = asyncio.create_task(serve())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(closed(services)) == 12
    assert scheduler_module.get_global_scheduler() is None
    assert connection_manager.get_global_channel_manager() is None


async def test_mcp_start_does_not_swallow_application_cancellation():
    entered = asyncio.Event()
    server = SimpleNamespace(id=uuid4(), name="test", transport_type="sse", url="http://invalid", headers=None, tool_prefix="", timeout=1)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [server]))))

    @asynccontextmanager
    async def factory():
        yield db

    async def add_server(*args):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(bootstrap.connect_mcp_servers(SimpleNamespace(add_server=add_server), factory))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("running", [False, True])
async def test_scheduler_always_closes_claims_after_partial_start(running):
    scheduler = scheduler_module.Scheduler.__new__(scheduler_module.Scheduler)
    scheduler._scheduler = SimpleNamespace(running=running, shutdown=Mock(side_effect=RuntimeError("shutdown failed")))
    scheduler._occurrence_executor = SimpleNamespace(pending_tasks=lambda: ())
    scheduler._claims = SimpleNamespace(close=AsyncMock())
    if running:
        with pytest.raises(RuntimeError, match="shutdown failed"):
            await scheduler.shutdown()
    else:
        await scheduler.shutdown()
        scheduler._scheduler.shutdown.assert_not_called()
    scheduler._claims.close.assert_awaited_once()


async def test_pipeline_shutdown_waits_for_interrupted_turn_and_rejects_new_work(monkeypatch):
    entered = asyncio.Event()
    finished = asyncio.Event()
    pipeline = ChannelInboundPipeline(SimpleNamespace(id=uuid4()), None, None)
    starts = []

    async def handle(event):
        starts.append(event.event_id)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            finished.set()

    pipeline._safe_handle = handle
    event = InboundEvent(channel_id=pipeline.channel.id, event_id=str(uuid4()), chat_id="chat", external_id="user", text="hello")
    pipeline.submit(event)
    await entered.wait()
    pipeline.submit(InboundEvent(channel_id=pipeline.channel.id, event_id=str(uuid4()), chat_id="chat", external_id="user", text="queued"))
    await pipeline.shutdown()
    assert finished.is_set()
    assert starts == [event.event_id]
    assert not pipeline._running and not pipeline._chat_consumers
    pipeline.submit(InboundEvent(channel_id=pipeline.channel.id, event_id=str(uuid4()), chat_id="chat", external_id="user", text="late"))
    assert not pipeline._chat_consumers


async def test_pipeline_shutdown_does_not_cancel_existing_rescue_twice():
    entered = asyncio.Event()
    rescuing = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    pipeline = ChannelInboundPipeline(SimpleNamespace(id=uuid4()), None, None)

    async def handle(event):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            rescuing.set()
            await release.wait()
            finished.set()

    pipeline._safe_handle = handle
    event = InboundEvent(channel_id=pipeline.channel.id, event_id=str(uuid4()), chat_id="chat", external_id="user", text="hello")
    pipeline.submit(event)
    await entered.wait()
    pipeline._running[pipeline._chat_key(event)].task.cancel()
    await rescuing.wait()
    stopping = asyncio.create_task(pipeline.shutdown())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping
    assert finished.is_set()


async def test_hook_shutdown_awaits_initial_load_and_workers(monkeypatch):
    manager = HookManager()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def load():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            finished.set()

    monkeypatch.setattr(manager, "_load_hooks", load)
    monkeypatch.setattr(manager, "_flush_log_queue", AsyncMock())
    manager.start()
    await started.wait()
    tasks = [manager._load_task, manager._refresh_task, manager._log_task, *manager._workers]
    await manager.shutdown()
    assert finished.is_set()
    assert all(task.done() for task in tasks)
    assert manager._load_task is None and not manager._workers


async def test_broker_shutdown_awaits_listener_cleanup():
    broker = TaskEventBroker()
    entered = asyncio.Event()
    released = asyncio.Event()

    async def listen():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            released.set()

    broker._listen_loop = listen
    await broker._ensure_running()
    await entered.wait()
    await broker.shutdown()
    assert released.is_set()
    assert broker._listen_task is None


async def test_scheduler_waits_for_running_job_before_closing_claims(monkeypatch):
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def close_claims():
        assert finished.is_set()

    monkeypatch.setattr(scheduler_module, "OccurrenceClaims", lambda: SimpleNamespace(close=close_claims))
    scheduler = scheduler_module.Scheduler(None)

    async def job():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            finished.set()

    scheduler._scheduler.add_job(job, "date")
    scheduler._scheduler.start()
    await entered.wait()
    await scheduler.shutdown()
    assert finished.is_set()


async def test_scheduler_can_close_before_apscheduler_was_started(monkeypatch):
    claims = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(scheduler_module, "OccurrenceClaims", lambda: claims)
    scheduler = scheduler_module.Scheduler(None)
    await scheduler.shutdown()
    claims.close.assert_awaited_once()


async def test_channel_partial_start_releases_pipeline_and_adapter(monkeypatch):
    pipeline = SimpleNamespace(shutdown=AsyncMock())
    adapter = SimpleNamespace(pipeline=pipeline, start=AsyncMock(side_effect=asyncio.CancelledError()), stop=AsyncMock())
    monkeypatch.setattr(connection_manager, "get_channel_spec", lambda _: SimpleNamespace(build=lambda *args: adapter))
    manager = connection_manager.ChannelConnectionManager(None)
    channel = SimpleNamespace(id=uuid4(), channel_type="test")
    with pytest.raises(asyncio.CancelledError):
        await manager.start_channel(channel)
    pipeline.shutdown.assert_awaited_once()
    adapter.stop.assert_awaited_once()
    assert not manager._adapters


async def test_mcp_failure_does_not_leave_other_connections_open():
    manager = MCPManager()
    first = SimpleNamespace(disconnect=AsyncMock(side_effect=RuntimeError("disconnect failed")))
    second = SimpleNamespace(disconnect=AsyncMock())
    manager._connections = {uuid4(): first, uuid4(): second}
    with pytest.raises(RuntimeError, match="disconnect failed"):
        await manager.shutdown()
    second.disconnect.assert_awaited_once()
    assert not manager._connections


async def test_mcp_cancelled_start_closes_unpublished_connection(monkeypatch):
    connection = SimpleNamespace(connect=AsyncMock(side_effect=asyncio.CancelledError()), disconnect=AsyncMock())
    monkeypatch.setattr(mcp_module, "MCPServerConnection", lambda *args: connection)
    manager = MCPManager()
    with pytest.raises(asyncio.CancelledError):
        await manager.add_server(uuid4(), {})
    connection.disconnect.assert_awaited_once()
    assert not manager._connections


async def test_recorder_flush_failure_still_stops_worker(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(recorder, "flush", AsyncMock(side_effect=RuntimeError("flush failed")))
    recorder.start()
    task = recorder._task
    with pytest.raises(RuntimeError, match="flush failed"):
        await recorder.shutdown()
    assert task.done()
    assert recorder._task is None
