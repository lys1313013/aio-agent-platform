"""End-to-end tests for MCP integration.

Verifies that after an MCP Server is hooked up, the LLM invocation is
actually different from before: the MCP tools must appear in the tool
schema handed to the LLM provider, and when the LLM emits a tool_call
targeting an MCP tool, the executor must route it to the MCP server and
feed the result back into the conversation.

This is precisely the property the user flagged as broken: "after MCP is
hooked up, calling the LLM looks the same as before". These tests pin
down the expected differences.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="mcp is an optional dependency; install with: pip install mcp")

import asyncio
import json
import socket
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import pytest
import pytest_asyncio
import uvicorn

from aio_agent_platform.core.agent import AgentLoop, AgentStep
from aio_agent_platform.llm import LLMMessage
from aio_agent_platform.sandbox import SandboxManager
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.mcp.adapter import MCPServerConnection
from aio_agent_platform.tools.mcp.manager import MCPManager
from aio_agent_platform.tools.registry import ToolRegistry


# --------------------------------------------------------------------------- #
# Fake MCP Server (SSE transport, one test tool)                              #
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_fake_mcp_app():
    """Create a FastMCP ASGI app with a single `fake_weather` tool."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("FakeWeather")

    @mcp.tool()
    def fake_weather(city: str) -> str:
        """Return a fake weather report for the given city.

        Always returns a deterministic string so tests can assert on it.
        """
        return f"FAKE_WEATHER:{city}:sunny 25C"

    return mcp


@asynccontextmanager
async def _run_fake_mcp_server(port: int):
    """Run a FastMCP SSE server in a background uvicorn task."""
    mcp = _make_fake_mcp_app()
    # FastMCP exposes an ASGI app via .sse_app() for SSE transport
    asgi_app = mcp.sse_app()
    config = uvicorn.Config(
        asgi_app, host="127.0.0.1", port=port, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        # Wait for the server to actually start accepting connections
        for _ in range(50):
            try:
                _s = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                _s.close()
                break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError(f"Fake MCP server failed to start on port {port}")
        yield
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5.0)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def fake_mcp_server():
    """Start the fake MCP server and yield its SSE URL."""
    port = _free_port()
    async with _run_fake_mcp_server(port):
        yield f"http://127.0.0.1:{port}/sse"


@pytest_asyncio.fixture
async def mcp_manager() -> AsyncIterator[MCPManager]:
    mgr = MCPManager()
    yield mgr
    await mgr.shutdown()


# --------------------------------------------------------------------------- #
# Tests — Stage 1: adapter + manager wiring                                   #
# --------------------------------------------------------------------------- #


class TestMCPAdapterAndManager:
    """Confirm the adapter can connect, discover tools, and call them."""

    @pytest.mark.asyncio
    async def test_connect_and_discover_tools(self, fake_mcp_server):
        conn = MCPServerConnection(
            server_id=uuid.uuid4(),
            config={
                "name": "fake-weather",
                "transport_type": "sse",
                "url": fake_mcp_server,
                "headers": {},
                "tool_prefix": "",
                "timeout": 10,
            },
        )
        try:
            await conn.connect()
            assert conn.connected
            names = [t.name for t in conn.tools]
            assert "fake_weather" in names, f"Expected fake_weather, got {names}"
        finally:
            await conn.disconnect()

    @pytest.mark.asyncio
    async def test_call_tool_roundtrip(self, fake_mcp_server):
        conn = MCPServerConnection(
            server_id=uuid.uuid4(),
            config={
                "name": "fake-weather",
                "transport_type": "sse",
                "url": fake_mcp_server,
                "headers": {},
                "tool_prefix": "",
                "timeout": 10,
            },
        )
        try:
            await conn.connect()
            result = await conn.call_tool("fake_weather", {"city": "Berlin"})
            assert "FAKE_WEATHER:Berlin" in result
        finally:
            await conn.disconnect()

    @pytest.mark.asyncio
    async def test_manager_routes_tool_call(self, fake_mcp_server, mcp_manager):
        server_id = uuid.uuid4()
        await mcp_manager.add_server(
            server_id,
            {
                "name": "fake-weather",
                "transport_type": "sse",
                "url": fake_mcp_server,
                "headers": {},
                "tool_prefix": "wx_",
                "timeout": 10,
            },
        )

        # Tool must be discoverable via the manager with the prefix applied
        all_tools = mcp_manager.list_all_tools()
        full_names = [name for name, _ in all_tools]
        assert "wx_fake_weather" in full_names, full_names

        # And the manager must be able to route a call to it
        assert mcp_manager.is_mcp_tool("wx_fake_weather")
        out = await mcp_manager.call_tool("wx_fake_weather", {"city": "Paris"})
        assert "FAKE_WEATHER:Paris" in out


# --------------------------------------------------------------------------- #
# Tests — Stage 2: executor routes MCP tools                                  #
# --------------------------------------------------------------------------- #


class _FakeSandbox:
    """Stub SandboxManager so we can construct ToolExecutor without Docker."""

    async def get_or_create(self, *a, **kw):
        return None

    async def execute(self, *a, **kw):
        raise RuntimeError("sandbox should not be called for MCP tools")


class TestExecutorMCPRouting:
    @pytest.mark.asyncio
    async def test_executor_dispatches_mcp_tool(
        self, fake_mcp_server, mcp_manager
    ):
        server_id = uuid.uuid4()
        await mcp_manager.add_server(
            server_id,
            {
                "name": "fake-weather",
                "transport_type": "sse",
                "url": fake_mcp_server,
                "headers": {},
                "tool_prefix": "wx_",
                "timeout": 10,
            },
        )

        registry = ToolRegistry()
        executor = ToolExecutor(
            registry=registry,
            sandbox_mgr=_FakeSandbox(),
            mcp_manager=mcp_manager,
        )

        result = await executor.execute(
            tool_name="wx_fake_weather",
            arguments={"city": "Tokyo"},
            tool_call_id="call_1",
            user_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
        )
        assert result.success, result.error
        assert "FAKE_WEATHER:Tokyo" in result.output


# --------------------------------------------------------------------------- #
# Tests — Stage 3: the LLM actually SEES the MCP tools (the user's bug)       #
# --------------------------------------------------------------------------- #


@dataclass
class _CapturedLLMCall:
    """Holds everything the LLM provider was asked to do, so tests can assert."""

    messages: list[LLMMessage]
    tools: list[dict] | None


class _ScriptedLLMProvider:
    """A fake LLM provider that records what it was passed and emits a
    scripted sequence of chunks.

    Used to prove that MCP tools make it into the `tools=` argument of the
    provider's `stream()` call — which is exactly where a real OpenAI /
    Anthropic client would read them from.
    """

    def __init__(self, scripted_tool_calls: list[dict] | None = None):
        # Each entry: {"id": "...", "name": "...", "arguments": {...}}
        self.scripted_tool_calls = scripted_tool_calls or []
        self.captured: list[_CapturedLLMCall] = []
        self._call_index = 0
        self.model = "test-model"

    async def stream(self, messages, tools=None):
        self.captured.append(
            _CapturedLLMCall(messages=list(messages), tools=list(tools or []))
        )

        # First call: emit the scripted tool calls (if any)
        if self._call_index == 0 and self.scripted_tool_calls:
            for tc in self.scripted_tool_calls:
                from aio_agent_platform.llm import LLMChunk, ToolCall

                yield LLMChunk(
                    type="tool_call_start",
                    tool_call=ToolCall(
                        id=tc["id"], name=tc["name"], arguments={}
                    ),
                    argument_delta=json.dumps(tc["arguments"]),
                )
        else:
            # Subsequent calls (or no scripted tools): emit a final text answer
            from aio_agent_platform.llm import LLMChunk

            yield LLMChunk(type="text_delta", content="done.")

        self._call_index += 1


class TestLLMSeesMCPTools:
    """The user's actual complaint: after MCP is hooked up, the LLM call
    should be observably different. This test pins down the difference.
    """

    @pytest.mark.asyncio
    async def test_mcp_tools_injected_into_llm_schema(
        self, fake_mcp_server, mcp_manager
    ):
        server_id = uuid.uuid4()
        await mcp_manager.add_server(
            server_id,
            {
                "name": "fake-weather",
                "transport_type": "sse",
                "url": fake_mcp_server,
                "headers": {},
                "tool_prefix": "wx_",
                "timeout": 10,
            },
        )

        # --- Replicate the chat route's tool-building path ---
        from aio_agent_platform.interface.routes.chat import _filter_tools_by_agent

        registry = ToolRegistry()
        executor = ToolExecutor(
            registry=registry,
            sandbox_mgr=_FakeSandbox(),
            mcp_manager=mcp_manager,
        )

        _builtin, tools_schema = _filter_tools_by_agent(executor, agent=None)

        # BEFORE MCP was hooked up, tools_schema would only contain built-in
        # tools. AFTER hook-up it MUST contain the MCP tool.
        names = [t["function"]["name"] for t in tools_schema]
        assert "wx_fake_weather" in names, (
            f"MCP tool not injected into LLM schema. "
            f"This is exactly the bug the user reported. Got: {names}"
        )

        # The schema entry must look like a real OpenAI function tool
        mcp_entry = next(
            t for t in tools_schema if t["function"]["name"] == "wx_fake_weather"
        )
        assert mcp_entry["type"] == "function"
        assert "weather" in mcp_entry["function"]["description"].lower()

    @pytest.mark.asyncio
    async def test_agent_loop_roundtrip_via_mcp_tool(
        self, fake_mcp_server, mcp_manager
    ):
        """Full loop: LLM emits MCP tool_call -> executor -> MCP server -> LLM."""
        server_id = uuid.uuid4()
        await mcp_manager.add_server(
            server_id,
            {
                "name": "fake-weather",
                "transport_type": "sse",
                "url": fake_mcp_server,
                "headers": {},
                "tool_prefix": "wx_",
                "timeout": 10,
            },
        )

        from aio_agent_platform.interface.routes.chat import _filter_tools_by_agent

        registry = ToolRegistry()
        executor = ToolExecutor(
            registry=registry,
            sandbox_mgr=_FakeSandbox(),
            mcp_manager=mcp_manager,
        )
        _builtin, tools_schema = _filter_tools_by_agent(executor, agent=None)

        # Script the LLM: first turn emits the MCP tool call, second turn
        # returns a final text answer that can reference the tool output.
        scripted = [
            {
                "id": "call_wx_1",
                "name": "wx_fake_weather",
                "arguments": {"city": "Oslo"},
            }
        ]
        provider = _ScriptedLLMProvider(scripted_tool_calls=scripted)

        loop = AgentLoop(
            provider=provider,
            tool_executor=executor,
            system_prompt="You are a test agent.",
            max_iterations=3,
            trust_level="full",
        )

        events: list = []
        async for ev in loop.run(
            user_input="What is the weather in Oslo?",
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            conversation_history=[],
            tools=tools_schema,
        ):
            events.append(ev)

        # 1. The LLM's very first call must have received the MCP tool schema
        first_call = provider.captured[0]
        first_call_tool_names = [
            t["function"]["name"] for t in first_call.tools
        ]
        assert "wx_fake_weather" in first_call_tool_names, (
            "MCP tool was NOT passed to provider.stream(tools=...). "
            "This is the bug the user reported."
        )

        # 2. The executor must have routed the tool call through MCP
        tool_result_events = [
            e for e in events if isinstance(e, str) and e.startswith("tool_result:")
        ]
        assert tool_result_events, "Expected a tool_result event"
        # Format: tool_result:id:name:status:output_json
        parts = tool_result_events[0].split(":", 4)
        status = parts[3]
        preview = parts[4] if len(parts) > 4 else ""
        assert status == "ok", tool_result_events[0]
        assert "FAKE_WEATHER:Oslo" in preview, (
            f"MCP tool output not propagated. Got: {preview!r}"
        )

        # 3. The LLM's SECOND call must carry the tool result message so the
        # model can ground its final answer on the MCP output. This is the
        # definitive "calling the LLM is now different" signal.
        assert len(provider.captured) >= 2, (
            "LLM was not called a second time with the tool result — "
            "the ReAct loop did not feed the MCP observation back."
        )
        second_call_msgs = provider.captured[1].messages
        tool_messages = [m for m in second_call_msgs if m.role == "tool"]
        assert tool_messages, (
            "Second LLM call has no tool result message — the MCP observation "
            "was dropped before reaching the model."
        )
        tool_content = tool_messages[0].content or ""
        assert "FAKE_WEATHER:Oslo" in tool_content, (
            f"Tool result message did not carry MCP output: {tool_content!r}"
        )
