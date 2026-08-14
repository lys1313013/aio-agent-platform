"""WeCom (企业微信) channel package — internal-app integration.

企业内部应用形态：企业成员通过自建应用与机器人单聊，回调走应用回调 URL，
身份模型复用平台现有绑定/会话逻辑。无 WebSocket，仅 webhook 模式。
"""
from aio_agent_platform.channels.wecom.spec import WECOM_SPEC  # noqa: F401
