"""WeCom (企业微信) Open API client — gettoken cache, message/media APIs.

Protocol notes (企业内部应用):
  - ``gettoken`` is a **GET** with corpid/corpsecret in the query string.
  - ``message/send`` addresses users by ``touser`` (userid) plus ``agentid`` (int).
  - ``errcode`` == 0 with a non-empty ``invaliduser`` means delivery actually failed.
  - media is uploaded via ``media/upload`` then referenced by ``media_id`` in a
    subsequent ``message/send``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

_BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"
_TOKEN_REFRESH_BUFFER_SECONDS = 60


class WeComAPIError(Exception):
    """Raised when WeCom returns a non-zero errcode for a business call."""


class WeComClient:
    """HTTP client for the WeCom Open API with automatic token refresh."""

    def __init__(self, corpid: str, corpsecret: str, agentid: int):
        self.corpid = corpid
        self.corpsecret = corpsecret
        self.agentid = agentid
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        await self._http.aclose()

    async def access_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        async with self._lock:
            now = time.monotonic()
            if (
                self._token
                and now < self._token_expires_at - _TOKEN_REFRESH_BUFFER_SECONDS
            ):
                return self._token
            resp = await self._http.get(
                f"{_BASE_URL}/gettoken",
                params={"corpid": self.corpid, "corpsecret": self.corpsecret},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise WeComAPIError(f"gettoken failed: {data}")
            self._token = data["access_token"]
            self._token_expires_at = time.monotonic() + data.get("expires_in", 7200)
            logger.info("wecom_token_refreshed", corpid=self.corpid)
            return self._token

    async def verify_credentials(self) -> bool:
        """Return True if the corpid/corpsecret pair is valid."""
        try:
            await self.access_token()
            return True
        except Exception:
            return False

    async def _post(self, path: str, json: dict | None = None, **kwargs: Any) -> dict:
        token = await self.access_token()
        resp = await self._http.post(
            f"{_BASE_URL}{path}",
            params={"access_token": token},
            json=json,
            **kwargs,
        )
        resp.raise_for_status()
        return resp.json()

    # --- Message APIs ---

    async def send_text(self, touser: str, text: str) -> str | None:
        """Send a plain-text message to a user. Returns the msgid or None."""
        data = await self._post(
            "/message/send",
            json={
                "touser": touser,
                "msgtype": "text",
                "agentid": self.agentid,
                "text": {"content": text},
            },
        )
        return self._delivery_message_id(data)

    async def send_image(self, touser: str, media_id: str) -> str | None:
        data = await self._post(
            "/message/send",
            json={
                "touser": touser,
                "msgtype": "image",
                "agentid": self.agentid,
                "image": {"media_id": media_id},
            },
        )
        return self._delivery_message_id(data)

    async def send_file(self, touser: str, media_id: str) -> str | None:
        data = await self._post(
            "/message/send",
            json={
                "touser": touser,
                "msgtype": "file",
                "agentid": self.agentid,
                "file": {"media_id": media_id},
            },
        )
        return self._delivery_message_id(data)

    @staticmethod
    def _delivery_message_id(data: dict) -> str | None:
        """Return msgid on success; treat errcode==0 but invaliduser as failure."""
        if data.get("errcode", 0) != 0:
            logger.warning(
                "wecom_send_failed",
                errcode=data.get("errcode"),
                errmsg=data.get("errmsg"),
            )
            return None
        if data.get("invaliduser"):
            logger.warning("wecom_send_invalid_recipient", invaliduser=data["invaliduser"])
            return None
        return data.get("msgid")

    # --- Media APIs ---

    async def upload_media(self, filename: str, data: bytes, media_type: str) -> str | None:
        """Upload a file/image and return its media_id."""
        token = await self.access_token()
        resp = await self._http.post(
            f"{_BASE_URL}/media/upload",
            params={"access_token": token, "type": media_type},
            files={"media": (filename, data)},
        )
        resp.raise_for_status()
        out = resp.json()
        if out.get("errcode", 0) != 0:
            logger.warning(
                "wecom_media_upload_failed",
                errcode=out.get("errcode"),
                errmsg=out.get("errmsg"),
            )
            return None
        return out.get("media_id")

    async def download_media(self, media_id: str) -> bytes | None:
        """Download a media resource by media_id (valid within 3 days).

        WeCom returns the raw bytes for success and a JSON error body on
        failure, distinguished by content-type.
        """
        token = await self.access_token()
        resp = await self._http.get(
            f"{_BASE_URL}/media/get",
            params={"access_token": token, "media_id": media_id},
        )
        resp.raise_for_status()
        if "json" in resp.headers.get("content-type", ""):
            return None
        return resp.content
