"""send_file_to_user direct-handler tests.

Covers the full executor.execute() dispatch path, channel-context routing,
and the file-size / read / send failure branches.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from aio_agent_platform.channels.adapter import InboundEvent
from aio_agent_platform.channels.file_send import (
    SEND_FILE_TOOL_NAME,
    ChannelSendContext,
    current_channel_send_ctx,
    handle_send_file,
)
from aio_agent_platform.tools.builtin import register_builtin_tools
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import ToolRegistry

pytestmark = pytest.mark.asyncio


def _executor() -> ToolExecutor:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ex = ToolExecutor(registry=registry, sandbox_mgr=None)
    ex.register_direct_handler(SEND_FILE_TOOL_NAME, handle_send_file)
    return ex


def _event() -> InboundEvent:
    return InboundEvent(
        channel_id=uuid4(),
        event_id="evt",
        chat_id="oc_chat",
        external_id="ou_user",
        text="发给我",
    )


async def _run_with_ctx(adapter, args: dict) -> str:
    ex = _executor()
    token = current_channel_send_ctx.set(
        ChannelSendContext(adapter=adapter, event=_event())
    )
    try:
        result = await ex.execute(
            SEND_FILE_TOOL_NAME,
            args,
            tool_call_id="tc1",
            user_id=str(uuid4()),
            session_id=str(uuid4()),
            workspace_id=str(uuid4()),
            workspace_slug="default",
        )
    finally:
        current_channel_send_ctx.reset(token)
    assert result.success
    return result.output


async def test_send_file_success(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.send_file = AsyncMock(return_value="om_file")
    monkeypatch.setattr(
        "aio_agent_platform.channels.file_send._read_workspace_file",
        AsyncMock(return_value=b"csv-data"),
    )
    output = await _run_with_ctx(adapter, {"file_path": "uploads/a.csv"})
    assert "已通过当前渠道发送文件" in output
    assert "a.csv" in output
    adapter.send_file.assert_awaited_once()
    _filename, _data = adapter.send_file.await_args.args[1:3]
    assert _filename == "a.csv"
    assert _data == b"csv-data"


async def test_send_file_file_name_override(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.send_file = AsyncMock(return_value="om_file")
    monkeypatch.setattr(
        "aio_agent_platform.channels.file_send._read_workspace_file",
        AsyncMock(return_value=b"x"),
    )
    await _run_with_ctx(adapter, {"file_path": "uploads/result.csv", "file_name": "处理结果.csv"})
    filename = adapter.send_file.await_args.args[1]
    assert filename == "处理结果.csv"


async def test_no_channel_context_returns_fallback() -> None:
    ex = _executor()
    result = await ex.execute(
        SEND_FILE_TOOL_NAME,
        {"file_path": "uploads/a.csv"},
        tool_call_id="tc1",
        user_id=str(uuid4()),
        session_id=str(uuid4()),
    )
    assert result.success
    assert "不在聊天渠道" in result.output


async def test_missing_file_path_returns_prompt() -> None:
    adapter = MagicMock()
    adapter.send_file = AsyncMock()
    output = await _run_with_ctx(adapter, {})
    assert "file_path" in output
    adapter.send_file.assert_not_awaited()


async def test_file_read_failure(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.send_file = AsyncMock(return_value="om_file")
    monkeypatch.setattr(
        "aio_agent_platform.channels.file_send._read_workspace_file",
        AsyncMock(return_value=None),
    )
    output = await _run_with_ctx(adapter, {"file_path": "uploads/missing.csv"})
    assert "无法读取文件" in output
    adapter.send_file.assert_not_awaited()


async def test_oversize_file_rejected(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.send_file = AsyncMock(return_value="om_file")
    monkeypatch.setattr(
        "aio_agent_platform.channels.file_send._read_workspace_file",
        AsyncMock(return_value=b"0" * (30 * 1024 * 1024 + 1)),
    )
    output = await _run_with_ctx(adapter, {"file_path": "uploads/big.bin"})
    assert "超过" in output
    assert "30MB" in output
    adapter.send_file.assert_not_awaited()


async def test_send_failure(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.send_file = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "aio_agent_platform.channels.file_send._read_workspace_file",
        AsyncMock(return_value=b"x"),
    )
    output = await _run_with_ctx(adapter, {"file_path": "uploads/a.csv"})
    assert "发送失败" in output
