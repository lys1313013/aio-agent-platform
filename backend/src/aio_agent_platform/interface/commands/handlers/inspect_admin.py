"""Tool / MCP / skills / system status commands."""

from __future__ import annotations

from sqlalchemy import func, select

from aio_agent_platform.channels.connection_manager import get_global_channel_manager
from aio_agent_platform.core.chat import filter_tools_by_agent
from aio_agent_platform.cron_jobs.scheduler import get_global_scheduler
from aio_agent_platform.cron_jobs.service import CronJobService
from aio_agent_platform.db.models import Agent, LLMModel, MCPServer, Skill
from aio_agent_platform.interface.routes.mcp_servers import _build_config
from aio_agent_platform.skills.sh_client import clear_repo_cache

from ..models import CommandContext, CommandResult
from ..registry import command


@command("tools", group="工具", desc="列出当前 Agent 可用工具（内置 / MCP / 远程）")
async def cmd_tools(ctx: CommandContext) -> CommandResult:
    if ctx.tool_executor is None:
        return CommandResult(content="工具执行器不可用。")

    agent = None
    if ctx.session is not None and ctx.session.agent_id:
        agent = await ctx.db.get(Agent, ctx.session.agent_id)

    if agent is not None:
        tools_list, _tools_schema = filter_tools_by_agent(ctx.tool_executor, agent)
    else:
        tools_list = ctx.tool_executor.registry.list_tools()

    if not tools_list:
        return CommandResult(content="暂无可用工具。")

    lines = [f"**可用工具（{len(tools_list)}）：**", ""]
    for t in tools_list:
        kind = ""
        if ctx.tool_executor.mcp_manager is not None and ctx.tool_executor.mcp_manager.is_mcp_tool(t.name):
            kind = " [MCP]"
        elif getattr(t, "direct", False):
            kind = " [direct]"
        level = getattr(t, "permission_level", None) or "read"
        lines.append(f"- `{t.name}` — {t.description or '无描述'}（{level}）{kind}")
    if agent is not None:
        lines.append("")
        lines.append(f"范围：智能体「{agent.name}」生效工具。")
    else:
        lines.append("")
        lines.append("当前会话未绑定智能体，展示全部注册工具。")
    return CommandResult(content="\n".join(lines))


@command("mcp", group="工具", desc="查看 MCP 服务器连接状态", permission="admin")
async def cmd_mcp(ctx: CommandContext) -> CommandResult:
    mcp = ctx.tool_executor.mcp_manager if ctx.tool_executor else None
    if mcp is None:
        return CommandResult(content="MCP 管理器未初始化。")
    status = mcp.get_status()
    if not status:
        return CommandResult(content="未配置 MCP 服务器。")
    lines = ["**MCP 服务器状态：**", ""]
    for server_id, info in status.items():
        conn = "✅ 已连接" if info.get("connected") else "❌ 未连接"
        lines.append(
            f"- `{info.get('name', server_id)}` — {conn} · "
            f"传输 {info.get('transport', '?')} · 工具 {info.get('tools_count', 0)}"
        )
    return CommandResult(content="\n".join(lines))


@command("reload-mcp", group="工具", desc="热重载所有已配置的 MCP 服务器", permission="admin")
async def cmd_reload_mcp(ctx: CommandContext) -> CommandResult:
    if ctx.tool_executor is None or ctx.tool_executor.mcp_manager is None:
        return CommandResult(content="MCP 管理器未初始化。")
    servers = (
        await ctx.db.execute(select(MCPServer).where(MCPServer.is_active))
    ).scalars().all()
    if not servers:
        return CommandResult(content="没有已启用的 MCP 服务器。")
    lines = ["**MCP 重载结果：**", ""]
    ok = 0
    for server in servers:
        try:
            config = _build_config(server)
            await ctx.tool_executor.mcp_manager.add_server(server.id, config)
            ok += 1
            lines.append(f"- ✅ `{server.name}` 已重连")
        except Exception as exc:
            lines.append(f"- ❌ `{server.name}` 失败：{exc}")
    lines.append("")
    lines.append(f"共 {len(servers)} 个服务器，成功 {ok} 个。")
    return CommandResult(content="\n".join(lines))


@command("reload-skills", group="工具", desc="清除技能仓库元数据缓存", permission="admin")
async def cmd_reload_skills(ctx: CommandContext) -> CommandResult:
    clear_repo_cache()
    count = await ctx.db.scalar(select(func.count(Skill.id)).where(Skill.is_active))
    return CommandResult(content=f"✅ 已清除技能仓库缓存。当前启用技能 {count or 0} 个。")


@command("status", group="系统", desc="系统状态：在线模型、MCP、渠道、队列概况", permission="admin")
async def cmd_status(ctx: CommandContext) -> CommandResult:
    lines = ["**系统状态：**", ""]

    models = (
        await ctx.db.execute(
            select(LLMModel).where(LLMModel.is_active, LLMModel.tenant_id == ctx.user.tenant_id).order_by(LLMModel.is_default.desc())
        )
    ).scalars().all()
    lines.append(f"- **在线模型**：{len(models)} 个")
    for m in models[:5]:
        default = "（默认）" if m.is_default else ""
        lines.append(f"  - `{m.name}`{default}")
    if len(models) > 5:
        lines.append(f"  - …等共 {len(models)} 个")

    mcp = ctx.tool_executor.mcp_manager if ctx.tool_executor else None
    mcp_status = mcp.get_status() if mcp else {}
    connected = sum(1 for i in mcp_status.values() if i.get("connected"))
    lines.append(f"- **MCP**：{connected}/{len(mcp_status)} 已连接")

    scheduler = get_global_scheduler()
    if scheduler is not None:
        try:
            job_count = len(scheduler._scheduler.get_jobs())
        except Exception:
            job_count = 0
    else:
        job_count = 0
    lines.append(f"- **调度器**：{job_count} 个定时任务")

    active_jobs = await CronJobService.get_active_jobs(ctx.db)
    lines.append(f"  - 活动中 cron：{len(active_jobs)}")

    sandbox = ctx.tool_executor.sandbox_mgr if ctx.tool_executor else None
    sandbox_active = len(getattr(sandbox, "_active", {}) or {})
    lines.append(f"- **沙箱**：{sandbox_active} 个活跃")

    channel_mgr = get_global_channel_manager()
    channels = channel_mgr.get_status() if channel_mgr else []
    lines.append(f"- **渠道连接**：{len(channels)} 个")
    for ch in channels:
        lines.append(
            f"  - `{ch.get('name') or ch.get('channel_id')}` — {ch.get('channel_type')} · {ch.get('transport_state')}"
        )
    return CommandResult(content="\n".join(lines))
