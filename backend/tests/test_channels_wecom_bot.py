"""WeCom bot channel tests — spec, frame normalization, WS transport, adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from aio_agent_platform.channels.adapter import ChatKind, InboundEvent
from aio_agent_platform.channels.registry import get_channel_spec
from aio_agent_platform.channels.wecom_bot import ws_transport as _ws_mod
from aio_agent_platform.channels.wecom_bot.adapter import WeComBotAdapter, _decrypt_file
from aio_agent_platform.channels.wecom_bot.events import normalize_event
from aio_agent_platform.channels.wecom_bot.ws_transport import (
    StreamExpiredError,
    WeComBotTransport,
)

_BOT_ID = "aib_test_bot_123"
_SECRET = "bot_secret_abc"


# --- spec ---


def test_wecom_bot_spec_registered() -> None:
    spec = get_channel_spec("wecom_bot")
    assert spec.title_prefix == "企微机器人· "
    assert spec.allowed_modes == ("websocket",)
    assert spec.supports_file_send is True
    assert spec.build is not None
    assert spec.verify_credentials is not None


async def test_wecom_bot_spec_build_wires_transport() -> None:
    channel = SimpleNamespace(
        id=uuid4(),
        channel_type="wecom_bot",
        app_id=_BOT_ID,
        app_secret_encrypted=_SECRET,
        agent_id=uuid4(),
        mode="websocket",
    )
    spec = get_channel_spec("wecom_bot")
    adapter = spec.build(channel, MagicMock())
    assert isinstance(adapter, WeComBotAdapter)
    assert isinstance(adapter.transport, WeComBotTransport)
    assert adapter.transport.bot_id == _BOT_ID
    assert adapter.transport.secret == _SECRET
    assert adapter.pipeline is adapter.transport.pipeline


# --- frame normalization ---


def _frame(msgtype: str, body: dict) -> dict:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": f"aibot_msg_callback_{uuid4().hex}"},
        "body": {"msgtype": msgtype, "from": {"userid": "zhangsan"}, **body},
    }


def test_normalize_text_single() -> None:
    ev = normalize_event(uuid4(), _frame("text", {"msgid": "m1", "chattype": "single", "chatid": "zhangsan", "text": {"content": "你好"}}))
    assert ev is not None
    assert ev.text == "你好"
    assert ev.external_id == "zhangsan"
    assert ev.chat_id == "zhangsan"
    assert ev.chat_kind == ChatKind.DIRECT
    assert ev.message_id == "m1"
    assert ev.mentions_bot is True
    assert ev.raw["req_id"]
    assert ev.raw["chatid"] == "zhangsan"
    assert ev.attachment is None


def test_normalize_text_group() -> None:
    ev = normalize_event(uuid4(), _frame("text", {"msgid": "m2", "chattype": "group", "chatid": "grp1", "text": {"content": "hi"}}))
    assert ev is not None
    assert ev.chat_kind == ChatKind.GROUP
    assert ev.chat_id == "grp1"


def test_normalize_image() -> None:
    ev = normalize_event(uuid4(), _frame("image", {"msgid": "m3", "image": {"url": "https://qpic.cn/a.png?k=1", "aeskey": "k3"}}))
    assert ev is not None
    assert ev.attachment is not None
    assert ev.attachment.resource_type == "image"
    assert ev.attachment.resource_key == "https://qpic.cn/a.png?k=1"
    assert ev.attachment.filename == "a.png"
    assert ev.raw["aeskey"] == "k3"


def test_normalize_file() -> None:
    ev = normalize_event(uuid4(), _frame("file", {"msgid": "m4", "file": {"url": "https://qpic.cn/rpt.pdf", "aeskey": "k4"}}))
    assert ev is not None
    assert ev.attachment is not None
    assert ev.attachment.resource_type == "file"
    assert ev.attachment.filename == "rpt.pdf"


def test_normalize_skips_event_and_unsupported() -> None:
    event_frame = {"cmd": "aibot_event_callback", "headers": {"req_id": "r"}, "body": {"msgtype": "event", "event": {"eventtype": "enter_chat"}}}
    assert normalize_event(uuid4(), event_frame) is None
    assert normalize_event(uuid4(), _frame("voice", {"voice": {"url": "x"}})) is None
    assert normalize_event(uuid4(), _frame("video", {"video": {"url": "x"}})) is None


# --- transport ---


def _transport() -> WeComBotTransport:
    pipeline = MagicMock()
    pipeline.channel.id = uuid4()
    return WeComBotTransport(pipeline=pipeline, bot_id=_BOT_ID, secret=_SECRET)


async def test_transport_handle_callback_submits() -> None:
    transport = _transport()
    await transport._handle_frame(json.dumps(_frame("text", {"msgid": "t1", "text": {"content": "hi"}})))
    transport.pipeline.submit.assert_called_once()
    ev = transport.pipeline.submit.call_args[0][0]
    assert isinstance(ev, InboundEvent)
    assert ev.text == "hi"


async def test_transport_ack_resolves_pending() -> None:
    transport = _transport()
    transport._ws = AsyncMock()
    req_id = "reply_1"
    task = asyncio.create_task(transport.reply(req_id, {"msgtype": "stream", "stream": {"id": "s1", "finish": True, "content": "x"}}))
    await asyncio.sleep(0)  # let _send_and_wait register the future
    await transport._handle_frame(json.dumps({"headers": {"req_id": req_id}, "errcode": 0, "errmsg": "ok"}))
    ack = await task
    assert ack["errcode"] == 0
    transport._ws.send.assert_called_once()


async def test_transport_ack_stream_expired_raises() -> None:
    transport = _transport()
    transport._ws = AsyncMock()
    req_id = "reply_2"
    task = asyncio.create_task(transport.reply(req_id, {"msgtype": "stream", "stream": {"id": "s2", "finish": True, "content": "x"}}))
    await asyncio.sleep(0)
    await transport._handle_frame(json.dumps({"headers": {"req_id": req_id}, "errcode": 846608, "errmsg": "stream expired"}))
    with pytest.raises(StreamExpiredError):
        await task


async def test_transport_ping_ack_clears_missed() -> None:
    transport = _transport()
    req_id = "ping_x"
    transport._ping_req_ids.add(req_id)
    transport._ping_pending = True
    transport._missed_pong = 1
    await transport._handle_frame(json.dumps({"headers": {"req_id": req_id}, "errcode": 0}))
    assert transport._missed_pong == 0
    assert transport._ping_pending is False


async def test_transport_kicked_event_logs_and_skips() -> None:
    transport = _transport()
    frame = json.dumps({
        "cmd": "aibot_event_callback",
        "headers": {"req_id": "r_kick"},
        "body": {"msgtype": "event", "event": {"eventtype": "disconnected_event", "reason": "dup"}},
    })
    await transport._handle_frame(frame)
    transport.pipeline.submit.assert_not_called()


async def test_verify_bot_credentials(monkeypatch) -> None:
    class FakeWs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, data):
            sent = json.loads(data)
            assert sent["cmd"] == "aibot_subscribe"
            assert sent["body"]["bot_id"] == _BOT_ID
            assert sent["body"]["secret"] == _SECRET

        async def recv(self):
            return json.dumps({"headers": {"req_id": "r"}, "errcode": 0, "errmsg": "ok"})

    def fake_connect(url, **kwargs):
        assert url == _ws_mod._DEFAULT_WS_URL
        return FakeWs()

    monkeypatch.setattr(_ws_mod.websockets, "connect", fake_connect)
    assert await _ws_mod.verify_bot_credentials(_BOT_ID, _SECRET) is True


async def test_verify_bot_credentials_rejects_bad_secret(monkeypatch) -> None:
    class FakeWs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, data):
            pass

        async def recv(self):
            return json.dumps({"headers": {"req_id": "r"}, "errcode": 40061, "errmsg": "invalid botid"})

    monkeypatch.setattr(_ws_mod.websockets, "connect", lambda url, **kw: FakeWs())
    assert await _ws_mod.verify_bot_credentials(_BOT_ID, "bad") is False


# --- adapter ---


def _event(**kwargs) -> InboundEvent:
    base = {
        "channel_id": uuid4(),
        "event_id": "evt1",
        "chat_id": "zhangsan",
        "external_id": "zhangsan",
        "text": "hi",
        "raw": {"req_id": "cb_req_1", "chatid": "zhangsan"},
    }
    base.update(kwargs)
    return InboundEvent(**base)


def _adapter(ws=None) -> WeComBotAdapter:
    adapter = WeComBotAdapter(channel_id=uuid4(), pipeline=MagicMock())
    transport = ws or AsyncMock(spec=WeComBotTransport)
    adapter.set_transport(transport)
    return adapter


async def test_adapter_send_uses_callback_req_id() -> None:
    ws = AsyncMock(spec=WeComBotTransport)
    adapter = _adapter(ws)
    mid = await adapter.send(_event(), "你好")
    assert mid is not None
    ws.reply.assert_awaited_once()
    body = ws.reply.call_args[0][1]
    assert body["msgtype"] == "stream"
    assert body["stream"]["finish"] is True
    assert body["stream"]["content"] == "你好"
    assert ws.reply.call_args[0][0] == "cb_req_1"


async def test_adapter_stream_lifecycle() -> None:
    ws = AsyncMock(spec=WeComBotTransport)
    adapter = _adapter(ws)
    stream_id = await adapter.start_stream(_event(), "")
    assert stream_id is not None
    assert ws.reply.await_count == 1
    assert await adapter.update_stream(stream_id, "第一段", 1) is True
    assert await adapter.finish_stream(stream_id, "最终", 2) is True
    assert ws.reply.await_count == 3
    bodies = [c.args[1]["stream"] for c in ws.reply.await_args_list]
    assert bodies[0]["finish"] is False
    assert bodies[0]["content"] == "<think></think>"  # 空文本用可见占位
    assert bodies[1]["content"] == "第一段"
    assert bodies[2]["finish"] is True


async def test_adapter_send_to_user_markdown() -> None:
    ws = AsyncMock(spec=WeComBotTransport)
    ws.send_message.return_value = {"headers": {"req_id": "r"}, "errcode": 0, "body": {"msgid": "mid1"}}
    adapter = _adapter(ws)
    mid = await adapter.send_to_user("lisi", "**通知**")
    assert mid == "mid1"
    ws.send_message.assert_awaited_once_with("lisi", {"msgtype": "markdown", "markdown": {"content": "**通知**"}})


async def test_adapter_send_file_routes_image() -> None:
    ws = AsyncMock(spec=WeComBotTransport)
    ws.upload_media.return_value = {"media_id": "md_img", "type": "image"}
    adapter = _adapter(ws)
    mid = await adapter.send_file(_event(), "a.png", b"png")
    assert mid == "md_img"
    ws.upload_media.assert_awaited_once()
    assert ws.upload_media.call_args.kwargs["media_type"] == "image"
    sent = ws.reply.call_args[0][1]
    assert sent["msgtype"] == "image"
    assert sent["image"] == {"media_id": "md_img"}


# --- media decrypt ---


def test_decrypt_file_roundtrip() -> None:
    aeskey = base64.b64encode(os.urandom(32)).decode()
    key = base64.b64decode(aeskey)
    iv = key[:16]
    plaintext = b"hello wecom media"
    pad = 16 - len(plaintext) % 16
    payload = plaintext + bytes([pad]) * pad
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    encrypted = enc.update(payload) + enc.finalize()
    assert _decrypt_file(encrypted, aeskey) == plaintext
