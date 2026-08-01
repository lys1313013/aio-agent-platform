"""Feishu Webhook transport — receives events via HTTP POST.

Responsibilities:
  - Respond to Feishu's URL verification challenge.
  - Verify X-Lark-Signature header (when verification_token is set).
  - Decrypt the ``encrypt`` field (when encrypt_key is set).
  - Normalize valid ``im.message.receive_v1`` events into InboundEvent and
    hand them to the pipeline.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, Response

from aio_agent_platform.channels.adapter import Transport, TransportState
from aio_agent_platform.channels.feishu.crypto import decrypt_event, verify_signature
from aio_agent_platform.channels.feishu.events import normalize_event
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline

logger = structlog.get_logger()


class FeishuWebhookTransport(Transport):
    """Webhook transport.

    Unlike the WebSocket transport, the webhook doesn't run a persistent
    connection — instead it registers a FastAPI route on the shared webhook
    router. The route handler dispatches to the pipeline via
    ``pipeline.submit``.
    """

    def __init__(self, pipeline: ChannelInboundPipeline):
        self.pipeline = pipeline
        self.state = TransportState.CONNECTED  # Webhook is "always on" once registered.

    async def start(self) -> None:
        self.state = TransportState.CONNECTED

    async def stop(self) -> None:
        # Webhook routes are registered globally; remove our channel_key so
        # future events return 404.
        unregister_webhook(self.pipeline.channel.channel_key)
        self.state = TransportState.DISCONNECTED


# --- Shared webhook router ---

# Maps channel_key -> channel runtime info. Populated by the connection
# manager when a webhook channel is enabled.
_webhook_registry: dict[str, dict[str, Any]] = {}


def register_webhook(channel_key: str, pipeline: ChannelInboundPipeline, channel_row: Any) -> None:
    _webhook_registry[channel_key] = {"pipeline": pipeline, "channel": channel_row}


def unregister_webhook(channel_key: str) -> None:
    _webhook_registry.pop(channel_key, None)


def build_webhook_router() -> APIRouter:
    """Build the FastAPI router that serves all webhook channels.

    Mounted once at app startup under /api/channels/feishu/events. The
    ``{channel_key}`` path parameter selects the target channel.
    """
    router = APIRouter(prefix="/api/channels/feishu/events", tags=["channels"])

    @router.post("/{channel_key}")
    async def feishu_webhook(channel_key: str, request: Request) -> Response:
        entry = _webhook_registry.get(channel_key)
        if entry is None:
            raise HTTPException(status_code=404, detail="channel not found")

        channel_row = entry["channel"]
        pipeline: ChannelInboundPipeline = entry["pipeline"]

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
            vtoken = getattr(channel_row, "_verification_token", None)
            if vtoken and payload.get("token") != vtoken:
                raise HTTPException(status_code=401, detail="invalid token")
            return Response(
                content=json.dumps({"challenge": challenge}),
                media_type="application/json",
            )

        # 2. Signature verification (when verification_token set).
        vtoken = getattr(channel_row, "_verification_token", None)
        if vtoken:
            signature = request.headers.get("X-Lark-Signature", "")
            timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            if not signature or not verify_signature(vtoken, timestamp, nonce, body, signature):
                raise HTTPException(status_code=401, detail="invalid signature")

        # 3. Decryption (when encrypt_key set).
        encrypt_key = getattr(channel_row, "_encrypt_key", None)
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
            channel_id=channel_row.id,
            event_id=event_id,
            event=payload,
            bot_app_id=channel_row.app_id,
        )
        if inbound is None:
            return Response(status_code=200)

        pipeline.submit(inbound)
        return Response(status_code=200)

    return router
