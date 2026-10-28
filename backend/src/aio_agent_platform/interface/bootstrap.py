"""Business registration and optional integrations used by application startup."""

from __future__ import annotations

import asyncio
from functools import partial

import structlog
from fastapi import FastAPI
from sqlalchemy import select

from aio_agent_platform.artifacts.webpage import ARTIFACT_HANDLERS
from aio_agent_platform.channels.cron_notify import (
    CRON_RESPOND_TOOL_NAME,
    NOTIFY_CHANNEL_TOOL_NAME,
    handle_cron_respond,
    handle_notify_channel,
)
from aio_agent_platform.channels.file_send import SEND_FILE_TOOL_NAME, handle_send_file
from aio_agent_platform.cron_jobs.executor import execute_cron_job
from aio_agent_platform.cron_jobs.handlers import CRON_JOB_HANDLERS
from aio_agent_platform.cron_jobs.scheduler import Scheduler
from aio_agent_platform.db.models import ChannelBinding, ChannelConfig, MCPServer
from aio_agent_platform.delegation import DELEGATION_HANDLERS
from aio_agent_platform.graph_knowledge.handlers import GRAPH_HANDLERS
from aio_agent_platform.interaction import INTERACTION_HANDLERS
from aio_agent_platform.knowledge.handlers import KNOWLEDGE_HANDLERS
from aio_agent_platform.memory.handlers import MEMORY_HANDLERS
from aio_agent_platform.pets.smart import ensure_pet_tools_registered
from aio_agent_platform.portrait.handlers import PORTRAIT_HANDLERS
from aio_agent_platform.skills.handlers import SKILL_HANDLERS
from aio_agent_platform.tools import ToolExecutor, ToolRegistry, register_builtin_tools
from aio_agent_platform.tools import web as web_tools
from aio_agent_platform.tools.mcp.manager import MCPManager
from aio_agent_platform.tools.model_catalog import handle_list_models
from aio_agent_platform.tools.runtime_info import handle_get_runtime_info

logger = structlog.get_logger()


def create_tool_executor(sandbox_mgr) -> ToolExecutor:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry=registry, sandbox_mgr=sandbox_mgr)
    handlers = {
        "get_runtime_info": handle_get_runtime_info,
        "list_models": handle_list_models,
        SEND_FILE_TOOL_NAME: handle_send_file,
        CRON_RESPOND_TOOL_NAME: handle_cron_respond,
        NOTIFY_CHANNEL_TOOL_NAME: handle_notify_channel,
    }
    for group in (
        MEMORY_HANDLERS, SKILL_HANDLERS, KNOWLEDGE_HANDLERS, GRAPH_HANDLERS,
        DELEGATION_HANDLERS, INTERACTION_HANDLERS, PORTRAIT_HANDLERS,
        CRON_JOB_HANDLERS, ARTIFACT_HANDLERS,
    ):
        handlers.update(group)
    for name, handler in handlers.items():
        executor.register_direct_handler(name, handler)
    web_tools.register_handlers(executor)
    ensure_pet_tools_registered(executor)
    return executor


async def connect_mcp_servers(manager: MCPManager, factory) -> None:
    """An unreachable integration may degrade startup; cancellation may not."""
    try:
        async with factory() as db:
            result = await db.execute(select(MCPServer).where(MCPServer.is_active))
            servers = result.scalars().all()
        for server in servers:
            config = {
                "name": server.name,
                "transport_type": server.transport_type,
                "url": server.url,
                "headers": server.headers or {},
                "tool_prefix": server.tool_prefix,
                "timeout": server.timeout,
            }
            try:
                await asyncio.wait_for(manager.add_server(server.id, config), timeout=15)
            except asyncio.CancelledError:
                # Some MCP/anyio timeouts surface as cancellation of the child
                # operation. Never mistake cancellation of the application for it.
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                logger.warning("mcp_server_startup_skipped", server_id=str(server.id), name=server.name)
            except TimeoutError:
                logger.warning("mcp_server_startup_skipped", server_id=str(server.id), name=server.name)
            except Exception as error:
                logger.warning("mcp_server_startup_failed", server_id=str(server.id), name=server.name, error=str(error))
    except Exception as error:
        logger.warning("mcp_manager_init_failed", error=str(error))


async def push_cron_result_to_channel(app: FastAPI, db, job, text: str) -> bool:
    manager = getattr(app.state, "channel_connection_manager", None)
    if manager is None:
        logger.warning("cron_job_channel_manager_unavailable", job_id=str(job.id))
        return False
    adapter = manager.get_adapter(job.channel_id)
    if adapter is None:
        logger.warning("cron_job_channel_not_connected", job_id=str(job.id), channel_id=str(job.channel_id))
        return False
    channel = (await db.execute(select(ChannelConfig).where(
        ChannelConfig.id == job.channel_id,
        ChannelConfig.tenant_id == job.tenant_id,
        ChannelConfig.status == "enabled",
    ))).scalar_one_or_none()
    if channel is None:
        return False
    binding = (await db.execute(select(ChannelBinding).where(
        ChannelBinding.tenant_id == channel.tenant_id,
        ChannelBinding.channel_id == channel.id,
        ChannelBinding.user_id == job.user_id,
    ))).scalars().first()
    if binding is None:
        logger.warning("cron_job_channel_no_binding", job_id=str(job.id), user_id=str(job.user_id), channel_id=str(job.channel_id))
        return False
    message_id = await adapter.send_to_user(binding.external_id, f"**⏰ {job.name}**\n\n{text}")
    if message_id:
        logger.info("cron_job_channel_pushed", job_id=str(job.id), channel_id=str(job.channel_id))
    else:
        logger.warning("cron_job_channel_push_failed", job_id=str(job.id), channel_id=str(job.channel_id))
    return bool(message_id)


def create_scheduler(app: FastAPI, factory, tool_executor: ToolExecutor) -> Scheduler:
    return Scheduler(factory, executor=partial(
        execute_cron_job,
        tool_executor=tool_executor,
        push_to_channel=partial(push_cron_result_to_channel, app),
    ))
