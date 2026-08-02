"""飞书渠道单元测试。

覆盖上一个提交 (feat: 飞书渠道接入) 的核心模块：
- crypto: webhook 验签 / 事件解密
- events: 飞书事件 → InboundEvent 归一化
- pipeline: 事件去重、长文本切分
- binding: 外部用户解析（不建影子账号）、绑定码签发/消费/解绑（DB 测试无库时自动 skip）
- client: tenant_access_token 缓存与消息 API（MockTransport）
- adapter: 回复语义 / 卡片降级 / 表情指示
- webhook_transport: challenge、验签、解密、事件分发
- ws_transport: 帧处理、ACK、分片重组
- connection_manager: 渠道生命周期
"""

import base64
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from aio_agent_platform.channels.adapter import ChatKind, InboundEvent
from aio_agent_platform.channels.binding import (
    BindCodeInvalid,
    BindCodeRateLimited,
    _generate_code,
    consume_bind_code,
    issue_bind_code,
    resolve_external_user,
    unbind_external,
)
from aio_agent_platform.channels.connection_manager import ChannelConnectionManager
from aio_agent_platform.channels.feishu.adapter import FeishuAdapter
from aio_agent_platform.channels.feishu.client import FeishuClient
from aio_agent_platform.channels.feishu.crypto import decrypt_event, verify_signature
from aio_agent_platform.channels.feishu.events import normalize_event
from aio_agent_platform.channels.feishu.webhook_transport import (
    FeishuWebhookTransport,
    _webhook_registry,
    build_webhook_router,
    register_webhook,
)
from aio_agent_platform.channels.feishu.ws_transport import FeishuWebSocketTransport
from aio_agent_platform.channels.pipeline import _dedup, _event_seen, _split_text
from aio_agent_platform.db import Session as ChatSession
from aio_agent_platform.db.models import ChannelBinding, User

BOT_APP_ID = "cli_bot_app_id"


# ---------------------------------------------------------------------------
# crypto
# ---------------------------------------------------------------------------


def _sign(token: str, timestamp: str, nonce: str, body: bytes) -> str:
    content = timestamp + nonce + token + body.decode("utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello":"world"}'
    sig = _sign("tok", "1700000000", "nonce1", body)
    assert verify_signature("tok", "1700000000", "nonce1", body, sig) is True


def test_verify_signature_rejects_tampered() -> None:
    body = b'{"hello":"world"}'
    sig = _sign("tok", "1700000000", "nonce1", body)
    assert verify_signature("tok", "1700000000", "nonce1", b'{"hello":"evil"}', sig) is False
    assert verify_signature("other-token", "1700000000", "nonce1", body, sig) is False
    assert verify_signature("tok", "1700000001", "nonce1", body, sig) is False


def _encrypt(encrypt_key: str, payload: dict) -> str:
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = os.urandom(16)
    plaintext = json.dumps(payload).encode("utf-8")
    pad_len = 16 - len(plaintext) % 16
    plaintext += bytes([pad_len]) * pad_len
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("utf-8")


def test_decrypt_event_round_trip() -> None:
    payload = {"header": {"event_id": "evt_1"}, "event": {"foo": "bar"}}
    encrypted = _encrypt("my-encrypt-key", payload)
    assert decrypt_event("my-encrypt-key", encrypted) == payload


def test_decrypt_event_wrong_key_fails() -> None:
    encrypted = _encrypt("key-a", {"x": 1})
    # 错误密钥解密后 JSON 解析失败（或 PKCS#7 padding 校验失败）
    with pytest.raises((ValueError, json.JSONDecodeError)):
        decrypt_event("key-b", encrypted)


# ---------------------------------------------------------------------------
# events — normalize_event
# ---------------------------------------------------------------------------


def _make_event(
    *,
    text: str = "hello",
    chat_type: str = "p2p",
    message_type: str = "text",
    mentions: list | None = None,
    sender_open_id: str = "ou_user1",
) -> dict:
    message: dict = {
        "chat_id": "oc_chat1",
        "chat_type": chat_type,
        "message_id": "om_1",
        "message_type": message_type,
        "content": json.dumps({"text": text}),
    }
    if mentions is not None:
        message["mentions"] = mentions
    return {
        "event": {
            "message": message,
            "sender": {"sender_id": {"open_id": sender_open_id}},
        }
    }


def test_normalize_direct_text_message() -> None:
    channel_id = uuid4()
    inbound = normalize_event(channel_id, "evt_1", _make_event(), BOT_APP_ID)
    assert inbound is not None
    assert inbound.channel_id == channel_id
    assert inbound.event_id == "evt_1"
    assert inbound.chat_id == "oc_chat1"
    assert inbound.external_id == "ou_user1"
    assert inbound.text == "hello"
    assert inbound.chat_kind == ChatKind.DIRECT
    assert inbound.message_id == "om_1"
    assert inbound.mentions_bot is False


def test_normalize_skips_non_text() -> None:
    event = _make_event(message_type="image")
    assert normalize_event(uuid4(), "evt_2", event, BOT_APP_ID) is None


def test_normalize_group_message_with_bot_mention() -> None:
    mentions = [
        {"id": {"open_id": "ou_bot"}, "key": "@_user_1", "name": "Bot"},
    ]
    event = _make_event(text="@_user_1 你好", chat_type="group", mentions=mentions)
    inbound = normalize_event(uuid4(), "evt_3", event, bot_app_id="ou_bot")
    assert inbound is not None
    assert inbound.chat_kind == ChatKind.GROUP
    assert inbound.mentions_bot is True
    # @ 占位符被剥离，Agent 看到干净文本
    assert inbound.text == "你好"


def test_normalize_group_message_without_mention() -> None:
    event = _make_event(chat_type="group")
    inbound = normalize_event(uuid4(), "evt_4", event, BOT_APP_ID)
    assert inbound is not None
    assert inbound.chat_kind == ChatKind.GROUP
    assert inbound.mentions_bot is False


def test_normalize_missing_sender_returns_none() -> None:
    event = _make_event(sender_open_id="")
    assert normalize_event(uuid4(), "evt_5", event, BOT_APP_ID) is None


def test_normalize_malformed_content_yields_empty_text() -> None:
    event = _make_event()
    event["event"]["message"]["content"] = "not-json"
    inbound = normalize_event(uuid4(), "evt_6", event, BOT_APP_ID)
    assert inbound is not None
    assert inbound.text == ""


# ---------------------------------------------------------------------------
# pipeline — dedup & text splitting
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_dedup_cache():
    _event_seen.clear()
    yield
    _event_seen.clear()


def test_dedup_detects_duplicates() -> None:
    assert _dedup("evt_a") is False
    assert _dedup("evt_a") is True
    assert _dedup("evt_b") is False


def test_dedup_expires_old_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _dedup("evt_old") is False
    # 快进 2 小时，超过 1h TTL；触发 pruning 需要 >10000 条，
    # 直接塞入过期时间戳模拟。
    _event_seen["evt_old"] = time.monotonic() - 7200
    for i in range(10_001):
        _event_seen[f"filler_{i}"] = time.monotonic() - 7200
    assert _dedup("evt_new") is False
    # 过期条目被清理，evt_old 不再被视为重复
    assert "evt_old" not in _event_seen


def test_split_text_short_text_unchanged() -> None:
    assert _split_text("hello", 100) == ["hello"]


def test_split_text_prefers_newline_boundary() -> None:
    text = "aaaa\nbbbb\ncccc"
    chunks = _split_text(text, 6)
    assert chunks == ["aaaa", "bbbb", "cccc"]


def test_split_text_falls_back_to_space_then_hard_cut() -> None:
    chunks = _split_text("aa bb cc dd", 5)
    assert "".join(c.replace(" ", "") for c in chunks) == "aabbccdd"
    # 无空格无换行时硬切
    chunks = _split_text("x" * 25, 10)
    assert chunks == ["x" * 10, "x" * 10, "x" * 5]


# ---------------------------------------------------------------------------
# binding — pure helpers
# ---------------------------------------------------------------------------


def test_generate_code_is_6_digits() -> None:
    for _ in range(20):
        code = _generate_code()
        assert len(code) == 6
        assert code.isdigit()


# ---------------------------------------------------------------------------
# FeishuClient — mocked HTTP transport
# ---------------------------------------------------------------------------


class _FeishuAPIStub:
    """MockTransport handler 模拟飞书 Open API。"""

    def __init__(self) -> None:
        self.token_calls = 0
        self.token_code = 0
        self.send_code = 0
        self.update_code = 0
        self.reaction_code = 0
        self.last_send_url: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "tenant_access_token" in path:
            self.token_calls += 1
            if self.token_code != 0:
                return httpx.Response(200, json={"code": self.token_code, "msg": "bad credentials"})
            return httpx.Response(200, json={
                "code": 0,
                "tenant_access_token": f"tok-{self.token_calls}",
                "expire": 7200,
            })
        if path.endswith("/reactions") and request.method == "POST":
            return httpx.Response(200, json={"code": self.reaction_code, "data": {"reaction_id": "r_1"}})
        if "/reactions/" in path and request.method == "DELETE":
            return httpx.Response(200, json={"code": self.reaction_code, "data": {}})
        if request.method == "PUT":
            return httpx.Response(200, json={"code": self.update_code, "data": {}})
        if request.method == "POST" and "/messages" in path:
            self.last_send_url = str(request.url)
            return httpx.Response(200, json={"code": self.send_code, "data": {"message_id": "om_123"}})
        return httpx.Response(404, json={"code": 404})


@pytest.fixture
async def feishu_stub():
    stub = _FeishuAPIStub()
    client = FeishuClient("cli_test", "secret_test")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    yield client, stub
    await client.close()


async def test_token_cached_across_calls(feishu_stub) -> None:
    client, stub = feishu_stub
    assert await client.send_text("oc_1", "hi") == "om_123"
    assert await client.send_text("oc_1", "hi again") == "om_123"
    assert stub.token_calls == 1


async def test_token_refreshed_after_expiry(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_text("oc_1", "hi")
    client._token_expires_at = time.monotonic() - 1  # 强制过期
    await client.send_text("oc_1", "hi")
    assert stub.token_calls == 2


async def test_send_failure_returns_none(feishu_stub) -> None:
    client, stub = feishu_stub
    stub.send_code = 230002
    assert await client.send_text("oc_1", "hi") is None


async def test_reply_uses_reply_endpoint(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_text("oc_1", "hi", reply_to="om_parent")
    assert stub.last_send_url is not None
    assert "/messages/om_parent/reply" in stub.last_send_url


async def test_verify_credentials(feishu_stub) -> None:
    client, stub = feishu_stub
    assert await client.verify_credentials() is True
    stub.token_code = 10003
    client2 = FeishuClient("cli_bad", "bad")
    client2._http = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    assert await client2.verify_credentials() is False
    await client2.close()


async def test_update_message(feishu_stub) -> None:
    client, stub = feishu_stub
    assert await client.update_message("om_1", "new text") is True
    stub.update_code = 230001
    assert await client.update_message("om_1", "new text") is False


async def test_reactions(feishu_stub) -> None:
    client, stub = feishu_stub
    assert await client.add_reaction("om_1", "Typing") == "r_1"
    assert await client.delete_reaction("om_1", "r_1") is True
    stub.reaction_code = 123
    assert await client.add_reaction("om_1", "Typing") is None
    assert await client.delete_reaction("om_1", "r_1") is False


# ---------------------------------------------------------------------------
# FeishuAdapter — 出站语义
# ---------------------------------------------------------------------------


def _make_adapter() -> tuple[FeishuAdapter, AsyncMock]:
    client = AsyncMock(spec=FeishuClient)
    adapter = FeishuAdapter(channel_id=uuid4(), client=client, pipeline=MagicMock())
    return adapter, client


def _make_event_obj(*, mentions_bot: bool = False, message_id: str | None = "om_in") -> InboundEvent:
    return InboundEvent(
        channel_id=uuid4(),
        event_id="evt",
        chat_id="oc_chat",
        external_id="ou_user",
        text="hi",
        chat_kind=ChatKind.GROUP if mentions_bot else ChatKind.DIRECT,
        message_id=message_id,
        mentions_bot=mentions_bot,
    )


async def test_adapter_send_replies_when_mentioned() -> None:
    adapter, client = _make_adapter()
    client.send_text.return_value = "om_out"
    event = _make_event_obj(mentions_bot=True)
    result = await adapter.send(event, "回复")
    assert result == "om_out"
    client.send_text.assert_awaited_once_with(
        receive_id="oc_chat", text="回复", reply_to="om_in"
    )


async def test_adapter_send_no_reply_in_direct_chat() -> None:
    adapter, client = _make_adapter()
    event = _make_event_obj(mentions_bot=False)
    await adapter.send(event, "回复")
    client.send_text.assert_awaited_once_with(
        receive_id="oc_chat", text="回复", reply_to=None
    )


async def test_adapter_send_markdown_uses_card() -> None:
    adapter, client = _make_adapter()
    client.send_card_markdown.return_value = "om_card"
    result = await adapter.send_markdown(_make_event_obj(), "**md**")
    assert result == "om_card"
    client.send_text.assert_not_awaited()


async def test_adapter_send_markdown_falls_back_to_text() -> None:
    adapter, client = _make_adapter()
    client.send_card_markdown.return_value = None  # 卡片发送失败
    client.send_text.return_value = "om_text"
    result = await adapter.send_markdown(_make_event_obj(), "**md**")
    assert result == "om_text"
    client.send_text.assert_awaited_once()


async def test_adapter_reaction_requires_message_id() -> None:
    adapter, client = _make_adapter()
    event = _make_event_obj(message_id=None)
    assert await adapter.add_reaction(event, "Typing") is None
    client.add_reaction.assert_not_awaited()
    await adapter.delete_reaction(event, "r_1")
    client.delete_reaction.assert_not_awaited()


async def test_adapter_reaction_delegates_to_client() -> None:
    adapter, client = _make_adapter()
    client.add_reaction.return_value = "r_1"
    event = _make_event_obj()
    assert await adapter.add_reaction(event, "Typing") == "r_1"
    client.add_reaction.assert_awaited_once_with("om_in", "Typing")
    await adapter.delete_reaction(event, "r_1")
    client.delete_reaction.assert_awaited_once_with("om_in", "r_1")


# ---------------------------------------------------------------------------
# webhook_transport — HTTP 路由
# ---------------------------------------------------------------------------


def _make_webhook_app(channel: SimpleNamespace) -> tuple[FastAPI, MagicMock]:
    pipeline = MagicMock()
    register_webhook(channel.channel_key, pipeline, channel)
    app = FastAPI()
    app.include_router(build_webhook_router())
    return app, pipeline


def _message_event_payload(event_id: str = "evt_wh_1", event_type: str = "im.message.receive_v1") -> dict:
    return {
        "header": {"event_type": event_type, "event_id": event_id},
        "event": {
            "message": {
                "chat_id": "oc_wh",
                "chat_type": "p2p",
                "message_id": "om_wh",
                "message_type": "text",
                "content": json.dumps({"text": "webhook hi"}),
            },
            "sender": {"sender_id": {"open_id": "ou_wh_user"}},
        },
    }


@pytest.fixture(autouse=True)
def _clear_webhook_registry():
    _webhook_registry.clear()
    yield
    _webhook_registry.clear()


async def test_webhook_unknown_channel_returns_404() -> None:
    app = FastAPI()
    app.include_router(build_webhook_router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/channels/feishu/events/nope", json={})
    assert resp.status_code == 404


async def test_webhook_invalid_json_returns_400() -> None:
    channel = SimpleNamespace(id=uuid4(), app_id=BOT_APP_ID, channel_key="k400")
    app, _ = _make_webhook_app(channel)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/channels/feishu/events/k400",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 400


async def test_webhook_url_verification_challenge() -> None:
    channel = SimpleNamespace(id=uuid4(), app_id=BOT_APP_ID, channel_key="k_chal")
    app, _ = _make_webhook_app(channel)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/channels/feishu/events/k_chal",
            json={"type": "url_verification", "challenge": "abc123"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


async def test_webhook_challenge_rejects_wrong_token() -> None:
    channel = SimpleNamespace(
        id=uuid4(), app_id=BOT_APP_ID, channel_key="k_chal2", _verification_token="vtok"
    )
    app, _ = _make_webhook_app(channel)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/channels/feishu/events/k_chal2",
            json={"type": "url_verification", "challenge": "abc", "token": "wrong"},
        )
        assert resp.status_code == 401
        resp = await c.post(
            "/api/channels/feishu/events/k_chal2",
            json={"type": "url_verification", "challenge": "abc", "token": "vtok"},
        )
        assert resp.status_code == 200


async def test_webhook_dispatches_message_event() -> None:
    channel = SimpleNamespace(id=uuid4(), app_id=BOT_APP_ID, channel_key="k_evt")
    app, pipeline = _make_webhook_app(channel)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/channels/feishu/events/k_evt", json=_message_event_payload())
    assert resp.status_code == 200
    pipeline.submit.assert_called_once()
    inbound = pipeline.submit.call_args[0][0]
    assert isinstance(inbound, InboundEvent)
    assert inbound.text == "webhook hi"
    assert inbound.event_id == "evt_wh_1"


async def test_webhook_ignores_non_message_events() -> None:
    channel = SimpleNamespace(id=uuid4(), app_id=BOT_APP_ID, channel_key="k_other")
    app, pipeline = _make_webhook_app(channel)
    payload = _message_event_payload(event_type="im.chat.updated_v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/channels/feishu/events/k_other", json=payload)
    assert resp.status_code == 200
    pipeline.submit.assert_not_called()


async def test_webhook_signature_enforced_when_token_set() -> None:
    channel = SimpleNamespace(
        id=uuid4(), app_id=BOT_APP_ID, channel_key="k_sig", _verification_token="vtok"
    )
    app, pipeline = _make_webhook_app(channel)
    body = json.dumps(_message_event_payload()).encode("utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # 缺少签名 → 401
        resp = await c.post(
            "/api/channels/feishu/events/k_sig",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        # 正确签名 → 200
        ts, nonce = "1700000000", "n1"
        sig = _sign("vtok", ts, nonce, body)
        resp = await c.post(
            "/api/channels/feishu/events/k_sig",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lark-Signature": sig,
                "X-Lark-Request-Timestamp": ts,
                "X-Lark-Request-Nonce": nonce,
            },
        )
        assert resp.status_code == 200
    pipeline.submit.assert_called_once()


async def test_webhook_decrypts_encrypted_payload() -> None:
    channel = SimpleNamespace(
        id=uuid4(), app_id=BOT_APP_ID, channel_key="k_enc", _encrypt_key="enc-key-1"
    )
    app, pipeline = _make_webhook_app(channel)
    inner = _message_event_payload(event_id="evt_enc")
    body = json.dumps({"encrypt": _encrypt("enc-key-1", inner)}).encode("utf-8")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/channels/feishu/events/k_enc",
            content=body,
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 200
    pipeline.submit.assert_called_once()
    assert pipeline.submit.call_args[0][0].event_id == "evt_enc"


async def test_webhook_transport_stop_unregisters() -> None:
    pipeline = MagicMock()
    pipeline.channel = SimpleNamespace(channel_key="k_stop")
    register_webhook("k_stop", pipeline, SimpleNamespace())
    transport = FeishuWebhookTransport(pipeline)
    await transport.stop()
    assert "k_stop" not in _webhook_registry
    assert transport.state == "disconnected"


# ---------------------------------------------------------------------------
# ws_transport — 帧处理
# ---------------------------------------------------------------------------


def _make_ws_transport() -> tuple[FeishuWebSocketTransport, MagicMock]:
    pipeline = MagicMock()
    pipeline.channel = SimpleNamespace(id=uuid4())
    return FeishuWebSocketTransport(app_id=BOT_APP_ID, app_secret="s", pipeline=pipeline), pipeline


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


def _build_frame(*, method: int, headers: dict[str, str], payload: bytes) -> bytes:
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame

    frame = Frame()
    frame.method = method
    frame.payload = payload
    frame.SeqID = 0
    frame.LogID = 0
    frame.service = 0
    for k, v in headers.items():
        h = frame.headers.add()
        h.key = k
        h.value = v
    return frame.SerializeToString()


async def test_ws_data_frame_dispatches_and_acks() -> None:
    transport, pipeline = _make_ws_transport()
    ws = _FakeWS()
    payload = json.dumps(_message_event_payload(event_id="evt_ws")).encode()
    raw = _build_frame(method=1, headers={"type": "event", "message_id": "m1"}, payload=payload)
    await transport._handle_frame(raw, ws)

    pipeline.submit.assert_called_once()
    assert pipeline.submit.call_args[0][0].event_id == "evt_ws"

    # ACK：回显帧并附带 {"code": 200} 与 biz_rt 头
    assert len(ws.sent) == 1
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame

    ack = Frame()
    ack.ParseFromString(ws.sent[0])
    assert json.loads(ack.payload) == {"code": 200}
    assert any(h.key == "biz_rt" for h in ack.headers)


async def test_ws_non_event_frame_ignored() -> None:
    transport, pipeline = _make_ws_transport()
    ws = _FakeWS()
    raw = _build_frame(method=1, headers={"type": "something_else"}, payload=b"{}")
    await transport._handle_frame(raw, ws)
    pipeline.submit.assert_not_called()
    assert ws.sent == []


async def test_ws_control_pong_updates_config() -> None:
    transport, _ = _make_ws_transport()
    ws = _FakeWS()
    raw = _build_frame(
        method=0,
        headers={"type": "pong"},
        payload=json.dumps({"PingInterval": 30, "ReconnectInterval": 2}).encode(),
    )
    await transport._handle_frame(raw, ws)
    assert transport._ping_interval == 30
    assert transport._reconnect_interval == 2


async def test_ws_invalid_frame_does_not_crash() -> None:
    transport, pipeline = _make_ws_transport()
    ws = _FakeWS()
    await transport._handle_frame(b"\x08", ws)  # 截断的 protobuf
    pipeline.submit.assert_not_called()
    assert ws.sent == []


def test_ws_fragment_reassembly() -> None:
    transport, _ = _make_ws_transport()
    assert transport._combine_fragment("m1", 2, 0, b"hello ") is None
    assert transport._combine_fragment("m1", 2, 1, b"world") == b"hello world"
    # 重组完成后状态被清理
    assert "m1" not in transport._fragments


def test_ws_fragment_stale_entries_pruned() -> None:
    transport, _ = _make_ws_transport()
    transport._fragments["old"] = {"ts": time.monotonic() - 120, "parts": {0: b"x"}}
    transport._combine_fragment("m2", 1, 0, b"new")
    assert "old" not in transport._fragments


# ---------------------------------------------------------------------------
# connection_manager
# ---------------------------------------------------------------------------


def _make_channel(**overrides) -> SimpleNamespace:
    defaults = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "channel_type": "feishu",
        "name": "测试渠道",
        "agent_id": uuid4(),
        "app_id": "cli_x",
        "app_secret_encrypted": "secret",
        "encrypt_key_encrypted": None,
        "verification_token_encrypted": None,
        "mode": "webhook",
        "status": "enabled",
        "channel_key": f"key_{uuid4().hex[:8]}",
        "tool_blacklist": [],
        "last_error": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def test_manager_rejects_unsupported_channel_type() -> None:
    manager = ChannelConnectionManager(tool_executor=MagicMock())
    with pytest.raises(ValueError, match="Unsupported channel type"):
        await manager.start_channel(_make_channel(channel_type="dingtalk"))


async def test_manager_rejects_invalid_mode() -> None:
    manager = ChannelConnectionManager(tool_executor=MagicMock())
    with pytest.raises(ValueError, match="Invalid channel mode"):
        await manager.start_channel(_make_channel(mode="carrier-pigeon"))


async def test_manager_start_and_stop_webhook_channel() -> None:
    manager = ChannelConnectionManager(tool_executor=MagicMock())
    channel = _make_channel(mode="webhook")
    await manager.start_channel(channel)

    adapter = manager.get_adapter(channel.id)
    assert adapter is not None
    statuses = manager.get_status()
    assert len(statuses) == 1
    assert statuses[0]["mode"] == "webhook"
    assert statuses[0]["transport_state"] == "connected"
    # webhook 路由已注册
    assert channel.channel_key in _webhook_registry

    # 重复 start 是 no-op
    await manager.start_channel(channel)
    assert len(manager.get_status()) == 1

    await manager.stop_channel(channel.id)
    assert manager.get_adapter(channel.id) is None
    assert manager.get_status() == []
    assert channel.channel_key not in _webhook_registry


async def test_manager_stop_unknown_channel_is_noop() -> None:
    manager = ChannelConnectionManager(tool_executor=MagicMock())
    await manager.stop_channel(uuid4())  # 不抛异常


# ---------------------------------------------------------------------------
# binding — DB 集成测试（无数据库时自动 skip，由 conftest 处理）
# ---------------------------------------------------------------------------


async def test_resolve_external_user_unbound_and_bound(db_session) -> None:
    channel_id = uuid4()

    # 无任何绑定 → unbound，且不创建任何账户
    user_id, bind_type = await resolve_external_user(db_session, channel_id, "ou_ext1")
    assert user_id is None
    assert bind_type == "unbound"
    assert (await db_session.execute(select(User))).scalars().all() == []

    # 建立 bound 绑定后 → bound
    real_user = User(username="real_ext", email="real_ext@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()
    db_session.add(ChannelBinding(
        channel_id=channel_id, external_id="ou_ext1", user_id=real_user.id, bind_type="bound",
    ))
    await db_session.flush()

    user_id2, bind_type2 = await resolve_external_user(db_session, channel_id, "ou_ext1")
    assert user_id2 == real_user.id
    assert bind_type2 == "bound"

    # 存量影子账号绑定 → 仍视为 unbound，引导绑定
    shadow = User(
        username="feishu_legacy", email="feishu_legacy@channels.internal",
        password_hash="!", is_shadow=True,
    )
    db_session.add(shadow)
    await db_session.flush()
    db_session.add(ChannelBinding(
        channel_id=channel_id, external_id="ou_legacy", user_id=shadow.id, bind_type="shadow",
    ))
    await db_session.flush()

    user_id3, bind_type3 = await resolve_external_user(db_session, channel_id, "ou_legacy")
    assert user_id3 is None
    assert bind_type3 == "unbound"


async def test_issue_bind_code_invalidates_previous(db_session) -> None:
    channel_id = uuid4()
    code1, expires1 = await issue_bind_code(db_session, channel_id, "ou_binder")
    assert len(code1) == 6 and code1.isdigit()
    assert expires1 > datetime.now(UTC)

    code2, _ = await issue_bind_code(db_session, channel_id, "ou_binder")
    assert code2 != code1 or True  # 随机可能相同，不强制
    # 旧码已被作废
    with pytest.raises(BindCodeInvalid):
        await consume_bind_code(db_session, code1, uuid4())


async def test_issue_bind_code_rate_limited(db_session) -> None:
    channel_id = uuid4()
    for _ in range(3):
        await issue_bind_code(db_session, channel_id, "ou_spammer")
    with pytest.raises(BindCodeRateLimited):
        await issue_bind_code(db_session, channel_id, "ou_spammer")


async def test_consume_bind_code_merges_legacy_shadow(db_session) -> None:
    channel_id = uuid4()

    # 存量影子账号 + 绑定 + 会话
    shadow = User(
        username="feishu_merger", email="feishu_merger@channels.internal",
        password_hash="!", is_shadow=True,
    )
    db_session.add(shadow)
    await db_session.flush()
    shadow_id = shadow.id
    db_session.add(ChannelBinding(
        channel_id=channel_id, external_id="ou_merger", user_id=shadow_id, bind_type="shadow",
    ))
    await db_session.flush()

    session = ChatSession(user_id=shadow_id, title="渠道会话")
    db_session.add(session)
    await db_session.flush()

    code, _ = await issue_bind_code(db_session, channel_id, "ou_merger")

    real_user = User(username="real_merger", email="real_merger@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()

    merged_id = await consume_bind_code(db_session, code, real_user.id)
    assert merged_id == shadow_id

    # 绑定关系翻转
    binding = (
        await db_session.execute(
            select(ChannelBinding).where(
                ChannelBinding.channel_id == channel_id,
                ChannelBinding.external_id == "ou_merger",
            )
        )
    ).scalar_one()
    assert binding.user_id == real_user.id
    assert binding.bind_type == "bound"

    # 会话转移给真实账号
    await db_session.refresh(session)
    assert session.user_id == real_user.id

    # 影子账号被禁用
    shadow = (await db_session.execute(select(User).where(User.id == shadow_id))).scalar_one()
    assert shadow.is_active is False

    # 绑定码不能重复使用
    with pytest.raises(BindCodeInvalid):
        await consume_bind_code(db_session, code, real_user.id)


async def test_consume_bind_code_creates_binding(db_session) -> None:
    channel_id = uuid4()

    # 未绑定用户（无影子账号）首次绑定 → 直接创建 bound 绑定，不建账户
    code, _ = await issue_bind_code(db_session, channel_id, "ou_new")

    real_user = User(username="real_new", email="real_new@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()

    merged_id = await consume_bind_code(db_session, code, real_user.id)
    assert merged_id is None

    binding = (
        await db_session.execute(
            select(ChannelBinding).where(
                ChannelBinding.channel_id == channel_id,
                ChannelBinding.external_id == "ou_new",
            )
        )
    ).scalar_one()
    assert binding.user_id == real_user.id
    assert binding.bind_type == "bound"

    # 未创建任何影子账号
    shadows = (await db_session.execute(select(User).where(User.is_shadow.is_(True)))).scalars().all()
    assert shadows == []


async def test_consume_bind_code_rejects_invalid(db_session) -> None:
    with pytest.raises(BindCodeInvalid):
        await consume_bind_code(db_session, "000000", uuid4())


async def test_consume_bind_code_rejects_expired(db_session) -> None:
    from aio_agent_platform.db.models import ChannelBindCode

    record = ChannelBindCode(
        code="999888",
        channel_id=uuid4(),
        external_id="ou_expired",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(record)
    await db_session.flush()
    with pytest.raises(BindCodeInvalid, match="过期"):
        await consume_bind_code(db_session, "999888", uuid4())


async def test_consume_bind_code_rejects_already_bound(db_session) -> None:
    channel_id = uuid4()
    real_user = User(username="real_self", email="real_self@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()
    db_session.add(ChannelBinding(
        channel_id=channel_id, external_id="ou_self", user_id=real_user.id, bind_type="bound",
    ))
    await db_session.flush()

    # 绑定到同一账号 → 拒绝
    code, _ = await issue_bind_code(db_session, channel_id, "ou_self")
    with pytest.raises(BindCodeInvalid, match="已绑定"):
        await consume_bind_code(db_session, code, real_user.id)

    # 绑定到其他账号 → 拒绝
    other = User(username="real_other", email="real_other@test.com", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    code2, _ = await issue_bind_code(db_session, channel_id, "ou_self")
    with pytest.raises(BindCodeInvalid, match="其他账号"):
        await consume_bind_code(db_session, code2, other.id)


async def test_unbind_external_disables_shadow(db_session) -> None:
    channel_id = uuid4()
    shadow = User(
        username="feishu_unbind", email="feishu_unbind@channels.internal",
        password_hash="!", is_shadow=True,
    )
    db_session.add(shadow)
    await db_session.flush()
    db_session.add(ChannelBinding(
        channel_id=channel_id, external_id="ou_unbind", user_id=shadow.id, bind_type="shadow",
    ))
    await db_session.flush()

    await unbind_external(db_session, channel_id, "ou_unbind")

    binding = (
        await db_session.execute(
            select(ChannelBinding).where(
                ChannelBinding.channel_id == channel_id,
                ChannelBinding.external_id == "ou_unbind",
            )
        )
    ).scalar_one_or_none()
    assert binding is None
    shadow = (await db_session.execute(select(User).where(User.id == shadow.id))).scalar_one()
    assert shadow.is_active is False

    # 再次解绑是 no-op
    await unbind_external(db_session, channel_id, "ou_unbind")
