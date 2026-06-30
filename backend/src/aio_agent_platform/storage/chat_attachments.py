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
        return f"chat-attachments/{user_id}/{sid}/{uuid4()}.{ext}"

    def put(self, key: str, data: bytes, mime: str) -> str:
        """Upload bytes to the bucket. Returns the key."""
        return self._storage.put(key, data, content_type=mime)

    def presign(self, key: str) -> str:
        """Generate a presigned download URL (1h by default)."""
        return self._storage.presign_download(key)

    def get_public_url(self, key: str) -> str:
        """Return a stable, publicly-accessible URL for an attachment.

        Points to the agent-server's ``/api/public/images/{key}`` proxy
        endpoint, which serves the image from MinIO without authentication.
        """
        from aio_agent_platform.core.config import settings

        base = settings.server.server_url
        if not base:
            base = f"http://localhost:{settings.server.port}"
        return f"{base.rstrip('/')}/api/public/images/{key}"

    def to_data_uri(self, key: str, mime: str) -> str:
        """Download object bytes and return a base64 data URI.

        Used when re-feeding history to LLM providers that prefer base64
        (Anthropic) or when presigned URLs have expired.

        Compresses the image before encoding to reduce payload size.
        """
        raw = self._storage.get(key)
        compressed, compressed_mime = self._compress_image(raw, mime)
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
                img = img.resize((new_w, new_h), Image.LANCZOS)

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
