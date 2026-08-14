"""Feishu channel integration.

Importing this package registers the feishu ``ChannelTypeSpec`` (side-effect of
importing ``spec``), so any ``import aio_agent_platform.channels.feishu`` makes
``get_channel_spec("feishu")`` resolvable.
"""

from aio_agent_platform.channels.feishu.spec import FEISHU_SPEC  # noqa: F401
