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
_RESOURCE_URL = (
    "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{resource_key}"
)
_UPLOAD_FILE_URL = "https://open.feishu.cn/open-apis/im/v1/files"
# 下载消息资源所需权限：im:resource
# 上传文件所需权限：im:resource
# Minimum refresh buffer — refresh 60s before expiry.
_TOKEN_REFRESH_BUFFER_SECONDS = 60

# 飞书 im/v1/files 的 file_type 取值有限（opus/mp4/doc/xls 等），普通文件用 stream。
_FILE_TYPE_BY_EXTENSION = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
    ".pdf": "pdf",
}


class FeishuClient:
    """HTTP client for Feishu Open API with automatic token refresh."""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=60.0)

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

    async def upload_file(
        self, file_bytes: bytes, file_name: str, file_type: str | None = None
    ) -> str | None:
        """Upload a file to Feishu, returning the file_key for later sending.

        The file_key is bound to the app; use it with ``send_file`` to deliver
        a ``msg_type="file"`` message. Requires the ``im:resource`` permission.
        """
        import os

        if file_type is None:
            ext = os.path.splitext(file_name)[1].lower()
            file_type = _FILE_TYPE_BY_EXTENSION.get(ext, "stream")
        token = await self.tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await self._http.post(
                _UPLOAD_FILE_URL,
                headers=headers,
                data={"file_type": file_type, "file_name": file_name},
                files={"file": (file_name, file_bytes)},
            )
        except httpx.HTTPError:
            logger.warning("feishu_upload_file_http_error", file_name=file_name)
            return None
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(
                "feishu_upload_file_failed",
                file_name=file_name,
                code=data.get("code"),
                msg=data.get("msg"),
            )
            return None
        return data.get("data", {}).get("file_key")

    async def send_file(
        self,
        receive_id: str,
        file_key: str,
        reply_to: str | None = None,
        receive_id_type: str = "chat_id",
    ) -> str | None:
        """Send a file message using a previously uploaded file_key."""
        import json as _json

        content = _json.dumps({"file_key": file_key}, ensure_ascii=False)
        return await self.send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="file",
            content=content,
            reply_to=reply_to,
        )

    async def download_resource(
        self, message_id: str, resource_key: str, resource_type: str
    ) -> bytes | None:
        """Download a message resource (file or image) from Feishu.

        Returns the raw bytes, or None on failure. Requires the ``im:resource``
        permission on the app.
        """
        token = await self.tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = _RESOURCE_URL.format(message_id=message_id, resource_key=resource_key)
        try:
            resp = await self._http.get(
                url, headers=headers, params={"type": resource_type}
            )
        except httpx.HTTPError:
            logger.warning(
                "feishu_download_resource_http_error",
                resource_type=resource_type,
            )
            return None
        if resp.status_code != 200:
            # 错误响应是 JSON 封装的 {"code":..,"msg":..}；成功响应是文件二进制流。
            try:
                err = resp.json()
            except Exception:
                err = {}
            logger.warning(
                "feishu_download_resource_failed",
                resource_type=resource_type,
                status=resp.status_code,
                code=err.get("code"),
                msg=err.get("msg"),
            )
            return None
        return resp.content

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
