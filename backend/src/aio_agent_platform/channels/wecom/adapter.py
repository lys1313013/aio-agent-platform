"""WeCom channel adapter — concrete ChannelAdapter for 企业微信（企业内部应用）.

企业内部应用无 markdown 消息类型：``send_markdown`` 落基类纯文本。单聊场景下
``send`` 与 ``send_to_user`` 都按 ``touser``（= userid）发送，API 相同。
"""

from __future__ import annotations

from uuid import UUID

import structlog

from aio_agent_platform.channels.adapter import ChannelAdapter, InboundEvent
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
from aio_agent_platform.channels.wecom.client import WeComClient

logger = structlog.get_logger()

# 出站图片按扩展名路由为原生 image 消息，其余作为文件消息。
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}


class WeComAdapter(ChannelAdapter):
    """WeCom-specific adapter — one instance per enabled ``channel_configs`` row."""

    supports_file_send = True
    # 企微 text 消息上限 2048 字节；中文 1 字 3 字节，取 2000 字节留余量。
    max_message_bytes = 2000
    # 企微 media/upload：image 10MB、file 20MB，取宽松档。
    max_file_size_bytes = 20 * 1024 * 1024

    def __init__(
        self,
        channel_id: UUID,
        client: WeComClient,
        pipeline: ChannelInboundPipeline,
    ):
        self.channel_id = channel_id
        self.client = client
        self.pipeline = pipeline

    async def send(self, event: InboundEvent, text: str) -> str | None:
        """Reply in the originating single chat (touser == external_id)."""
        return await self.client.send_text(event.external_id, text)

    async def send_to_user(self, external_id: str, text: str) -> str | None:
        """Actively push a text message to a user (cron 推送，无 markdown 类型)。"""
        return await self.client.send_text(external_id, text)

    async def send_file(self, event: InboundEvent, filename: str, data: bytes) -> str | None:
        """Upload then send; images render natively, everything else as a file."""
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext in _IMAGE_EXTS:
            media_id = await self.client.upload_media(filename, data, media_type="image")
            if not media_id:
                return None
            return await self.client.send_image(event.external_id, media_id)
        media_id = await self.client.upload_media(filename, data, media_type="file")
        if not media_id:
            return None
        return await self.client.send_file(event.external_id, media_id)

    async def download_attachment(self, event: InboundEvent) -> bytes | None:
        """Download the attached file/image by media_id (no message_id needed)."""
        if not event.attachment:
            return None
        return await self.client.download_media(event.attachment.resource_key)

    async def stop(self) -> None:
        if self.transport:
            await self.transport.stop()
        await self.client.close()
