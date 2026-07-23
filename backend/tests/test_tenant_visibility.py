"""Tenant ownership and visibility schema tests."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from aio_agent_platform.interface.routes.agents import AgentCreate, AgentUpdate
from aio_agent_platform.interface.routes.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)


@pytest.mark.parametrize("visibility", ["tenant", "private"])
def test_agent_visibility_values(visibility: str) -> None:
    assert AgentCreate(name="agent", visibility=visibility).visibility == visibility
    assert AgentUpdate(visibility=visibility).visibility == visibility


def test_agent_visibility_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError):
        AgentCreate(name="agent", visibility="public")


@pytest.mark.parametrize("visibility", ["tenant", "private"])
def test_knowledge_base_visibility_values(visibility: str) -> None:
    create = KnowledgeBaseCreate(
        name="kb",
        dataset_id=str(uuid4()),
        visibility=visibility,
    )
    assert create.visibility == visibility
    assert KnowledgeBaseUpdate(visibility=visibility).visibility == visibility


def test_knowledge_base_visibility_rejects_unknown_scope() -> None:
    with pytest.raises(ValidationError):
        KnowledgeBaseCreate(name="kb", dataset_id="dataset", visibility="public")
