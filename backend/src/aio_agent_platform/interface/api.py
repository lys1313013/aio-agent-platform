"""FastAPI application entry point."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aio_agent_platform.auth import auth_router
from aio_agent_platform.core.config import settings
from aio_agent_platform.cron_jobs.handlers import CRON_JOB_HANDLERS
from aio_agent_platform.db.connection import close_db, init_db
from aio_agent_platform.delegation import DELEGATION_HANDLERS
from aio_agent_platform.graph_knowledge.handlers import GRAPH_HANDLERS
from aio_agent_platform.interaction import INTERACTION_HANDLERS

# Importing the commands package registers all built-in slash commands.
from aio_agent_platform.interface import commands as _commands  # noqa: F401
from aio_agent_platform.interface.routes import (
    admin_models_router,
    admin_pets_router,
    agent_api_router,
    agents_router,
    analytics_router,
    channel_bindings_router,
    channels_router,
    chat_router,
    commands_router,
    confirmations_router,
    cron_jobs_router,
    daily_memories_router,
    delegations_router,
    graph_knowledge_router,
    knowledge_router,
    mcp_servers_router,
    memories_router,
    models_router,
    observability_router,
    pets_router,
    public_router,
    remote_tools_router,
    sessions_router,
    settings_router,
    skills_router,
    system_config_router,
    tenants_router,
    tools_router,
    users_router,
    web_tools_router,
    webpages_router,
)
from aio_agent_platform.knowledge.handlers import KNOWLEDGE_HANDLERS
from aio_agent_platform.memory.handlers import MEMORY_HANDLERS
from aio_agent_platform.observation import init_langfuse, shutdown_langfuse
from aio_agent_platform.portrait.handlers import PORTRAIT_HANDLERS
from aio_agent_platform.sandbox import SandboxManager
from aio_agent_platform.skills.handlers import SKILL_HANDLERS
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.tools import ToolExecutor, ToolRegistry, register_builtin_tools
from aio_agent_platform.workspaces.routes import router as workspaces_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: startup / shutdown."""
    # ---- Startup ----
    import time as _time

    import structlog as _structlog

    _startup_log = _structlog.get_logger()
    _t0 = _time.monotonic()

    def _mark(phase: str) -> None:
        nonlocal _t0
        now = _time.monotonic()
        _startup_log.info("startup_phase", phase=phase, seconds=round(now - _t0, 2))
        _t0 = now

    # 1. Database bootstrap (create tables if not exist — for dev convenience)
    try:
        # 远程库 create_all 全量建表较慢，放宽到 10 分钟；连接本身另有 8s connect
        # 超时，外部 DB 不可达时仍会快速失败，不会因本超时无限等待
        await asyncio.wait_for(init_db(), timeout=600)
    except TimeoutError:
        import structlog

        structlog.get_logger().warning("init_db timeout (database unreachable?)")
    except Exception as e:
        import structlog

        structlog.get_logger().warning("init_db failed (may already exist)", error=str(e))
    _mark("init_db")

    # 2. Object storage + workspace storage
    workspace_storage = None
    try:
        object_storage = ObjectStorage()
        workspace_storage = WorkspaceStorage(object_storage)
    except Exception as e:
        import structlog
        structlog.get_logger().warning(
            "object_storage_init_failed (sandbox will run without file sync)",
            error=str(e),
        )

    # 3. Sandbox manager (stateless — workspace files via MinIO)
    sandbox_mgr = SandboxManager(workspace_storage=workspace_storage)

    # 4. Start periodic sync (background task)
    if workspace_storage:
        await sandbox_mgr.start_periodic_sync()
    _mark("object_storage+sandbox")

    # 5. Tool registry + executor
    registry = ToolRegistry()
    register_builtin_tools(registry)
    tool_executor = ToolExecutor(registry=registry, sandbox_mgr=sandbox_mgr)

    # 6. Register memory tool handlers
    for name, handler in MEMORY_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 7. Register skill tool handlers
    for name, handler in SKILL_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 7.5 Register knowledge tool handlers
    for name, handler in KNOWLEDGE_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 7.6 Register graph knowledge tool handlers
    for name, handler in GRAPH_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 8. Register delegation tool handlers
    for name, handler in DELEGATION_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 9. Register interaction tool handlers (AskUserQuestion)
    for name, handler in INTERACTION_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 9.5 Register user portrait tool handlers
    for name, handler in PORTRAIT_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 9.6 Register cron job tool handlers
    for name, handler in CRON_JOB_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)

    # 9.7 Register web tool handlers (web_search / web_fetch)
    from aio_agent_platform.tools import web as web_tools

    web_tools.register_handlers(tool_executor)

    # 9.8 Register pet action tool handler (宠物闲聊主动触发动作)
    from aio_agent_platform.pets.smart import ensure_pet_tools_registered

    ensure_pet_tools_registered(tool_executor)

    # 9.9 Register channel file-send tool handler (飞书渠道向用户发送文件)
    from aio_agent_platform.channels.file_send import (
        SEND_FILE_TOOL_NAME,
        handle_send_file,
    )

    tool_executor.register_direct_handler(SEND_FILE_TOOL_NAME, handle_send_file)

    # 9.10 Register cron channel-notify tool handler (定时任务主动通知渠道)
    from aio_agent_platform.channels.cron_notify import (
        NOTIFY_CHANNEL_TOOL_NAME,
        handle_notify_channel,
    )

    tool_executor.register_direct_handler(NOTIFY_CHANNEL_TOOL_NAME, handle_notify_channel)

    # 9.11 Register artifact tool handlers (create_webpage 网页产物)
    from aio_agent_platform.artifacts.webpage import ARTIFACT_HANDLERS

    for name, handler in ARTIFACT_HANDLERS.items():
        tool_executor.register_direct_handler(name, handler)
    _mark("tool_registry+handlers")

    # 10. MCP Manager — connect to configured MCP Servers
    from aio_agent_platform.tools.mcp.manager import MCPManager

    mcp_manager = MCPManager()
    try:
        from sqlalchemy import select

        from aio_agent_platform.db.connection import get_session_factory
        from aio_agent_platform.db.models import MCPServer

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(MCPServer).where(MCPServer.is_active)
            )
            mcp_servers = result.scalars().all()

        for server in mcp_servers:
            config = {
                "name": server.name,
                "transport_type": server.transport_type,
                "url": server.url,
                "headers": server.headers or {},
                "tool_prefix": server.tool_prefix,
                "timeout": server.timeout,
            }
            try:
                # 单个 MCP 服务器连接限时 15s：不可达时跳过该服务器而非卡死启动
                await asyncio.wait_for(
                    mcp_manager.add_server(server.id, config), timeout=15
                )
            except TimeoutError:
                import structlog
                structlog.get_logger().warning(
                    "mcp_server_startup_timeout",
                    server_id=str(server.id),
                    name=server.name,
                )
            except Exception as e:
                import structlog
                structlog.get_logger().warning(
                    "mcp_server_startup_failed",
                    server_id=str(server.id),
                    name=server.name,
                    error=str(e),
                )
    except Exception as e:
        import structlog
        structlog.get_logger().warning(
            "mcp_manager_init_failed",
            error=str(e),
        )

    # Inject MCPManager into ToolExecutor
    tool_executor.mcp_manager = mcp_manager
    _mark("mcp_connect")

    # 11. Remote Tool Manager — load remote HTTP tools from DB
    from aio_agent_platform.tools.remote.executor import RemoteToolExecutor
    from aio_agent_platform.tools.remote.manager import RemoteToolManager

    remote_manager = RemoteToolManager(registry)
    try:
        factory = get_session_factory()
        await remote_manager.initialize(factory)
    except Exception as e:
        import structlog
        structlog.get_logger().warning(
            "remote_manager_init_failed",
            error=str(e),
        )

    remote_executor = RemoteToolExecutor(remote_manager)

    # Inject into ToolExecutor
    tool_executor.remote_manager = remote_manager
    tool_executor.remote_executor = remote_executor

    # Store on app.state for access in routes
    app.state.sandbox_mgr = sandbox_mgr
    app.state.tool_executor = tool_executor
    app.state.mcp_manager = mcp_manager
    app.state.remote_manager = remote_manager

    # 11.5 Langfuse observability
    init_langfuse()
    _mark("remote_tools+langfuse")

    # 11.6 Observation recorder — async batch writer for observability tables
    from aio_agent_platform.observation.recorder import get_recorder

    recorder = get_recorder()
    recorder.start()
    app.state.recorder = recorder

    # 11.7 Hook manager — 事件驱动的自动化动作（webhook / 沙箱命令）
    from aio_agent_platform.hooks import get_hook_manager

    hook_manager = get_hook_manager()
    hook_manager.start(sandbox_mgr)
    app.state.hook_manager = hook_manager

    # 12. Cron Job Scheduler — load and schedule all active jobs
    from aio_agent_platform.cron_jobs.scheduler import Scheduler

    async def _push_cron_result_to_channel(db, job, text: str) -> bool:
        """Push the cron job result to the job owner's bound IM account.

        Returns True only if a message was actually sent.
        """
        import structlog
        from sqlalchemy import select

        from aio_agent_platform.db.models import ChannelBinding, ChannelConfig

        log = structlog.get_logger()
        conn_manager = getattr(app.state, "channel_connection_manager", None)
        if conn_manager is None:
            log.warning("cron_job_channel_manager_unavailable", job_id=str(job.id))
            return False
        adapter = conn_manager.get_adapter(job.channel_id)
        client = getattr(adapter, "client", None)
        if client is None:
            log.warning(
                "cron_job_channel_not_connected",
                job_id=str(job.id),
                channel_id=str(job.channel_id),
            )
            return False

        ch_result = await db.execute(
            select(ChannelConfig).where(ChannelConfig.id == job.channel_id)
        )
        channel = ch_result.scalar_one_or_none()
        if channel is None:
            return False
        binding_result = await db.execute(
            select(ChannelBinding).where(
                ChannelBinding.tenant_id == channel.tenant_id,
                ChannelBinding.user_id == job.user_id,
                ChannelBinding.bind_type == "bound",
            )
        )
        binding = binding_result.scalars().first()
        if binding is None:
            log.warning(
                "cron_job_channel_no_binding",
                job_id=str(job.id),
                user_id=str(job.user_id),
                channel_id=str(job.channel_id),
            )
            return False

        message_id = await client.send_card_markdown(
            receive_id=binding.external_id,
            markdown=f"**⏰ {job.name}**\n\n{text}",
            receive_id_type="open_id",
        )
        if message_id is None:
            message_id = await client.send_text(
                receive_id=binding.external_id,
                text=f"【{job.name}】\n{text}",
                receive_id_type="open_id",
            )
        if message_id:
            log.info(
                "cron_job_channel_pushed",
                job_id=str(job.id),
                channel_id=str(job.channel_id),
            )
        else:
            log.warning(
                "cron_job_channel_push_failed",
                job_id=str(job.id),
                channel_id=str(job.channel_id),
            )
        return bool(message_id)

    async def _cron_job_executor(job, db, run_id):
        """Execute a cron job by running its agent with the configured message."""
        from datetime import UTC, datetime

        from sqlalchemy import select

        from aio_agent_platform.core.chat import (
            build_agent_loop,
            build_system_prompt_with_memories,
            filter_tools_by_agent,
            load_agent,
        )
        from aio_agent_platform.db.models import CronJobRun, User
        from aio_agent_platform.db.models import Session as ChatSession

        started_at = datetime.now(UTC)

        async def _finalize(
            status: str,
            *,
            output: str | None = None,
            error: str | None = None,
            session_id=None,
        ) -> None:
            """Write the final status/result into the run record (always called)."""
            run = await db.get(CronJobRun, run_id)
            if run:
                run.finished_at = datetime.now(UTC)
                run.duration_ms = int(
                    (run.finished_at - started_at).total_seconds() * 1000
                )
                if session_id:
                    run.session_id = session_id
                run.status = status
                if output:
                    run.output = output
                if error:
                    run.error = error
            await db.commit()

        if not job.agent_id or not job.message:
            await _finalize("failed", error="任务未配置 agent 或 message，无法执行")
            return

        user_result = await db.execute(select(User).where(User.id == job.user_id))
        job_user = user_result.scalar_one_or_none()
        if not job_user:
            import structlog
            structlog.get_logger().warning(
                "cron_job_user_not_found",
                job_id=str(job.id),
                user_id=str(job.user_id),
            )
            await _finalize("failed", error="任务所属用户不存在")
            return

        agent = await load_agent(db, job.agent_id, user=job_user)
        if not agent:
            import structlog
            structlog.get_logger().warning(
                "cron_job_agent_not_found",
                job_id=str(job.id),
                agent_id=str(job.agent_id),
            )
            await _finalize("failed", error="任务关联的智能体不存在")
            return

        # Cron jobs have no delegation context; keep delegate_task out so a
        # tool that cannot run is never offered.
        tools_list, tools_schema = filter_tools_by_agent(
            tool_executor, agent, extra_blacklist={"delegate_task"}
        )

        # 配置了渠道时，注入 notify_channel 工具让 agent 决定是否主动通知（默认静默）
        if job.channel_id:
            from aio_agent_platform.channels.cron_notify import (
                NOTIFY_CHANNEL_TOOL_NAME,
                NOTIFY_CHANNEL_TOOL_SCHEMA,
            )
            tools_list = [*tools_list, NOTIFY_CHANNEL_TOOL_NAME]
            tools_schema = [*tools_schema, NOTIFY_CHANNEL_TOOL_SCHEMA]

        system_prompt = await build_system_prompt_with_memories(
            db, job.user_id, job.message, tools_list, agent=agent,
        )
        if job.channel_id:
            system_prompt += (
                "\n\n[通知规则] 你可以调用 notify_channel 工具主动把消息推送到用户的 IM 渠道。"
                "默认保持静默：只有当发现需要用户关注的问题、或任务要求必须报告结果时才调用；"
                "一切正常、无需打扰用户时不要调用。"
            )
        loop = await build_agent_loop(
            tool_executor, system_prompt, db,
            agent_model_id=agent.model_id,
            agent_temperature=agent.temperature,
            agent_max_iterations=agent.max_iterations,
            agent_enable_retry=agent.enable_retry if agent.enable_retry is not None else True,
            tenant_id=agent.tenant_id,
        )

        # Create a session for this execution
        new_session = ChatSession(
            user_id=job.user_id,
            agent_id=job.agent_id,
            title=job.name,
        )
        db.add(new_session)
        await db.flush()

        # Prepare context and run
        from aio_agent_platform.core.context import prepare_context

        prepared_messages, _ = await prepare_context(
            system_prompt=system_prompt,
            history=[],
            user_input=job.message,
            provider=loop.provider,
        )
        conversation_history = [
            m for m in prepared_messages
            if m.role != "system"
        ]

        import structlog
        log = structlog.get_logger()

        error_msg = ""
        final_output = ""
        notify_token = None
        if job.channel_id:
            from aio_agent_platform.channels.cron_notify import (
                CronNotifyContext,
                current_cron_notify_ctx,
            )
            notify_ctx = CronNotifyContext(
                push_fn=lambda text: _push_cron_result_to_channel(db, job, text),
                job_id=str(job.id),
                channel_id=str(job.channel_id),
            )
            notify_token = current_cron_notify_ctx.set(notify_ctx)

        try:
            async for event in loop.run(
                user_input=job.message,
                user_id=job.user_id,
                session_id=new_session.id,
                conversation_history=conversation_history,
                tools=tools_schema,
            ):
                if isinstance(event, str):
                    continue
                elif getattr(event, 'done', False):
                    final_output = getattr(event, 'final_output', '') or ''
        except Exception as exc:
            error_msg = str(exc) or "agent loop failed"
            log.exception(
                "cron_job_agent_loop_failed",
                job_id=str(job.id),
            )
        finally:
            if notify_token is not None:
                current_cron_notify_ctx.reset(notify_token)

        # Save assistant message + finalize run log record
        if final_output:
            from aio_agent_platform.db.models import Message
            msg = Message(
                session_id=new_session.id,
                user_id=job.user_id,
                role="assistant",
                content=final_output,
            )
            db.add(msg)

        await _finalize(
            "success" if final_output else "failed",
            output=final_output or None,
            error=None if final_output else (error_msg or "agent loop failed"),
            session_id=new_session.id,
        )

        if final_output:
            log.info(
                "cron_job_executed",
                job_id=str(job.id),
                session_id=str(new_session.id),
            )

    scheduler = Scheduler(factory, executor=_cron_job_executor)
    try:
        await scheduler.start()
        app.state.scheduler = scheduler
        from aio_agent_platform.cron_jobs.scheduler import set_global_scheduler
        set_global_scheduler(scheduler)

        # Built-in: consolidate yesterday's sessions into daily memories at 00:40
        from aio_agent_platform.memory.daily import run_daily_consolidation

        async def _daily_memory_job() -> None:
            await run_daily_consolidation()

        scheduler.add_system_job(
            "daily-memory-consolidation", "40 0 * * *", _daily_memory_job
        )
    except Exception as e:
        import structlog
        structlog.get_logger().warning(
            "scheduler_start_failed",
            error=str(e),
        )
    _mark("scheduler")

    # 13. Channel connection manager — starts all enabled channel transports.
    from aio_agent_platform.channels.connection_manager import (
        ChannelConnectionManager,
        set_global_channel_manager,
    )
    from aio_agent_platform.channels.feishu.webhook_transport import build_webhook_router

    conn_manager = ChannelConnectionManager(tool_executor)
    set_global_channel_manager(conn_manager)
    try:
        async with factory() as startup_db:
            await conn_manager.start_all(startup_db)
        app.state.channel_connection_manager = conn_manager
    except Exception as e:
        import structlog
        structlog.get_logger().warning(
            "channel_manager_start_failed",
            error=str(e),
        )

    # Mount the shared webhook router for all Feishu webhook channels
    webhook_router = build_webhook_router()
    app.include_router(webhook_router)
    _mark("channels")

    yield

    # ---- Shutdown ----
    conn_manager = getattr(app.state, "channel_connection_manager", None)
    if conn_manager:
        await conn_manager.stop_all()
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        await scheduler.shutdown()
    await mcp_manager.shutdown()
    await sandbox_mgr.shutdown()
    recorder = getattr(app.state, "recorder", None)
    if recorder:
        await recorder.shutdown()
    hook_manager = getattr(app.state, "hook_manager", None)
    if hook_manager:
        await hook_manager.shutdown()
    await shutdown_langfuse()
    await close_db()


def create_app() -> FastAPI:
    """Create and configure FastAPI app."""
    app = FastAPI(
        title="AIO Agent Platform API",
        description="Multi-tenant AI Agent Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    origins = [o.strip() for o in settings.server.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(auth_router)
    app.include_router(public_router)
    app.include_router(sessions_router)
    app.include_router(analytics_router)
    app.include_router(chat_router)
    app.include_router(commands_router)
    app.include_router(models_router)
    app.include_router(observability_router)
    app.include_router(settings_router)
    app.include_router(daily_memories_router)  # before memories_router: /daily vs /{memory_id}
    app.include_router(memories_router)
    app.include_router(pets_router)
    app.include_router(admin_pets_router)
    app.include_router(skills_router)
    app.include_router(admin_models_router)
    app.include_router(agents_router)
    app.include_router(delegations_router)
    app.include_router(tools_router)
    app.include_router(confirmations_router)
    app.include_router(workspaces_router)
    app.include_router(mcp_servers_router)
    app.include_router(knowledge_router)
    app.include_router(graph_knowledge_router)
    app.include_router(agent_api_router)
    app.include_router(remote_tools_router)
    app.include_router(cron_jobs_router)
    app.include_router(tenants_router)
    app.include_router(users_router)
    app.include_router(channels_router)
    app.include_router(channel_bindings_router)
    app.include_router(web_tools_router)
    app.include_router(webpages_router)
    app.include_router(system_config_router)

    # Health check
    @app.get("/health")
    async def health():
        sandbox_mgr = getattr(app.state, "sandbox_mgr", None)
        return {
            "status": "ok",
            "sandbox_active": len(sandbox_mgr._active) if sandbox_mgr else 0,
        }

    return app


app = create_app()


def _run_server() -> None:
    """Serve the app with uvicorn (each reload child binds the port itself)."""
    import uvicorn

    uvicorn.run(
        "aio_agent_platform.interface.api:app",
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level.lower(),
    )


def run():
    """Run with uvicorn, optionally with file-watch reload.

    不用 uvicorn 自带的 reload:它在 macOS + Python 3.13(spawn)下靠
    multiprocessing pickling 传递监听 socket,重启后 socket 丢失,服务假死
    (TCP 能连上但无人 accept)。改用 watchfiles.run_process 整体杀掉并重启
    子进程,子进程每次从零 bind 端口,不涉及 socket 继承。
    """
    if settings.server.reload:
        from watchfiles import run_process

        package_root = Path(__file__).resolve().parents[1]
        run_process(
            str(package_root),
            target=_run_server,
            debounce=int(settings.server.reload_delay * 1000),
        )
        return

    _run_server()


if __name__ == "__main__":
    run()
