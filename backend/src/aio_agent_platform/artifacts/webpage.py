"""Webpage artifact — create_webpage tool + token-based page access.

The agent generates a full HTML document; the handler stores it in object
storage under ``webpages/{user_id}/{page_id}.html`` and returns a small JSON
payload (page_id/title/url) that the frontend renders as a clickable card.

Pages are untrusted content: they are meant to be served from a separate
origin (``PAGES_BASE_URL``) and rendered inside a sandboxed iframe. Because a
separate origin can't carry the main app's JWT, access is authorized by an
HMAC-signed expiring token; the frontend re-mints fresh URLs via the
authenticated ``POST /api/webpages/{page_id}/access`` endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import structlog

from aio_agent_platform.core.config import settings
from aio_agent_platform.storage.client import ObjectStorage

logger = structlog.get_logger()

CREATE_WEBPAGE_TOOL_NAME = "create_webpage"

_MAX_HTML_BYTES = 1 * 1024 * 1024  # 1MB per page
_TOKEN_TTL_SECONDS = 2 * 3600  # short-lived; frontend re-mints on demand


def _object_key(user_id: str, page_id: str) -> str:
    return f"webpages/{user_id}/{page_id}.html"


def mint_page_token(user_id: str, page_id: str) -> str:
    """Mint an HMAC-signed, expiring access token for a page."""
    exp = int(time.time()) + _TOKEN_TTL_SECONDS
    payload = f"{user_id}:{page_id}:{exp}"
    sig = hmac.new(
        settings.jwt.secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def verify_page_token(token: str, page_id: str) -> str | None:
    """Return the owning user_id if the token is valid for ``page_id``."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, tok_page_id, exp_str, sig = raw.rsplit(":", 3)
        payload = f"{user_id}:{tok_page_id}:{exp_str}"
        expected = hmac.new(
            settings.jwt.secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if tok_page_id != page_id:
            return None
        if int(exp_str) < int(time.time()):
            return None
        return user_id
    except Exception:
        return None


def page_url(page_id: str, token: str) -> str:
    """Build the public page URL on the isolated pages origin."""
    base = (
        settings.server.pages_base_url
        or settings.server.server_url
        or f"http://localhost:{settings.server.port}"
    ).rstrip("/")
    return f"{base}/api/webpages/{page_id}?token={token}"


async def handle_create_webpage(
    args: dict,
    user_id: str,
    session_id: str,
    **kwargs: Any,
) -> str:
    """Direct handler for ``create_webpage``: store HTML, return card payload."""
    title = (args.get("title") or "").strip() or "未命名网页"
    html = args.get("html") or ""
    if not html.strip():
        return "错误：html 参数为空。请提供完整的 HTML 文档。"
    data = html.encode("utf-8")
    if len(data) > _MAX_HTML_BYTES:
        return (
            f"错误：HTML 超过大小上限（{_MAX_HTML_BYTES // 1024}KB），"
            f"当前 {len(data) // 1024}KB。请精简内容后重试。"
        )

    page_id = uuid.uuid4().hex
    try:
        ObjectStorage().put(_object_key(user_id, page_id), data, "text/html")
    except Exception as e:
        logger.warning("create_webpage_storage_failed", error=str(e))
        return "错误：网页存储失败，请稍后重试。"

    url = page_url(page_id, mint_page_token(user_id, page_id))
    logger.info(
        "webpage_created", user_id=user_id, page_id=page_id, size=len(data)
    )
    return json.dumps(
        {
            "page_id": page_id,
            "title": title,
            "url": url,
            "hint": "网页已生成。告知用户可以点击下方的网页卡片内嵌预览或新标签页打开，不要在回复中粘贴 HTML 源码。",
        },
        ensure_ascii=False,
    )


ARTIFACT_HANDLERS = {CREATE_WEBPAGE_TOOL_NAME: handle_create_webpage}
