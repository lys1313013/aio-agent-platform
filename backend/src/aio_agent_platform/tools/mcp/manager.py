"""MCP Manager — lifecycle management for multiple MCP servers."""

from __future__ import annotations

from uuid import UUID

import structlog

from aio_agent_platform.tools.mcp.adapter import MCPServerConnection, MCPToolInfo

logger = structlog.get_logger()


class MCPManager:
    """
    MCP Server connection manager.

    Responsibilities:
    - Manage connections to multiple MCP Servers (lazy + eager loading)
    - Provide unified tool discovery and invocation interface
    - Error isolation (one server crash doesn't affect others)
    - Connection status monitoring
    """

    def __init__(self) -> None:
        self._connections: dict[UUID, MCPServerConnection] = {}
        self._tool_to_server: dict[str, UUID] = {}  # full_tool_name -> server_id

    async def add_server(self, server_id: UUID, config: dict) -> list[MCPToolInfo]:
        """
        Add and connect an MCP Server.

        Args:
            server_id: Database ID of the MCP server
            config: Server configuration dict (transport_type, command, args, env, url, etc.)

        Returns:
            List of discovered tools

        Raises:
            Exception: If connection fails
        """
        # Remove existing connection if any
        if server_id in self._connections:
            await self.remove_server(server_id)

        conn = MCPServerConnection(server_id, config)
        await conn.connect()

        self._connections[server_id] = conn

        # Register tool mappings
        prefix = config.get("tool_prefix") or ""
        discovered_tools = []
        for tool in conn.tools:
            full_name = f"{prefix}{tool.name}" if prefix else tool.name
            self._tool_to_server[full_name] = server_id
            discovered_tools.append(tool)

        logger.info(
            "mcp_server_added",
            server_id=str(server_id),
            name=config.get("name"),
            tools_count=len(discovered_tools),
        )
        return discovered_tools

    async def remove_server(self, server_id: UUID) -> None:
        """Disconnect and remove an MCP Server."""
        conn = self._connections.pop(server_id, None)
        if conn:
            # Clean up tool mappings
            tools_to_remove = [
                name
                for name, sid in self._tool_to_server.items()
                if sid == server_id
            ]
            for name in tools_to_remove:
                del self._tool_to_server[name]

            await conn.disconnect()
            logger.info("mcp_server_removed", server_id=str(server_id))

    async def refresh_server(self, server_id: UUID, config: dict) -> list[MCPToolInfo]:
        """Refresh an MCP Server connection (reconnect and rediscover tools)."""
        await self.remove_server(server_id)
        return await self.add_server(server_id, config)

    async def refresh_tools(self, server_id: UUID) -> list[MCPToolInfo]:
        """Refresh tool list for a specific server (without reconnecting)."""
        conn = self._connections.get(server_id)
        if not conn:
            raise RuntimeError(f"MCP Server not connected: {server_id}")

        # Clear old tool mappings for this server
        old_tools = [
            name for name, sid in self._tool_to_server.items() if sid == server_id
        ]
        for name in old_tools:
            del self._tool_to_server[name]

        # Refresh tools
        tools = await conn.refresh_tools()

        # Re-register mappings
        prefix = conn.config.get("tool_prefix") or ""
        for tool in tools:
            full_name = f"{prefix}{tool.name}" if prefix else tool.name
            self._tool_to_server[full_name] = server_id

        return tools

    def get_tool_info(self, tool_name: str) -> MCPToolInfo | None:
        """Get tool info by full name (with prefix)."""
        server_id = self._tool_to_server.get(tool_name)
        if not server_id:
            return None

        conn = self._connections.get(server_id)
        if not conn:
            return None

        # Strip prefix to find the base tool name
        prefix = conn.config.get("tool_prefix") or ""
        base_name = tool_name[len(prefix):] if prefix and tool_name.startswith(prefix) else tool_name

        for tool in conn.tools:
            if tool.name == base_name:
                return tool
        return None

    def list_all_tools(self) -> list[tuple[str, MCPToolInfo]]:
        """
        List all MCP tools across all connected servers.

        Returns:
            List of (full_name, tool_info) tuples
        """
        result = []
        for _server_id, conn in self._connections.items():
            prefix = conn.config.get("tool_prefix") or ""
            for tool in conn.tools:
                full_name = f"{prefix}{tool.name}" if prefix else tool.name
                result.append((full_name, tool))
        return result

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name is an MCP tool."""
        return tool_name in self._tool_to_server

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Call an MCP tool.

        Args:
            tool_name: Full tool name (with prefix if configured)
            arguments: Tool arguments

        Returns:
            Tool output as text
        """
        server_id = self._tool_to_server.get(tool_name)
        if not server_id:
            raise ValueError(f"Unknown MCP tool: {tool_name}")

        conn = self._connections.get(server_id)
        if not conn or not conn.connected:
            raise RuntimeError(f"MCP Server not connected: {server_id}")

        # Strip prefix to get base tool name
        prefix = conn.config.get("tool_prefix") or ""
        base_name = (
            tool_name[len(prefix):]
            if prefix and tool_name.startswith(prefix)
            else tool_name
        )

        return await conn.call_tool(base_name, arguments)

    async def shutdown(self) -> None:
        """Close all MCP Server connections."""
        server_ids = list(self._connections.keys())
        for server_id in server_ids:
            await self.remove_server(server_id)
        logger.info("mcp_manager_shutdown", servers_closed=len(server_ids))

    def get_status(self) -> dict[str, dict]:
        """
        Get status of all MCP Server connections.

        Returns:
            Dict mapping server_id to status info
        """
        return {
            str(server_id): {
                "name": conn.config.get("name", "unknown"),
                "transport": conn.config.get("transport_type"),
                "connected": conn.connected,
                "tools_count": len(conn.tools),
                "tool_prefix": conn.config.get("tool_prefix"),
            }
            for server_id, conn in self._connections.items()
        }

    def get_server_tools(self, server_id: UUID) -> list[MCPToolInfo] | None:
        """Get tools for a specific server."""
        conn = self._connections.get(server_id)
        if not conn:
            return None
        return conn.tools
