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
