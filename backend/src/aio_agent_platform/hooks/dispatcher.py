"""Hook 动作执行 — webhook / sandbox_command 分发，超时与重试。"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import structlog

from aio_agent_platform.core.config import settings
from aio_agent_platform.hooks.signing import sign_hook

if TYPE_CHECKING:
    from aio_agent_platform.hooks.manager import HookDef

logger = structlog.get_logger(__name__)

BACKOFF_SECONDS = (1.0, 2.0, 4.0)


@dataclass
class ActionResult:
    """单次 Hook 动作执行结果。"""

    status: str  # success / failed / timeout / skipped
    duration_ms: int = 0
    http_status: int | None = None
    exit_code: int | None = None
    error: str | None = None
    response_preview: str | None = None


def validate_url(url: str, *, allow_private: bool = False, allowlist: str = "") -> None:
    """SSRF 防护：拒绝环回/私网/链路本地/保留地址；白名单域名优先。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"webhook 仅支持 http/https，收到: {parsed.scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise ValueError("webhook URL 缺少主机名")

    allow_domains = {d.strip().lower() for d in allowlist.split(",") if d.strip()}
    if allow_domains and host.lower() in allow_domains:
        return
    if allow_private:
        return

    try:
        infos = socket.getaddrinfo(host, parsed.port or 80, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError(f"无法解析 webhook 域名: {host}") from None

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"webhook 目标为内网/保留地址，已拦截: {host} ({ip})")


async def execute(
    hook: HookDef,
    payload: dict,
    *,
    sandbox_mgr=None,
    transport: httpx.AsyncBaseTransport | None = None,
    ctx: dict | None = None,
) -> ActionResult:
    """执行 Hook 动作（负载已在入队前脱敏）。"""
    t_start = time.monotonic()
    ctx = ctx or {}

    async def _run() -> ActionResult:
        if hook.action_type == "webhook":
            return await _webhook(hook, payload, transport=transport)
        if hook.action_type == "sandbox_command":
            if sandbox_mgr is None:
                return ActionResult(status="failed", error="sandbox_mgr 未初始化，无法执行沙箱命令")
            return await _sandbox_command(hook, payload, sandbox_mgr, ctx)
        return ActionResult(status="failed", error=f"未知动作类型: {hook.action_type}")

    timeout_s = (hook.timeout_ms or 5000) / 1000.0
    try:
        result = await asyncio.wait_for(_run(), timeout=timeout_s)
    except TimeoutError:
        result = ActionResult(status="timeout", error=f"动作超过 {timeout_s:.1f}s 超时")
    except ValueError as e:  # SSRF 拦截等配置错误
        result = ActionResult(status="failed", error=str(e))
    except Exception as e:  # 网络/执行错误，失败静默
        logger.warning("hook_action_failed", hook_id=str(hook.id), error=str(e))
        result = ActionResult(status="failed", error=str(e))
    result.duration_ms = int((time.monotonic() - t_start) * 1000)
    return result


async def _webhook(
    hook: HookDef,
    payload: dict,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ActionResult:
    url = (hook.config or {}).get("url", "")
    validate_url(
        url,
        allow_private=settings.hook.allow_private_urls,
        allowlist=settings.hook.url_allowlist,
    )
    headers = {str(k): str(v) for k, v in (hook.config.get("headers") or {}).items()}
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(body) > settings.hook.max_payload_bytes:
        raise ValueError(f"webhook 负载超过 {settings.hook.max_payload_bytes} 字节上限")

    if hook.config.get("sign") and hook.config.get("secret"):
        headers["X-Hook-Signature"] = sign_hook(body, hook.config["secret"])
    headers["X-Hook-Event"] = payload.get("event", "")

    timeout = httpx.Timeout(hook.timeout_ms / 1000.0)
    attempts = (hook.retry_count or 0) + 1
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        for attempt in range(attempts):
            try:
                resp = await client.post(url, content=body, headers=headers)
                preview = resp.text[:1000] if resp.text else ""
                if resp.status_code < 400:
                    return ActionResult(
                        status="success",
                        http_status=resp.status_code,
                        response_preview=preview or None,
                    )
                if attempt < attempts - 1:
                    await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                return ActionResult(
                    status="failed",
                    http_status=resp.status_code,
                    response_preview=preview or None,
                    error=f"HTTP {resp.status_code}",
                )
            except httpx.HTTPError:
                if attempt < attempts - 1:
                    await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                raise


async def _sandbox_command(hook: HookDef, payload: dict, sandbox_mgr, ctx: dict) -> ActionResult:
    cmd = (hook.config or {}).get("command", "")
    if not cmd:
        return ActionResult(status="failed", error="sandbox_command 缺少 command")
    user_id = ctx.get("user_id") or payload.get("user_id") or "unknown"
    session_id = ctx.get("session_id") or payload.get("session_id") or user_id
    workspace_id = ctx.get("workspace_id") or user_id
    workspace_slug = ctx.get("workspace_slug") or "default"

    # 负载经 base64 写入容器 /tmp/hook_payload.json，避免 shell 拼接注入
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False, default=str).encode()).decode()
    full = f"echo '{b64}' | base64 -d > /tmp/hook_payload.json && {cmd}"

    sandbox = await sandbox_mgr.get_or_create(user_id, session_id, workspace_id, workspace_slug)
    result = await sandbox_mgr.execute(sandbox, full, timeout=(hook.timeout_ms or 5000) // 1000)
    if result.exit_code == 0:
        return ActionResult(
            status="success",
            exit_code=0,
            response_preview=(result.stdout or "")[:1000] or None,
        )
    return ActionResult(
        status="failed",
        exit_code=result.exit_code,
        error=(result.stderr or result.stdout or "")[:1000],
    )
