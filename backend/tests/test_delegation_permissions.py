"""Delegation never widens the invoking agent's actual tool grant."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from aio_agent_platform.delegation.handler import (
    _build_child_tools,
    _build_dynamic_agent,
    handle_delegate_task,
)
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import Tool, ToolRegistry


def executor():
    registry = ToolRegistry()
    for name in ("read_file", "write_file", "run_shell", "delegate_task"):
        registry.register(Tool(name, name, {}, requires_sandbox=False))
    return ToolExecutor(registry, SimpleNamespace())


@pytest.mark.parametrize("grant", [None, set(), {"read_file"}, {"read_file", "delegate_task"}])
@pytest.mark.parametrize("configured", [False, True])
def test_children_only_narrow_parent_permissions(grant, configured):
    child = _build_dynamic_agent(uuid4(), "specialist", "", uuid4())
    if configured:
        child.enabled_tools = ["read_file", "write_file", "run_shell"]
    tools, schemas = _build_child_tools(executor(), child, 1, 3, parent_allowed_tools=grant)
    names = {tool.name for tool in tools}
    assert names == (grant or set())
    assert {schema["function"]["name"] for schema in schemas} == names
    nested, _ = _build_child_tools(executor(), child, 2, 3, parent_allowed_tools=names)
    assert {tool.name for tool in nested} == names


@pytest.mark.parametrize("grant", [None, set(), {"read_file"}])
async def test_missing_or_empty_delegation_authorization_rejected_before_db(grant):
    result = await handle_delegate_task({"task": "write", "role_name": "writer"},
        str(uuid4()), str(uuid4()), delegation=SimpleNamespace(), tool_executor=executor(), allowed_tools=grant)
    assert "explicit parent tool permission grant" in result


@pytest.mark.parametrize("registered", [True, False])
async def test_executor_preserves_parent_grant_for_direct_handlers(registered):
    ex = executor()
    name = "delegate_task" if registered else "custom_direct"
    handler = AsyncMock(return_value="ok")
    ex.register_direct_handler(name, handler)
    grant = {name, "read_file"}
    result = await ex.execute(name, {}, "call", str(uuid4()), str(uuid4()), allowed_tools=grant)
    assert result.success
    assert handler.await_args.kwargs["allowed_tools"] == grant
