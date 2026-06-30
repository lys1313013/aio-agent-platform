"""Public endpoints — no authentication required."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from aio_agent_platform.storage.client import ObjectStorage

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/images/{key:path}")
async def get_public_image(key: str):
    """Serve an image from object storage without authentication.

    The key should be the full object key (e.g. ``chat-attachments/user_id/session_id/file.png``).
    Only serves files from the ``chat-attachments/`` prefix for security.
    """
    if not key.startswith("chat-attachments/"):
        raise HTTPException(status_code=404, detail="Not found")

    try:
        storage = ObjectStorage()
        data = storage.get(key)
        stat = storage.stat(key)
        content_type = stat.content_type if stat and stat.content_type else "image/png"

        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail="Not found") from e
