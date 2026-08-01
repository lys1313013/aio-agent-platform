"""Channel adapters — IM integrations (Feishu, etc.)."""

from aio_agent_platform.channels.adapter import (
    ChannelAdapter,
    ChatKind,
    InboundEvent,
    OutboundMessage,
    Transport,
    TransportState,
)

__all__ = [
    "ChannelAdapter",
    "ChatKind",
    "InboundEvent",
    "OutboundMessage",
    "Transport",
    "TransportState",
]
