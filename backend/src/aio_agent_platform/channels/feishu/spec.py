"""Feishu channel type spec — registers build/verify for the feishu type.

Module-level ``register_channel_type`` runs on import (via
``channels.feishu.__init__``), so simply importing the feishu package makes
``get_channel_spec("feishu")`` resolvable.
"""

from __future__ import annotations

from typing import Any

from aio_agent_platform.channels.registry import ChannelTypeSpec, register_channel_type
from aio_agent_platform.db.models import ChannelConfig
from aio_agent_platform.tools.executor import ToolExecutor


def _build(channel: ChannelConfig, tool_executor: ToolExecutor):
    """Build the full runtime chain for a Feishu channel row.

    Creates client → pipeline → adapter → transport, wires the pipeline back to
    the adapter, and attaches the transport. Webhook channels register
    themselves when the adapter is started.
    """
    from aio_agent_platform.channels.feishu.adapter import FeishuAdapter
    from aio_agent_platform.channels.feishu.client import FeishuClient
    from aio_agent_platform.channels.feishu.webhook_transport import (
        FeishuWebhookTransport,
    )
    from aio_agent_platform.channels.feishu.ws_transport import FeishuWebSocketTransport
    from aio_agent_platform.channels.pipeline import ChannelInboundPipeline

    client = FeishuClient(
        app_id=channel.app_id,
        app_secret=channel.app_secret_encrypted,
    )

    pipeline = ChannelInboundPipeline(
        channel=channel, adapter=None, tool_executor=tool_executor  # type: ignore[arg-type]
    )

    adapter = FeishuAdapter(channel_id=channel.id, client=client, pipeline=pipeline)
    pipeline.adapter = adapter  # type: ignore[assignment]

    if channel.mode == "websocket":
        transport = FeishuWebSocketTransport(
            app_id=channel.app_id,
            app_secret=channel.app_secret_encrypted,
            pipeline=pipeline,
        )
    elif channel.mode == "webhook":
        # Webhook transport reads the keys via ``channel._encrypt_key`` /
        # ``channel._verification_token`` (also matches test rows that
        # monkey-patch those attributes).
        channel._encrypt_key = channel.encrypt_key_encrypted  # type: ignore[attr-defined]
        channel._verification_token = channel.verification_token_encrypted  # type: ignore[attr-defined]
        transport = FeishuWebhookTransport(pipeline=pipeline, channel=channel)
    else:
        raise ValueError(f"Invalid channel mode: {channel.mode}")

    adapter.set_transport(transport)
    return adapter


async def _verify_credentials(
    app_id: str, app_secret: str, extra_config: dict[str, Any]
) -> bool:
    from aio_agent_platform.channels.feishu.client import FeishuClient

    client = FeishuClient(app_id=app_id, app_secret=app_secret)
    try:
        return await client.verify_credentials()
    finally:
        await client.close()


FEISHU_SPEC = ChannelTypeSpec(
    channel_type="feishu",
    title_prefix="飞书· ",
    allowed_modes=("websocket", "webhook"),
    supports_file_send=True,
    build=_build,
    verify_credentials=_verify_credentials,
)

register_channel_type(FEISHU_SPEC)
