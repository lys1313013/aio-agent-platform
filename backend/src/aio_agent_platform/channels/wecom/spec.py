"""WeCom channel type spec — registers build/verify for the ``wecom`` type.

Module-level ``register_channel_type`` runs on import (via
``channels.wecom.__init__``), so simply importing the wecom package makes
``get_channel_spec("wecom")`` resolvable.
"""

from __future__ import annotations

from typing import Any

from aio_agent_platform.channels.registry import ChannelTypeSpec, register_channel_type
from aio_agent_platform.db.models import ChannelConfig
from aio_agent_platform.tools.executor import ToolExecutor


def _parse_agentid(extra_config: dict[str, Any] | None) -> int:
    """Parse the wecom app AgentID defensively; 0 on missing/invalid values.

    Routes already reject non-positive agentids with a 400, but rows written
    before that check (or via other paths) must not crash ``int()`` at enable
    time — they degrade to agentid=0 instead of a 500.
    """
    try:
        return max(0, int((extra_config or {}).get("agentid") or 0))
    except (TypeError, ValueError):
        return 0


def _build(channel: ChannelConfig, tool_executor: ToolExecutor):
    """Build the full runtime chain for a WeCom channel row.

    企业内部应用仅支持 webhook 回调模式（无 WebSocket）。配置字段映射：
    app_id = corpid、app_secret_encrypted = corpsecret、
    verification_token_encrypted = 回调 Token、encrypt_key_encrypted = EncodingAESKey、
    extra_config.agentid = 数字应用 ID。
    """
    from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
    from aio_agent_platform.channels.wecom.adapter import WeComAdapter
    from aio_agent_platform.channels.wecom.client import WeComClient
    from aio_agent_platform.channels.wecom.webhook_transport import (
        WeComWebhookTransport,
    )

    agentid = _parse_agentid(getattr(channel, "extra_config", None))
    client = WeComClient(
        corpid=channel.app_id,
        corpsecret=channel.app_secret_encrypted,
        agentid=agentid,
    )

    pipeline = ChannelInboundPipeline(
        channel=channel, adapter=None, tool_executor=tool_executor  # type: ignore[arg-type]
    )

    adapter = WeComAdapter(channel_id=channel.id, client=client, pipeline=pipeline)
    pipeline.adapter = adapter  # type: ignore[assignment]

    transport = WeComWebhookTransport(
        pipeline=pipeline,
        corpid=channel.app_id,
        token=channel.verification_token_encrypted or "",
        encoding_aes_key=channel.encrypt_key_encrypted or "",
    )
    adapter.set_transport(transport)
    return adapter


async def _verify_credentials(
    app_id: str, app_secret: str, extra_config: dict[str, Any]
) -> bool:
    from aio_agent_platform.channels.wecom.client import WeComClient

    agentid = _parse_agentid(extra_config)
    client = WeComClient(corpid=app_id, corpsecret=app_secret, agentid=agentid)
    try:
        return await client.verify_credentials()
    finally:
        await client.close()


WECOM_SPEC = ChannelTypeSpec(
    channel_type="wecom",
    title_prefix="企微· ",
    allowed_modes=("webhook",),
    supports_file_send=True,
    build=_build,
    verify_credentials=_verify_credentials,
)

register_channel_type(WECOM_SPEC)
