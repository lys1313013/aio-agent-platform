"""Feishu Open API client — tenant_access_token cache, send/update messages."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_SEND_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
_UPDATE_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
_REACTIONS_URL = "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
# Minimum refresh buffer — refresh 60s before expiry.
_TOKEN_REFRESH_BUFFER_SECONDS = 60


class FeishuClient:
    """HTTP client for Feishu Open API with automatic token refresh."""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def tenant_access_token(self) -> str:
        """Return a valid tenant_access_token, refreshing if necessary."""
        async with self._lock:
            now = time.monotonic()
            if self._token and now < self._token_expires_at - _TOKEN_REFRESH_BUFFER_SECONDS:
                return self._token
            await self._refresh_token()
            return self._token  # type: ignore[return-value]

    async def _refresh_token(self) -> None:
        resp = await self._http.post(
            _TOKEN_URL,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"tenant_access_token failed: {data}")
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.monotonic() + data.get("expire", 7200)
        logger.info("feishu_token_refreshed", app_id=self.app_id)

    async def verify_credentials(self) -> bool:
        """Return True if the app_id/app_secret pair is valid."""
        try:
            await self._refresh_token()
            return True
        except Exception:
            return False

    # --- Message APIs ---

    async def send_message(
        self,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: str,
        reply_to: str | None = None,
    ) -> str | None:
        """Send a message. Returns the message_id or None on failure."""
        token = await self.tenant_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        params = {"receive_id_type": receive_id_type}
        payload: dict[str, Any] = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        url = _SEND_MSG_URL
        if reply_to:
            url = f"{_SEND_MSG_URL}/{reply_to}/reply"
        resp = await self._http.post(url, headers=headers, params=params, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("feishu_send_failed", code=data.get("code"), msg=data.get("msg"))
            return None
        return data.get("data", {}).get("message_id")

    async def send_text(
        self,
        receive_id: str,
        text: str,
        reply_to: str | None = None,
        receive_id_type: str = "chat_id",
    ) -> str | None:
        import json as _json

        content = _json.dumps({"text": text}, ensure_ascii=False)
        return await self.send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content=content,
            reply_to=reply_to,
        )

    async def send_card_markdown(
        self, receive_id: str, markdown: str, reply_to: str | None = None,
        receive_id_type: str = "chat_id",
    ) -> str | None:
        """Send an interactive card rendering markdown (card schema 2.0)."""
        import json as _json

        card = {
            "schema": "2.0",
            "body": {"elements": [{"tag": "markdown", "content": markdown}]},
        }
        return await self.send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            content=_json.dumps(card, ensure_ascii=False),
            reply_to=reply_to,
        )

    async def update_message(self, message_id: str, text: str) -> bool:
        """Update an existing message's content.

        Uses PUT (edit message). PATCH on this endpoint is card-only and
        returns 230001 'This message is NOT a card' for text messages.
        """
        import json as _json

        token = await self.tenant_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        url = _UPDATE_MSG_URL.format(message_id=message_id)
        content = _json.dumps({"text": text}, ensure_ascii=False)
        payload = {"msg_type": "text", "content": content}
        resp = await self._http.put(url, headers=headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(
                "feishu_update_failed",
                message_id=message_id,
                code=data.get("code"),
                msg=data.get("msg"),
            )
            return False
        return True

    # --- Reaction APIs (typing indicator) ---

    async def add_reaction(self, message_id: str, emoji_type: str) -> str | None:
        """Add an emoji reaction to a message. Returns reaction_id or None."""
        token = await self.tenant_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        resp = await self._http.post(
            _REACTIONS_URL.format(message_id=message_id),
            headers=headers,
            json={"reaction_type": {"emoji_type": emoji_type}},
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(
                "feishu_add_reaction_failed",
                message_id=message_id,
                code=data.get("code"),
                msg=data.get("msg"),
            )
            return None
        return data.get("data", {}).get("reaction_id")

    async def delete_reaction(self, message_id: str, reaction_id: str) -> bool:
        """Remove a previously added reaction."""
        token = await self.tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        resp = await self._http.delete(
            f"{_REACTIONS_URL.format(message_id=message_id)}/{reaction_id}",
            headers=headers,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(
                "feishu_delete_reaction_failed",
                message_id=message_id,
                code=data.get("code"),
                msg=data.get("msg"),
            )
            return False
        return True


class FeishuAPIError(Exception):
    pass
