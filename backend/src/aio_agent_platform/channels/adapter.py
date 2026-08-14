"""Channel abstractions — common types for IM channel adapters.

A channel connects an external IM (Feishu/DingTalk/WeCom) to the platform's
AgentLoop. The abstraction is split into three pieces:

- ``InboundEvent``: normalized representation of an incoming message, produced
  by a transport and consumed by the pipeline.
- ``Transport``: network-level receiver (WebSocket / Webhook). Responsible for
  authentication, message delivery, and translating raw payloads into
  ``InboundEvent`` instances.
- ``ChannelAdapter``: IM-specific orchestration. Owns the transport lifecycle
  and outbound formatting. One adapter per channel row in ``channel_configs``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


class ChatKind(enum.StrEnum):
    """Conversation topology."""

    DIRECT = "direct"  # 单聊 / P2P
    GROUP = "group"    # 群聊


@dataclass
class AttachmentInfo:
    """A file/image the user sent as a separate message (e.g. Feishu file msg)."""

    resource_key: str   # 飞书 file_key / image_key，用于下载
    resource_type: str  # "file" | "image"
    filename: str


@dataclass
class InboundEvent:
    """Normalized inbound message from an IM channel.

    Transports translate raw payloads (Feishu event cards, webhook JSON, ...)
    into this shape so the downstream pipeline is transport-agnostic.
    """

    channel_id: UUID
    event_id: str                     # 飞书 event_id，用于去重
    chat_id: str                      # 会话 ID（飞书 chat_id）
    external_id: str                  # 发送者的外部 ID（飞书 open_id）
    text: str                         # 纯文本内容（已剥离 @ 占位符等）
    chat_kind: ChatKind = ChatKind.DIRECT
    message_id: str | None = None     # 原始消息 ID（用于回复/引用）
    mentions_bot: bool = False        # 群聊中是否 @ 了本机器人
    attachment: AttachmentInfo | None = None  # 文件/图片消息的附件信息
    raw: dict[str, Any] = field(default_factory=dict)  # 原始 payload，需要时可用


@dataclass
class OutboundMessage:
    """A message to send back to the IM channel."""

    text: str
    message_id: str | None = None     # 若设置则更新该消息，否则新建
    reply_to: str | None = None       # 引用/回复的消息 ID


class TransportState(enum.StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class Transport:
    """Base transport — subclasses handle network specifics."""

    state: TransportState = TransportState.DISCONNECTED

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def handle_webhook(self, request) -> Any:
        """Handle an inbound webhook HTTP request (webhook-mode transports).

        Returns a FastAPI ``Response`` (or any object the router can return).
        WebSocket transports never receive webhook traffic.
        """
        raise NotImplementedError


class ChannelAdapter:
    """Base adapter — one instance per enabled channel row.

    Subclasses implement IM-specific behaviour (token refresh, message
    formatting, outbound API calls). The transport is plugged in via
    ``set_transport``.
    """

    channel_id: UUID
    transport: Transport | None = None

    # Channel capability knobs. Adapters override to reflect what their IM
    # supports; the pipeline and file-send tool key off these instead of the
    # channel_type string.
    supports_file_send: bool = False
    max_message_bytes: int | None = None     # 出站文本单条上限（字节）
    max_file_size_bytes: int | None = None   # 出站文件上传上限（字节）

    async def start(self) -> None:
        if self.transport:
            await self.transport.start()

    async def stop(self) -> None:
        if self.transport:
            await self.transport.stop()

    def set_transport(self, transport: Transport) -> None:
        self.transport = transport

    async def send(self, event: InboundEvent, text: str) -> str | None:
        """Send a new message. Returns the outbound message_id if available."""
        raise NotImplementedError

    async def send_markdown(self, event: InboundEvent, text: str) -> str | None:
        """Send a message with markdown rendering if the IM supports it.

        Base implementation falls back to plain ``send`` — adapters override
        when the IM has a rich-text/markdown message type.
        """
        return await self.send(event, text)

    async def update(self, message_id: str, text: str) -> None:
        """Update an already-sent message."""
        raise NotImplementedError

    async def start_stream(self, event: InboundEvent, text: str) -> str | None:
        """Create and send a native streaming message, returning its stream ID."""
        return None

    async def update_stream(self, stream_id: str, text: str, sequence: int) -> bool:
        """Push the accumulated text to a native streaming message."""
        return False

    async def finish_stream(
        self, stream_id: str, text: str, sequence: int
    ) -> bool:
        """Finalize a native streaming message and its preview/summary."""
        return False

    async def send_file(self, event: InboundEvent, filename: str, data: bytes) -> str | None:
        """Send a file to the originating chat. Returns the message_id, or None
        if the channel does not support file delivery."""
        return None

    async def download_attachment(self, event: InboundEvent) -> bytes | None:
        """Download the file/image resource attached to an inbound message.

        Called unconditionally by the pipeline for attachment events; adapters
        that never produce attachments can keep the base ``None``.
        """
        return None

    async def send_to_user(self, external_id: str, text: str) -> str | None:
        """Actively push a message to a user by their external id.

        Used for out-of-band pushes (e.g. cron results) where no inbound event
        is available. Base class is unsupported; adapters override when their IM
        can address a user directly.
        """
        return None

    async def add_reaction(self, event: InboundEvent, emoji_type: str) -> str | None:
        """Add an emoji reaction to the inbound message (e.g. typing indicator).

        Returns the reaction_id, or None if unsupported/failed. Base class is a
        no-op so adapters without reaction support don't need to override.
        """
        return None

    async def delete_reaction(self, event: InboundEvent, reaction_id: str) -> None:
        """Remove a reaction previously added with ``add_reaction``."""
        return None
