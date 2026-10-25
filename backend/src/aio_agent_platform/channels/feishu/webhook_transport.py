"""Feishu Webhook transport — receives events via HTTP POST.

The shared webhook registry and router live in ``channels.webhook``; this
module keeps the historical import surface used by existing tests
(``FeishuWebhookTransport`` / ``register_webhook`` / ``_webhook_registry`` /
``build_webhook_router``) and implements the Feishu-specific protocol in
``FeishuWebhookTransport.handle_webhook``.

Responsibilities:
  - Respond to Feishu's URL verification challenge.
  - Verify the X-Lark-Signature header using encrypt_key when encryption is enabled.
  - Decrypt the ``encrypt`` field (when encrypt_key is set).
  - Normalize valid ``im.message.receive_v1`` events into InboundEvent and
    hand them to the pipeline.
"""

from __future__ import annotations

import hmac
import json

import structlog
from fastapi import HTTPException, Request, Response

from aio_agent_platform.channels.adapter import Transport, TransportState
from aio_agent_platform.channels.feishu.crypto import decrypt_event, verify_signature
from aio_agent_platform.channels.feishu.events import normalize_event, normalize_recall_event
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
from aio_agent_platform.channels.webhook import (
    _webhook_registry,  # noqa: F401  (re-exported for test compatibility)
    build_webhook_router,  # noqa: F401  (re-exported for test compatibility)
)
from aio_agent_platform.channels.webhook import (
    register_webhook as _register_webhook,
)
from aio_agent_platform.channels.webhook import (
    unregister_webhook as _unregister_webhook,
)

logger = structlog.get_logger()


class FeishuWebhookTransport(Transport):
    """Webhook transport.

    Unlike the WebSocket transport, the webhook doesn't run a persistent
    connection — instead it registers itself in the shared webhook registry
    (keyed by ``channel_key``) on ``start()``. The router dispatches requests
    here via ``handle_webhook``.
    """

    def __init__(
        self,
        pipeline: ChannelInboundPipeline,
        channel,
        channel_key: str | None = None,
    ):
        self.pipeline = pipeline
        self.channel = channel
        self.channel_key = channel_key or getattr(channel, "channel_key", None)
        self.state = TransportState.CONNECTED

    async def start(self) -> None:
        self.state = TransportState.CONNECTED
        _register_webhook(self.channel_key, self)

    async def stop(self) -> None:
        _unregister_webhook(self.channel_key)
        self.state = TransportState.DISCONNECTED

    async def handle_webhook(self, request: Request) -> Response:
        channel = self.channel
        body = await request.body()

        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid event payload")

        vtoken = getattr(channel, "_verification_token", None)
        encrypt_key = getattr(channel, "_encrypt_key", None)
        if not vtoken and not encrypt_key:
            raise HTTPException(status_code=401, detail="callback authentication not configured")

        # Encrypted callbacks, including URL verification, must be decrypted first.
        # Never accept a plaintext downgrade when this channel enables encryption.
        if encrypt_key:
            if not isinstance(payload.get("encrypt"), str) or not payload["encrypt"]:
                raise HTTPException(status_code=401, detail="encrypted callback required")
            try:
                payload = decrypt_event(encrypt_key, payload["encrypt"])
            except Exception:
                raise HTTPException(status_code=400, detail="decrypt failed")
        elif payload.get("encrypt"):
            raise HTTPException(status_code=401, detail="encryption key not configured")

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid event payload")
        header = payload.get("header") or {}
        if not isinstance(header, dict):
            raise HTTPException(status_code=400, detail="invalid event header")
        received_token = payload.get("token") if payload.get("type") == "url_verification" else header.get("token")
        if vtoken and (
            not isinstance(received_token, str)
            or not hmac.compare_digest(received_token.encode(), vtoken.encode())
        ):
            raise HTTPException(status_code=401, detail="invalid token")

        # Feishu's URL challenge does not require the event signature headers.
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise HTTPException(status_code=400, detail="missing challenge")
            return Response(content=json.dumps({"challenge": challenge}), media_type="application/json")

        # Official event signature: SHA256(timestamp + nonce + encrypt_key + raw body).
        if encrypt_key:
            signature = request.headers.get("X-Lark-Signature", "")
            timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            if not timestamp or not nonce or not signature or not verify_signature(
                encrypt_key, timestamp, nonce, body, signature
            ):
                raise HTTPException(status_code=401, detail="invalid signature")

        # 4. Schema v2.0 — event is wrapped in a header envelope.
        header = payload.get("header") or {}
        event_type = header.get("event_type", "")
        event_id = header.get("event_id", "")

        if event_type == "im.message.receive_v1":
            inbound = normalize_event(
                channel_id=channel.id,
                event_id=event_id,
                event=payload,
                bot_app_id=channel.app_id,
            )
        elif event_type == "im.message.recalled_v1":
            inbound = normalize_recall_event(
                channel_id=channel.id,
                event_id=event_id,
                event=payload,
            )
        else:
            return Response(status_code=200)

        if inbound is None:
            return Response(status_code=200)

        self.pipeline.submit(inbound)
        return Response(status_code=200)


def register_webhook(channel_key: str, pipeline: ChannelInboundPipeline, channel) -> None:
    """Legacy 3-arg registration — build a transport from (pipeline, channel).

    Kept so existing callers (tests, pre-registry code) register a
    ``FeishuWebhookTransport`` under ``channel_key`` with the shared router.
    """
    _register_webhook(
        channel_key,
        FeishuWebhookTransport(
            pipeline=pipeline, channel=channel, channel_key=channel_key
        ),
    )
