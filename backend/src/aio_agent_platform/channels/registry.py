"""Channel type registry — one spec per IM channel type.

The connection manager, credential verification and pipeline lookups all go
through this registry instead of hardcoding a channel type. Adding a new IM
(WeCom/DingTalk) means implementing a ``ChannelAdapter`` + transports and
registering a ``ChannelTypeSpec``; no shared code needs editing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aio_agent_platform.channels.adapter import ChannelAdapter
from aio_agent_platform.db.models import ChannelConfig
from aio_agent_platform.tools.executor import ToolExecutor

# (app_id, app_secret, extra_config) -> whether the credential pair is valid.
CredentialVerifier = Callable[[str, str, dict[str, Any]], Awaitable[bool]]
# Builds the whole runtime chain (client → pipeline → adapter → transport).
AdapterBuilder = Callable[[ChannelConfig, ToolExecutor], ChannelAdapter]


@dataclass(frozen=True)
class ChannelTypeSpec:
    """Static description of one IM channel type."""

    channel_type: str
    title_prefix: str            # 会话标题前缀，如 "飞书· " / "企微· "
    allowed_modes: tuple[str, ...] = field(default=("websocket", "webhook"))
    supports_file_send: bool = False
    build: AdapterBuilder | None = None
    verify_credentials: CredentialVerifier | None = None


_REGISTRY: dict[str, ChannelTypeSpec] = {}


def register_channel_type(spec: ChannelTypeSpec) -> None:
    """Register a channel type spec (idempotent — later registration wins)."""
    _REGISTRY[spec.channel_type] = spec


def get_channel_spec(channel_type: str) -> ChannelTypeSpec:
    """Return the spec for a channel type, raising for unknown types."""
    spec = _REGISTRY.get(channel_type)
    if spec is None:
        raise ValueError(f"Unsupported channel type: {channel_type}")
    return spec


def has_channel_spec(channel_type: str) -> bool:
    return channel_type in _REGISTRY


def list_channel_specs() -> list[ChannelTypeSpec]:
    return list(_REGISTRY.values())
