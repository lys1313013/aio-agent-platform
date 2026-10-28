"""FastAPI application entry point."""

import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aio_agent_platform.auth import auth_router
from aio_agent_platform.core.config import settings

# Importing the commands package registers all built-in slash commands.
from aio_agent_platform.interface import commands as _commands  # noqa: F401
from aio_agent_platform.interface.lifecycle import lifespan
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
    portal_router,
    public_router,
    remote_tools_router,
    sessions_router,
    settings_router,
    skills_router,
    system_config_router,
    tenants_router,
    tools_router,
    ui_actions_router,
    users_router,
    web_tools_router,
    webpages_router,
)
from aio_agent_platform.interface.routes.rooms import router as rooms_router
from aio_agent_platform.workspaces.routes import router as workspaces_router


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
    app.include_router(rooms_router)
    app.include_router(commands_router)
    app.include_router(models_router)
    app.include_router(observability_router)
    app.include_router(settings_router)
    app.include_router(daily_memories_router)  # before memories_router: /daily vs /{memory_id}
    app.include_router(memories_router)
    app.include_router(pets_router)
    app.include_router(portal_router)
    app.include_router(admin_pets_router)
    app.include_router(skills_router)
    app.include_router(admin_models_router)
    app.include_router(agents_router)
    app.include_router(delegations_router)
    app.include_router(tools_router)
    app.include_router(confirmations_router)
    app.include_router(ui_actions_router)
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

# 环境变量名:reload 父进程 pid,由 run() 在 spawn 子进程前写入,
# 子进程靠它探测父进程是否还活着(见 _start_supervisor_watchdog)。
_SUPERVISOR_PID_ENV = "AIO_SUPERVISOR_PID"


def _start_supervisor_watchdog() -> None:
    """父进程一死就退出本进程,避免孤儿进程持有端口假死。

    reload 模式下真正的 server 是 watchfiles spawn 出的子进程。父进程被杀
    (如关闭启动它的终端)后,子进程会被 launchd 收养、继续持有监听 socket,
    且 stdout 仍指向死掉的 pty,写日志阻塞后整个事件循环冻住——表现为
    "TCP 能连上但无任何响应"的假死。macOS 没有 PR_SET_PDEATHSIG,
    只能轮询探测父进程是否存活。
    """
    supervisor_pid = os.environ.get(_SUPERVISOR_PID_ENV)
    if not supervisor_pid:
        return

    def _watch() -> None:
        pid = int(supervisor_pid)
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass  # 进程还在,只是无权 signal(理论上同用户不会发生)
            time.sleep(2)

    threading.Thread(target=_watch, daemon=True).start()


def _run_server() -> None:
    """Serve the app with uvicorn (each reload child binds the port itself)."""
    import uvicorn

    _start_supervisor_watchdog()

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

        os.environ[_SUPERVISOR_PID_ENV] = str(os.getpid())
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
