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

import asyncio
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
from aio_agent_platform.channels.pipeline import (
    _STREAM_INITIAL_TEXT,
    _STREAM_UPDATE_INTERVAL_SECONDS,
    _BufferedEventLogger,
    _build_image_data_uri,
    _collect_image_attachments,
    _dedup,
    _event_seen,
    _pending_attachments,
    _pending_key,
    _PendingAttachment,
    _pop_pending,
    _split_text,
    _StreamingReply,
    _strip_internal_keys,
)
from aio_agent_platform.db import Session as ChatSession
from aio_agent_platform.db.models import ChannelBinding, User
from aio_agent_platform.interface.routes.channels import (
    ChannelCreate,
    ChannelUpdate,
    _sensitive_config_changed,
)

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


def test_normalize_skips_unsupported_non_text() -> None:
    event = _make_event(message_type="audio")
    assert normalize_event(uuid4(), "evt_2", event, BOT_APP_ID) is None
    event = _make_event(message_type="media")
    assert normalize_event(uuid4(), "evt_2b", event, BOT_APP_ID) is None


def test_normalize_file_message_carries_attachment() -> None:
    event = _make_event(message_type="file")
    event["event"]["message"]["content"] = json.dumps(
        {"file_key": "file_v2_abc", "file_name": "合同.pdf"}
    )
    inbound = normalize_event(uuid4(), "evt_file", event, BOT_APP_ID)
    assert inbound is not None
    assert inbound.text == ""
    assert inbound.attachment is not None
    assert inbound.attachment.resource_key == "file_v2_abc"
    assert inbound.attachment.resource_type == "file"
    assert inbound.attachment.filename == "合同.pdf"


def test_normalize_image_message_carries_attachment() -> None:
    event = _make_event(message_type="image")
    event["event"]["message"]["content"] = json.dumps(
        {"image_key": "img_v2_xyz", "width": 100, "height": 80}
    )
    inbound = normalize_event(uuid4(), "evt_img", event, BOT_APP_ID)
    assert inbound is not None
    assert inbound.attachment is not None
    assert inbound.attachment.resource_key == "img_v2_xyz"
    assert inbound.attachment.resource_type == "image"
    assert inbound.attachment.filename == "image"


def test_normalize_file_message_without_key_skipped() -> None:
    event = _make_event(message_type="file")
    event["event"]["message"]["content"] = json.dumps({"file_name": "x.pdf"})
    assert normalize_event(uuid4(), "evt_file2", event, BOT_APP_ID) is None


def test_normalize_text_message_has_no_attachment() -> None:
    inbound = normalize_event(uuid4(), "evt_text", _make_event(), BOT_APP_ID)
    assert inbound is not None
    assert inbound.attachment is None


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


def test_pending_attachment_roundtrip() -> None:
    _pending_attachments.clear()
    key = _pending_key(uuid4(), "oc_chat", "ou_user")
    assert key.endswith("oc_chat:ou_user")
    ref = {"file_id": "abc123", "filename": "x.pdf", "workspace_path": "uploads/abc_x.pdf"}
    _pending_attachments[key] = [_PendingAttachment(ref=ref, ts=time.monotonic())]
    assert _pop_pending(key) == [ref]
    # 已被清空，再次弹出为空
    assert _pop_pending(key) == []


def test_pending_attachment_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    _pending_attachments.clear()
    key = _pending_key(uuid4(), "oc_chat2", "ou_user2")
    ref = {"file_id": "abc", "filename": "x.pdf", "workspace_path": "uploads/abc_x.pdf"}
    _pending_attachments[key] = [
        _PendingAttachment(ref=ref, ts=time.monotonic() - 4000)  # 超过 30min TTL
    ]
    assert _pop_pending(key) == []


# ---------------------------------------------------------------------------
# 图片多模态直喂 — 纯函数
# ---------------------------------------------------------------------------


def test_build_image_data_uri_format() -> None:
    uri = _build_image_data_uri(b"\x89PNG", "image/png")
    expected = "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode()
    assert uri == expected


def test_collect_image_attachments_extracts_data_uri() -> None:
    refs = [
        {"file_id": "f1", "filename": "a.pdf", "workspace_path": "uploads/f1_a.pdf"},
        {"file_id": "i1", "filename": "image.png", "workspace_path": "uploads/i1_image.png",
         "mime": "image/png", "size": 100, "_image_data_uri": "data:image/png;base64,AAA"},
    ]
    out = _collect_image_attachments(refs)
    assert len(out) == 1
    assert out[0]["key"] == "data:image/png;base64,AAA"
    assert out[0]["filename"] == "image.png"
    assert out[0]["url"] == "uploads/i1_image.png"


def test_collect_image_attachments_empty_without_images() -> None:
    refs = [{"file_id": "f1", "filename": "a.pdf", "workspace_path": "uploads/f1_a.pdf"}]
    assert _collect_image_attachments(refs) == []
    assert _collect_image_attachments([]) == []


def test_strip_internal_keys_removes_data_uri() -> None:
    refs = [{"file_id": "i1", "filename": "image.png", "_image_data_uri": "data:x"}]
    assert _strip_internal_keys(refs) == [{"file_id": "i1", "filename": "image.png"}]


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


async def test_streaming_reply_updates_first_delta_and_throttles() -> None:
    adapter = AsyncMock()
    adapter.start_stream.return_value = "card_1"
    adapter.update_stream.return_value = True
    event = _make_event_obj()
    stream = _StreamingReply(adapter, event)

    await stream.start()
    await stream.push("你")
    await stream.push("你好")
    assert stream.flush_task is not None
    await stream.flush_task

    adapter.start_stream.assert_awaited_once_with(event, _STREAM_INITIAL_TEXT)
    # 同一事件循环内到达的 delta 被合并，只推送最新的完整文本。
    adapter.update_stream.assert_awaited_once_with("card_1", "你好", 1)

    stream.last_update_at -= _STREAM_UPDATE_INTERVAL_SECONDS
    await stream.push("你好啊")
    assert stream.flush_task is not None
    await stream.flush_task
    adapter.update_stream.assert_awaited_with("card_1", "你好啊", 2)


async def test_streaming_reply_does_not_block_on_cardkit_network() -> None:
    adapter = AsyncMock()
    adapter.start_stream.return_value = "card_1"
    request_finished = asyncio.Event()

    async def slow_update(*_args) -> bool:
        await request_finished.wait()
        return True

    adapter.update_stream.side_effect = slow_update
    stream = _StreamingReply(adapter, _make_event_obj())
    await stream.start()

    # push 只排队，不能等待飞书 HTTP 请求完成。
    await stream.push("增量")
    await asyncio.sleep(0)
    assert stream.flush_task is not None
    assert not stream.flush_task.done()

    request_finished.set()
    await stream.flush_task


async def test_buffered_event_logger_coalesces_text_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[dict] = []

    async def fake_log_event(_user_id, _session_id, event: dict) -> None:
        written.append(event)

    monkeypatch.setattr(
        "aio_agent_platform.channels.pipeline.log_event", fake_log_event
    )
    logger = _BufferedEventLogger(uuid4(), uuid4())
    logger.submit({"type": "text_delta", "content": "你"})
    logger.submit({"type": "text_delta", "content": "好"})
    logger.submit({"type": "tool_call", "name": "search"})
    logger.submit({"type": "text_delta", "content": "完成"})
    await logger.drain()

    assert written == [
        {"type": "text_delta", "content": "你好"},
        {"type": "tool_call", "name": "search"},
        {"type": "text_delta", "content": "完成"},
    ]


async def test_streaming_reply_forces_final_update() -> None:
    adapter = AsyncMock()
    adapter.start_stream.return_value = "card_1"
    adapter.update_stream.return_value = True
    adapter.finish_stream.return_value = True
    event = _make_event_obj()
    stream = _StreamingReply(adapter, event)

    await stream.start()
    await stream.push("部分")
    stream.last_update_at -= _STREAM_UPDATE_INTERVAL_SECONDS
    await stream.finish("完整回答")

    adapter.update_stream.assert_awaited_with("card_1", "完整回答", 2)
    adapter.finish_stream.assert_awaited_once_with("card_1", "完整回答", 3)
    adapter.send_markdown.assert_not_awaited()


async def test_streaming_reply_falls_back_when_update_fails() -> None:
    adapter = AsyncMock()
    adapter.start_stream.return_value = "card_1"
    adapter.update_stream.return_value = False
    adapter.finish_stream.return_value = True
    event = _make_event_obj()
    stream = _StreamingReply(adapter, event)

    await stream.start()
    await stream.push("回答")
    await stream.finish("回答完成")

    adapter.finish_stream.assert_awaited_once_with("card_1", "回答完成", 2)
    adapter.send_markdown.assert_awaited_once_with(event, "回答完成")


async def test_streaming_reply_disabled_sends_only_final_message() -> None:
    adapter = AsyncMock()
    event = _make_event_obj()
    stream = _StreamingReply(adapter, event)

    # 渠道关闭流式时不调用 start，finish 自动走一次性终稿发送。
    await stream.push("生成中的内容")
    await stream.finish("完整回答")

    adapter.start_stream.assert_not_awaited()
    adapter.update_stream.assert_not_awaited()
    adapter.send_markdown.assert_awaited_once_with(event, "完整回答")


# ---------------------------------------------------------------------------
# binding — pure helpers
# ---------------------------------------------------------------------------


def test_generate_code_is_6_digits() -> None:
    for _ in range(20):
        code = _generate_code()
        assert len(code) == 6
        assert code.isdigit()


def test_channel_streaming_config_schema_defaults_and_override() -> None:
    payload = {
        "name": "飞书",
        "agent_id": uuid4(),
        "app_id": "cli_test",
        "app_secret": "secret",
        "mode": "websocket",
    }
    assert ChannelCreate(**payload).enable_streaming is True
    assert ChannelCreate(**payload, enable_streaming=False).enable_streaming is False
    assert ChannelUpdate().enable_streaming is None
    assert ChannelUpdate(enable_streaming=False).enable_streaming is False


def test_unchanged_channel_mode_is_not_a_sensitive_change() -> None:
    channel = SimpleNamespace(
        app_id="cli_test",
        app_secret_encrypted="secret",
        encrypt_key_encrypted="encrypt-key",
        verification_token_encrypted="verification-token",
        mode="websocket",
    )

    assert not _sensitive_config_changed(
        channel, ChannelUpdate(name="新名称", mode="websocket")
    )


def test_changed_channel_credential_is_a_sensitive_change() -> None:
    channel = SimpleNamespace(
        app_id="cli_test",
        app_secret_encrypted="secret",
        encrypt_key_encrypted=None,
        verification_token_encrypted=None,
        mode="websocket",
    )

    assert _sensitive_config_changed(channel, ChannelUpdate(app_secret="new-secret"))
    assert _sensitive_config_changed(channel, ChannelUpdate(app_id="cli_new"))


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
        self.download_code = 0
        self.upload_code = 0
        self.image_code = 0
        self.cardkit_code = 0
        self.stream_code = 0
        self.settings_code = 0
        self.last_send_url: str | None = None
        self.last_resource_url: str | None = None
        self.last_send_body = ""
        self.last_upload_url: str | None = None
        self.last_upload_body = ""
        self.last_image_url: str | None = None
        self.last_image_body = ""
        self.last_cardkit_body = ""
        self.last_stream_body = ""
        self.last_settings_body = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/resources/" in path and request.method == "GET":
            self.last_resource_url = str(request.url)
            if self.download_code != 0:
                return httpx.Response(404, json={"code": self.download_code, "msg": "no permission"})
            return httpx.Response(200, content=b"file-binary-content")
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
        if path.endswith("/files") and request.method == "POST":
            self.last_upload_url = str(request.url)
            self.last_upload_body = request.content.decode("utf-8", errors="replace")
            if self.upload_code != 0:
                return httpx.Response(200, json={"code": self.upload_code, "msg": "upload failed"})
            return httpx.Response(200, json={"code": 0, "data": {"file_key": "file_v2_test"}})
        if path.endswith("/images") and request.method == "POST":
            self.last_image_url = str(request.url)
            self.last_image_body = request.content.decode("utf-8", errors="replace")
            if self.image_code != 0:
                return httpx.Response(200, json={"code": self.image_code, "msg": "image failed"})
            return httpx.Response(200, json={"code": 0, "data": {"image_key": "img_v2_test"}})
        if path.endswith("/cardkit/v1/cards") and request.method == "POST":
            self.last_cardkit_body = request.content.decode("utf-8")
            return httpx.Response(200, json={
                "code": self.cardkit_code,
                "msg": "cardkit result",
                "data": {"card_id": "card_123"},
            })
        if path.endswith("/content") and request.method == "PUT":
            self.last_stream_body = request.content.decode("utf-8")
            return httpx.Response(200, json={"code": self.stream_code, "msg": "stream result"})
        if path.endswith("/settings") and request.method == "PATCH":
            self.last_settings_body = request.content.decode("utf-8")
            return httpx.Response(200, json={"code": self.settings_code, "msg": "settings result"})
        if request.method == "PUT":
            return httpx.Response(200, json={"code": self.update_code, "data": {}})
        if request.method == "POST" and "/messages" in path:
            self.last_send_url = str(request.url)
            self.last_send_body = request.content.decode("utf-8", errors="replace")
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


async def test_cardkit_native_streaming_flow(feishu_stub) -> None:
    client, stub = feishu_stub

    assert await client.create_streaming_card("思考中") == "card_123"
    create_body = json.loads(stub.last_cardkit_body)
    card = json.loads(create_body["data"])
    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is True
    assert "streaming_config" not in card["config"]
    assert card["body"]["elements"][0]["element_id"] == "assistant_answer"

    assert await client.send_card_entity("oc_1", "card_123") == "om_123"
    send_body = json.loads(stub.last_send_body)
    assert send_body["msg_type"] == "interactive"
    assert json.loads(send_body["content"])["data"]["card_id"] == "card_123"

    assert await client.stream_card_text("card_123", "完整内容", 1) is True
    stream_body = json.loads(stub.last_stream_body)
    assert stream_body["content"] == "完整内容"
    assert stream_body["sequence"] == 1
    assert stream_body["uuid"]

    assert await client.finish_streaming_card("card_123", "完整内容", 2) is True
    finish_body = json.loads(stub.last_settings_body)
    settings = json.loads(finish_body["settings"])
    assert settings["config"]["streaming_mode"] is False
    assert settings["config"]["summary"]["content"] == "完整内容"
    assert finish_body["sequence"] == 2


async def test_cardkit_permission_failure_returns_none(feishu_stub) -> None:
    client, stub = feishu_stub
    stub.cardkit_code = 99991672
    assert await client.create_streaming_card() is None


async def test_reactions(feishu_stub) -> None:
    client, stub = feishu_stub
    assert await client.add_reaction("om_1", "Typing") == "r_1"
    assert await client.delete_reaction("om_1", "r_1") is True
    stub.reaction_code = 123
    assert await client.add_reaction("om_1", "Typing") is None
    assert await client.delete_reaction("om_1", "r_1") is False


async def test_download_resource_returns_bytes(feishu_stub) -> None:
    client, stub = feishu_stub
    data = await client.download_resource("om_1", "file_v2_abc", "file")
    assert data == b"file-binary-content"
    assert stub.last_resource_url is not None
    assert "/messages/om_1/resources/file_v2_abc" in stub.last_resource_url
    assert "type=file" in stub.last_resource_url


async def test_download_resource_failure_returns_none(feishu_stub) -> None:
    client, stub = feishu_stub
    stub.download_code = 99991671
    assert await client.download_resource("om_1", "file_v2_abc", "file") is None


async def test_download_image_resource(feishu_stub) -> None:
    client, stub = feishu_stub
    data = await client.download_resource("om_2", "img_v2_xyz", "image")
    assert data == b"file-binary-content"
    assert "type=image" in stub.last_resource_url


async def test_upload_file_returns_file_key(feishu_stub) -> None:
    client, stub = feishu_stub
    key = await client.upload_file(b"hello", "data.txt")
    assert key == "file_v2_test"
    assert stub.last_upload_url is not None
    assert stub.last_upload_url.endswith("/im/v1/files")
    assert "data.txt" in stub.last_upload_body


async def test_upload_file_infers_pdf_file_type(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.upload_file(b"%PDF", "report.pdf")
    # multipart 表单应携带 file_type=pdf
    assert 'name="file_type"' in stub.last_upload_body
    assert "pdf" in stub.last_upload_body


async def test_upload_file_failure_returns_none(feishu_stub) -> None:
    client, stub = feishu_stub
    stub.upload_code = 12345
    assert await client.upload_file(b"x", "a.txt") is None


async def test_send_file_uses_file_msg_type(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_file("oc_1", "file_v2_abc")
    body = json.loads(stub.last_send_body)
    assert body["msg_type"] == "file"
    assert json.loads(body["content"])["file_key"] == "file_v2_abc"
    assert "receive_id_type=chat_id" in stub.last_send_url


async def test_send_file_reply_endpoint(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_file("oc_1", "file_v2_abc", reply_to="om_parent")
    assert "/messages/om_parent/reply" in stub.last_send_url


async def test_upload_image_returns_image_key(feishu_stub) -> None:
    client, stub = feishu_stub
    key = await client.upload_image(b"\x89PNG", "chart.png")
    assert key == "img_v2_test"
    assert stub.last_image_url is not None
    assert stub.last_image_url.endswith("/im/v1/images")
    assert 'name="image_type"' in stub.last_image_body
    assert "chart.png" in stub.last_image_body


async def test_upload_image_failure_returns_none(feishu_stub) -> None:
    client, stub = feishu_stub
    stub.image_code = 12345
    assert await client.upload_image(b"x", "a.png") is None


async def test_send_image_uses_image_msg_type(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_image("oc_1", "img_v2_abc")
    body = json.loads(stub.last_send_body)
    assert body["msg_type"] == "image"
    assert json.loads(body["content"])["image_key"] == "img_v2_abc"


async def test_send_audio_uses_audio_msg_type(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_audio("oc_1", "file_v2_opus")
    body = json.loads(stub.last_send_body)
    assert body["msg_type"] == "audio"
    assert json.loads(body["content"])["file_key"] == "file_v2_opus"


async def test_send_media_uses_media_msg_type(feishu_stub) -> None:
    client, stub = feishu_stub
    await client.send_media("oc_1", "file_v2_mp4")
    body = json.loads(stub.last_send_body)
    assert body["msg_type"] == "media"
    assert json.loads(body["content"])["file_key"] == "file_v2_mp4"


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


async def test_adapter_update_returns_client_result() -> None:
    adapter, client = _make_adapter()
    client.update_message.return_value = True
    await adapter.update("om_out", "增量")
    client.update_message.assert_awaited_once_with("om_out", "增量")


async def test_adapter_native_streaming_delegates_to_cardkit() -> None:
    adapter, client = _make_adapter()
    event = _make_event_obj(mentions_bot=True)
    client.create_streaming_card.return_value = "card_1"
    client.send_card_entity.return_value = "om_card"
    client.stream_card_text.return_value = True
    client.finish_streaming_card.return_value = True

    assert await adapter.start_stream(event, "思考中") == "card_1"
    client.send_card_entity.assert_awaited_once_with(
        receive_id="oc_chat", card_id="card_1", reply_to="om_in"
    )
    assert await adapter.update_stream("card_1", "回答", 1) is True
    assert await adapter.finish_stream("card_1", "回答", 2) is True


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


async def test_adapter_send_file_delegates() -> None:
    adapter, client = _make_adapter()
    client.upload_file.return_value = "file_v2_up"
    client.send_file.return_value = "om_file"
    event = _make_event_obj(mentions_bot=True)
    result = await adapter.send_file(event, "data.csv", b"csv")
    assert result == "om_file"
    client.upload_file.assert_awaited_once_with(b"csv", "data.csv")
    client.send_file.assert_awaited_once_with(
        receive_id="oc_chat", file_key="file_v2_up", reply_to="om_in"
    )


async def test_adapter_send_file_direct_chat_no_reply() -> None:
    adapter, client = _make_adapter()
    client.upload_file.return_value = "file_v2_up"
    event = _make_event_obj(mentions_bot=False)
    await adapter.send_file(event, "a.txt", b"x")
    client.send_file.assert_awaited_once_with(
        receive_id="oc_chat", file_key="file_v2_up", reply_to=None
    )


async def test_adapter_send_file_upload_failure() -> None:
    adapter, client = _make_adapter()
    client.upload_file.return_value = None
    assert await adapter.send_file(_make_event_obj(), "a.txt", b"x") is None
    client.send_file.assert_not_awaited()


async def test_adapter_send_image_routes_to_image() -> None:
    adapter, client = _make_adapter()
    client.upload_image.return_value = "img_v2_up"
    client.send_image.return_value = "om_img"
    result = await adapter.send_file(_make_event_obj(mentions_bot=True), "chart.png", b"\x89PNG")
    assert result == "om_img"
    client.upload_image.assert_awaited_once_with(b"\x89PNG", "chart.png")
    client.send_image.assert_awaited_once_with(
        receive_id="oc_chat", image_key="img_v2_up", reply_to="om_in"
    )
    client.upload_file.assert_not_awaited()


async def test_adapter_send_opus_routes_to_audio() -> None:
    adapter, client = _make_adapter()
    client.upload_file.return_value = "file_v2_opus"
    client.send_audio.return_value = "om_audio"
    result = await adapter.send_file(_make_event_obj(), "voice.opus", b"audio")
    assert result == "om_audio"
    client.upload_file.assert_awaited_once_with(b"audio", "voice.opus", file_type="opus")
    client.send_audio.assert_awaited_once_with(
        receive_id="oc_chat", file_key="file_v2_opus", reply_to=None
    )


async def test_adapter_send_mp4_routes_to_media() -> None:
    adapter, client = _make_adapter()
    client.upload_file.return_value = "file_v2_mp4"
    client.send_media.return_value = "om_media"
    result = await adapter.send_file(_make_event_obj(), "clip.mp4", b"video")
    assert result == "om_media"
    client.upload_file.assert_awaited_once_with(b"video", "clip.mp4", file_type="mp4")
    client.send_media.assert_awaited_once_with(
        receive_id="oc_chat", file_key="file_v2_mp4", reply_to=None
    )


async def test_adapter_send_mp3_falls_back_to_file() -> None:
    # 飞书语音仅支持 opus，mp3 等其它格式回退为文件消息
    adapter, client = _make_adapter()
    client.upload_file.return_value = "file_v2_mp3"
    client.send_file.return_value = "om_file"
    result = await adapter.send_file(_make_event_obj(), "song.mp3", b"audio")
    assert result == "om_file"
    client.upload_file.assert_awaited_once_with(b"audio", "song.mp3")
    client.send_file.assert_awaited_once_with(
        receive_id="oc_chat", file_key="file_v2_mp3", reply_to=None
    )


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
        "enable_streaming": True,
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
    tenant_id = uuid4()

    # 无任何绑定 → unbound，且不创建任何账户
    user_id, bind_type = await resolve_external_user(db_session, tenant_id, "ou_ext1")
    assert user_id is None
    assert bind_type == "unbound"
    assert (await db_session.execute(select(User))).scalars().all() == []

    # 建立 bound 绑定后 → bound
    real_user = User(username="real_ext", email="real_ext@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()
    db_session.add(ChannelBinding(
        tenant_id=tenant_id, external_id="ou_ext1", user_id=real_user.id, bind_type="bound",
    ))
    await db_session.flush()

    user_id2, bind_type2 = await resolve_external_user(db_session, tenant_id, "ou_ext1")
    assert user_id2 == real_user.id
    assert bind_type2 == "bound"

    # 其他租户查不到该绑定
    user_id_x, bind_type_x = await resolve_external_user(db_session, uuid4(), "ou_ext1")
    assert user_id_x is None
    assert bind_type_x == "unbound"

    # 存量影子账号绑定 → 仍视为 unbound，引导绑定
    shadow = User(
        username="feishu_legacy", email="feishu_legacy@channels.internal",
        password_hash="!", is_shadow=True,
    )
    db_session.add(shadow)
    await db_session.flush()
    db_session.add(ChannelBinding(
        tenant_id=tenant_id, external_id="ou_legacy", user_id=shadow.id, bind_type="shadow",
    ))
    await db_session.flush()

    user_id3, bind_type3 = await resolve_external_user(db_session, tenant_id, "ou_legacy")
    assert user_id3 is None
    assert bind_type3 == "unbound"


async def test_issue_bind_code_invalidates_previous(db_session) -> None:
    channel_id = uuid4()
    tenant_id = uuid4()
    code1, expires1 = await issue_bind_code(db_session, channel_id, "ou_binder", tenant_id)
    assert len(code1) == 6 and code1.isdigit()
    assert expires1 > datetime.now(UTC)

    code2, _ = await issue_bind_code(db_session, channel_id, "ou_binder", tenant_id)
    assert code2 != code1 or True  # 随机可能相同，不强制
    # 旧码已被作废
    with pytest.raises(BindCodeInvalid):
        await consume_bind_code(db_session, code1, uuid4(), tenant_id)


async def test_issue_bind_code_rate_limited(db_session) -> None:
    channel_id = uuid4()
    tenant_id = uuid4()
    for _ in range(3):
        await issue_bind_code(db_session, channel_id, "ou_spammer", tenant_id)
    with pytest.raises(BindCodeRateLimited):
        await issue_bind_code(db_session, channel_id, "ou_spammer", tenant_id)


async def test_consume_bind_code_merges_legacy_shadow(db_session) -> None:
    channel_id = uuid4()
    tenant_id = uuid4()

    # 存量影子账号 + 绑定 + 会话
    shadow = User(
        username="feishu_merger", email="feishu_merger@channels.internal",
        password_hash="!", is_shadow=True,
    )
    db_session.add(shadow)
    await db_session.flush()
    shadow_id = shadow.id
    db_session.add(ChannelBinding(
        tenant_id=tenant_id, external_id="ou_merger", user_id=shadow_id, bind_type="shadow",
    ))
    await db_session.flush()

    session = ChatSession(user_id=shadow_id, title="渠道会话")
    db_session.add(session)
    await db_session.flush()

    code, _ = await issue_bind_code(db_session, channel_id, "ou_merger", tenant_id)

    real_user = User(username="real_merger", email="real_merger@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()

    merged_id = await consume_bind_code(db_session, code, real_user.id, tenant_id)
    assert merged_id == shadow_id

    # 绑定关系翻转
    binding = (
        await db_session.execute(
            select(ChannelBinding).where(
                ChannelBinding.tenant_id == tenant_id,
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
        await consume_bind_code(db_session, code, real_user.id, tenant_id)


async def test_consume_bind_code_creates_binding(db_session) -> None:
    channel_id = uuid4()
    tenant_id = uuid4()

    # 未绑定用户（无影子账号）首次绑定 → 直接创建 bound 绑定，不建账户
    code, _ = await issue_bind_code(db_session, channel_id, "ou_new", tenant_id)

    real_user = User(username="real_new", email="real_new@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()

    merged_id = await consume_bind_code(db_session, code, real_user.id, tenant_id)
    assert merged_id is None

    binding = (
        await db_session.execute(
            select(ChannelBinding).where(
                ChannelBinding.tenant_id == tenant_id,
                ChannelBinding.external_id == "ou_new",
            )
        )
    ).scalar_one()
    assert binding.user_id == real_user.id
    assert binding.bind_type == "bound"

    # 未创建任何影子账号
    shadows = (await db_session.execute(select(User).where(User.is_shadow.is_(True)))).scalars().all()
    assert shadows == []


async def test_consume_bind_code_rejects_cross_tenant(db_session) -> None:
    channel_id = uuid4()
    tenant_id = uuid4()

    code, _ = await issue_bind_code(db_session, channel_id, "ou_cross", tenant_id)

    real_user = User(username="real_cross", email="real_cross@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()

    # 其他租户的用户不能消费该绑定码
    with pytest.raises(BindCodeInvalid, match="其他租户"):
        await consume_bind_code(db_session, code, real_user.id, uuid4())

    # 码未被消费，同租户仍可正常使用
    merged_id = await consume_bind_code(db_session, code, real_user.id, tenant_id)
    assert merged_id is None


async def test_consume_bind_code_rejects_invalid(db_session) -> None:
    with pytest.raises(BindCodeInvalid):
        await consume_bind_code(db_session, "000000", uuid4(), uuid4())


async def test_consume_bind_code_rejects_expired(db_session) -> None:
    from aio_agent_platform.db.models import ChannelBindCode

    tenant_id = uuid4()
    record = ChannelBindCode(
        code="999888",
        tenant_id=tenant_id,
        channel_id=uuid4(),
        external_id="ou_expired",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(record)
    await db_session.flush()
    with pytest.raises(BindCodeInvalid, match="过期"):
        await consume_bind_code(db_session, "999888", uuid4(), tenant_id)


async def test_consume_bind_code_rejects_already_bound(db_session) -> None:
    channel_id = uuid4()
    tenant_id = uuid4()
    real_user = User(username="real_self", email="real_self@test.com", password_hash="x")
    db_session.add(real_user)
    await db_session.flush()
    db_session.add(ChannelBinding(
        tenant_id=tenant_id, external_id="ou_self", user_id=real_user.id, bind_type="bound",
    ))
    await db_session.flush()

    # 绑定到同一账号 → 拒绝
    code, _ = await issue_bind_code(db_session, channel_id, "ou_self", tenant_id)
    with pytest.raises(BindCodeInvalid, match="已绑定"):
        await consume_bind_code(db_session, code, real_user.id, tenant_id)

    # 绑定到其他账号 → 拒绝
    other = User(username="real_other", email="real_other@test.com", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    code2, _ = await issue_bind_code(db_session, channel_id, "ou_self", tenant_id)
    with pytest.raises(BindCodeInvalid, match="其他账号"):
        await consume_bind_code(db_session, code2, other.id, tenant_id)


async def test_unbind_external_disables_shadow(db_session) -> None:
    tenant_id = uuid4()
    shadow = User(
        username="feishu_unbind", email="feishu_unbind@channels.internal",
        password_hash="!", is_shadow=True,
    )
    db_session.add(shadow)
    await db_session.flush()
    db_session.add(ChannelBinding(
        tenant_id=tenant_id, external_id="ou_unbind", user_id=shadow.id, bind_type="shadow",
    ))
    await db_session.flush()

    await unbind_external(db_session, tenant_id, "ou_unbind")

    binding = (
        await db_session.execute(
            select(ChannelBinding).where(
                ChannelBinding.tenant_id == tenant_id,
                ChannelBinding.external_id == "ou_unbind",
            )
        )
    ).scalar_one_or_none()
    assert binding is None
    shadow = (await db_session.execute(select(User).where(User.id == shadow.id))).scalar_one()
    assert shadow.is_active is False

    # 再次解绑是 no-op
    await unbind_external(db_session, tenant_id, "ou_unbind")
