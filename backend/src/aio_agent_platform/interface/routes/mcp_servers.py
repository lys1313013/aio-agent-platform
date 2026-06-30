"""MCP Server management routes — admin only."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import MCPServer

logger = structlog.get_logger()

router = APIRouter(prefix="/api/admin/mcp-servers", tags=["admin-mcp"])

# Valid transport types
VALID_TRANSPORTS = {"sse", "streamable-http"}


# ---- Schemas ----


class MCPServerOut(BaseModel):
    id: UUID
    name: str
    transport_type: str
    url: str
    headers: dict = {}
    is_active: bool
    tool_prefix: str | None = None
    timeout: int
    status: str
    last_error: str | None = None
    tools_count: int = 0
    tools: list[dict] = []

    model_config = {"from_attributes": True}


class MCPServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    transport_type: str = Field(..., pattern=r"^(sse|streamable-http)$")
    url: str = Field(..., min_length=1, max_length=512)
    headers: dict[str, str] = Field(default_factory=dict)
    tool_prefix: str | None = Field(default=None, max_length=32)
    timeout: int = Field(default=60, ge=5, le=600)
    is_active: bool = True


class MCPServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    transport_type: str | None = Field(default=None, pattern=r"^(sse|streamable-http)$")
    url: str | None = Field(default=None, min_length=1, max_length=512)
    headers: dict[str, str] | None = None
    is_active: bool | None = None
    tool_prefix: str | None = None
    timeout: int | None = Field(default=None, ge=5, le=600)


# ---- Helpers ----


def _server_to_dict(server: MCPServer, tools_count: int = 0, tools: list[dict] | None = None) -> dict:
    """Convert MCPServer model to response dict."""
    return {
        "id": server.id,
        "name": server.name,
        "transport_type": server.transport_type,
        "url": server.url,
        "headers": server.headers or {},
        "is_active": server.is_active,
        "tool_prefix": server.tool_prefix,
        "timeout": server.timeout,
        "status": server.status,
        "last_error": server.last_error,
        "tools_count": tools_count,
        "tools": tools or [],
    }


def _build_config(server: MCPServer) -> dict:
    """Build config dict from MCPServer model for MCPManager."""
    return {
        "name": server.name,
        "transport_type": server.transport_type,
        "url": server.url,
        "headers": server.headers or {},
        "tool_prefix": server.tool_prefix,
        "timeout": server.timeout,
    }


def _get_live_tools(mcp_manager, server: MCPServer) -> tuple[int, list[dict]]:
    """Get live tool info from MCPManager for a server."""
    if not mcp_manager:
        return 0, []
    conn = mcp_manager._connections.get(server.id)
    if conn and conn.connected:
        server.status = "connected"
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in conn.tools
        ]
        return len(tools), tools
    if server.is_active:
        server.status = "disconnected"
    return 0, []


async def _try_connect(mcp_manager, server: MCPServer) -> tuple[str, str | None, int, list[dict]]:
    """Try to connect to an MCP server and return (status, error, tools_count, tools)."""
    config = _build_config(server)
    tools_list = []
    try:
        tools = await mcp_manager.add_server(server.id, config)
        tools_list = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        return "connected", None, len(tools), tools_list
    except Exception as e:
        error_msg = str(e)
        logger.error(
            "mcp_server_connect_failed_api",
            server_id=str(server.id),
            name=server.name,
            error=error_msg,
        )
        return "error", error_msg, 0, []


# ---- CRUD Endpoints ----


@router.get("", response_model=list[MCPServerOut])
async def list_mcp_servers(
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List all MCP Servers with their current status."""
    result = await db.execute(select(MCPServer).order_by(MCPServer.created_at))
    servers = result.scalars().all()

    mcp_manager = getattr(request.app.state, "mcp_manager", None)

    items = []
    for server in servers:
        tools_count, tools = _get_live_tools(mcp_manager, server)
        items.append(_server_to_dict(server, tools_count=tools_count, tools=tools))

    return items


@router.post("", response_model=MCPServerOut)
async def create_mcp_server(
    req: MCPServerCreate,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new MCP Server and attempt to connect."""
    server = MCPServer(
        name=req.name,
        transport_type=req.transport_type,
        url=req.url,
        headers=req.headers,
        is_active=req.is_active,
        tool_prefix=req.tool_prefix,
        timeout=req.timeout,
        status="disconnected",
    )
    db.add(server)
    await db.flush()

    tools_count = 0
    tools_list: list[dict] = []

    # Try to connect if active
    if req.is_active:
        mcp_manager = getattr(request.app.state, "mcp_manager", None)
        if mcp_manager:
            status, error, tools_count, tools_list = await _try_connect(mcp_manager, server)
            server.status = status
            server.last_error = error
            await db.flush()

    return _server_to_dict(server, tools_count=tools_count, tools=tools_list)


@router.get("/{server_id}", response_model=MCPServerOut)
async def get_mcp_server(
    server_id: UUID,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a specific MCP Server."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")

    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    tools_count, tools = _get_live_tools(mcp_manager, server)

    return _server_to_dict(server, tools_count=tools_count, tools=tools)


@router.put("/{server_id}", response_model=MCPServerOut)
async def update_mcp_server(
    server_id: UUID,
    req: MCPServerUpdate,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update an MCP Server configuration."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")

    # Apply updates
    if req.name is not None:
        server.name = req.name
    if req.transport_type is not None:
        server.transport_type = req.transport_type
    if req.url is not None:
        server.url = req.url
    if req.headers is not None:
        server.headers = req.headers
    if req.tool_prefix is not None:
        server.tool_prefix = req.tool_prefix
    if req.timeout is not None:
        server.timeout = req.timeout
    if req.is_active is not None:
        server.is_active = req.is_active

    await db.flush()

    # Reconnect if config changed or activation state changed
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    tools_count = 0
    tools_list: list[dict] = []

    if mcp_manager:
        # Remove old connection
        await mcp_manager.remove_server(server_id)

        # Reconnect if active
        if server.is_active:
            status, error, tools_count, tools_list = await _try_connect(mcp_manager, server)
            server.status = status
            server.last_error = error
            await db.flush()
        else:
            server.status = "disconnected"
            await db.flush()

    return _server_to_dict(server, tools_count=tools_count, tools=tools_list)


@router.delete("/{server_id}")
async def delete_mcp_server(
    server_id: UUID,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete an MCP Server."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")

    # Disconnect first
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if mcp_manager:
        await mcp_manager.remove_server(server_id)

    await db.delete(server)
    await db.flush()
    return {"message": "MCP Server 已删除"}


@router.post("/{server_id}/refresh", response_model=MCPServerOut)
async def refresh_mcp_server(
    server_id: UUID,
    request: Request,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Refresh MCP Server connection and rediscover tools."""
    result = await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")

    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if not mcp_manager:
        raise HTTPException(status_code=503, detail="MCP Manager 未初始化")

    status, error, tools_count, tools_list = await _try_connect(mcp_manager, server)
    server.status = status
    server.last_error = error
    await db.flush()

    return _server_to_dict(server, tools_count=tools_count, tools=tools_list)


@router.get("/{server_id}/tools")
async def list_mcp_server_tools(
    server_id: UUID,
    request: Request,
    _admin: AdminUser,
) -> list[dict]:
    """List tools provided by a specific MCP Server."""
    mcp_manager = getattr(request.app.state, "mcp_manager", None)
    if not mcp_manager:
        raise HTTPException(status_code=503, detail="MCP Manager 未初始化")

    tools = mcp_manager.get_server_tools(server_id)
    if tools is None:
        raise HTTPException(status_code=404, detail="MCP Server 未连接")

    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]
