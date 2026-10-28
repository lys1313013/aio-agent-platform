"""Agent update responses must reflect the persisted ORM relationships."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import (
    Agent,
    GraphKnowledgeBase,
    KnowledgeBase,
    LLMModel,
    LLMProvider,
    Skill,
    Tenant,
    User,
)
from aio_agent_platform.interface.routes.agents import AgentUpdate, admin_update_agent

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def agent_config(db_session: AsyncSession):
    tenant = Tenant(id=uuid4(), name="agent config", slug="agent-config")
    owner = User(
        id=uuid4(), tenant_id=tenant.id, username="agent-owner",
        email="agent-owner@example.com", password_hash="test",
    )
    provider = LLMProvider(
        id=uuid4(), tenant_id=tenant.id, name="provider", provider_type="openai",
        base_url="https://example.invalid", api_key_encrypted="test",
    )
    models = [
        LLMModel(
            id=uuid4(), tenant_id=tenant.id, provider_id=provider.id,
            name=f"model {index}", model_name=f"model-{index}",
        )
        for index in range(2)
    ]
    parent = Agent(
        id=uuid4(), tenant_id=tenant.id, created_by=owner.id,
        name="parent", model_id=models[0].id,
    )
    resources = {
        "skill_ids": [
            Skill(id=uuid4(), user_id=owner.id, name=f"skill {index}")
            for index in range(2)
        ],
        "knowledge_base_ids": [
            KnowledgeBase(
                id=uuid4(), tenant_id=tenant.id, created_by=owner.id,
                name=f"knowledge {index}", dataset_id=f"dataset-{index}",
            )
            for index in range(2)
        ],
        "graph_knowledge_base_ids": [
            GraphKnowledgeBase(
                id=uuid4(), tenant_id=tenant.id, created_by=owner.id,
                name=f"graph {index}",
            )
            for index in range(2)
        ],
        "child_ids": [
            Agent(
                id=uuid4(), tenant_id=tenant.id, created_by=owner.id,
                name=f"child {index}",
            )
            for index in range(2)
        ],
    }
    db_session.add_all([
        tenant, owner, provider, parent, *models,
        *(resource for group in resources.values() for resource in group),
    ])
    await db_session.flush()
    return SimpleNamespace(
        db=db_session, owner=owner, parent=parent, models=models, resources=resources,
    )


async def test_model_response_refreshes_changed_and_cleared_model(agent_config):
    config = agent_config
    model = config.models[1]
    updated = await admin_update_agent(
        config.parent.id, AgentUpdate(model_id=model.id), config.owner, config.db,
    )
    assert updated["model_id"] == model.id
    assert updated["model_name"] == model.name

    cleared = await admin_update_agent(
        config.parent.id, AgentUpdate(model_id=None), config.owner, config.db,
    )
    assert cleared["model_id"] is None
    assert cleared["model_name"] is None


@pytest.mark.parametrize("field", [
    "skill_ids", "knowledge_base_ids", "graph_knowledge_base_ids", "child_ids",
])
async def test_binding_response_refreshes_added_and_removed_resources(agent_config, field):
    config = agent_config
    first, second = [str(resource.id) for resource in config.resources[field]]
    for selected in ([first], [first, second], [second], []):
        updated = await admin_update_agent(
            config.parent.id, AgentUpdate(**{field: selected}), config.owner, config.db,
        )
        assert set(updated[field]) == set(selected)
        if field == "child_ids":
            assert updated["children_count"] == len(selected)


async def test_parent_response_refreshes_loaded_inverse_relationship(agent_config):
    config = agent_config
    child = config.resources["child_ids"][0]
    await config.db.refresh(child, ["parents"])
    assert child.parents == []

    for selected in ([str(child.id)], []):
        await admin_update_agent(
            config.parent.id, AgentUpdate(child_ids=selected), config.owner, config.db,
        )
        updated_child = await admin_update_agent(
            child.id, AgentUpdate(description="updated child"), config.owner, config.db,
        )
        assert updated_child["parent_ids"] == ([str(config.parent.id)] if selected else [])
