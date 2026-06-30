"""MCP Server adapter — wraps the MCP Python SDK client."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog

logger = structlog.get_logger()


@dataclass
class MCPToolInfo:
    """MCP tool information (discovered from MCP Server)."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)  # JSON Schema

    def to_openai_tool(self, prefix: str = "") -> dict:
        """Convert to OpenAI function-calling format."""
        full_name = f"{prefix}{self.name}" if prefix else self.name
        return {
            "type": "function",
            "function": {
                "name": full_name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


class MCPServerConnection:
    """
    Encapsulates a single MCP Server connection.

    Manages the lifecycle of the transport (SSE / streamable-http) and session,
    provides tool discovery and invocation.
    """

    def __init__(self, server_id: UUID, config: dict):
        self.server_id = server_id
        self.config = config
        self.session = None  # ClientSession
        self.tools: list[MCPToolInfo] = []
        self._transport_cm = None  # transport context manager
        self._session_cm = None  # session context manager
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.session is not None

    async def connect(self) -> None:
        """Establish connection and discover tools."""
        try:
            transport_type = self.config["transport_type"]

            if transport_type == "sse":
                await self._connect_sse()
            elif transport_type == "streamable-http":
                await self._connect_streamable_http()
            else:
                raise ValueError(f"Unknown transport type: {transport_type}")

            self._connected = True
            logger.info(
                "mcp_server_connected",
                server_id=str(self.server_id),
                name=self.config.get("name"),
                transport=transport_type,
            )

            # Discover tools
            await self.refresh_tools()

        except Exception as e:
            logger.error(
                "mcp_server_connect_failed",
                server_id=str(self.server_id),
                name=self.config.get("name"),
                error=str(e),
            )
            # Clean up on failure
            await self._cleanup()
            raise

    async def _connect_sse(self) -> None:
        """Connect via SSE (HTTP endpoint with Server-Sent Events)."""
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        url = self.config["url"]
        headers = self.config.get("headers") or None

        self._transport_cm = sse_client(url=url, headers=headers)
        read_stream, write_stream = await self._transport_cm.__aenter__()

        self._session_cm = ClientSession(read_stream, write_stream)
        self.session = await self._session_cm.__aenter__()

        await self.session.initialize()

    async def _connect_streamable_http(self) -> None:
        """Connect via Streamable HTTP (HTTP POST + optional SSE streaming)."""
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        url = self.config["url"]
        headers = self.config.get("headers") or None
        timeout = self.config.get("timeout", 60)

        self._transport_cm = streamablehttp_client(
            url=url, headers=headers, timeout=float(timeout)
        )
        # streamablehttp_client yields (read_stream, write_stream, get_session_id_callback)
        read_stream, write_stream, _get_session_id = await self._transport_cm.__aenter__()

        self._session_cm = ClientSession(read_stream, write_stream)
        self.session = await self._session_cm.__aenter__()

        await self.session.initialize()

    async def disconnect(self) -> None:
        """Close the connection and clean up resources."""
        await self._cleanup()
        self._connected = False
        self.tools = []
        logger.info(
            "mcp_server_disconnected",
            server_id=str(self.server_id),
            name=self.config.get("name"),
        )

    async def _cleanup(self) -> None:
        """Clean up session and transport context managers."""
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("mcp_session_cleanup_error", error=str(e))
            self._session_cm = None
            self.session = None

        if self._transport_cm is not None:
            try:
                await self._transport_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("mcp_transport_cleanup_error", error=str(e))
            self._transport_cm = None

    async def refresh_tools(self) -> list[MCPToolInfo]:
        """Refresh the list of tools from the MCP Server."""
        if not self.session:
            raise RuntimeError("Not connected")

        result = await self.session.list_tools()
        self.tools = []
        for tool in result.tools:
            self.tools.append(
                MCPToolInfo(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                )
            )

        logger.info(
            "mcp_tools_discovered",
            server_id=str(self.server_id),
            name=self.config.get("name"),
            tools_count=len(self.tools),
            tool_names=[t.name for t in self.tools],
        )
        return self.tools

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        Call a tool on the MCP Server and return the result as text.

        Args:
            tool_name: The tool name (without prefix)
            arguments: Tool arguments

        Returns:
            Text output from the tool
        """
        if not self.session:
            raise RuntimeError(f"MCP Server not connected: {self.config.get('name')}")

        try:
            result = await self.session.call_tool(tool_name, arguments)

            if result.isError:
                # Extract error text
                error_parts = []
                if result.content:
                    for item in result.content:
                        if hasattr(item, "text"):
                            error_parts.append(item.text)
                error_text = "\n".join(error_parts) if error_parts else "Unknown MCP error"
                raise RuntimeError(f"MCP tool error: {error_text}")

            # Extract text content from result
            if result.content:
                text_parts = []
                for item in result.content:
                    if hasattr(item, "text"):
                        text_parts.append(item.text)
                return "\n".join(text_parts) if text_parts else "(no output)"

            return "(no output)"

        except Exception as e:
            logger.error(
                "mcp_tool_call_failed",
                server_id=str(self.server_id),
                tool_name=tool_name,
                error=str(e),
            )
            raise
