"""MCP server-level all-tools policy tests."""

from types import SimpleNamespace
from uuid import uuid4

from aio_agent_platform.core.chat import filter_tools_by_agent
from aio_agent_platform.tools.mcp.adapter import MCPToolInfo
from aio_agent_platform.tools.mcp.selection import is_mcp_tool_allowed
from aio_agent_platform.tools.registry import ToolRegistry


def test_all_tools_policy_allows_new_upstream_tool() -> None:
    server_id = uuid4()

    assert is_mcp_tool_allowed(
        server_id=server_id,
        full_name="docs_new_tool",
        allowed_server_ids={str(server_id)},
        enabled_tools={"docs_existing_tool"},
        all_tools_server_ids={str(server_id)},
    )


def test_custom_selection_rejects_unselected_tool() -> None:
    server_id = uuid4()

    assert not is_mcp_tool_allowed(
        server_id=server_id,
        full_name="docs_new_tool",
        allowed_server_ids={str(server_id)},
        enabled_tools={"docs_existing_tool"},
        all_tools_server_ids=set(),
    )


def test_all_tools_policy_cannot_bypass_server_binding() -> None:
    server_id = uuid4()

    assert not is_mcp_tool_allowed(
        server_id=server_id,
        full_name="docs_new_tool",
        allowed_server_ids=set(),
        enabled_tools=None,
        all_tools_server_ids={str(server_id)},
    )


def test_chat_filter_includes_tool_added_after_all_was_selected() -> None:
    server_id = uuid4()
    tool_info = MCPToolInfo(name="new_tool", description="New upstream tool")
    mcp_manager = SimpleNamespace(
        _tool_to_server={"docs_new_tool": server_id},
        list_all_tools=lambda: [("docs_new_tool", tool_info)],
    )
    executor = SimpleNamespace(registry=ToolRegistry(), mcp_manager=mcp_manager)
    agent = SimpleNamespace(
        id=uuid4(),
        enabled_tools=["docs_existing_tool"],
        mcp_server_ids=[str(server_id)],
        mcp_all_tools_server_ids=[str(server_id)],
        knowledge_bases=[],
        graph_knowledge_bases=[],
        children=[],
    )

    _tools, schema = filter_tools_by_agent(executor, agent)

    assert [item["function"]["name"] for item in schema] == ["docs_new_tool"]
