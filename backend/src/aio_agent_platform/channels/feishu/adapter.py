"""Feishu channel adapter — concrete ChannelAdapter for the Feishu IM.

The adapter is IM-specific; it owns the FeishuClient (HTTP) and a Transport
(WebSocket or Webhook) that feeds InboundEvents into the pipeline.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from aio_agent_platform.channels.adapter import ChannelAdapter, InboundEvent, Transport
from aio_agent_platform.channels.feishu.client import FeishuClient
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline

logger = structlog.get_logger()

# 出站媒体按扩展名路由：飞书语音仅支持 opus、视频仅支持 mp4，其余格式回退为文件消息。
_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
_AUDIO_EXTS = {"opus"}
_VIDEO_EXTS = {"mp4"}


class FeishuAdapter(ChannelAdapter):
    """Feishu-specific adapter.

    One instance per enabled ``channel_configs`` row. The transport is created
    by the connection manager based on the row's ``mode`` field.
    """

    def __init__(
        self,
        channel_id: UUID,
        client: FeishuClient,
        pipeline: ChannelInboundPipeline,
    ):
        self.channel_id = channel_id
        self.client = client
        self.pipeline = pipeline

    def set_transport(self, transport: Transport) -> None:
        self.transport = transport

    async def send(self, event: InboundEvent, text: str) -> str | None:
        """Send a reply to the chat that originated the event.

        For group chats where the bot was @-mentioned, we use reply semantics
        so the user can correlate the reply with their message.
        """
        reply_to = event.message_id if event.mentions_bot else None
        return await self.client.send_text(
            receive_id=event.chat_id,
            text=text,
            reply_to=reply_to,
        )

    async def send_markdown(self, event: InboundEvent, text: str) -> str | None:
        """Send an interactive card rendering markdown; falls back to plain text."""
        reply_to = event.message_id if event.mentions_bot else None
        message_id = await self.client.send_card_markdown(
            receive_id=event.chat_id,
            markdown=text,
            reply_to=reply_to,
        )
        if message_id is None:
            logger.warning("feishu_card_send_failed_fallback_text")
            message_id = await self.send(event, text)
        return message_id

    async def update(self, message_id: str, text: str) -> None:
        await self.client.update_message(message_id, text)

    async def send_file(self, event: InboundEvent, filename: str, data: bytes) -> str | None:
        """Upload and send a message to the originating chat.

        Routes by file extension so images/audio/video render as native Feishu
        message types instead of a generic file attachment.
        """
        reply_to = event.message_id if event.mentions_bot else None
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if ext in _IMAGE_EXTS:
            image_key = await self.client.upload_image(data, filename)
            if not image_key:
                return None
            return await self.client.send_image(
                receive_id=event.chat_id, image_key=image_key, reply_to=reply_to
            )
        if ext in _AUDIO_EXTS:
            file_key = await self.client.upload_file(data, filename, file_type="opus")
            if not file_key:
                return None
            return await self.client.send_audio(
                receive_id=event.chat_id, file_key=file_key, reply_to=reply_to
            )
        if ext in _VIDEO_EXTS:
            file_key = await self.client.upload_file(data, filename, file_type="mp4")
            if not file_key:
                return None
            return await self.client.send_media(
                receive_id=event.chat_id, file_key=file_key, reply_to=reply_to
            )
        file_key = await self.client.upload_file(data, filename)
        if not file_key:
            return None
        return await self.client.send_file(
            receive_id=event.chat_id,
            file_key=file_key,
            reply_to=reply_to,
        )

    async def download_attachment(self, event: InboundEvent) -> bytes | None:
        """Download the file/image resource attached to an inbound message."""
        if not event.message_id or not event.attachment:
            return None
        return await self.client.download_resource(
            event.message_id,
            event.attachment.resource_key,
            event.attachment.resource_type,
        )

    async def add_reaction(self, event: InboundEvent, emoji_type: str) -> str | None:
        if not event.message_id:
            return None
        return await self.client.add_reaction(event.message_id, emoji_type)

    async def delete_reaction(self, event: InboundEvent, reaction_id: str) -> None:
        if event.message_id:
            await self.client.delete_reaction(event.message_id, reaction_id)

    async def stop(self) -> None:
        if self.transport:
            await self.transport.stop()
        await self.client.close()
