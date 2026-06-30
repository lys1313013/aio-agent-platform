"""MCP (Model Context Protocol) integration for connecting to external tool servers."""

from aio_agent_platform.tools.mcp.adapter import MCPServerConnection, MCPToolInfo
from aio_agent_platform.tools.mcp.manager import MCPManager

__all__ = ["MCPManager", "MCPServerConnection", "MCPToolInfo"]
