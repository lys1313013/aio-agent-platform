"""Shared webhook routing — one router for all webhook-mode channels.

Channels with ``mode == "webhook"`` register a :class:`Transport` under their
globally-unique ``channel_key``. Requests arrive via a single generic route
(``/api/channels/webhook/{channel_key}``); the legacy Feishu path
(``/api/channels/feishu/events/{channel_key}``) maps to the *same* handler so
callback URLs already configured in the Feishu console keep working. Each
transport's ``handle_webhook`` implements the platform-specific protocol
(signature verification, decryption, normalization).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from aio_agent_platform.channels.adapter import Transport

# channel_key -> Transport instance. Populated by webhook transports on start().
_webhook_registry: dict[str, Transport] = {}


def register_webhook(channel_key: str, transport: Transport) -> None:
    _webhook_registry[channel_key] = transport


def unregister_webhook(channel_key: str) -> None:
    _webhook_registry.pop(channel_key, None)


async def _dispatch(channel_key: str, request: Request) -> Response:
    transport = _webhook_registry.get(channel_key)
    if transport is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return await transport.handle_webhook(request)


def build_webhook_router() -> APIRouter:
    """Build the router serving all webhook channels.

    Mounted once at app startup. The generic route serves new channels; the
    legacy Feishu route stays as an alias to the same handler (not a redirect —
    callback clients don't reliably follow 307s).
    """
    router = APIRouter(prefix="/api/channels", tags=["channels"])

    @router.api_route("/webhook/{channel_key}", methods=["GET", "POST"])
    async def channel_webhook(channel_key: str, request: Request) -> Response:
        return await _dispatch(channel_key, request)

    @router.api_route("/feishu/events/{channel_key}", methods=["GET", "POST"])
    async def feishu_webhook_legacy(channel_key: str, request: Request) -> Response:
        return await _dispatch(channel_key, request)

    return router
