"""API route modules."""

from aio_agent_platform.interface.routes.admin_models import router as admin_models_router
from aio_agent_platform.interface.routes.agent_api import router as agent_api_router
from aio_agent_platform.interface.routes.agents import router as agents_router
from aio_agent_platform.interface.routes.channels import router as channels_router
from aio_agent_platform.interface.routes.channels import user_router as channel_bindings_router
from aio_agent_platform.interface.routes.chat import router as chat_router
from aio_agent_platform.interface.routes.confirmations import router as confirmations_router
from aio_agent_platform.interface.routes.cron_jobs import router as cron_jobs_router
from aio_agent_platform.interface.routes.delegations import router as delegations_router
from aio_agent_platform.interface.routes.knowledge import router as knowledge_router
from aio_agent_platform.interface.routes.mcp_servers import router as mcp_servers_router
from aio_agent_platform.interface.routes.memories import router as memories_router
from aio_agent_platform.interface.routes.public import router as public_router
from aio_agent_platform.interface.routes.remote_tools import router as remote_tools_router
from aio_agent_platform.interface.routes.sessions import router as sessions_router
from aio_agent_platform.interface.routes.settings import router as settings_router
from aio_agent_platform.interface.routes.skills import router as skills_router
from aio_agent_platform.interface.routes.tenants import router as tenants_router
from aio_agent_platform.interface.routes.tools import router as tools_router
from aio_agent_platform.interface.routes.users import router as users_router

__all__ = [
    "admin_models_router",
    "agent_api_router",
    "agents_router",
    "channel_bindings_router",
    "channels_router",
    "chat_router",
    "confirmations_router",
    "cron_jobs_router",
    "delegations_router",
    "knowledge_router",
    "mcp_servers_router",
    "memories_router",
    "public_router",
    "remote_tools_router",
    "sessions_router",
    "settings_router",
    "skills_router",
    "tenants_router",
    "tools_router",
    "users_router",
]
