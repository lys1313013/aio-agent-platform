"""Hook 机制单元测试：脱敏、签名、SSRF、作用域匹配、调度、动作执行、递归防护。

纯单元测试：不依赖数据库（直接构造 HookDef / MockTransport）。
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from aio_agent_platform.core.config import settings
from aio_agent_platform.hooks import dispatcher
from aio_agent_platform.hooks.manager import HookDef, HookManager, _hook_action_ctx
from aio_agent_platform.hooks.masking import mask_payload
from aio_agent_platform.hooks.signing import sign_hook, verify_hook_signature

T = uuid.uuid4


def _def(
    *,
    event: str = "PostToolUse",
    scope: str = "global",
    tenant_id=None,
    agent_id=None,
    action_type: str = "webhook",
    config: dict | None = None,
    timeout_ms: int = 5000,
    retry_count: int = 1,
) -> HookDef:
    return HookDef(
        id=uuid.uuid4(),
        name="t",
        scope=scope,
        tenant_id=tenant_id,
        agent_id=agent_id,
        event=event,
        action_type=action_type,
        config=config or {"url": "https://example.com/hook"},
        timeout_ms=timeout_ms,
        retry_count=retry_count,
    )


# ---- masking ----


def test_mask_payload_removes_sensitive_keys_recursively():
    payload = {
        "data": {
            "arguments": {
                "content": "hello",
                "api_key": "sk-123",
                "nested": {"password": "pw", "ok": 1},
                "list": ["a", {"token": "t"}],
            },
            "authorization": "Bearer xxx",
        }
    }
    out = mask_payload(payload)
    assert out["data"]["arguments"]["content"] == "hello"
    assert out["data"]["arguments"]["api_key"] == "***"
    assert out["data"]["arguments"]["nested"]["password"] == "***"
    assert out["data"]["arguments"]["nested"]["ok"] == 1
    assert out["data"]["arguments"]["list"][1]["token"] == "***"
    assert out["data"]["authorization"] == "***"


# ---- signing ----


def test_sign_hook_is_deterministic_and_verifies():
    body = b'{"event":"PostToolUse"}'
    s1 = sign_hook(body, "secret-abc")
    s2 = sign_hook(body, "secret-abc")
    assert s1 == s2
    assert verify_hook_signature(body, "secret-abc", s1)
    assert not verify_hook_signature(body, "wrong-secret", s1)


# ---- SSRF ----


def test_validate_url_blocks_loopback():
    with pytest.raises(ValueError):
        dispatcher.validate_url("http://127.0.0.1:8080/x")


def test_validate_url_blocks_private_ip():
    with pytest.raises(ValueError):
        dispatcher.validate_url("http://192.168.1.1/x")


def test_validate_url_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        dispatcher.validate_url("ftp://example.com/x")


def test_validate_url_allowlist_bypasses():
    dispatcher.validate_url("http://example.com/x", allowlist="example.com")


def test_validate_url_allow_private_bypasses():
    dispatcher.validate_url("http://127.0.0.1:8080/x", allow_private=True)


# ---- manager matching ----


def test_matching_scopes():
    mgr = HookManager()
    tenant = uuid.uuid4()
    agent = uuid.uuid4()

    g = _def(scope="global")
    t = _def(scope="tenant", tenant_id=tenant)
    a = _def(scope="agent", agent_id=agent)

    assert mgr._matches(g, {"tenant_id": str(tenant), "agent_id": str(agent)})
    assert not mgr._matches(t, {})  # 无 tenant 上下文，租户级不命中
    assert mgr._matches(t, {"tenant_id": str(tenant)})
    assert not mgr._matches(t, {"tenant_id": str(uuid.uuid4())})
    assert mgr._matches(a, {"agent_id": str(agent)})
    assert not mgr._matches(a, {"agent_id": str(uuid.uuid4())})


def test_build_ctx_without_context_returns_none_not_str():
    mgr = HookManager()
    ctx = mgr._build_ctx({})
    assert ctx["session_id"] is None
    assert ctx["tenant_id"] is None
    assert ctx["user_id"] is None


def test_is_missing_table_detects_undefined_table():
    from sqlalchemy.exc import ProgrammingError

    from aio_agent_platform.hooks.manager import _is_missing_table

    err = ProgrammingError("stmt", {}, Exception('relation "hooks" does not exist'))
    assert _is_missing_table(err)
    assert not _is_missing_table(Exception("boom"))
    assert not _is_missing_table(ProgrammingError("stmt", {}, Exception("connection reset")))


# ---- dispatch / enqueue ----


def test_fire_enqueues_matched_hook_and_masks_payload():
    mgr = HookManager()
    mgr._started = True
    h = _def(event="PostToolUse", scope="global")
    mgr._hooks = [h]

    mgr.fire_nowait(
        "PostToolUse",
        user_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
        data={"tool_name": "run_shell", "arguments": {"command": "x", "token": "secret"}},
    )
    hook, payload, _ = mgr._queue.get_nowait()

    assert hook.id == h.id
    assert payload["event"] == "PostToolUse"
    assert payload["event_id"]
    assert payload["data"]["tool_name"] == "run_shell"
    assert payload["data"]["arguments"]["token"] == "***"  # 入队前已脱敏
    assert payload["data"]["arguments"]["command"] == "x"
    assert payload["user_id"]


def test_fire_skips_when_recursion_guard_active():
    mgr = HookManager()
    mgr._started = True
    mgr._hooks = [_def(event="SessionStart")]
    token = _hook_action_ctx.set(1)
    try:
        mgr.fire_nowait("SessionStart")
        assert mgr._queue.empty()
    finally:
        _hook_action_ctx.reset(token)


def test_fire_respects_scope():
    mgr = HookManager()
    mgr._started = True
    tenant = uuid.uuid4()
    agent = uuid.uuid4()
    mgr._hooks = [
        _def(event="SessionEnd", scope="global"),
        _def(event="SessionEnd", scope="tenant", tenant_id=tenant),
        _def(event="SessionEnd", scope="agent", agent_id=agent),
    ]
    # 只有 tenant 命中（ctx 带 tenant，无 agent）
    mgr.fire_nowait("SessionEnd", tenant_id=str(tenant), agent_id=None)
    matched = 0
    while not mgr._queue.empty():
        mgr._queue.get_nowait()
        matched += 1
    assert matched == 2  # global + tenant


# ---- webhook action execution ----


@pytest.mark.asyncio
async def test_webhook_action_posts_payload(monkeypatch):
    monkeypatch.setattr(settings.hook, "allow_private_urls", True)
    received: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["headers"] = dict(request.headers)
        received["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    hook = _def(
        event="PostToolUse",
        config={"url": "http://127.0.0.1:1/hook", "headers": {"X-Custom": "v"}},
    )
    result = await dispatcher.execute(hook, {"event": "PostToolUse", "data": {}}, transport=transport)

    assert result.status == "success"
    assert result.http_status == 200
    assert received["url"] == "http://127.0.0.1:1/hook"
    assert received["headers"]["x-custom"] == "v"
    assert received["headers"]["x-hook-event"] == "PostToolUse"
    assert received["body"]["event"] == "PostToolUse"


@pytest.mark.asyncio
async def test_webhook_action_signs_body(monkeypatch):
    monkeypatch.setattr(settings.hook, "allow_private_urls", True)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["signature"] = request.headers.get("x-hook-signature")
        captured["body"] = request.content
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    hook = _def(
        config={
            "url": "http://127.0.0.1:1/hook",
            "sign": True,
            "secret": "sec",
        }
    )
    await dispatcher.execute(hook, {"event": "PostToolUse", "data": {}}, transport=transport)

    assert captured["signature"] == sign_hook(captured["body"], "sec")


@pytest.mark.asyncio
async def test_webhook_action_retries_on_5xx(monkeypatch):
    monkeypatch.setattr(settings.hook, "allow_private_urls", True)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    hook = _def(config={"url": "http://127.0.0.1:1/hook"}, retry_count=2)
    result = await dispatcher.execute(hook, {"event": "PostToolUse", "data": {}}, transport=transport)

    assert result.status == "failed"
    assert result.http_status == 500
    assert attempts["n"] == 3  # 初始 1 次 + 重试 2 次


@pytest.mark.asyncio
async def test_webhook_action_rejects_ssrf_url():
    hook = _def(config={"url": "http://127.0.0.1:1/hook"})
    result = await dispatcher.execute(hook, {"event": "PostToolUse", "data": {}})
    assert result.status == "failed"
    assert "内网" in (result.error or "")


@pytest.mark.asyncio
async def test_sandbox_command_requires_manager():
    hook = _def(action_type="sandbox_command", config={"command": "true"})
    result = await dispatcher.execute(hook, {"event": "PostToolUse", "data": {}})
    assert result.status == "failed"
    assert "sandbox_mgr" in (result.error or "")


# ---- logging ----


def test_log_skip_pre_events_by_default():
    mgr = HookManager()
    assert not mgr._should_log("PreToolUse")
    assert mgr._should_log("PostToolUse")
    assert mgr._should_log("SessionEnd")


def test_log_fields_masks_url_query():
    hook = _def(config={"url": "https://example.com/hook?token=abc"})
    fields = HookManager._log_fields(
        hook, {"event": "PostToolUse"}, {}, dispatcher.ActionResult(status="success")
    )
    assert fields["target"] == "https://example.com/hook?***"
    assert "token=abc" not in fields["target"]
