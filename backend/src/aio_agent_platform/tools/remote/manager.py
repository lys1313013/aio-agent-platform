"""RemoteToolManager — lifecycle management for remote HTTP tools."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import RemoteTool
from aio_agent_platform.tools.registry import Tool, ToolRegistry

logger = structlog.get_logger()


@dataclass
class RemoteToolConfig:
    """In-memory representation of a remote tool's configuration."""

    id: UUID
    name: str
    label: str
    description: str
    method: str
    url_template: str
    parameters_schema: dict
    headers: dict | None
    auth_type: str
    auth_config: dict | None
    query_params: dict | None
    body_template: dict | None
    response_extract: str | None
    timeout: int
    is_active: bool

    @classmethod
    def from_db(cls, tool: RemoteTool) -> RemoteToolConfig:
        return cls(
            id=tool.id,
            name=tool.name,
            label=tool.label,
            description=tool.description,
            method=tool.method,
            url_template=tool.url_template,
            parameters_schema=tool.parameters_schema or {},
            headers=tool.headers,
            auth_type=tool.auth_type or "none",
            auth_config=tool.auth_config,
            query_params=tool.query_params,
            body_template=tool.body_template,
            response_extract=tool.response_extract,
            timeout=tool.timeout,
            is_active=tool.is_active,
        )


class RemoteToolManager:
    """Manages remote HTTP tools — loads from DB, registers in ToolRegistry, provides lookup."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._tools: dict[str, RemoteToolConfig] = {}  # name → config

    async def initialize(self, session_factory) -> None:
        """Load all active remote tools from DB and register them."""
        try:
            async with session_factory() as db:
                result = await db.execute(
                    select(RemoteTool).where(RemoteTool.is_active)
                )
                tools = result.scalars().all()

            for tool in tools:
                config = RemoteToolConfig.from_db(tool)
                await self._register(config)

            logger.info("remote_tools_initialized", count=len(self._tools))
        except Exception as e:
            logger.warning("remote_tools_init_failed", error=str(e))

    async def _register(self, config: RemoteToolConfig) -> None:
        """Register a remote tool into the ToolRegistry and internal map."""
        tool = Tool(
            name=config.name,
            description=config.description,
            parameters=config.parameters_schema,
            requires_sandbox=False,
            permission_level="read",
            timeout=config.timeout,
        )
        self._registry.register(tool)
        self._tools[config.name] = config
        logger.debug("remote_tool_registered", name=config.name)

    async def _unregister(self, name: str) -> None:
        """Remove a remote tool from the ToolRegistry and internal map."""
        self._tools.pop(name, None)
        # Remove from registry
        if name in self._registry._tools:
            del self._registry._tools[name]
        logger.debug("remote_tool_unregistered", name=name)

    def is_remote_tool(self, name: str) -> bool:
        return name in self._tools

    def get_config(self, name: str) -> RemoteToolConfig | None:
        return self._tools.get(name)

    def list_all_tools(self) -> list[tuple[str, RemoteToolConfig]]:
        """Return list of (name, config) for all registered remote tools."""
        return list(self._tools.items())

    async def add_tool(self, config: RemoteToolConfig) -> None:
        """Register a new remote tool (called after DB insert)."""
        await self._register(config)

    async def update_tool(self, config: RemoteToolConfig) -> None:
        """Re-register a remote tool after update."""
        await self._unregister(config.name)
        if config.is_active:
            await self._register(config)

    async def remove_tool(self, name: str) -> None:
        """Unregister a remote tool (called before/after DB delete)."""
        await self._unregister(name)

    async def reload_from_db(self, session_factory) -> None:
        """Full reload — clear all and re-register from DB."""
        for name in list(self._tools.keys()):
            await self._unregister(name)
        await self.initialize(session_factory)
