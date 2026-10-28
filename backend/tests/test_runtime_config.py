"""Live and published agents must agree on provider settings and tool boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aio_agent_platform.core import chat
from aio_agent_platform.core.runtime_config import AgentRuntimeConfig
from aio_agent_platform.db.models import Agent, LLMModel, LLMProvider
from aio_agent_platform.delegation import handler as delegation
from aio_agent_platform.interface.routes import agent_api
from aio_agent_platform.tools.mcp.adapter import MCPToolInfo
from aio_agent_platform.tools.registry import Tool, ToolRegistry


@pytest_asyncio.fixture
async def model_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(LLMProvider.__table__.create)
        await conn.run_sync(LLMModel.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id, other_tenant_id = uuid4(), uuid4()
    async with factory() as db:
        provider = LLMProvider(
            id=uuid4(), tenant_id=tenant_id, name="local", provider_type="openai",
            base_url="https://example.invalid", api_key_encrypted="test",
        )
        other_provider = LLMProvider(
            id=uuid4(), tenant_id=other_tenant_id, name="other", provider_type="anthropic",
            base_url="https://other.invalid", api_key_encrypted="other-secret",
        )
        model = LLMModel(
            id=uuid4(), tenant_id=tenant_id, provider=provider,
            name="vision", model_name="local-vision", is_default=True, is_multimodal=True,
        )
        other_model = LLMModel(
            id=uuid4(), tenant_id=other_tenant_id, provider=other_provider,
            name="other", model_name="other-model", is_default=True,
        )
        db.add_all([provider, other_provider, model, other_model])
        await db.commit()
        yield SimpleNamespace(db=db, tenant_id=tenant_id, model=model, other_model=other_model)
    await engine.dispose()


@pytest.mark.parametrize("enable_retry", [True, False])
async def test_published_provider_matches_live_settings(model_db, monkeypatch, enable_retry):
    fixture = model_db
    agent = Agent(
        model_id=fixture.model.id, temperature=0.0, max_iterations=3,
        enable_retry=enable_retry, system_prompt="frozen prompt",
        enabled_tools=["read_file"], skills=[], knowledge_bases=[], children=[],
    )
    snapshot = agent_api._build_config_snapshot(agent)
    # Publishing captures the setting even when the editable agent changes later.
    agent.enable_retry = not enable_retry
    agent.temperature = 1.2
    marker = Mock()
    provider_factory = Mock()
    monkeypatch.setattr(chat, "create_provider", provider_factory)
    monkeypatch.setattr(chat, "get_langfuse_client", lambda: marker)
    executor = Mock()
    workspace_id = uuid4()
    allowed_tools = {"read_file"}

    live = await chat.build_agent_loop(
        executor, "frozen prompt", fixture.db, fixture.tenant_id,
        agent_model_id=fixture.model.id, agent_temperature=0.0,
        agent_max_iterations=3, agent_enable_retry=enable_retry,
        workspace_id=workspace_id, workspace_slug="files", allowed_tools=allowed_tools,
    )
    published = await agent_api._build_agent_loop_for_version(
        executor, "frozen prompt", fixture.db, snapshot, fixture.tenant_id,
        workspace_id=workspace_id, workspace_slug="files", allowed_tools=allowed_tools,
    )

    live_kwargs, published_kwargs = [call.kwargs for call in provider_factory.call_args_list]
    assert live_kwargs == published_kwargs
    assert published_kwargs["enable_retry"] is enable_retry
    assert published_kwargs["temperature"] == 0.0
    assert published_kwargs["supports_vision"] is True
    assert published_kwargs["langfuse_client"] is marker
    for loop in (live, published):
        assert loop.execution_tenant_id == fixture.tenant_id
        assert loop.max_iterations == 3
        assert loop.allowed_tools == allowed_tools
        assert loop.workspace_id == workspace_id
        assert loop.workspace_slug == "files"


@pytest.mark.parametrize("model_reference", ["missing", "invalid", "foreign"])
async def test_old_snapshot_defaults_stay_inside_execution_tenant(model_db, monkeypatch, model_reference):
    fixture = model_db
    snapshot = {}
    if model_reference == "invalid":
        snapshot["model_id"] = "old-invalid-reference"
    elif model_reference == "foreign":
        snapshot["model_id"] = str(fixture.other_model.id)
    provider_factory = Mock()
    monkeypatch.setattr(chat, "create_provider", provider_factory)
    monkeypatch.setattr(chat, "get_langfuse_client", lambda: None)

    loop = await agent_api._build_agent_loop_for_version(
        Mock(), "", fixture.db, snapshot, fixture.tenant_id,
    )

    kwargs = provider_factory.call_args.kwargs
    assert kwargs["model"] == "local-vision"
    assert kwargs["api_key"] == "test"
    assert kwargs["enable_retry"] is True
    assert kwargs["temperature"] == chat.settings.llm.temperature
    assert loop.max_iterations == chat.settings.agent.max_iterations
    assert loop.execution_tenant_id == fixture.tenant_id


async def test_published_missing_model_keeps_http_error_contract(model_db, monkeypatch):
    provider_factory = Mock()
    monkeypatch.setattr(chat, "create_provider", provider_factory)
    with pytest.raises(HTTPException) as error:
        await agent_api._build_agent_loop_for_version(
            Mock(), "", model_db.db, {"model_id": str(model_db.model.id)}, uuid4(),
        )
    assert error.value.status_code == 400
    provider_factory.assert_not_called()


@pytest.mark.parametrize("dynamic", [False, True])
async def test_delegated_provider_uses_common_settings_and_observation(model_db, monkeypatch, dynamic):
    fixture = model_db
    child = delegation.DynamicSubAgent(id=uuid4(), name="child", tenant_id=fixture.tenant_id) if dynamic else Agent(
        tenant_id=fixture.tenant_id, model_id=fixture.model.id,
        temperature=0.0, enable_retry=False, max_iterations=3,
    )
    marker = Mock()
    provider_factory = Mock()
    monkeypatch.setattr(chat, "create_provider", provider_factory)
    monkeypatch.setattr(chat, "get_langfuse_client", lambda: marker)

    provider = await delegation._build_child_provider(fixture.db, child)

    assert provider is provider_factory.return_value
    kwargs = provider_factory.call_args.kwargs
    assert kwargs["model"] == "local-vision"
    assert kwargs["supports_vision"] is True
    assert kwargs["langfuse_client"] is marker
    assert kwargs["enable_retry"] is dynamic
    assert kwargs["temperature"] == (chat.settings.llm.temperature if dynamic else 0.0)


async def test_delegated_missing_model_keeps_none_contract(model_db, monkeypatch):
    provider_factory = Mock()
    monkeypatch.setattr(chat, "create_provider", provider_factory)
    child = delegation.DynamicSubAgent(id=uuid4(), name="child", tenant_id=uuid4())
    assert await delegation._build_child_provider(model_db.db, child) is None
    provider_factory.assert_not_called()


async def test_provider_failures_are_not_mapped_to_missing_model(model_db, monkeypatch):
    monkeypatch.setattr(chat, "create_provider", Mock(side_effect=RuntimeError("provider failed")))
    with pytest.raises(RuntimeError, match="provider failed"):
        await agent_api._build_agent_loop_for_version(Mock(), "", model_db.db, {}, model_db.tenant_id)
    child = delegation.DynamicSubAgent(id=uuid4(), name="child", tenant_id=model_db.tenant_id)
    with pytest.raises(RuntimeError, match="provider failed"):
        await delegation._build_child_provider(model_db.db, child)


def test_nullable_legacy_retry_uses_historical_default():
    assert AgentRuntimeConfig.from_snapshot({"enable_retry": None}).enable_retry is True
    assert AgentRuntimeConfig.from_agent(Agent(enable_retry=None)).enable_retry is True


@pytest.mark.parametrize("all_tools", [False, True])
def test_shared_mcp_selection_preserves_entry_capabilities(all_tools):
    server_id, unbound_id = uuid4(), uuid4()
    registry = ToolRegistry()
    for name, location in (("read_file", "sandbox"), ("ui_click", "frontend")):
        registry.register(Tool(name, name, {}, False, execution_location=location))
    infos = {
        name: MCPToolInfo(name=name.removeprefix("docs_"), description=name)
        for name in ("docs_selected", "docs_new", "docs_unbound")
    }
    manager = SimpleNamespace(
        list_all_tools=lambda: list(infos.items()),
        _tool_to_server={
            "docs_selected": server_id, "docs_new": server_id, "docs_unbound": unbound_id,
        },
    )
    executor = SimpleNamespace(registry=registry, mcp_manager=manager)
    snapshot = {
        "enabled_tools": ["read_file", "ui_click", "docs_selected"],
        "mcp_server_ids": [str(server_id)],
        # An unbound server must not become accessible via its all-tools flag.
        "mcp_all_tools_server_ids": [str(server_id), str(unbound_id)] if all_tools else [],
    }
    agent = SimpleNamespace(
        id=uuid4(), **snapshot, knowledge_bases=[], graph_knowledge_bases=[], children=[],
    )
    _, live_schema = chat.filter_tools_by_agent(executor, agent)
    _, published_schema = agent_api._filter_tools_for_agent(executor, snapshot)
    live_names = {item["function"]["name"] for item in live_schema}
    published_names = {item["function"]["name"] for item in published_schema}
    expected = {"read_file", "docs_selected"} | ({"docs_new"} if all_tools else set())
    assert published_names == expected
    assert live_names == expected | {"ui_click"}


def test_published_empty_mcp_binding_does_not_mean_all_servers():
    server_id = uuid4()
    executor = SimpleNamespace(
        registry=ToolRegistry(),
        mcp_manager=SimpleNamespace(
            list_all_tools=lambda: [("docs_read", MCPToolInfo(name="read", description="read"))],
            _tool_to_server={"docs_read": server_id},
        ),
    )
    assert agent_api._filter_tools_for_agent(executor, {}) == ([], [])
