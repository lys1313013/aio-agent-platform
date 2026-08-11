"""Webpage artifact routes — token-gated page serving + access URL minting."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from aio_agent_platform.artifacts.webpage import (
    _object_key,
    mint_page_token,
    page_url,
    verify_page_token,
)
from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.storage.client import ObjectStorage

logger = structlog.get_logger()

router = APIRouter(prefix="/api/webpages", tags=["webpages"])


@router.get("/{page_id}")
async def get_webpage(page_id: str, token: str = Query(...)) -> Response:
    """Serve a generated webpage inline (text/html), authorized by HMAC token.

    No JWT: this endpoint is meant to be reached from a separate pages origin
    inside a sandboxed iframe, which can't carry the main app's credentials.
    """
    user_id = verify_page_token(token, page_id)
    if user_id is None:
        raise HTTPException(status_code=403, detail="无效或已过期的访问令牌")
    try:
        data = ObjectStorage().get(_object_key(user_id, page_id))
    except Exception:
        raise HTTPException(status_code=404, detail="网页不存在或已被删除")
    return Response(
        content=data,
        media_type="text/html",
        headers={
            # Belt-and-suspenders next to the iframe sandbox attribute: opaque
            # origin, scripts allowed (pages are meant to be interactive).
            "Content-Security-Policy": "sandbox allow-scripts",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


class WebpageAccessResponse(BaseModel):
    url: str


@router.post("/{page_id}/access")
async def mint_webpage_access(
    page_id: str, current_user: CurrentUser
) -> WebpageAccessResponse:
    """Mint a fresh access URL for a page owned by the current user."""
    storage = ObjectStorage()
    try:
        exists = storage.exists(_object_key(str(current_user.id), page_id))
    except Exception as e:
        logger.warning("webpage_access_storage_failed", error=str(e))
        raise HTTPException(status_code=503, detail="存储服务不可用")
    if not exists:
        raise HTTPException(status_code=404, detail="网页不存在")
    token = mint_page_token(str(current_user.id), page_id)
    return WebpageAccessResponse(url=page_url(page_id, token))
