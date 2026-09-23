"""Full-result preservation and paging regressions, without Docker or DB I/O."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.core.chat_history import ChatTurnRecorder
from aio_agent_platform.core.context import compress_early_tool_results
from aio_agent_platform.llm.client import LLMChunk, LLMMessage, ToolCall
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.tools.builtin import READ_FILE
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import Tool, ToolRegistry


@pytest.fixture
def setup(monkeypatch):
    hooks, recorder = Mock(), Mock()
    monkeypatch.setattr("aio_agent_platform.tools.executor.get_hook_manager", lambda: hooks)
    monkeypatch.setattr("aio_agent_platform.tools.executor.get_recorder", lambda: recorder)
    manager = SimpleNamespace(
        get_or_create=AsyncMock(return_value=object()),
        write_workspace_file=AsyncMock(return_value=True),
    )
    return manager, hooks, recorder


def make_executor(manager, kind, output):
    registry = ToolRegistry()
    handler = AsyncMock(return_value=output)
    executor = ToolExecutor(registry, manager)
    if kind == "mcp":
        executor.mcp_manager = SimpleNamespace(is_mcp_tool=lambda _: True, call_tool=handler)
    elif kind == "remote":
        executor.remote_manager = SimpleNamespace(is_remote_tool=lambda _: True)
        executor.remote_executor = SimpleNamespace(call=handler)
    else:
        if kind == "builtin":
            registry.register(Tool("sample", "test", {}, requires_sandbox=False))
        executor.register_direct_handler("sample", handler)
    return executor, handler


async def execute(executor, **kwargs):
    return await executor.execute(
        "sample", {}, "call-1", "user-1", "session-1",
        workspace_id="workspace-1", workspace_slug="project", **kwargs,
    )


@pytest.mark.parametrize("kind", ["builtin", "direct", "mcp", "remote"])
@pytest.mark.parametrize("size", [10001, 50000, 50001])
async def test_threshold_and_every_dispatch_route(setup, kind, size):
    manager, hooks, recorder = setup
    raw = "中" * size + ("" if size <= 50000 else "TAIL")
    executor, handler = make_executor(manager, kind, raw)
    result = await execute(executor)
    assert result.success
    assert handler.await_count == 1
    if size <= 50000:
        assert result.output == raw
        manager.get_or_create.assert_not_awaited()
    else:
        args = manager.write_workspace_file.await_args.args
        assert args[1:3] == ("workspace-1", "project")
        assert args[4].decode() == raw
        assert len(result.output) <= 50000
        assert result.output_file in result.output
        assert "offset_chars" in result.output
        assert result.file_changes[0]["size"] == len(raw.encode())
        assert result.file_changes[0]["path"] == args[3]
    metrics = recorder.record_tool_call.call_args.kwargs
    assert metrics["output_chars"] == len(raw)
    assert metrics["output_bytes"] == len(raw.encode())
    assert metrics["is_truncated"] == (size > 50000)
    assert hooks.fire_nowait.call_args.kwargs["data"]["is_truncated"] == (size > 50000)


async def test_failed_persistence_retains_result_and_does_not_repeat_mutation(setup):
    manager, _, _ = setup
    manager.write_workspace_file.side_effect = RuntimeError("storage offline")
    raw = "x" * 70000 + "TAIL"
    executor, handler = make_executor(manager, "remote", raw)
    result = await execute(executor)
    assert result.success and result.output.endswith(raw)
    assert "could not be saved" in result.output
    assert result.output_file is None and result.file_changes == []
    assert handler.await_count == 1


async def test_sandbox_only_file_is_explicitly_marked(setup):
    manager, _, _ = setup
    manager.write_workspace_file.return_value = False
    executor, _ = make_executor(manager, "mcp", "x" * 50001)
    result = await execute(executor)
    assert result.output_file
    assert "durable storage is unavailable" in result.output


async def test_unique_files_and_no_permission_bypass(setup):
    manager, _, _ = setup
    executor, handler = make_executor(manager, "mcp", "x" * 50001)
    results = await asyncio.gather(execute(executor), execute(executor))
    assert results[0].output_file != results[1].output_file
    result = await execute(executor, allowed_tools=set())
    assert not result.success and "Permission denied" in result.error
    assert handler.await_count == 2
    assert manager.write_workspace_file.await_count == 2


async def test_nul_is_retained_in_file_but_not_in_model_content(setup):
    manager, _, _ = setup
    raw = "中" * 50001 + "\x00tail"
    executor, _ = make_executor(manager, "direct", raw)
    result = await execute(executor)
    assert manager.write_workspace_file.await_args.args[-1].decode() == raw
    assert "\x00" not in result.output


async def test_single_line_json_character_paging_reads_tail(setup, tmp_path):
    manager, _, _ = setup
    raw = '{"data":"' + "中文🙂" * 30000 + 'TAIL"}'
    (tmp_path / "result.txt").write_text(raw, encoding="utf-8")

    async def shell(_sandbox, command):
        command = command.replace("/workspace/project", str(tmp_path))
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return SimpleNamespace(stdout=stdout.decode(), stderr=stderr.decode(), exit_code=process.returncode)

    manager.execute = shell
    registry = ToolRegistry()
    registry.register(READ_FILE)
    executor = ToolExecutor(registry, manager)
    pages = []
    for offset in range(0, len(raw), 20000):
        result = await executor.execute(
            "read_file", {"path": "result.txt", "offset_chars": offset, "limit_chars": 20000},
            "read", "u", "s", workspace_id="workspace-1", workspace_slug="project",
        )
        assert result.success
        page, _, metadata = result.output.rpartition("\n[Characters ")
        assert metadata
        pages.append(page)
    assert "".join(pages) == raw
    assert "end of file" in result.output
    manager.write_workspace_file.assert_not_awaited()


async def test_chunk_failure_does_not_report_success():
    manager = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(exit_code=0),  # mkdir
        SimpleNamespace(exit_code=0),  # first chunk
        SimpleNamespace(exit_code=1),  # second chunk
        SimpleNamespace(exit_code=0),  # cleanup
    ]))
    assert not await WorkspaceStorage.write_file_live(manager, object(), "file.txt", b"x" * 100000, "p")
    commands = [call.args[1] for call in manager.execute.await_args_list]
    assert not any("base64 -d" in cmd for cmd in commands)
    assert commands[-1].startswith("rm -f /tmp/_workspace_upload_")


async def test_chunked_files_preserve_bytes_under_concurrent_writes(tmp_path):
    async def shell(_sandbox, command):
        command = command.replace("/workspace/project", str(tmp_path))
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode()
        return SimpleNamespace(exit_code=process.returncode)

    manager = SimpleNamespace(execute=shell)
    payloads = [("中文🙂\x00" * 30000).encode(), b"different" * 30000]
    written = await asyncio.gather(*(
        WorkspaceStorage.write_file_live(manager, object(), f"result-{i}.txt", data, "project")
        for i, data in enumerate(payloads)
    ))
    assert all(written)
    for i, data in enumerate(payloads):
        assert (tmp_path / f"result-{i}.txt").read_bytes() == data


@pytest.mark.parametrize("size", [30000, 60000])
async def test_loop_and_history_keep_preview_and_file_reference(setup, size):
    manager, _, _ = setup
    executor, _ = make_executor(manager, "builtin", "x" * size + "TAIL")
    call = ToolCall(id="call", name="sample", arguments={})
    seen = []

    async def stream(messages, tools=None):
        seen.append(list(messages))
        if len(seen) == 1:
            yield LLMChunk(type="tool_call_start", tool_call=call)
        else:
            yield LLMChunk(type="text_delta", content="done")
        yield LLMChunk(type="done", usage={})

    loop = AgentLoop(
        provider=SimpleNamespace(model="test", stream=stream), tool_executor=executor,
        system_prompt="test", max_iterations=2, trust_level="auto_all",
        workspace_id=uuid4(), workspace_slug="project",
    )
    recorder = ChatTurnRecorder(uuid4(), uuid4())
    events = [event async for event in loop.run(
        user_input="test", user_id=uuid4(), session_id=uuid4(),
        conversation_history=[], tools=executor.registry.to_openai_tools(),
    )]
    for event in events:
        recorder.record(event)
    preview = recorder.tool_calls[0]["result"]["preview"]
    assert len(preview) > 10000
    assert next(msg.content for msg in seen[1] if msg.role == "tool") == preview
    if size > 50000:
        assert preview.startswith("[Full tool output saved:")
        file_event = next(e for e in events if isinstance(e, str) and e.startswith("file_changes:"))
        assert json.loads(file_event.split(":", 1)[1])[0]["path"] in preview
    else:
        assert preview.endswith("TAIL")


def test_old_result_compression_keeps_complete_file_reference():
    reference = "[Full tool output saved: /workspace/" + "project" * 20 + "/tool-results/id.txt]"
    messages = []
    for i in range(9):
        messages.extend([
            LLMMessage(role="assistant", content="", tool_calls=[ToolCall(id=str(i), name="sample", arguments={})]),
            LLMMessage(role="tool", content=reference + "\n" + "x" * 50000, tool_call_id=str(i)),
        ])
    compressed = compress_early_tool_results(messages, 9)
    assert compressed[1].content == reference
