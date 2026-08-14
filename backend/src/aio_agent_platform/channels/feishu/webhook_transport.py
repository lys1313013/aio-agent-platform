"""Feishu Webhook transport — receives events via HTTP POST.

The shared webhook registry and router live in ``channels.webhook``; this
module keeps the historical import surface used by existing tests
(``FeishuWebhookTransport`` / ``register_webhook`` / ``_webhook_registry`` /
``build_webhook_router``) and implements the Feishu-specific protocol in
``FeishuWebhookTransport.handle_webhook``.

Responsibilities:
  - Respond to Feishu's URL verification challenge.
  - Verify the X-Lark-Signature header (when verification_token is set).
  - Decrypt the ``encrypt`` field (when encrypt_key is set).
  - Normalize valid ``im.message.receive_v1`` events into InboundEvent and
    hand them to the pipeline.
"""

from __future__ import annotations

import json

import structlog
from fastapi import HTTPException, Request, Response

from aio_agent_platform.channels.adapter import Transport, TransportState
from aio_agent_platform.channels.feishu.crypto import decrypt_event, verify_signature
from aio_agent_platform.channels.feishu.events import normalize_event
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

        # 1. URL verification challenge.
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not challenge:
                raise HTTPException(status_code=400, detail="missing challenge")
            vtoken = getattr(channel, "_verification_token", None)
            if vtoken and payload.get("token") != vtoken:
                raise HTTPException(status_code=401, detail="invalid token")
            return Response(
                content=json.dumps({"challenge": challenge}),
                media_type="application/json",
            )

        # 2. Signature verification (when verification_token set).
        vtoken = getattr(channel, "_verification_token", None)
        if vtoken:
            signature = request.headers.get("X-Lark-Signature", "")
            timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            if not signature or not verify_signature(vtoken, timestamp, nonce, body, signature):
                raise HTTPException(status_code=401, detail="invalid signature")

        # 3. Decryption (when encrypt_key set).
        encrypt_key = getattr(channel, "_encrypt_key", None)
        if payload.get("encrypt") and encrypt_key:
            try:
                payload = decrypt_event(encrypt_key, payload["encrypt"])
            except Exception:
                raise HTTPException(status_code=400, detail="decrypt failed")

        # 4. Schema v2.0 — event is wrapped in a header envelope.
        header = payload.get("header") or {}
        event_type = header.get("event_type", "")
        event_id = header.get("event_id", "")

        if event_type != "im.message.receive_v1":
            return Response(status_code=200)

        # 5. Normalize and submit to the pipeline.
        inbound = normalize_event(
            channel_id=channel.id,
            event_id=event_id,
            event=payload,
            bot_app_id=channel.app_id,
        )
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
