"""Chat attachment storage — thin wrapper over ObjectStorage for chat image uploads.

Stores user-uploaded images in the unified MinIO bucket, scoped by
user_id and (optionally) session_id. Used for two purposes:
1. Generating presigned download URLs for the frontend <img> preview and
   for LLM providers that accept URL sources (current message).
2. Re-feeding base64 data URIs to LLM providers when re-hydrating
   conversation history (URLs may have expired since first upload).
"""

from __future__ import annotations

import base64
import re
from pathlib import PurePosixPath
from uuid import uuid4

from aio_agent_platform.storage.client import ObjectStorage

ALLOWED_MIME: set[str] = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}
MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB per image


class AttachmentAccessError(ValueError):
    """An image reference is outside the authorized owner/session scope."""


def validate_attachment_key(key: str, *, user_id: str, session_id: str | None) -> None:
    """Validate the exact object namespace before any storage access.

    The caller must obtain user/session IDs from an authenticated, authorized
    execution context, never from the attachment or its persisted metadata.
    """
    parts = key.split("/") if isinstance(key, str) else []
    if (
        not user_id
        or len(parts) != 4
        or parts[0] != "chat-attachments"
        or parts[1] != str(user_id)
        or parts[2] not in {"_pending", str(session_id) if session_id else "_pending"}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parts[3])
        or parts[3] in {".", ".."}
    ):
        raise AttachmentAccessError("图片不属于当前用户或会话")


def validate_attachment_refs(
    attachments: list[dict] | None, *, user_id: str, session_id: str | None,
) -> list[dict] | None:
    """Authorize all references, including text-only models, without I/O.

    URLs are always constructed by the server. Declared MIME is only a hint;
    storage metadata and actual bytes are checked again before model input.
    """
    if not attachments:
        return attachments
    result = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise AttachmentAccessError("无效的图片引用")
        key = attachment.get("key")
        validate_attachment_key(key, user_id=user_id, session_id=session_id)
        if attachment.get("mime") not in ALLOWED_MIME:
            raise AttachmentAccessError("不支持的图片格式")
        result.append({**attachment, "url": ChatAttachmentStorage.get_public_url(key)})
    return result


class ChatAttachmentStorage:
    """Manages chat image attachment objects in MinIO."""

    def __init__(self, storage: ObjectStorage | None = None) -> None:
        self._storage = storage or ObjectStorage()

    @staticmethod
    def make_key(user_id: str, session_id: str | None, filename: str) -> str:
        """Build an object key for an uploaded chat image.

        Layout: chat-attachments/{user_id}/{session_id|_pending}/{uuid}.{ext}
        """
        sid = session_id or "_pending"
        ext = PurePosixPath(filename or "image").suffix.lstrip(".").lower() or "bin"
        if not re.fullmatch(r"[a-z0-9]{1,16}", ext):
            ext = "bin"
        return f"chat-attachments/{user_id}/{sid}/{uuid4()}.{ext}"

    def put(self, key: str, data: bytes, mime: str) -> str:
        """Upload bytes to the bucket. Returns the key."""
        return self._storage.put(key, data, content_type=mime)

    def presign(self, key: str) -> str:
        """Generate a presigned download URL (1h by default)."""
        return self._storage.presign_download(key)

    @staticmethod
    def get_public_url(key: str) -> str:
        """Return a stable, publicly-accessible URL for an attachment.

        Points to the agent-server's ``/api/public/images/{key}`` proxy
        endpoint, which requires the owner's authentication.
        """
        from aio_agent_platform.core.config import settings

        base = settings.server.server_url
        if not base:
            base = f"http://localhost:{settings.server.port}"
        return f"{base.rstrip('/')}/api/public/images/{key}"

    def to_data_uri(
        self, key: str, mime: str, *, user_id: str, session_id: str | None,
    ) -> str:
        """Download object bytes and return a base64 data URI.

        Used when re-feeding history to LLM providers that prefer base64
        (Anthropic) or when presigned URLs have expired.

        Compresses the image before encoding to reduce payload size.
        """
        validate_attachment_key(key, user_id=user_id, session_id=session_id)
        info = self._storage.stat(key)
        actual_mime = info.content_type.split(";", 1)[0].strip().lower() if info else ""
        if not info or actual_mime not in ALLOWED_MIME or not 0 < info.size <= MAX_BYTES:
            raise AttachmentAccessError("图片不存在或格式、大小无效")
        raw = self._storage.get(key)
        if len(raw) != info.size or len(raw) > MAX_BYTES:
            raise AttachmentAccessError("图片大小无效")
        compressed, compressed_mime = self._compress_image(raw, actual_mime)
        encoded = base64.b64encode(compressed).decode("ascii")
        return f"data:{compressed_mime};base64,{encoded}"

    @staticmethod
    def _compress_image(data: bytes, mime: str) -> tuple[bytes, str]:
        """Compress an image for LLM vision input.

        - Resizes so the longest side ≤ 2048px (preserves aspect ratio).
        - Converts to JPEG for better compression.
        - Skips compression if already small (< 500 KB).
        - GIF images are returned as-is (may be animated).

        Returns (compressed_bytes, new_mime).
        """
        import io
        threshold = 500 * 1024  # 500 KB
        max_long_side = 2048
        jpeg_quality = 85

        if len(data) < threshold or mime == "image/gif":
            return data, mime

        try:
            from PIL import Image

            img = Image.open(io.BytesIO(data))

            # Convert RGBA/P palette to RGB for JPEG output
            if img.mode in ("RGBA", "P", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                bg.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Resize if longer side exceeds threshold
            w, h = img.size
            long_side = max(w, h)
            if long_side > max_long_side:
                ratio = max_long_side / long_side
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            compressed = buf.getvalue()

            # Only use compressed version if it's actually smaller
            if len(compressed) < len(data):
                return compressed, "image/jpeg"
        except Exception:
            pass  # Fall back to original on any error

        return data, mime

    def delete_session(self, user_id: str, session_id: str) -> int:
        """Delete all attachments belonging to a session."""
        return self._storage.delete_prefix(f"chat-attachments/{user_id}/{session_id}/")
