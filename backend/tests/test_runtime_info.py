"""Runtime diagnostics through the real executor, without DB or Docker writes."""

import asyncio
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from docker.errors import DockerException, NotFound

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.execution_scope import bind_execution_scope, execution_tenant_id
from aio_agent_platform.tools import runtime_info
from aio_agent_platform.tools.builtin import register_builtin_tools
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import ToolRegistry


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setattr("aio_agent_platform.tools.executor.get_hook_manager", Mock())
    monkeypatch.setattr("aio_agent_platform.tools.executor.get_recorder", Mock())
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executor = ToolExecutor(registry, None)
    executor.register_direct_handler("get_runtime_info", runtime_info.handle_get_runtime_info)
    return executor


async def query(executor, arguments=None, user_id=None, **kwargs):
    return await executor.execute(
        "get_runtime_info", arguments or {}, "call", str(user_id or uuid4()), "session",
        **kwargs,
    )


def test_macos_automatically_uses_computer_name(monkeypatch):
    monkeypatch.setattr(runtime_info.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime_info.platform, "mac_ver", lambda: ("15.0", (), "arm64"))
    monkeypatch.setattr(runtime_info.socket, "gethostname", lambda: "office.local")
    command = Mock(return_value="办公室 Mac")
    monkeypatch.setattr(runtime_info, "_mac_value", command)
    data = runtime_info.collect_runtime_info()
    assert data["device"]["name"] == "办公室 Mac"
    assert data["device"]["hostname"] == "office.local"
    assert data["os"]["name"] == "macOS"
    command.assert_called_once_with(["/usr/sbin/scutil", "--get", "ComputerName"])


def test_macos_name_failure_falls_back_to_hostname(monkeypatch):
    monkeypatch.setattr(runtime_info.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(runtime_info.socket, "gethostname", lambda: "office.local")
    monkeypatch.setattr(runtime_info, "_mac_value", Mock(side_effect=subprocess.TimeoutExpired("scutil", 1)))
    assert runtime_info.collect_runtime_info()["device"]["name"] == "office.local"


def test_linux_resources_and_container_scope_are_explicit(monkeypatch):
    monkeypatch.setattr(runtime_info.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime_info.socket, "gethostname", lambda: "linux-node")
    monkeypatch.setattr(runtime_info.platform, "freedesktop_os_release", lambda: {
        "PRETTY_NAME": "Test Linux", "VERSION_ID": "1",
    })
    monkeypatch.setattr(runtime_info, "_container_evidence", lambda: ["/.dockerenv"])
    monkeypatch.setattr(runtime_info, "_read_text", lambda _: "MemTotal: 4096 kB\nMemAvailable: 2048 kB")
    data = runtime_info.collect_runtime_info(True)
    assert data["device"]["name"] == "linux-node"
    assert data["os"]["name"] == "Test Linux"
    assert data["runtime"]["container"]["detected"] is True
    assert data["resources"]["scope"] == "backend_os_view"
    assert data["resources"]["memory"]["total_bytes"] == 4096 * 1024
    assert data["resources"]["memory"]["available_bytes"] == 2048 * 1024


def test_unavailable_resource_fields_do_not_hide_identity(monkeypatch):
    monkeypatch.setattr(runtime_info.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime_info, "_read_text", Mock(side_effect=PermissionError))
    monkeypatch.setattr(runtime_info.shutil, "disk_usage", Mock(side_effect=OSError))
    data = runtime_info.collect_runtime_info(True)
    assert data["runtime"]["location"] == "backend"
    assert data["resources"]["memory"]["status"] == "unknown"
    assert data["resources"]["disk"]["status"] == "unknown"


async def test_query_succeeds_without_sandbox_or_config(executor, monkeypatch):
    monkeypatch.setenv("RUNTIME_SECRET_TEST", "must-never-be-returned")
    connect = Mock(side_effect=AssertionError("must not connect without manager"))
    monkeypatch.setattr(runtime_info.docker, "from_env", connect)
    result = await query(executor)
    assert result.success
    data = json.loads(result.output)
    assert data["runtime"]["location"] == "backend"
    assert data["device"]["name"]
    assert "resources" not in data
    assert data["sandbox"]["status"] == "unavailable"
    assert "must-never-be-returned" not in result.output
    connect.assert_not_called()


async def test_explicitly_skipping_sandbox_never_connects(executor, monkeypatch):
    executor.sandbox_mgr = object()
    connect = Mock(side_effect=AssertionError("must not check Docker"))
    monkeypatch.setattr(runtime_info.docker, "from_env", connect)
    result = await query(executor, {"check_sandbox": False})
    assert result.success
    assert "sandbox" not in json.loads(result.output)
    connect.assert_not_called()


@pytest.mark.parametrize("arguments", [{"command": "uname"}, {"check_sandbox": "false"}])
async def test_rejects_unexpected_or_invalid_arguments(executor, arguments):
    result = await query(executor, arguments)
    assert not result.success


async def test_tool_allowlist_is_enforced(executor):
    result = await query(executor, allowed_tools={"memory_read"})
    assert not result.success
    assert "Permission denied" in result.error


async def test_parallel_queries_use_pinned_execution_tenants(executor):
    uid = uuid4()
    tenants, agents = [uuid4(), uuid4()], [uuid4(), uuid4()]

    async def run(tenant, agent):
        with bind_execution_scope(uid, tenant, agent_id=agent):
            result = await query(executor, {"check_sandbox": False}, uid, workspace_id="workspace")
            assert result.success
            return json.loads(result.output)["execution"]

    results = await asyncio.gather(*(run(t, a) for t, a in zip(tenants, agents, strict=True)))
    assert [r["tenant_id"] for r in results] == list(map(str, tenants))
    assert [r["agent_id"] for r in results] == list(map(str, agents))
    assert all(r["workspace_id"] == "workspace" for r in results)
    assert execution_tenant_id(uid) is None
    assert current_agent_id.get() is None


async def test_mismatched_user_is_rejected_without_reading_ui_tenant(executor):
    uid = uuid4()
    with bind_execution_scope(uid, uuid4()):
        result = await query(executor, user_id=uuid4())
    assert not result.success
    assert "执行上下文与用户不匹配" in result.error
    assert execution_tenant_id(uid) is None


async def test_cancellation_does_not_leak_scope(executor, monkeypatch):
    started, released = threading.Event(), threading.Event()
    uid = uuid4()

    def collect(*args):
        started.set()
        assert released.wait(5)
        return {}

    monkeypatch.setattr(runtime_info, "collect_runtime_info", collect)

    async def run():
        with bind_execution_scope(uid, uuid4(), agent_id=uuid4()):
            await query(executor, {"check_sandbox": False}, uid)

    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert execution_tenant_id(uid) is None
        assert current_agent_id.get() is None
    finally:
        released.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("state", ["running", "exited", "missing", "foreign"])
def test_sandbox_probe_only_inspects_current_user(monkeypatch, state):
    user = str(uuid4())
    manager = SimpleNamespace(_name=lambda uid: f"sandbox-{uid}", _namespace="deployment")
    container = SimpleNamespace(
        id="1234567890abcdef", status=state,
        labels={"aio.user_id": user, "aio.namespace": "deployment", "aio.ephemeral": "true"},
        attrs={"Config": {"Image": "sandbox:test"}},
    )
    client = Mock()
    client.ping.return_value = True
    client.containers.get.return_value = container
    if state == "missing":
        client.containers.get.side_effect = NotFound("missing")
    elif state == "foreign":
        container.labels["aio.user_id"] = "someone-else"
    connect = Mock(return_value=client)
    monkeypatch.setattr(runtime_info.docker, "from_env", connect)
    result = runtime_info.inspect_sandbox(manager, user)
    assert result["docker"] == "reachable"
    assert result["execution_verified"] is False
    expected = {"missing": "not_created", "foreign": "unknown"}.get(state, state)
    assert result["status"] == expected
    if state == "foreign":
        assert "container_id" not in result
        assert "image" not in result
    connect.assert_called_once_with(timeout=1)
    client.containers.get.assert_called_once_with(f"sandbox-{user}")
    client.containers.create.assert_not_called()
    client.containers.list.assert_not_called()
    client.close.assert_called_once()


async def test_docker_failure_does_not_hide_identity_or_leak_url(executor, monkeypatch):
    executor.sandbox_mgr = object()
    monkeypatch.setattr(runtime_info.docker, "from_env", Mock(
        side_effect=DockerException("https://username:password@private.example"),
    ))
    result = await query(executor)
    assert result.success
    data = json.loads(result.output)
    assert data["device"]["name"]
    assert data["sandbox"]["docker"] == "unavailable"
    assert "password" not in result.output


def test_diagnostic_close_failure_keeps_result(monkeypatch):
    client = Mock()
    client.containers.get.side_effect = NotFound("missing")
    client.close.side_effect = OSError("close failed")
    monkeypatch.setattr(runtime_info.docker, "from_env", lambda **kwargs: client)
    manager = SimpleNamespace(_name=lambda _: "sandbox")
    assert runtime_info.inspect_sandbox(manager, "user")["status"] == "not_created"
