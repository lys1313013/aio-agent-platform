"""Tests for RemoteToolManager."""

import pytest
from unittest.mock import Mock, AsyncMock
from uuid import uuid4

from aio_agent_platform.tools.remote.manager import RemoteToolManager, RemoteToolConfig
from aio_agent_platform.tools.registry import ToolRegistry


def make_config(name="test_tool", is_active=True, **kwargs):
    """Helper to create a RemoteToolConfig."""
    defaults = {
        "id": uuid4(),
        "name": name,
        "label": "测试工具",
        "description": "Test tool",
        "method": "POST",
        "url_template": "https://api.example.com/test",
        "parameters_schema": {"type": "object", "properties": {}},
        "headers": None,
        "auth_type": "none",
        "auth_config": None,
        "query_params": None,
        "body_template": None,
        "response_extract": None,
        "timeout": 30,
        "is_active": is_active,
    }
    defaults.update(kwargs)
    return RemoteToolConfig(**defaults)


class TestRemoteToolManager:
    """Test RemoteToolManager operations."""

    @pytest.fixture
    def setup(self):
        registry = ToolRegistry()
        manager = RemoteToolManager(registry)
        return registry, manager

    @pytest.mark.asyncio
    async def test_add_tool(self, setup):
        registry, manager = setup
        config = make_config(name="my_tool")

        await manager.add_tool(config)

        assert manager.is_remote_tool("my_tool")
        assert manager.get_config("my_tool") == config
        assert registry.get("my_tool") is not None

    @pytest.mark.asyncio
    async def test_remove_tool(self, setup):
        registry, manager = setup
        config = make_config(name="my_tool")
        await manager.add_tool(config)

        await manager.remove_tool("my_tool")

        assert not manager.is_remote_tool("my_tool")
        assert manager.get_config("my_tool") is None
        assert registry.get("my_tool") is None

    @pytest.mark.asyncio
    async def test_update_tool(self, setup):
        registry, manager = setup
        config1 = make_config(name="my_tool", description="Old description")
        await manager.add_tool(config1)

        config2 = make_config(name="my_tool", description="New description")
        await manager.update_tool(config2)

        assert manager.get_config("my_tool").description == "New description"
        assert registry.get("my_tool").description == "New description"

    @pytest.mark.asyncio
    async def test_update_tool_deactivate(self, setup):
        registry, manager = setup
        config_active = make_config(name="my_tool", is_active=True)
        await manager.add_tool(config_active)

        config_inactive = make_config(name="my_tool", is_active=False)
        await manager.update_tool(config_inactive)

        assert not manager.is_remote_tool("my_tool")
        assert registry.get("my_tool") is None

    @pytest.mark.asyncio
    async def test_list_all_tools(self, setup):
        registry, manager = setup
        config1 = make_config(name="tool1")
        config2 = make_config(name="tool2")
        await manager.add_tool(config1)
        await manager.add_tool(config2)

        tools = manager.list_all_tools()

        assert len(tools) == 2
        names = {name for name, _ in tools}
        assert names == {"tool1", "tool2"}

    def test_is_remote_tool_negative(self, setup):
        registry, manager = setup
        assert not manager.is_remote_tool("nonexistent")

    def test_get_config_negative(self, setup):
        registry, manager = setup
        assert manager.get_config("nonexistent") is None

    @pytest.mark.asyncio
    async def test_registry_tool_properties(self, setup):
        registry, manager = setup
        config = make_config(
            name="prop_tool",
            description="A tool with properties",
            parameters_schema={
                "type": "object",
                "properties": {"arg1": {"type": "string"}}
            },
            timeout=60,
        )

        await manager.add_tool(config)
        tool = registry.get("prop_tool")

        assert tool is not None
        assert tool.name == "prop_tool"
        assert tool.description == "A tool with properties"
        assert tool.parameters == config.parameters_schema
        assert tool.timeout == 60
        assert tool.requires_sandbox is False
        assert tool.permission_level == "read"


class TestRemoteToolConfigFromDb:
    """Test RemoteToolConfig.from_db conversion."""

    def test_from_db_basic(self):
        """Test conversion from DB model to config."""
        tool_id = uuid4()
        mock_db_tool = Mock()
        mock_db_tool.id = tool_id
        mock_db_tool.name = "db_tool"
        mock_db_tool.label = "数据库工具"
        mock_db_tool.description = "From DB"
        mock_db_tool.method = "GET"
        mock_db_tool.url_template = "https://db.example.com/api"
        mock_db_tool.parameters_schema = {"type": "object"}
        mock_db_tool.headers = {"Accept": "application/json"}
        mock_db_tool.auth_type = "bearer"
        mock_db_tool.auth_config = {"token": "secret"}
        mock_db_tool.query_params = None
        mock_db_tool.body_template = None
        mock_db_tool.response_extract = "$.data"
        mock_db_tool.timeout = 45
        mock_db_tool.is_active = True

        config = RemoteToolConfig.from_db(mock_db_tool)

        assert config.id == tool_id
        assert config.name == "db_tool"
        assert config.description == "From DB"
        assert config.method == "GET"
        assert config.url_template == "https://db.example.com/api"
        assert config.parameters_schema == {"type": "object"}
        assert config.headers == {"Accept": "application/json"}
        assert config.auth_type == "bearer"
        assert config.auth_config == {"token": "secret"}
        assert config.response_extract == "$.data"
        assert config.timeout == 45
        assert config.is_active is True

    def test_from_db_with_nulls(self):
        """Test conversion handles null fields."""
        mock_db_tool = Mock()
        mock_db_tool.id = uuid4()
        mock_db_tool.name = "minimal"
        mock_db_tool.label = "最小工具"
        mock_db_tool.description = "Minimal tool"
        mock_db_tool.method = "POST"
        mock_db_tool.url_template = "https://example.com"
        mock_db_tool.parameters_schema = None
        mock_db_tool.headers = None
        mock_db_tool.auth_type = None
        mock_db_tool.auth_config = None
        mock_db_tool.query_params = None
        mock_db_tool.body_template = None
        mock_db_tool.response_extract = None
        mock_db_tool.timeout = 30
        mock_db_tool.is_active = True

        config = RemoteToolConfig.from_db(mock_db_tool)

        assert config.parameters_schema == {}
        assert config.headers is None
        assert config.auth_type == "none"
        assert config.auth_config is None
        assert config.response_extract is None
