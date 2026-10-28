"""Application resource ownership, including partial startup and cancellation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from fastapi import FastAPI

from aio_agent_platform.channels.connection_manager import (
    ChannelConnectionManager,
    get_global_channel_manager,
    set_global_channel_manager,
)
from aio_agent_platform.channels.feishu.webhook_transport import build_webhook_router
from aio_agent_platform.core.chat import background_tasks
from aio_agent_platform.core.chat_runs import shutdown as shutdown_chat_runs
from aio_agent_platform.core.task_events import broker
from aio_agent_platform.cron_jobs.scheduler import get_global_scheduler, set_global_scheduler
from aio_agent_platform.db.connection import close_db, get_session_factory, init_db
from aio_agent_platform.hooks import get_hook_manager
from aio_agent_platform.interface.bootstrap import (
    connect_mcp_servers,
    create_scheduler,
    create_tool_executor,
)
from aio_agent_platform.memory.daily import run_daily_consolidation
from aio_agent_platform.observation import init_langfuse, shutdown_langfuse
from aio_agent_platform.observation.recorder import get_recorder
from aio_agent_platform.rooms.runtime import shutdown as shutdown_rooms
from aio_agent_platform.sandbox import SandboxManager
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.tools.mcp.manager import MCPManager
from aio_agent_platform.tools.remote.executor import RemoteToolExecutor
from aio_agent_platform.tools.remote.manager import RemoteToolManager

logger = structlog.get_logger()
Cleanup = Callable[[], Awaitable[None]]


def own(stack: AsyncExitStack, name: str, cleanup: Cleanup) -> Cleanup:
    """Register before starting a resource; optional startup can release it early."""
    closed = False

    async def close_once() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            await cleanup()
        except BaseException:
            logger.exception("service_cleanup_failed", service=name)
            raise

    stack.push_async_callback(close_once)
    return close_once


async def release_optional(cleanup: Cleanup) -> None:
    try:
        await cleanup()
    except Exception:
        # Optional integration startup already failed. All other resources are
        # still owned by their stacks; cleanup errors are logged by own().
        pass


async def shutdown_background_tasks() -> None:
    tasks = list(background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    background_tasks.difference_update(tasks)


def clear_state(app: FastAPI, name: str, resource) -> None:
    if getattr(app.state, name, None) is resource:
        delattr(app.state, name)


async def start_scheduler(app: FastAPI, factory, executor, stack: AsyncExitStack) -> None:
    scheduler = create_scheduler(app, factory, executor)

    async def shutdown() -> None:
        try:
            await scheduler.shutdown()
        finally:
            if get_global_scheduler() is scheduler:
                set_global_scheduler(None)
            clear_state(app, "scheduler", scheduler)

    cleanup = own(stack, "scheduler", shutdown)
    try:
        await scheduler.start()
        scheduler.add_system_job("daily-memory-consolidation", "40 0 * * *", run_daily_consolidation)
        app.state.scheduler = scheduler
        set_global_scheduler(scheduler)
    except Exception as error:
        logger.warning("scheduler_start_failed", error=str(error))
        await release_optional(cleanup)


async def start_channels(app: FastAPI, factory, executor, stack: AsyncExitStack) -> None:
    manager = ChannelConnectionManager(executor)

    async def shutdown() -> None:
        try:
            await manager.stop_all()
        finally:
            if get_global_channel_manager() is manager:
                set_global_channel_manager(None)
            clear_state(app, "channel_connection_manager", manager)

    cleanup = own(stack, "channels", shutdown)
    try:
        async with factory() as db:
            await manager.start_all(db)
        app.state.channel_connection_manager = manager
        set_global_channel_manager(manager)
    except Exception as error:
        logger.warning("channel_manager_start_failed", error=str(error))
        await release_optional(cleanup)
    # Lifespan may be entered repeatedly by local tooling and tests.
    if not getattr(app.state, "channel_webhooks_mounted", False):
        app.include_router(build_webhook_router())
        app.state.channel_webhooks_mounted = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The stacks encode dependency order, independent of startup order:
    # ingress -> active runs -> tools/hooks -> observations -> database.
    # AsyncExitStack runs every callback even if an earlier one fails/cancels,
    # and keeps cleanup in this task (important for async context managers).
    async with AsyncExitStack() as resources:
        owned_state: list[tuple[str, object]] = []

        def forget_resources() -> None:
            for name, resource in owned_state:
                clear_state(app, name, resource)

        resources.callback(forget_resources)
        own(resources, "database", close_db)
        observations = await resources.enter_async_context(AsyncExitStack())
        infrastructure = await resources.enter_async_context(AsyncExitStack())
        runs = await resources.enter_async_context(AsyncExitStack())
        ingress = await resources.enter_async_context(AsyncExitStack())
        started_at = time.monotonic()

        def mark(phase: str) -> None:
            nonlocal started_at
            now = time.monotonic()
            logger.info("startup_phase", phase=phase, seconds=round(now - started_at, 2))
            started_at = now

        try:
            await asyncio.wait_for(init_db(), timeout=600)
        except TimeoutError as error:
            raise RuntimeError("数据库初始化或自动迁移超时，服务停止启动") from error
        mark("init_db")
        factory = get_session_factory()
        workspace_storage = None
        try:
            workspace_storage = WorkspaceStorage(ObjectStorage())
        except Exception as error:
            logger.warning("object_storage_init_failed (new sandbox workspaces unavailable; existing files retained)", error=str(error))
        sandbox = SandboxManager(workspace_storage=workspace_storage)
        own(infrastructure, "sandbox", sandbox.shutdown)
        await sandbox.start_periodic_sync()
        mark("object_storage+sandbox")
        executor = create_tool_executor(sandbox)
        mark("tool_registry+handlers")
        mcp = MCPManager()
        own(infrastructure, "mcp", mcp.shutdown)
        await connect_mcp_servers(mcp, factory)
        executor.mcp_manager = mcp
        mark("mcp_connect")
        remote = RemoteToolManager(executor.registry)
        try:
            await remote.initialize(factory)
        except Exception as error:
            logger.warning("remote_manager_init_failed", error=str(error))
        executor.remote_manager = remote
        executor.remote_executor = RemoteToolExecutor(remote)
        for name, resource in (
            ("sandbox_mgr", sandbox), ("tool_executor", executor),
            ("mcp_manager", mcp), ("remote_manager", remote),
        ):
            setattr(app.state, name, resource)
            owned_state.append((name, resource))
        own(observations, "langfuse", shutdown_langfuse)
        init_langfuse()
        mark("remote_tools+langfuse")
        recorder = get_recorder()
        own(observations, "recorder", recorder.shutdown)
        recorder.start()
        app.state.recorder = recorder
        owned_state.append(("recorder", recorder))
        hooks = get_hook_manager()
        own(infrastructure, "hooks", hooks.shutdown)
        hooks.start(sandbox)
        app.state.hook_manager = hooks
        owned_state.append(("hook_manager", hooks))
        own(runs, "background_tasks", shutdown_background_tasks)
        own(runs, "rooms", shutdown_rooms)
        own(runs, "chat_runs", shutdown_chat_runs)
        own(runs, "task_event_broker", broker.shutdown)
        await start_scheduler(app, factory, executor, ingress)
        mark("scheduler")
        await start_channels(app, factory, executor, ingress)
        mark("channels")
        yield
