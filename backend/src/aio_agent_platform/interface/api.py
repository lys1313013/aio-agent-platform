"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aio_agent_platform.auth import auth_router
from aio_agent_platform.core.config import settings
from aio_agent_platform.db.connection import close_db, init_db
from aio_agent_platform.delegation import DELEGATION_HANDLERS
from aio_agent_platform.interaction import INTERACTION_HANDLERS
from aio_agent_platform.interface.routes import (
    admin_models_router,
    agent_api_router,
    agents_router,
    chat_router,
    confirmations_router,
    cron_jobs_router,
    delegations_router,
    knowledge_router,
    mcp_servers_router,
    memories_router,
    public_router,
    remote_tools_router,
    sessions_router,
    settings_router,
    skills_router,
    tools_router,
)
from aio_agent_platform.memory.handlers import MEMORY_HANDLERS
from aio_agent_platform.knowledge.handlers import KNOWLEDGE_HANDLERS
from aio_agent_platform.cron_jobs.handlers import CRON_JOB_HANDLERS
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

    # 1. Database bootstrap (create tables if not exist — for dev convenience)
    try:
        await init_db()
    except Exception as e:
        import structlog

        structlog.get_logger().warning("init_db failed (may already exist)", error=str(e))

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
                await mcp_manager.add_server(server.id, config)
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

    # 11. Remote Tool Manager — load remote HTTP tools from DB
    from aio_agent_platform.tools.remote.manager import RemoteToolManager
    from aio_agent_platform.tools.remote.executor import RemoteToolExecutor

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

    # 12. Cron Job Scheduler — load and schedule all active jobs
    from aio_agent_platform.cron_jobs.scheduler import Scheduler

    async def _cron_job_executor(job, db):
        """Execute a cron job by running its agent with the configured message."""
        from aio_agent_platform.db.connection import get_session_factory
        from aio_agent_platform.db.models import Session as ChatSession
        from aio_agent_platform.interface.routes.chat import (
            _build_agent_loop,
            _build_system_prompt_with_memories,
            _filter_tools_by_agent,
            _load_agent,
        )

        if not job.agent_id or not job.message:
            return

        agent = await _load_agent(db, job.agent_id)
        if not agent:
            import structlog
            structlog.get_logger().warning(
                "cron_job_agent_not_found",
                job_id=str(job.id),
                agent_id=str(job.agent_id),
            )
            return

        tools_list, tools_schema = _filter_tools_by_agent(tool_executor, agent)
        system_prompt = await _build_system_prompt_with_memories(
            db, job.user_id, job.message, tools_list, agent=agent,
        )
        loop = await _build_agent_loop(
            tool_executor, system_prompt, db,
            agent_model_id=agent.model_id,
            agent_temperature=agent.temperature,
            agent_max_iterations=agent.max_iterations,
            agent_enable_retry=agent.enable_retry if agent.enable_retry is not None else True,
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
        from aio_agent_platform.llm import LLMMessage

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

        final_output = ""
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
        except Exception:
            log.exception(
                "cron_job_agent_loop_failed",
                job_id=str(job.id),
            )

        # Save assistant message
        if final_output:
            from aio_agent_platform.db.models import Message
            msg = Message(
                session_id=new_session.id,
                user_id=job.user_id,
                role="assistant",
                content=final_output,
            )
            db.add(msg)
            await db.commit()
            log.info(
                "cron_job_executed",
                job_id=str(job.id),
                session_id=str(new_session.id),
            )

    scheduler = Scheduler(factory, executor=_cron_job_executor)
    try:
        await scheduler.start()
        app.state.scheduler = scheduler
    except Exception as e:
        import structlog
        structlog.get_logger().warning(
            "scheduler_start_failed",
            error=str(e),
        )

    yield

    # ---- Shutdown ----
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        await scheduler.shutdown()
    await mcp_manager.shutdown()
    await sandbox_mgr.shutdown()
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
    app.include_router(chat_router)
    app.include_router(settings_router)
    app.include_router(memories_router)
    app.include_router(skills_router)
    app.include_router(admin_models_router)
    app.include_router(agents_router)
    app.include_router(delegations_router)
    app.include_router(tools_router)
    app.include_router(confirmations_router)
    app.include_router(workspaces_router)
    app.include_router(mcp_servers_router)
    app.include_router(knowledge_router)
    app.include_router(agent_api_router)
    app.include_router(remote_tools_router)
    app.include_router(cron_jobs_router)

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


def run():
    """Run with uvicorn."""
    import uvicorn

    uvicorn.run(
        "aio_agent_platform.interface.api:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=False,
        log_level=settings.server.log_level.lower(),
    )


if __name__ == "__main__":
    run()
