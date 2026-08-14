"""WeCom webhook transport — verifies callback signatures and decrypts messages.

企微企业内部应用回调（接收消息与事件，回调 URL 形如
``{base}/api/channels/webhook/{channel_key}``）：

- GET：URL 验证。query 携带 ``msg_signature``/``timestamp``/``nonce``/``echostr``。
  验签后 AES 解密 echostr，返回解密明文中的消息体（已去 random16/len4/corpid）。
- POST：body 为 XML（含 ``<Encrypt>``），query 携带验签三参。验签 → 解密 →
  解析内层 XML → ``normalize_event`` → ``pipeline.submit``，随后返回 ``"success"``
  （不被动回复，管线异步回话）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import structlog
from fastapi import HTTPException, Request, Response

from aio_agent_platform.channels.adapter import Transport, TransportState
from aio_agent_platform.channels.webhook import register_webhook as _register_webhook
from aio_agent_platform.channels.webhook import (
    unregister_webhook as _unregister_webhook,
)
from aio_agent_platform.channels.wecom.crypto import decrypt, verify_signature
from aio_agent_platform.channels.wecom.events import normalize_event

logger = structlog.get_logger()


def _extract_encrypt(xml_text: str) -> str:
    """Pull the ``<Encrypt>`` CDATA content out of the callback envelope XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    return root.findtext("Encrypt") or ""


class WeComWebhookTransport(Transport):
    """Webhook transport for WeCom internal-app callbacks."""

    def __init__(
        self,
        pipeline,
        corpid: str,
        token: str,
        encoding_aes_key: str,
        channel_key: str | None = None,
    ):
        self.pipeline = pipeline
        self.corpid = corpid
        self.token = token
        self.encoding_aes_key = encoding_aes_key
        self.channel_key = channel_key or getattr(
            getattr(pipeline, "channel", None), "channel_key", None
        )
        self.state = TransportState.CONNECTED

    async def start(self) -> None:
        self.state = TransportState.CONNECTED
        _register_webhook(self.channel_key, self)

    async def stop(self) -> None:
        _unregister_webhook(self.channel_key)
        self.state = TransportState.DISCONNECTED

    def _check_signature(self, request: Request, encrypt: str) -> None:
        if not self.token:
            return  # 未配置回调 Token 时不验签（测试/内网直连场景）
        signature = request.query_params.get("msg_signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        if not signature or not verify_signature(
            self.token, timestamp, nonce, encrypt, signature
        ):
            raise HTTPException(status_code=401, detail="invalid signature")

    async def handle_webhook(self, request: Request) -> Response:
        if request.method == "GET":
            return await self._handle_url_verification(request)
        return await self._handle_message(request)

    async def _handle_url_verification(self, request: Request) -> Response:
        echostr = request.query_params.get("echostr", "")
        if not echostr:
            raise HTTPException(status_code=400, detail="missing echostr")
        self._check_signature(request, echostr)
        try:
            plain = decrypt(self.encoding_aes_key, echostr)
        except Exception:
            logger.warning("wecom_url_verify_decrypt_failed", exc_info=True)
            raise HTTPException(status_code=400, detail="decrypt failed")
        # 返回解密明文消息体本身（纯文本，非加密响应）。
        return Response(content=plain, media_type="text/plain")

    async def _handle_message(self, request: Request) -> Response:
        body = (await request.body()).decode("utf-8", errors="replace")
        encrypt = _extract_encrypt(body)
        if not encrypt:
            raise HTTPException(status_code=400, detail="missing Encrypt")
        self._check_signature(request, encrypt)
        try:
            xml_text = decrypt(self.encoding_aes_key, encrypt)
        except Exception:
            logger.warning("wecom_message_decrypt_failed", exc_info=True)
            raise HTTPException(status_code=400, detail="decrypt failed")

        channel_id = self.pipeline.channel.id
        inbound = normalize_event(channel_id, xml_text)
        if inbound is None:
            return Response(content="success", media_type="text/plain")
        self.pipeline.submit(inbound)
        return Response(content="success", media_type="text/plain")
