"""Channel adapters — IM integrations (Feishu, WeCom, etc.)."""

# Importing the concrete channel packages registers their ChannelTypeSpec in
# the shared registry (side-effect), so every import path that reaches this
# package — connection manager, routes, api — can resolve channel types.
from aio_agent_platform.channels import feishu as _feishu  # noqa: F401
from aio_agent_platform.channels import wecom as _wecom  # noqa: F401
from aio_agent_platform.channels import wecom_bot as _wecom_bot  # noqa: F401
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
