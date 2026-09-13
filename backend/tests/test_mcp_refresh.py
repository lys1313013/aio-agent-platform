"""On-demand MCP schema refresh, routing and failure recovery."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from aio_agent_platform.core.chat import filter_tools_by_agent, refresh_mcp_tools_for_agent
from aio_agent_platform.tools.mcp.adapter import MCPServerConnection, MCPToolInfo
from aio_agent_platform.tools.mcp.manager import MCPManager
from aio_agent_platform.tools.registry import ToolRegistry


def tool(name, parameter="query"):
    return SimpleNamespace(
        name=name, description=f"Description of {name}",
        inputSchema={"type": "object", "properties": {parameter: {"type": "string"}}},
    )


def setup_server():
    manager = MCPManager()
    sid = uuid4()
    conn = MCPServerConnection(sid, {"tool_prefix": "docs_", "timeout": 1})
    conn.tools = [MCPToolInfo("search", "old", {"type": "object"}), MCPToolInfo("removed", "old")]
    conn.tools_refreshed_at = time.monotonic() - 61
    conn.session = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(
        tools=[tool("search", "keyword"), tool("added")], nextCursor=None,
    )))
    conn._connected = True
    manager._connections[sid] = conn
    manager._sync_tool_mappings(sid, conn)
    return manager, sid, conn


async def test_ttl_refresh_rebuilds_schema_and_routes_before_execution():
    manager, sid, conn = setup_server()
    executor = SimpleNamespace(mcp_manager=manager, registry=ToolRegistry())
    await refresh_mcp_tools_for_agent(executor, None)
    _, schema = filter_tools_by_agent(executor, None)
    assert [s["function"]["name"] for s in schema] == ["docs_search", "docs_added"]
    assert "keyword" in schema[0]["function"]["parameters"]["properties"]
    assert not manager.is_mcp_tool("docs_removed")
    assert manager._tool_to_server["docs_added"] == sid
    await manager.ensure_tools_fresh()
    assert conn.session.list_tools.await_count == 1


async def test_fresh_cache_does_not_request_upstream():
    manager, _, conn = setup_server()
    conn.tools_refreshed_at = time.monotonic()
    await manager.ensure_tools_fresh()
    conn.session.list_tools.assert_not_awaited()


async def test_concurrent_requests_share_one_refresh():
    manager, _, conn = setup_server()
    response = conn.session.list_tools.return_value

    async def delayed(**kwargs):
        await asyncio.sleep(0.01)
        return response

    conn.session.list_tools.side_effect = delayed
    await asyncio.gather(*(manager.ensure_tools_fresh() for _ in range(12)))
    assert conn.session.list_tools.await_count == 1


async def test_failure_keeps_cache_and_routes_and_backs_off():
    manager, sid, conn = setup_server()
    old_tools = conn.tools
    old_routes = manager._tool_to_server.copy()
    conn.session.list_tools.side_effect = RuntimeError("upstream unavailable")
    await manager.ensure_tools_fresh()
    await manager.ensure_tools_fresh()
    assert conn.tools is old_tools
    assert manager._tool_to_server == old_routes
    assert conn.session.list_tools.await_count == 1
    manager._retry_after[sid] = 0
    conn.session.list_tools.side_effect = None
    await manager.ensure_tools_fresh()
    assert manager.is_mcp_tool("docs_added")


async def test_manual_refresh_bypasses_ttl_and_backoff_and_raises_errors():
    manager, sid, conn = setup_server()
    conn.tools_refreshed_at = time.monotonic()
    manager._retry_after[sid] = time.monotonic() + 60
    await manager.refresh_tools(sid)
    assert conn.session.list_tools.await_count == 1
    conn.session.list_tools.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError, match="failed"):
        await manager.refresh_tools(sid)
    assert manager.is_mcp_tool("docs_added")


async def test_only_selected_servers_are_refreshed():
    manager, _, conn = setup_server()
    await manager.ensure_tools_fresh(set())
    await manager.ensure_tools_fresh({str(uuid4())})
    conn.session.list_tools.assert_not_awaited()


async def test_pagination_is_complete_and_partial_failure_does_not_replace_cache():
    _, _, conn = setup_server()
    old_tools = conn.tools
    page = SimpleNamespace(tools=[tool("first")], nextCursor="page2")
    conn.session.list_tools.side_effect = [page, RuntimeError("page2 failed")]
    with pytest.raises(RuntimeError):
        await conn.refresh_tools()
    assert conn.tools is old_tools
    conn.session.list_tools.side_effect = [page, SimpleNamespace(tools=[tool("last")], nextCursor=None)]
    await conn.refresh_tools()
    assert [t.name for t in conn.tools] == ["first", "last"]
    assert conn.session.list_tools.call_args.kwargs == {"cursor": "page2"}


async def test_call_reconnect_updates_manager_routes():
    manager, _, conn = setup_server()

    async def call(*args):
        await conn.refresh_tools()
        return "ok"

    conn.call_tool = call
    assert await manager.call_tool("docs_search", {}) == "ok"
    assert manager.is_mcp_tool("docs_added")
    assert not manager.is_mcp_tool("docs_removed")
