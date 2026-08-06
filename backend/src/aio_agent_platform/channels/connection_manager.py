"""Channel connection manager — lifecycle management for all channel transports."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.channels.adapter import ChannelAdapter
from aio_agent_platform.channels.feishu.adapter import FeishuAdapter
from aio_agent_platform.channels.feishu.client import FeishuClient
from aio_agent_platform.channels.feishu.webhook_transport import (
    FeishuWebhookTransport,
    register_webhook,
)
from aio_agent_platform.channels.feishu.ws_transport import FeishuWebSocketTransport
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
from aio_agent_platform.db.models import ChannelConfig
from aio_agent_platform.tools.executor import ToolExecutor

logger = structlog.get_logger()

# Process-wide singleton, set during app lifespan. Command handlers have no
# app.state access, so /status reaches the channel manager through this.
_global_channel_manager: ChannelConnectionManager | None = None


def set_global_channel_manager(manager: ChannelConnectionManager | None) -> None:
    global _global_channel_manager
    _global_channel_manager = manager


def get_global_channel_manager() -> ChannelConnectionManager | None:
    return _global_channel_manager


class ChannelConnectionManager:
    """Manages the lifecycle of all channel connections.

    On startup, loads all enabled channels and starts their transports.
    Provides methods to enable/disable channels dynamically.
    """

    def __init__(self, tool_executor: ToolExecutor):
        self.tool_executor = tool_executor
        self._adapters: dict[UUID, ChannelAdapter] = {}
        self._channel_configs: dict[UUID, ChannelConfig] = {}

    async def start_all(self, db: AsyncSession) -> None:
        """Load and start all enabled channels."""
        result = await db.execute(
            select(ChannelConfig).where(ChannelConfig.status == "enabled")
        )
        channels = result.scalars().all()
        for channel in channels:
            try:
                await self._start_channel(channel)
            except Exception:
                logger.exception(
                    "channel_start_failed",
                    channel_id=str(channel.id),
                    name=channel.name,
                )
                channel.status = "error"
                channel.last_error = "Failed to start channel"
                await db.commit()

    async def stop_all(self) -> None:
        """Stop all active channels."""
        for channel_id in list(self._adapters.keys()):
            await self.stop_channel(channel_id)

    async def start_channel(self, channel: ChannelConfig) -> None:
        """Start a single channel."""
        if channel.id in self._adapters:
            logger.warning("channel_already_started", channel_id=str(channel.id))
            return
        await self._start_channel(channel)

    async def stop_channel(self, channel_id: UUID) -> None:
        """Stop a single channel."""
        adapter = self._adapters.pop(channel_id, None)
        if adapter is None:
            return
        try:
            await adapter.stop()
        except Exception:
            logger.exception("channel_stop_failed", channel_id=str(channel_id))
        self._channel_configs.pop(channel_id, None)
        logger.info("channel_stopped", channel_id=str(channel_id))

    async def _start_channel(self, channel: ChannelConfig) -> None:
        """Internal: create adapter and transport, start the transport."""
        if channel.channel_type != "feishu":
            raise ValueError(f"Unsupported channel type: {channel.channel_type}")

        # Create Feishu client
        client = FeishuClient(
            app_id=channel.app_id,
            app_secret=channel.app_secret_encrypted,
        )

        # Create pipeline
        pipeline = ChannelInboundPipeline(channel=channel, adapter=None, tool_executor=self.tool_executor)  # type: ignore

        # Create adapter
        adapter = FeishuAdapter(channel_id=channel.id, client=client, pipeline=pipeline)
        pipeline.adapter = adapter  # type: ignore

        # Create and attach transport
        if channel.mode == "websocket":
            transport = FeishuWebSocketTransport(
                app_id=channel.app_id,
                app_secret=channel.app_secret_encrypted,
                pipeline=pipeline,
            )
        elif channel.mode == "webhook":
            transport = FeishuWebhookTransport(pipeline=pipeline)
            # Attach decrypt/verify keys so the webhook route can access them.
            channel._encrypt_key = channel.encrypt_key_encrypted  # type: ignore[attr-defined]
            channel._verification_token = channel.verification_token_encrypted  # type: ignore[attr-defined]
            # Register with the webhook router
            register_webhook(channel.channel_key, pipeline, channel)
        else:
            raise ValueError(f"Invalid channel mode: {channel.mode}")

        adapter.set_transport(transport)

        # Start the transport
        await adapter.start()

        self._adapters[channel.id] = adapter
        self._channel_configs[channel.id] = channel
        logger.info("channel_started", channel_id=str(channel.id), mode=channel.mode)

    def get_adapter(self, channel_id: UUID) -> ChannelAdapter | None:
        return self._adapters.get(channel_id)

    def get_status(self) -> list[dict[str, Any]]:
        """Return status of all managed channels."""
        statuses = []
        for channel_id, adapter in self._adapters.items():
            channel = self._channel_configs.get(channel_id)
            if channel is None:
                continue
            statuses.append({
                "channel_id": str(channel_id),
                "name": channel.name,
                "channel_type": channel.channel_type,
                "mode": channel.mode,
                "transport_state": adapter.transport.state.value if adapter.transport else "unknown",
            })
        return statuses
