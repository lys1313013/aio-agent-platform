"""WeCom bot channel type spec — registers build/verify for ``wecom_bot``.

WeCom API-mode smart bots (智能机器人，长连接) use a WebSocket long connection
instead of webhook callbacks. Config field mapping:
- ``app_id`` = Bot ID（机器人专属 ID）
- ``app_secret_encrypted`` = Bot Secret
- mode 强制 ``websocket``（无 webhook 形态）
"""

from __future__ import annotations

from typing import Any

from aio_agent_platform.channels.registry import ChannelTypeSpec, register_channel_type
from aio_agent_platform.db.models import ChannelConfig
from aio_agent_platform.tools.executor import ToolExecutor


def _build(channel: ChannelConfig, tool_executor: ToolExecutor):
    """Build the full runtime chain for a WeCom bot channel row."""
    from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
    from aio_agent_platform.channels.wecom_bot.adapter import WeComBotAdapter
    from aio_agent_platform.channels.wecom_bot.ws_transport import WeComBotTransport

    pipeline = ChannelInboundPipeline(
        channel=channel, adapter=None, tool_executor=tool_executor  # type: ignore[arg-type]
    )

    adapter = WeComBotAdapter(channel_id=channel.id, pipeline=pipeline)
    pipeline.adapter = adapter  # type: ignore[assignment]

    transport = WeComBotTransport(
        pipeline=pipeline,
        bot_id=channel.app_id,
        secret=channel.app_secret_encrypted,
    )
    adapter.set_transport(transport)
    return adapter


async def _verify_credentials(
    app_id: str, app_secret: str, extra_config: dict[str, Any]
) -> bool:
    from aio_agent_platform.channels.wecom_bot.ws_transport import verify_bot_credentials

    return await verify_bot_credentials(app_id, app_secret)


WECOM_BOT_SPEC = ChannelTypeSpec(
    channel_type="wecom_bot",
    title_prefix="企微机器人· ",
    allowed_modes=("websocket",),
    supports_file_send=True,
    build=_build,
    verify_credentials=_verify_credentials,
)

register_channel_type(WECOM_BOT_SPEC)
