"""WeCom bot channel adapter — outbound formatting over the long-connection WS.

Everything the adapter sends travels over the same WebSocket the transport
owns, so the adapter delegates to ``self.transport`` (a ``WeComBotTransport``)
for reply / proactive send / media upload. Media downloads are plain HTTPS
GETs whose URL is pre-signed by WeCom, AES-256-CBC encrypted with a per-message
``aeskey`` carried in the callback.
"""

from __future__ import annotations

import base64
import contextlib
import secrets
import time
from uuid import UUID

import httpx
import structlog

from aio_agent_platform.channels.adapter import ChannelAdapter, InboundEvent, Transport
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
from aio_agent_platform.channels.wecom_bot.ws_transport import (
    StreamExpiredError,
    WeComBotTransport,
)

logger = structlog.get_logger()

# 流式占位：开启流式时先发一条可见的「思考中」消息（企微忽略纯空白内容）。
_THINKING_MESSAGE = "<think></think>"
# 流式超时/过期后兜底的可见文案。
_STREAM_FALLBACK_TEXT = "✅ 已收到"

_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
_VIDEO_EXTS = {"mp4"}

_HTTP_TIMEOUT = 30.0


class WeComBotAdapter(ChannelAdapter):
    """Adapter for WeCom API-mode smart bots (WebSocket long connection)."""

    supports_file_send = True
    # 长连接消息体上限由企微侧约束；保守取与飞书一致的 3500 字节。
    max_message_bytes = 3500
    # 分片上传上限 100 × 512KB ≈ 50MB。
    max_file_size_bytes = 50 * 1024 * 1024

    def __init__(
        self,
        channel_id: UUID,
        pipeline: ChannelInboundPipeline,
    ):
        self.channel_id = channel_id
        self.pipeline = pipeline
        self._stream_req_id = ""
        self._stream_chatid = ""

    @property
    def ws(self) -> WeComBotTransport | None:
        """The transport doubles as the outbound WS client."""
        return self.transport if isinstance(self.transport, WeComBotTransport) else None

    def set_transport(self, transport: Transport) -> None:
        self.transport = transport

    # ---- outbound helpers ----

    def _req_id(self, event: InboundEvent) -> str:
        return (event.raw or {}).get("req_id") or ""

    def _chatid(self, event: InboundEvent) -> str:
        return (event.raw or {}).get("chatid") or event.chat_id

    async def _stream_reply(self, req_id: str, stream_id: str, text: str, finish: bool) -> None:
        """Send a ``aibot_respond_msg`` streaming frame (plain text or markdown)."""
        ws = self.ws
        if ws is None:
            return
        await ws.reply(req_id, {
            "msgtype": "stream",
            "stream": {"id": stream_id, "finish": finish, "content": text},
        })

    async def _fallback_proactive(self, chatid: str, text: str) -> None:
        """Stream expired → proactively send the full text as markdown."""
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send_message(chatid, {
                "msgtype": "markdown",
                "markdown": {"content": text},
            })
        except Exception:
            logger.warning("wecom_bot_fallback_send_failed", exc_info=True)

    # ---- send ----

    async def send(self, event: InboundEvent, text: str) -> str | None:
        """One-shot reply to the originating chat (streaming frame, finish=true)."""
        req_id = self._req_id(event)
        if not req_id:
            return None
        stream_id = _new_stream_id()
        try:
            await self._stream_reply(req_id, stream_id, text, finish=True)
            return stream_id
        except StreamExpiredError:
            await self._fallback_proactive(self._chatid(event), text)
            return stream_id
        except Exception:
            logger.warning("wecom_bot_send_failed", exc_info=True)
            return None

    async def send_markdown(self, event: InboundEvent, text: str) -> str | None:
        # 流式内容天然支持 markdown，与普通发送走同一通道。
        return await self.send(event, text)

    async def update(self, message_id: str, text: str) -> None:
        raise NotImplementedError("wecom_bot uses stream updates, not message update")

    # ---- native streaming ----

    async def start_stream(self, event: InboundEvent, text: str) -> str | None:
        req_id = self._req_id(event)
        if not req_id:
            return None
        stream_id = _new_stream_id()
        self._stream_req_id = req_id
        self._stream_chatid = self._chatid(event)
        try:
            await self._stream_reply(
                req_id, stream_id, text or _THINKING_MESSAGE, finish=False
            )
            return stream_id
        except Exception:
            logger.warning("wecom_bot_stream_start_failed", exc_info=True)
            self._stream_req_id = ""
            return None

    async def update_stream(self, stream_id: str, text: str, sequence: int) -> bool:
        if not self._stream_req_id:
            return False
        try:
            await self._stream_reply(self._stream_req_id, stream_id, text, finish=False)
            return True
        except Exception:
            logger.warning("wecom_bot_stream_update_failed", exc_info=True)
            return False

    async def finish_stream(self, stream_id: str, text: str, sequence: int) -> bool:
        if not self._stream_req_id:
            return False
        content = text or _STREAM_FALLBACK_TEXT
        try:
            await self._stream_reply(self._stream_req_id, stream_id, content, finish=True)
            return True
        except StreamExpiredError:
            await self._fallback_proactive(self._stream_chatid, content)
            return True
        except Exception:
            logger.warning("wecom_bot_stream_finish_failed", exc_info=True)
            return False

    # ---- proactive push (cron etc.) ----

    async def send_to_user(self, external_id: str, text: str) -> str | None:
        ws = self.ws
        if ws is None:
            return None
        try:
            ack = await ws.send_message(external_id, {
                "msgtype": "markdown",
                "markdown": {"content": text},
            })
            return (ack.get("body") or {}).get("msgid") or None
        except Exception:
            logger.warning("wecom_bot_send_to_user_failed", exc_info=True)
            return None

    # ---- media ----

    async def send_file(self, event: InboundEvent, filename: str, data: bytes) -> str | None:
        req_id = self._req_id(event)
        ws = self.ws
        if ws is None or not req_id:
            return None
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        media_type = "image" if ext in _IMAGE_EXTS else "video" if ext in _VIDEO_EXTS else "file"
        try:
            uploaded = await ws.upload_media(data, media_type=media_type, filename=filename)
        except Exception:
            logger.warning("wecom_bot_upload_failed", exc_info=True)
            return None
        media_id = uploaded["media_id"]
        content: dict = {"media_id": media_id}
        if media_type == "video":
            content["title"] = filename
        try:
            await ws.reply(req_id, {"msgtype": media_type, media_type: content})
            return media_id
        except Exception:
            logger.warning("wecom_bot_media_send_failed", exc_info=True)
            return None

    async def download_attachment(self, event: InboundEvent) -> bytes | None:
        if not event.attachment:
            return None
        url = event.attachment.resource_key
        aeskey = (event.raw or {}).get("aeskey")
        try:
            encrypted = await _http_download(url)
        except Exception:
            logger.warning("wecom_bot_media_download_failed", exc_info=True)
            return None
        if not aeskey:
            return encrypted
        try:
            return _decrypt_file(encrypted, aeskey)
        except Exception:
            logger.warning("wecom_bot_media_decrypt_failed", exc_info=True)
            return None

    async def stop(self) -> None:
        if self.transport:
            await self.transport.stop()


def _new_stream_id() -> str:
    return f"stream_{int(time.time() * 1000)}_{secrets.token_hex(6)}"


async def _http_download(url: str) -> bytes:
    """Download the pre-signed WeCom media URL (no auth headers needed)."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _decrypt_file(encrypted: bytes, aeskey: str) -> bytes:
    """AES-256-CBC decrypt (key = base64(aeskey), IV = key[:16], PKCS#7)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(aeskey)
    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor()
    padded = dec.update(encrypted) + dec.finalize()
    # PKCS#7 手动去填充（企微按 32 字节块填充，pad 值 1..32）。
    if not padded:
        return b""
    pad_len = padded[-1]
    if 1 <= pad_len <= 32:
        with contextlib.suppress(ValueError):
            if padded.endswith(bytes([pad_len]) * pad_len):
                return padded[:-pad_len]
    return padded
