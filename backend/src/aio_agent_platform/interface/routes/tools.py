"""Tools routes — list registered tools."""

from __future__ import annotations

from fastapi import APIRouter, Request

from aio_agent_platform.auth.dependencies import CurrentUser

router = APIRouter(prefix="/api/tools", tags=["tools"])

# Chinese labels and metadata for built-in tools
# These are UI display properties, not part of the tool's functional definition
TOOL_META: dict[str, dict] = {
    "run_shell": {"label": "Shell 命令", "category": "sandbox"},
    "run_code": {"label": "运行代码", "category": "sandbox"},
    "read_file": {"label": "读取文件", "category": "sandbox"},
    "write_file": {"label": "写入文件", "category": "sandbox"},
    "edit_file": {"label": "编辑文件", "category": "sandbox"},
    "list_directory": {"label": "列出目录", "category": "sandbox"},
    "memory_read": {"label": "读取记忆", "category": "memory"},
    "memory_write": {"label": "写入记忆", "category": "memory"},
    "search_skills": {"label": "搜索技能", "category": "skills"},
    "view_skill": {"label": "查看技能", "category": "skills"},
    "create_skill": {"label": "创建技能", "category": "skills"},
    "deploy_skill_files": {"label": "部署技能文件", "category": "skills"},
    "report_skill_result": {"label": "上报技能结果", "category": "skills"},
    "delegate_task": {"label": "委派任务", "category": "multi_agent"},
    "AskUserQuestion": {"label": "询问用户", "category": "interaction"},
    "knowledge_retrieval": {"label": "知识库检索", "category": "knowledge"},
    "file_info": {"label": "文件信息", "category": "file"},
    "file_grep": {"label": "文件检索", "category": "file"},
    "file_query": {"label": "文件查询", "category": "file"},
    "read_pdf": {"label": "读取 PDF", "category": "file"},
    "update_user_portrait": {"label": "更新用户画像", "category": "memory"},
    "create_cron_job": {"label": "创建定时任务", "category": "automation"},
    "list_cron_jobs": {"label": "列出定时任务", "category": "automation"},
    "delete_cron_job": {"label": "删除定时任务", "category": "automation"},
}


@router.get("")
async def list_tools(
    request: Request,
    _user: CurrentUser,
) -> list[dict]:
    """List all registered tools available to agents (built-in + MCP)."""
    tool_executor = request.app.state.tool_executor
    tools = tool_executor.registry.list_tools()

    # Remote tools are also registered in the registry (for execution dispatch),
    # but we list them separately below with richer metadata, so skip them here.
    remote_manager = getattr(request.app.state, "remote_manager", None)

    result = []
    for tool in tools:
        if remote_manager and remote_manager.is_remote_tool(tool.name):
            continue
        meta = TOOL_META.get(tool.name, {})
        result.append({
            "name": tool.name,
            "description": tool.description,
            "label": meta.get("label", tool.name),
            "category": meta.get("category", "other"),
            "permission_level": tool.permission_level,
            "requires_sandbox": tool.requires_sandbox,
            "timeout": tool.timeout,
        })

    # Add MCP tools
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager:
        for full_name, tool_info in mcp_manager.list_all_tools():
            # Determine server name from prefix
            prefix = full_name[:len(full_name) - len(tool_info.name)] if len(full_name) > len(tool_info.name) else ""
            result.append({
                "name": full_name,
                "description": tool_info.description,
                "label": f"[MCP] {tool_info.name}",
                "category": "mcp",
                "permission_level": "read",
                "requires_sandbox": False,
                "timeout": 60,
                "mcp_server_prefix": prefix,
            })

    # Add remote tools (already looked up above via remote_manager)
    if remote_manager:
        for name, config in remote_manager.list_all_tools():
            result.append({
                "name": name,
                "description": config.description,
                "label": config.label,
                "category": "remote",
                "permission_level": "read",
                "requires_sandbox": False,
                "timeout": config.timeout,
                "method": config.method,
            })

    return result
