"""Response schema ↔ ORM model conformance.

Every field a response schema declares must exist as an attribute on the ORM
model it serializes. If a model column is renamed or dropped but the schema
still references the old name, serialization crashes (or the response silently
loses the field). Computed/joined fields are listed per schema and skipped.

This runs without a database — it is a pure static contract check.
"""

import pytest

from aio_agent_platform.db.models import (
    Agent,
    ChannelBinding,
    ChannelConfig,
    CronJob,
    CronJobRun,
    Delegation,
    KnowledgeBase,
    MCPServer,
    Memory,
    Message,
    PetPackage,
    RemoteTool,
    Session,
    UserPet,
)
from aio_agent_platform.interface.routes.agents import AgentOut
from aio_agent_platform.interface.routes.channels import ChannelBindingOut, ChannelOut
from aio_agent_platform.interface.routes.cron_jobs import CronJobOut, CronJobRunOut
from aio_agent_platform.interface.routes.delegations import DelegationOut
from aio_agent_platform.interface.routes.knowledge import KnowledgeBaseOut
from aio_agent_platform.interface.routes.mcp_servers import MCPServerOut
from aio_agent_platform.interface.routes.memories import MemoryOut
from aio_agent_platform.interface.routes.pets import PetPackageOut, UserPetOut
from aio_agent_platform.interface.routes.remote_tools import RemoteToolOut
from aio_agent_platform.interface.routes.sessions import MessageOut, SessionOut

# (response schema, ORM model it serializes, computed/joined fields to skip)
REGISTRY = [
    (SessionOut, Session, set()),
    (MessageOut, Message, set()),
    (CronJobOut, CronJob, set()),
    (CronJobRunOut, CronJobRun, set()),
    (ChannelOut, ChannelConfig, set()),
    (ChannelBindingOut, ChannelBinding, set()),
    (
        AgentOut, Agent,
        {"model_name", "skill_ids", "knowledge_base_ids", "graph_knowledge_base_ids",
         "parent_ids", "child_ids", "children_count", "can_edit"},
    ),
    (MCPServerOut, MCPServer, {"tools_count", "tools"}),
    (PetPackageOut, PetPackage, {"spritesheet_url"}),
    (UserPetOut, UserPet, {"package", "agent", "actions"}),
    (DelegationOut, Delegation, {"parent_agent_name", "child_agent_name"}),
    (KnowledgeBaseOut, KnowledgeBase, {"can_edit"}),
    (RemoteToolOut, RemoteTool, {"auth_config_masked"}),
    (MemoryOut, Memory, {"metadata"}),  # schema name differs from the "meta" column attr
]


@pytest.mark.parametrize(
    "schema,model,skip",
    REGISTRY,
    ids=[schema.__name__ for schema, _, _ in REGISTRY],
)
def test_schema_fields_exist_on_model(schema, model, skip):
    """Every non-computed response field must map to a real model attribute."""
    missing = [
        name
        for name in schema.model_fields
        if name not in skip and not hasattr(model, name)
    ]
    assert not missing, (
        f"{schema.__name__} references fields missing from {model.__name__}: {missing}. "
        "Model column was renamed/dropped but the response schema was not updated — "
        "serialization would crash or the field would be lost."
    )
