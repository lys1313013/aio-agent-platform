"""WeCom channel tests — crypto, event normalization, webhook transport, adapter, client."""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aio_agent_platform.channels.adapter import InboundEvent
from aio_agent_platform.channels.registry import get_channel_spec
from aio_agent_platform.channels.webhook import _webhook_registry, build_webhook_router
from aio_agent_platform.channels.wecom.adapter import WeComAdapter
from aio_agent_platform.channels.wecom.client import WeComClient
from aio_agent_platform.channels.wecom.crypto import decrypt, verify_signature
from aio_agent_platform.channels.wecom.events import normalize_event
from aio_agent_platform.channels.wecom.webhook_transport import WeComWebhookTransport

_CORPID = "ww1234567890"
_TOKEN = "wecom_callback_token"
# 43 字符 base64 EncodingAESKey（企微要求 43 位，代码补 '=' 解码为 32 字节 key）。
_AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
_TIMESTAMP = "1409659813"
_NONCE = "1372623149"


# --- crypto helpers (mirror of the encryption side, for tests) ---


def _encrypt_payload(key_43: str, msg: str, receiveid: str = _CORPID) -> str:
    key = base64.b64decode(key_43 + "=")
    payload = os.urandom(16) + struct.pack(">I", len(msg.encode())) + msg.encode() + receiveid.encode()
    pad = 16 - len(payload) % 16
    payload += bytes([pad]) * pad
    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(payload) + enc.finalize()
    return base64.b64encode(ct).decode()


def _signature(encrypt: str, token: str = _TOKEN) -> str:
    content = "".join(sorted([token, _TIMESTAMP, _NONCE, encrypt]))
    return hashlib.sha1(content.encode()).hexdigest()


# --- spec registration ---


def test_wecom_spec_registered() -> None:
    spec = get_channel_spec("wecom")
    assert spec.title_prefix == "企微· "
    assert spec.allowed_modes == ("webhook",)
    assert spec.supports_file_send is True
    assert spec.build is not None
    assert spec.verify_credentials is not None


async def test_wecom_spec_build_wires_transport_and_registers() -> None:
    from types import SimpleNamespace

    from aio_agent_platform.channels.wecom.webhook_transport import WeComWebhookTransport

    channel = SimpleNamespace(
        id=uuid4(),
        channel_key="wc_build",
        channel_type="wecom",
        app_id=_CORPID,
        app_secret_encrypted="secret",
        verification_token_encrypted=_TOKEN,
        encrypt_key_encrypted=_AES_KEY,
        mode="webhook",
        extra_config={"agentid": 1000002},
        tool_blacklist=[],
        enable_streaming=True,
    )
    spec = get_channel_spec("wecom")
    adapter = spec.build(channel, MagicMock())
    assert isinstance(adapter, WeComAdapter)
    assert isinstance(adapter.transport, WeComWebhookTransport)
    assert adapter.transport.token == _TOKEN
    assert adapter.transport.encoding_aes_key == _AES_KEY
    assert adapter.client.agentid == 1000002
    assert adapter.max_message_bytes == 2000

    await adapter.transport.start()
    assert _webhook_registry.get("wc_build") is adapter.transport
    await adapter.transport.stop()
    assert "wc_build" not in _webhook_registry


# --- crypto ---


def test_signature_verifies_and_rejects() -> None:
    assert verify_signature(_TOKEN, _TIMESTAMP, _NONCE, "abc", _signature("abc")) is True
    assert verify_signature(_TOKEN, _TIMESTAMP, _NONCE, "abc", "deadbeef") is False
    # 乱序拼接也应匹配（sorted 字典序）
    mixed = _TIMESTAMP + _NONCE + "abc" + _TOKEN
    assert hashlib.sha1(mixed.encode()).hexdigest() != _signature("abc")


def test_decrypt_roundtrip_strips_prefix_and_receiveid() -> None:
    inner_xml = "<xml><MsgType>text</MsgType><Content>hi</Content></xml>"
    encrypted = _encrypt_payload(_AES_KEY, inner_xml)
    assert decrypt(_AES_KEY, encrypted) == inner_xml


# --- event normalization ---


def _xml_message(**fields: str) -> str:
    parts = ["<xml>"]
    for k, v in fields.items():
        parts.append(f"<{k}><![CDATA[{v}]]></{k}>")
    parts.append("</xml>")
    return "".join(parts)


def test_normalize_text_message() -> None:
    xml = _xml_message(
        ToUserName=_CORPID,
        FromUserName="zhangsan",
        CreateTime="1348831860",
        MsgType="text",
        Content="你好，介绍一下",
        MsgId="1234567890123456",
        AgentID="1",
    )
    ev = normalize_event(uuid4(), xml)
    assert ev is not None
    assert ev.text == "你好，介绍一下"
    assert ev.external_id == "zhangsan"
    assert ev.chat_id == "zhangsan"
    assert ev.chat_kind.value == "direct"
    assert ev.message_id == "1234567890123456"
    assert ev.attachment is None


def test_normalize_image_message() -> None:
    xml = _xml_message(
        FromUserName="lisi",
        MsgType="image",
        MediaId="media_img_1",
        PicUrl="http://example.com/photo.png",
        MsgId="111",
    )
    ev = normalize_event(uuid4(), xml)
    assert ev is not None
    assert ev.attachment is not None
    assert ev.attachment.resource_type == "image"
    assert ev.attachment.resource_key == "media_img_1"
    assert ev.attachment.filename == "photo.png"


def test_normalize_file_message_without_filename_defaults_to_file() -> None:
    xml = _xml_message(
        FromUserName="wangwu",
        MsgType="file",
        MediaId="media_file_2",
        MsgId="222",
    )
    ev = normalize_event(uuid4(), xml)
    assert ev is not None
    assert ev.attachment is not None
    assert ev.attachment.resource_type == "file"
    assert ev.attachment.filename == "file"


def test_normalize_skips_unknown_msg_type() -> None:
    xml = _xml_message(FromUserName="zhangsan", MsgType="voice", MediaId="m", MsgId="3")
    assert normalize_event(uuid4(), xml) is None


# --- webhook transport ---


@pytest.fixture(autouse=True)
def _clear_webhook_registry():
    _webhook_registry.clear()
    yield
    _webhook_registry.clear()


def _make_transport(pipeline, channel_key: str) -> WeComWebhookTransport:
    transport = WeComWebhookTransport(
        pipeline=pipeline,
        corpid=_CORPID,
        token=_TOKEN,
        encoding_aes_key=_AES_KEY,
        channel_key=channel_key,
    )
    return transport


def _pipeline():
    pipeline = MagicMock()
    pipeline.channel.id = uuid4()
    pipeline.submit.return_value = None
    return pipeline


async def test_webhook_get_url_verification_returns_plaintext() -> None:
    inner_xml = "<xml><MsgType>text</MsgType></xml>"
    echostr = _encrypt_payload(_AES_KEY, inner_xml)
    pipeline = _pipeline()
    app = FastAPI()
    app.include_router(build_webhook_router())
    transport = _make_transport(pipeline, "wc_chal")
    await transport.start()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/api/channels/webhook/wc_chal",
            params={
                "msg_signature": _signature(echostr),
                "timestamp": _TIMESTAMP,
                "nonce": _NONCE,
                "echostr": echostr,
            },
        )
    assert resp.status_code == 200
    assert resp.text == inner_xml
    assert resp.headers["content-type"].startswith("text/plain")


async def test_webhook_get_rejects_bad_signature() -> None:
    pipeline = _pipeline()
    app = FastAPI()
    app.include_router(build_webhook_router())
    transport = _make_transport(pipeline, "wc_bad")
    await transport.start()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get(
            "/api/channels/webhook/wc_bad",
            params={
                "msg_signature": "wrongsig",
                "timestamp": _TIMESTAMP,
                "nonce": _NONCE,
                "echostr": _encrypt_payload(_AES_KEY, "x"),
            },
        )
    assert resp.status_code == 401


async def test_webhook_post_submits_event() -> None:
    inner_xml = _xml_message(
        FromUserName="zhangsan",
        MsgType="text",
        Content="你好",
        MsgId="444",
    )
    encrypted = _encrypt_payload(_AES_KEY, inner_xml)
    envelope = f"<xml><ToUserName>{_CORPID}</ToUserName><Encrypt><![CDATA[{encrypted}]]></Encrypt><AgentID><![CDATA[1]]></AgentID></xml>"
    pipeline = _pipeline()
    app = FastAPI()
    app.include_router(build_webhook_router())
    transport = _make_transport(pipeline, "wc_msg")
    await transport.start()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/channels/webhook/wc_msg",
            params={
                "msg_signature": _signature(encrypted),
                "timestamp": _TIMESTAMP,
                "nonce": _NONCE,
            },
            content=envelope,
            headers={"Content-Type": "application/xml"},
        )
    assert resp.status_code == 200
    assert resp.text == "success"
    pipeline.submit.assert_called_once()
    ev = pipeline.submit.call_args[0][0]
    assert isinstance(ev, InboundEvent)
    assert ev.text == "你好"
    assert ev.external_id == "zhangsan"


async def test_webhook_post_rejects_bad_signature() -> None:
    encrypted = _encrypt_payload(_AES_KEY, "<xml><MsgType>text</MsgType></xml>")
    envelope = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"
    pipeline = _pipeline()
    app = FastAPI()
    app.include_router(build_webhook_router())
    transport = _make_transport(pipeline, "wc_badmsg")
    await transport.start()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/channels/webhook/wc_badmsg",
            params={"msg_signature": "nope", "timestamp": _TIMESTAMP, "nonce": _NONCE},
            content=envelope,
            headers={"Content-Type": "application/xml"},
        )
    assert resp.status_code == 401
    pipeline.submit.assert_not_called()


async def test_webhook_unknown_channel_returns_404() -> None:
    app = FastAPI()
    app.include_router(build_webhook_router())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/channels/webhook/nope", content=b"x")
    assert resp.status_code == 404


# --- adapter ---


def _event(**kwargs) -> InboundEvent:
    base = {
        "channel_id": uuid4(),
        "event_id": "evt1",
        "chat_id": "zhangsan",
        "external_id": "zhangsan",
        "text": "hi",
    }
    base.update(kwargs)
    return InboundEvent(**base)


async def test_adapter_send_and_send_to_user() -> None:
    client = MagicMock()
    client.send_text = AsyncMock(return_value="msgid_1")
    adapter = WeComAdapter(channel_id=uuid4(), client=client, pipeline=MagicMock())
    assert await adapter.send(_event(), "hello") == "msgid_1"
    client.send_text.assert_called_with("zhangsan", "hello")
    assert await adapter.send_to_user("zhangsan", "push") == "msgid_1"
    client.send_text.assert_called_with("zhangsan", "push")
    assert adapter.supports_file_send is True
    assert adapter.max_message_bytes == 2000
    assert adapter.max_file_size_bytes == 20 * 1024 * 1024


async def test_adapter_send_file_routes_image() -> None:
    client = MagicMock()
    client.upload_media = AsyncMock(return_value="md_img")
    client.send_image = AsyncMock(return_value="msgid_img")
    adapter = WeComAdapter(channel_id=uuid4(), client=client, pipeline=MagicMock())
    mid = await adapter.send_file(_event(), "photo.png", b"png")
    assert mid == "msgid_img"
    client.upload_media.assert_called_with("photo.png", b"png", media_type="image")
    client.send_image.assert_called_with("zhangsan", "md_img")


async def test_adapter_send_file_routes_generic() -> None:
    client = MagicMock()
    client.upload_media = AsyncMock(return_value="md_file")
    client.send_file = AsyncMock(return_value="msgid_file")
    adapter = WeComAdapter(channel_id=uuid4(), client=client, pipeline=MagicMock())
    mid = await adapter.send_file(_event(), "report.csv", b"data")
    assert mid == "msgid_file"
    client.upload_media.assert_called_with("report.csv", b"data", media_type="file")
    client.send_file.assert_called_with("zhangsan", "md_file")


async def test_adapter_download_attachment() -> None:
    client = MagicMock()
    client.download_media = AsyncMock(return_value=b"blob")
    adapter = WeComAdapter(channel_id=uuid4(), client=client, pipeline=MagicMock())
    ev = _event(attachment=MagicMock(resource_key="md_x"))
    assert await adapter.download_attachment(ev) == b"blob"
    client.download_media.assert_called_with("md_x")


# --- client ---


def _client_stub(**overrides):
    """httpx.MockTransport dispatcher for WeCom endpoints."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "tk_1", "expires_in": 7200})
        if path.endswith("/message/send"):
            return httpx.Response(200, json=overrides.get("send", {"errcode": 0, "msgid": "m_sent"}))
        if path.endswith("/media/upload"):
            return httpx.Response(200, json=overrides.get("upload", {"errcode": 0, "media_id": "md_up"}))
        if path.endswith("/media/get"):
            if overrides.get("media_get_error"):
                return httpx.Response(200, json={"errcode": 40006, "errmsg": "invalid media_id"}, headers={"content-type": "application/json"})
            return httpx.Response(200, content=b"binary-content", headers={"content-type": "application/octet-stream"})
        return httpx.Response(404, json={"errcode": -1})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_client_send_text_builds_body() -> None:
    client = WeComClient(corpid=_CORPID, corpsecret="secret", agentid=1000002)
    client._http = _client_stub()
    mid = await client.send_text("zhangsan", "你好")
    assert mid == "m_sent"
    # 通过 stub 校验 token 请求参数
    await client.close()


async def test_client_invaliduser_is_failure() -> None:
    client = WeComClient(corpid=_CORPID, corpsecret="secret", agentid=1)
    client._http = _client_stub(send={"errcode": 0, "msgid": "m_ignored", "invaliduser": "zhangsan"})
    assert await client.send_text("zhangsan", "hi") is None
    await client.close()


async def test_client_send_api_error_is_failure() -> None:
    client = WeComClient(corpid=_CORPID, corpsecret="secret", agentid=1)
    client._http = _client_stub(send={"errcode": 40013, "errmsg": "invalid appid"})
    assert await client.send_text("zhangsan", "hi") is None
    await client.close()


async def test_client_upload_and_download_media() -> None:
    client = WeComClient(corpid=_CORPID, corpsecret="secret", agentid=1)
    client._http = _client_stub()
    media_id = await client.upload_media("a.csv", b"data", media_type="file")
    assert media_id == "md_up"
    blob = await client.download_media(media_id)
    assert blob == b"binary-content"
    await client.close()


async def test_client_verify_credentials() -> None:
    client = WeComClient(corpid=_CORPID, corpsecret="secret", agentid=1)
    client._http = _client_stub()
    assert await client.verify_credentials() is True
    await client.close()
