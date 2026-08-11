"""API route modules."""

from aio_agent_platform.graph_knowledge.routes import router as graph_knowledge_router
from aio_agent_platform.interface.routes.admin_models import router as admin_models_router
from aio_agent_platform.interface.routes.agent_api import router as agent_api_router
from aio_agent_platform.interface.routes.agents import router as agents_router
from aio_agent_platform.interface.routes.analytics import router as analytics_router
from aio_agent_platform.interface.routes.channels import router as channels_router
from aio_agent_platform.interface.routes.channels import user_router as channel_bindings_router
from aio_agent_platform.interface.routes.chat import router as chat_router
from aio_agent_platform.interface.routes.commands import router as commands_router
from aio_agent_platform.interface.routes.confirmations import router as confirmations_router
from aio_agent_platform.interface.routes.cron_jobs import router as cron_jobs_router
from aio_agent_platform.interface.routes.daily_memories import router as daily_memories_router
from aio_agent_platform.interface.routes.delegations import router as delegations_router
from aio_agent_platform.interface.routes.knowledge import router as knowledge_router
from aio_agent_platform.interface.routes.mcp_servers import router as mcp_servers_router
from aio_agent_platform.interface.routes.memories import router as memories_router
from aio_agent_platform.interface.routes.models import router as models_router
from aio_agent_platform.interface.routes.observability import router as observability_router
from aio_agent_platform.interface.routes.pets import admin_router as admin_pets_router
from aio_agent_platform.interface.routes.pets import router as pets_router
from aio_agent_platform.interface.routes.public import router as public_router
from aio_agent_platform.interface.routes.remote_tools import router as remote_tools_router
from aio_agent_platform.interface.routes.sessions import router as sessions_router
from aio_agent_platform.interface.routes.settings import router as settings_router
from aio_agent_platform.interface.routes.skills import router as skills_router
from aio_agent_platform.interface.routes.system_config import router as system_config_router
from aio_agent_platform.interface.routes.tenants import router as tenants_router
from aio_agent_platform.interface.routes.tools import router as tools_router
from aio_agent_platform.interface.routes.users import router as users_router
from aio_agent_platform.interface.routes.web_tools import router as web_tools_router
from aio_agent_platform.interface.routes.webpages import router as webpages_router

__all__ = [
    "admin_models_router",
    "admin_pets_router",
    "agent_api_router",
    "agents_router",
    "analytics_router",
    "channel_bindings_router",
    "channels_router",
    "chat_router",
    "commands_router",
    "confirmations_router",
    "cron_jobs_router",
    "daily_memories_router",
    "delegations_router",
    "graph_knowledge_router",
    "knowledge_router",
    "mcp_servers_router",
    "memories_router",
    "models_router",
    "observability_router",
    "pets_router",
    "public_router",
    "remote_tools_router",
    "sessions_router",
    "settings_router",
    "skills_router",
    "system_config_router",
    "tenants_router",
    "tools_router",
    "users_router",
    "web_tools_router",
    "webpages_router",
]
