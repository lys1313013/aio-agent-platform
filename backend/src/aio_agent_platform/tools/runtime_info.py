"""Read-only backend identity and diagnostics, independent of sandbox execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import UUID, uuid4

import docker
from docker.errors import NotFound
from pydantic import BaseModel, ConfigDict, StrictBool

from aio_agent_platform.sandbox.models import docker_unavailable_reason

_INSTANCE_ID = str(uuid4())
_INITIALIZED_AT = datetime.now(UTC).isoformat()
_INITIALIZED_MONOTONIC = time.monotonic()


class RuntimeInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_resources: StrictBool = False
    check_sandbox: StrictBool = True


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def _mac_value(command: list[str]) -> str:
    # Fixed read-only commands only; arguments never come from the model.
    return subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=1,
    ).stdout.strip()


def _container_evidence() -> list[str]:
    evidence = [path for path in ("/.dockerenv", "/run/.containerenv") if Path(path).exists()]
    try:
        cgroup = _read_text("/proc/self/cgroup")
        if any(marker in cgroup for marker in ("docker", "kubepods", "containerd", "libpod")):
            evidence.append("/proc/self/cgroup")
    except OSError:
        pass
    return evidence


def _resources(system: str) -> dict:
    result: dict = {
        "scope": "backend_os_view",
        "note": "容器中可能显示宿主机资源；这些数值不代表容器配额或沙箱资源。",
        "logical_cpu_count": os.cpu_count(),
        "process_cpu_count": os.process_cpu_count(),
    }
    try:
        result["load_average_1_5_15"] = list(os.getloadavg())
    except OSError:
        result["load_average_1_5_15"] = None
    try:
        disk = shutil.disk_usage(Path.cwd())
        result["disk"] = {
            "scope": "filesystem_containing_backend_working_directory",
            "total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free,
        }
    except OSError:
        result["disk"] = {"status": "unknown"}
    try:
        if system == "Linux":
            mem = {}
            for line in _read_text("/proc/meminfo").splitlines():
                key, value = line.split(":", 1)
                if key in {"MemTotal", "MemAvailable"}:
                    mem[key] = int(value.strip().split()[0]) * 1024
            result["memory"] = {
                "source": "/proc/meminfo", "total_bytes": mem.get("MemTotal"),
                "available_bytes": mem.get("MemAvailable"),
            }
        elif system == "Darwin":
            result["memory"] = {
                "source": "sysctl hw.memsize",
                "total_bytes": int(_mac_value(["/usr/sbin/sysctl", "-n", "hw.memsize"])),
                "available_bytes": None,
            }
        else:
            result["memory"] = {"status": "unknown"}
    except (OSError, ValueError, subprocess.SubprocessError):
        result["memory"] = {"status": "unknown"}
    return result


def collect_runtime_info(include_resources: bool = False) -> dict:
    """Collect only allowlisted fields; never enumerate env vars or network interfaces."""
    system = platform.system()
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = None
    device_name = hostname
    device_name_source = "hostname"
    if system == "Darwin":
        try:
            computer_name = _mac_value(["/usr/sbin/scutil", "--get", "ComputerName"])
            if computer_name:
                device_name = computer_name
                device_name_source = "macos_computer_name"
        except (OSError, subprocess.SubprocessError):
            pass
    os_name = "macOS" if system == "Darwin" else system
    os_version = platform.mac_ver()[0] if system == "Darwin" else platform.release()
    if system == "Linux":
        try:
            release = platform.freedesktop_os_release()
            os_name = release.get("PRETTY_NAME", system)
            os_version = release.get("VERSION_ID", os_version)
        except OSError:
            pass
    evidence = _container_evidence()
    now = datetime.now().astimezone()
    try:
        app_version = version("aio-agent-platform")
    except PackageNotFoundError:
        app_version = None
    result = {
        "collected_at": now.isoformat(),
        "device": {
            "name": device_name, "name_source": device_name_source, "hostname": hostname,
            "id": hashlib.sha256(hostname.encode()).hexdigest()[:16] if hostname else None,
            "id_source": "hostname_sha256_not_hardware_id",
        },
        "os": {
            "system": system, "name": os_name, "version": os_version,
            "kernel": platform.release(), "architecture": platform.machine(),
        },
        "runtime": {
            "location": "backend", "python_version": platform.python_version(),
            "platform_version": app_version, "pid": os.getpid(),
            "instance_id": f"{_INSTANCE_ID}:{os.getpid()}",
            "initialized_at": _INITIALIZED_AT,
            "uptime_seconds": round(time.monotonic() - _INITIALIZED_MONOTONIC, 1),
            "container": {"detected": bool(evidence), "evidence": evidence},
            "note": (
                "信息来自执行智能体的后端进程环境，不是浏览器设备或模型服务。"
                "未检测到容器标记不保证未容器化；容器内无法可靠确定物理宿主机名称。"
            ),
        },
        "time": {"timezone_name": now.tzname(), "utc_offset": now.strftime("%z")},
    }
    if include_resources:
        result["resources"] = _resources(system)
    return result


def inspect_sandbox(manager, user_id: str) -> dict:
    """Probe Docker with a separate short-timeout client; never create/adopt containers."""
    if manager is None:
        return {
            "docker": "unknown", "status": "unavailable", "execution_verified": False,
            "reason": "未初始化沙箱管理器",
        }
    client = None
    try:
        client = docker.from_env(timeout=1)
        if not client.ping():
            return {
                "docker": "unavailable", "status": "unknown", "execution_verified": False,
                "reason": "Docker 未响应",
            }
        result = {"docker": "reachable", "execution_verified": False}
        try:
            container = client.containers.get(manager._name(user_id))
        except NotFound:
            return {**result, "status": "not_created"}
        labels = container.labels
        if (
            labels.get("aio.user_id") != user_id
            or labels.get("aio.namespace") != manager._namespace
            or labels.get("aio.ephemeral") != "true"
        ):
            return {**result, "status": "unknown", "reason": "沙箱归属标记不匹配"}
        return {
            **result, "status": container.status, "container_id": container.id[:12],
            "image": container.attrs.get("Config", {}).get("Image"),
            "note": "仅检查连接及当前用户沙箱状态，未验证代码执行；Docker 可能运行于远程节点。",
        }
    except Exception as exc:
        return {
            "docker": "unavailable", "status": "unknown", "execution_verified": False,
            "reason": docker_unavailable_reason(exc),
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass  # Closing a diagnostic client must not hide the collected data.


async def handle_get_runtime_info(
    arguments: dict, user_id: str, session_id: str, *, tool_executor=None,
    workspace_id: str | None = None, **kwargs,
) -> str:
    from aio_agent_platform.core.context import current_agent_id
    from aio_agent_platform.core.execution_scope import execution_tenant_id

    request = RuntimeInfoInput.model_validate(arguments)
    # Capture pinned scope in this task. Never resolve ownership from users.tenant_id.
    tenant_id = execution_tenant_id(UUID(user_id))
    data = await asyncio.to_thread(collect_runtime_info, request.include_resources)
    data["execution"] = {
        "tenant_id": str(tenant_id) if tenant_id is not None else None,
        "agent_id": current_agent_id.get(), "session_id": session_id,
        "workspace_id": workspace_id,
    }
    if request.check_sandbox:
        data["sandbox"] = await asyncio.to_thread(
            inspect_sandbox, getattr(tool_executor, "sandbox_mgr", None), user_id,
        )
    return json.dumps(data, ensure_ascii=False)
