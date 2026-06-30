"""Remote tools management routes — admin only."""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import RemoteTool, Agent
from aio_agent_platform.tools.remote.manager import RemoteToolConfig

logger = structlog.get_logger()

router = APIRouter(prefix="/api/admin/remote-tools", tags=["admin-remote-tools"])

VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
VALID_AUTH_TYPES = {"none", "bearer", "api_key", "basic", "custom_header"}
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Names reserved by built-in tools
BUILTIN_TOOL_NAMES = {
    "run_shell", "run_code", "read_file", "write_file", "edit_file", "list_directory",
    "memory_read", "memory_write",
    "search_skills", "view_skill", "create_skill",
    "delegate_task", "AskUserQuestion",
    "knowledge_retrieval",
}


# ---- Schemas ----


class RemoteToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., min_length=1, max_length=1024)
    method: str = Field(..., pattern=r"^(GET|POST|PUT|DELETE|PATCH)$")
    url_template: str = Field(..., min_length=1, max_length=1024)
    parameters_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    headers: dict | None = None
    auth_type: str = Field(default="none", pattern=r"^(none|bearer|api_key|basic|custom_header)$")
    auth_config: dict | None = None
    query_params: dict | None = None
    body_template: dict | None = None
    response_extract: str | None = Field(default=None, max_length=256)
    timeout: int = Field(default=30, ge=5, le=600)
    is_active: bool = True


class RemoteToolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=1024)
    method: str | None = Field(default=None, pattern=r"^(GET|POST|PUT|DELETE|PATCH)$")
    url_template: str | None = Field(default=None, min_length=1, max_length=1024)
    parameters_schema: dict | None = None
    headers: dict | None = None
    auth_type: str | None = Field(default=None, pattern=r"^(none|bearer|api_key|basic|custom_header)$")
    auth_config: dict | None = None
    query_params: dict | None = None
    body_template: dict | None = None
    response_extract: str | None = Field(default=None, max_length=256)
    timeout: int | None = Field(default=None, ge=5, le=600)
    is_active: bool | None = None


class RemoteToolOut(BaseModel):
    id: UUID
    name: str
    label: str
    description: str
    method: str
    url_template: str
    parameters_schema: dict
    headers: dict | None = None
    auth_type: str
    auth_config_masked: dict | None = None
    query_params: dict | None = None
    body_template: dict | None = None
    response_extract: str | None = None
    timeout: int
    is_active: bool
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class RemoteToolTestRequest(BaseModel):
    arguments: dict = Field(default_factory=dict)


class RemoteToolTestResponse(BaseModel):
    success: bool
    status_code: int | None = None
    response_body: str | None = None
    duration_ms: float = 0
    error: str | None = None


# ---- Helpers ----


def _mask_auth_config(auth_config: dict | None) -> dict | None:
    """Mask sensitive auth values for display."""
    if not auth_config:
        return None
    masked = {}
    for k, v in auth_config.items():
        if isinstance(v, str):
            masked[k] = "****"
        elif isinstance(v, dict):
            masked[k] = {sk: "****" for sk in v}
        else:
            masked[k] = v
    return masked


def _tool_to_dict(tool: RemoteTool) -> dict:
    """Convert RemoteTool model to response dict."""
    return {
        "id": tool.id,
        "name": tool.name,
        "label": tool.label,
        "description": tool.description,
        "method": tool.method,
        "url_template": tool.url_template,
        "parameters_schema": tool.parameters_schema or {},
        "headers": tool.headers,
        "auth_type": tool.auth_type,
        "auth_config_masked": _mask_auth_config(tool.auth_config),
        "query_params": tool.query_params,
        "body_template": tool.body_template,
        "response_extract": tool.response_extract,
        "timeout": tool.timeout,
        "is_active": tool.is_active,
        "created_at": tool.created_at.isoformat() if tool.created_at else "",
        "updated_at": tool.updated_at.isoformat() if tool.updated_at else "",
    }


async def _check_name_conflict(
    db: AsyncSession, name: str, request: Request, exclude_id: UUID | None = None
) -> None:
    """Check if tool name conflicts with built-in, MCP, or other remote tools."""
    # Check built-in names
    if name in BUILTIN_TOOL_NAMES:
        raise HTTPException(status_code=400, detail=f"工具名称 '{name}' 与内置工具冲突")

    # Check MCP tool names
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager and mcp_manager.is_mcp_tool(name):
        raise HTTPException(status_code=400, detail=f"工具名称 '{name}' 与 MCP 工具冲突")

    # Check other remote tools
    query = select(RemoteTool).where(RemoteTool.name == name)
    if exclude_id:
        query = query.where(RemoteTool.id != exclude_id)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail=f"工具名称 '{name}' 已存在")


# ---- CRUD Endpoints ----


@router.get("", response_model=list[RemoteToolOut])
async def list_remote_tools(
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List all remote tools."""
    result = await db.execute(select(RemoteTool).order_by(RemoteTool.created_at))
    tools = result.scalars().all()
    return [_tool_to_dict(t) for t in tools]


@router.post("", response_model=RemoteToolOut, status_code=201)
async def create_remote_tool(
    req: RemoteToolCreate,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new remote tool."""
    await _check_name_conflict(db, req.name, request)

    tool = RemoteTool(
        name=req.name,
        label=req.label,
        description=req.description,
        method=req.method,
        url_template=req.url_template,
        parameters_schema=req.parameters_schema,
        headers=req.headers,
        auth_type=req.auth_type,
        auth_config=req.auth_config,
        query_params=req.query_params,
        body_template=req.body_template,
        response_extract=req.response_extract,
        timeout=req.timeout,
        is_active=req.is_active,
    )
    db.add(tool)
    await db.flush()
    await db.refresh(tool)

    # Register in RemoteToolManager
    remote_manager = getattr(request.app.state, "remote_manager", None)
    if remote_manager:
        config = RemoteToolConfig.from_db(tool)
        await remote_manager.add_tool(config)

    return _tool_to_dict(tool)


@router.get("/{tool_id}", response_model=RemoteToolOut)
async def get_remote_tool(
    tool_id: UUID,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a specific remote tool."""
    result = await db.execute(select(RemoteTool).where(RemoteTool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="远程工具不存在")
    return _tool_to_dict(tool)


@router.put("/{tool_id}", response_model=RemoteToolOut)
async def update_remote_tool(
    tool_id: UUID,
    req: RemoteToolUpdate,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update a remote tool configuration."""
    result = await db.execute(select(RemoteTool).where(RemoteTool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="远程工具不存在")

    old_name = tool.name

    # Check name conflict if name changed
    if req.name is not None and req.name != old_name:
        await _check_name_conflict(db, req.name, request, exclude_id=tool_id)

    # Apply updates
    if req.name is not None:
        tool.name = req.name
    if req.label is not None:
        tool.label = req.label
    if req.description is not None:
        tool.description = req.description
    if req.method is not None:
        tool.method = req.method
    if req.url_template is not None:
        tool.url_template = req.url_template
    if req.parameters_schema is not None:
        tool.parameters_schema = req.parameters_schema
    if req.headers is not None:
        tool.headers = req.headers
    if req.auth_type is not None:
        tool.auth_type = req.auth_type
    if req.auth_config is not None:
        tool.auth_config = req.auth_config
    if req.query_params is not None:
        tool.query_params = req.query_params
    if req.body_template is not None:
        tool.body_template = req.body_template
    if req.response_extract is not None:
        tool.response_extract = req.response_extract
    if req.timeout is not None:
        tool.timeout = req.timeout
    if req.is_active is not None:
        tool.is_active = req.is_active

    await db.flush()

    # Cascade: if name changed, update agent references
    if tool.name != old_name:
        agent_result = await db.execute(select(Agent))
        agents = agent_result.scalars().all()
        for agent in agents:
            if agent.enabled_tools and old_name in agent.enabled_tools:
                agent.enabled_tools = [
                    tool.name if t == old_name else t
                    for t in agent.enabled_tools
                ]

    # Refresh tool to reload server-side defaults (updated_at, etc.)
    # Avoids MissingGreenlet when _tool_to_dict accesses lazy attributes
    await db.refresh(tool)

    # Reload in RemoteToolManager
    remote_manager = getattr(request.app.state, "remote_manager", None)
    if remote_manager:
        # Remove old registration if name changed
        if tool.name != old_name:
            await remote_manager.remove_tool(old_name)
        config = RemoteToolConfig.from_db(tool)
        await remote_manager.update_tool(config)

    return _tool_to_dict(tool)


@router.delete("/{tool_id}")
async def delete_remote_tool(
    tool_id: UUID,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete a remote tool."""
    result = await db.execute(select(RemoteTool).where(RemoteTool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="远程工具不存在")

    tool_name = tool.name

    # Unregister from RemoteToolManager first
    remote_manager = getattr(request.app.state, "remote_manager", None)
    if remote_manager:
        await remote_manager.remove_tool(tool_name)

    await db.delete(tool)
    await db.flush()
    return {"message": "远程工具已删除"}


@router.patch("/{tool_id}/toggle", response_model=RemoteToolOut)
async def toggle_remote_tool(
    tool_id: UUID,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Toggle a remote tool's active status."""
    result = await db.execute(select(RemoteTool).where(RemoteTool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="远程工具不存在")

    tool.is_active = not tool.is_active
    await db.flush()
    await db.refresh(tool)

    # Update in RemoteToolManager
    remote_manager = getattr(request.app.state, "remote_manager", None)
    if remote_manager:
        config = RemoteToolConfig.from_db(tool)
        await remote_manager.update_tool(config)

    return _tool_to_dict(tool)


@router.post("/{tool_id}/test", response_model=RemoteToolTestResponse)
async def test_remote_tool(
    tool_id: UUID,
    req: RemoteToolTestRequest,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Test a remote tool by sending an actual HTTP request."""
    result = await db.execute(select(RemoteTool).where(RemoteTool.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="远程工具不存在")

    remote_manager = getattr(request.app.state, "remote_manager", None)
    if not remote_manager:
        raise HTTPException(status_code=503, detail="Remote Tool Manager 未初始化")

    # Temporarily register if not active
    config = RemoteToolConfig.from_db(tool)
    if not remote_manager.is_remote_tool(tool.name):
        await remote_manager.add_tool(config)

    try:
        from aio_agent_platform.tools.remote.executor import RemoteToolExecutor

        executor = RemoteToolExecutor(remote_manager)
        response_body = await executor.call(tool.name, req.arguments)
        return {
            "success": True,
            "status_code": 200,
            "response_body": response_body,
            "duration_ms": 0,
            "error": None,
        }
    except Exception as e:
        logger.warning(
            "remote_tool_test_failed",
            tool_name=tool.name,
            error=str(e),
        )
        return {
            "success": False,
            "status_code": None,
            "response_body": None,
            "duration_ms": 0,
            "error": str(e),
        }
    finally:
        # Clean up temp registration if tool is not active
        if not tool.is_active:
            await remote_manager.remove_tool(tool.name)
