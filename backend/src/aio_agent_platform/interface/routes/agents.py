"""Agent management routes — admin CRUD + user read-only + multi-agent hierarchy."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload, with_loader_criteria

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import (
    Agent,
    AgentGraphKnowledgeBase,
    AgentKnowledgeBase,
    AgentRelationship,
    AgentSkill,
    GraphKnowledgeBase,
    KnowledgeBase,
    LLMModel,
    MCPServer,
)

router = APIRouter(tags=["agents"])


# ---- Schemas ----


class AgentBrief(BaseModel):
    """Brief agent info for hierarchy display."""
    id: UUID
    name: str
    description: str | None = None
    icon: str = "robot"
    is_active: bool = True

    model_config = {"from_attributes": True}


class AgentOut(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    icon: str = "robot"
    system_prompt: str | None = None
    model_id: UUID | None = None
    model_name: str | None = None
    enabled_tools: list[str] = []
    mcp_server_ids: list[str] = []
    temperature: float | None = None
    max_iterations: int | None = None
    welcome_message: str | None = None
    starter_prompts: list[dict] | None = None
    enable_memory_extraction: bool = True
    enable_retry: bool = True
    enable_auto_title: bool = True
    is_active: bool = True
    skill_ids: list[str] = []
    knowledge_base_ids: list[str] = []
    graph_knowledge_base_ids: list[str] = []
    # Multi-agent fields
    parent_ids: list[str] = []
    child_ids: list[str] = []
    children_count: int = 0
    tenant_id: UUID | None = None
    created_by: UUID | None = None
    visibility: str = "tenant"
    can_edit: bool = False

    model_config = {"from_attributes": True}


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    icon: str = Field(default="robot", max_length=64)
    system_prompt: str | None = None
    model_id: UUID | None = None
    enabled_tools: list[str] = Field(default_factory=list)
    mcp_server_ids: list[str] = Field(default_factory=list)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_iterations: int | None = Field(default=None, ge=1, le=500)
    welcome_message: str | None = None
    starter_prompts: list[dict] | None = None
    enable_memory_extraction: bool = True
    enable_retry: bool = True
    enable_auto_title: bool = True
    skill_ids: list[str] = Field(default_factory=list)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    graph_knowledge_base_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    visibility: str = Field(default="tenant", pattern="^(tenant|private)$")


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=64)
    system_prompt: str | None = None
    model_id: UUID | None = None
    enabled_tools: list[str] | None = None
    mcp_server_ids: list[str] | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_iterations: int | None = Field(default=None, ge=1, le=500)
    welcome_message: str | None = None
    starter_prompts: list[dict] | None = None
    enable_memory_extraction: bool | None = None
    enable_retry: bool | None = None
    enable_auto_title: bool | None = None
    skill_ids: list[str] | None = None
    knowledge_base_ids: list[str] | None = None
    graph_knowledge_base_ids: list[str] | None = None
    is_active: bool | None = None
    child_ids: list[str] | None = None
    visibility: str | None = Field(default=None, pattern="^(tenant|private)$")

    def is_set(self, field_name: str) -> bool:
        """Check if a field was explicitly provided in the request (even if null)."""
        return field_name in self.model_fields_set


# ---- Admin CRUD ----


@router.get("/api/admin/agents", response_model=list[AgentOut])
async def admin_list_agents(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(Agent)
        .options(
            joinedload(Agent.model),
            selectinload(Agent.skills),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
            selectinload(Agent.children),
            selectinload(Agent.parents),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(_agent_visible_to(user))
        .order_by(Agent.created_at)
    )
    agents = result.scalars().all()
    return [_agent_to_dict(a, user=user) for a in agents]


@router.post("/api/admin/agents", response_model=AgentOut)
async def admin_create_agent(
    req: AgentCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    if req.model_id:
        result = await db.execute(
            select(LLMModel).where(LLMModel.id == req.model_id, LLMModel.tenant_id == user.tenant_id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="模型不存在")

    agent = Agent(
        name=req.name,
        description=req.description,
        icon=req.icon,
        system_prompt=req.system_prompt,
        model_id=req.model_id,
        enabled_tools=req.enabled_tools,
        mcp_server_ids=req.mcp_server_ids,
        temperature=req.temperature,
        max_iterations=req.max_iterations,
        welcome_message=req.welcome_message,
        starter_prompts=req.starter_prompts,
        enable_memory_extraction=req.enable_memory_extraction,
        enable_retry=req.enable_retry,
        enable_auto_title=req.enable_auto_title,
        created_by=user.id,
        tenant_id=user.tenant_id,
        visibility=req.visibility,
    )
    db.add(agent)
    await db.flush()

    # Validate MCP server bindings belong to the same tenant
    if req.mcp_server_ids:
        await _validate_mcp_servers(db, user, req.mcp_server_ids)

    # Bind skills
    if req.skill_ids:
        for sid in req.skill_ids:
            skill_uuid = UUID(sid) if isinstance(sid, str) else sid
            db.add(AgentSkill(agent_id=agent.id, skill_id=skill_uuid))

    # Bind knowledge bases
    if req.knowledge_base_ids:
        await _validate_knowledge_bases(db, user, req.knowledge_base_ids)
        for kb_id in req.knowledge_base_ids:
            kb_uuid = UUID(kb_id) if isinstance(kb_id, str) else kb_id
            db.add(AgentKnowledgeBase(agent_id=agent.id, knowledge_base_id=kb_uuid))

    # Bind graph knowledge bases
    if req.graph_knowledge_base_ids:
        await _validate_graph_knowledge_bases(db, user, req.graph_knowledge_base_ids)
        for kb_id in req.graph_knowledge_base_ids:
            kb_uuid = UUID(kb_id) if isinstance(kb_id, str) else kb_id
            db.add(AgentGraphKnowledgeBase(agent_id=agent.id, knowledge_base_id=kb_uuid))

    # Bind child relationships
    if req.child_ids:
        await _set_child_relationships(db, agent.id, req.child_ids, user)

    await db.flush()

    # Reload with relationships
    result = await db.execute(
        select(Agent)
        .options(
            joinedload(Agent.model),
            selectinload(Agent.skills),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
            selectinload(Agent.children),
            selectinload(Agent.parents),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(Agent.id == agent.id)
    )
    agent = result.scalar_one()
    return _agent_to_dict(agent, user=user)


@router.put("/api/admin/agents/{agent_id}", response_model=AgentOut)
async def admin_update_agent(
    agent_id: UUID,
    req: AgentUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(Agent)
        .options(
            joinedload(Agent.model),
            selectinload(Agent.skills),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
            selectinload(Agent.children),
            selectinload(Agent.parents),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(Agent.id == agent_id, Agent.tenant_id == user.tenant_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if agent.created_by != user.id:
        raise HTTPException(status_code=403, detail="只有智能体创建者可以修改配置")

    if req.is_set("name"):
        agent.name = req.name
    if req.is_set("description"):
        agent.description = req.description
    if req.is_set("icon"):
        agent.icon = req.icon
    if req.is_set("system_prompt"):
        agent.system_prompt = req.system_prompt
    if req.is_set("model_id"):
        if req.model_id is not None:
            model_result = await db.execute(
                select(LLMModel).where(LLMModel.id == req.model_id, LLMModel.tenant_id == user.tenant_id)
            )
            if not model_result.scalar_one_or_none():
                raise HTTPException(status_code=404, detail="模型不存在")
        agent.model_id = req.model_id
    if req.is_set("enabled_tools"):
        agent.enabled_tools = req.enabled_tools if req.enabled_tools is not None else []
    if req.is_set("mcp_server_ids"):
        if req.mcp_server_ids:
            await _validate_mcp_servers(db, user, req.mcp_server_ids)
        agent.mcp_server_ids = req.mcp_server_ids if req.mcp_server_ids is not None else []
    if req.is_set("temperature"):
        agent.temperature = req.temperature
    if req.is_set("max_iterations"):
        agent.max_iterations = req.max_iterations
    if req.is_set("welcome_message"):
        agent.welcome_message = req.welcome_message
    if req.is_set("starter_prompts"):
        agent.starter_prompts = req.starter_prompts
    if req.is_set("enable_memory_extraction"):
        agent.enable_memory_extraction = req.enable_memory_extraction
    if req.is_set("enable_retry"):
        agent.enable_retry = req.enable_retry
    if req.is_set("enable_auto_title"):
        agent.enable_auto_title = req.enable_auto_title
    if req.is_set("is_active"):
        agent.is_active = req.is_active
    if req.is_set("visibility"):
        agent.visibility = req.visibility

    # Update skill bindings if provided
    if req.is_set("skill_ids"):
        await db.execute(
            AgentSkill.__table__.delete().where(
                AgentSkill.__table__.c.agent_id == agent_id
            )
        )
        if req.skill_ids:
            for sid in req.skill_ids:
                skill_uuid = UUID(sid) if isinstance(sid, str) else sid
                db.add(AgentSkill(agent_id=agent_id, skill_id=skill_uuid))

    # Update knowledge base bindings if provided
    if req.is_set("knowledge_base_ids"):
        await db.execute(
            AgentKnowledgeBase.__table__.delete().where(
                AgentKnowledgeBase.__table__.c.agent_id == agent_id
            )
        )
        if req.knowledge_base_ids:
            await _validate_knowledge_bases(db, user, req.knowledge_base_ids)
            for kb_id in req.knowledge_base_ids:
                kb_uuid = UUID(kb_id) if isinstance(kb_id, str) else kb_id
                db.add(AgentKnowledgeBase(agent_id=agent_id, knowledge_base_id=kb_uuid))

    # Update graph knowledge base bindings if provided
    if req.is_set("graph_knowledge_base_ids"):
        await db.execute(
            AgentGraphKnowledgeBase.__table__.delete().where(
                AgentGraphKnowledgeBase.__table__.c.agent_id == agent_id
            )
        )
        if req.graph_knowledge_base_ids:
            await _validate_graph_knowledge_bases(db, user, req.graph_knowledge_base_ids)
            for kb_id in req.graph_knowledge_base_ids:
                kb_uuid = UUID(kb_id) if isinstance(kb_id, str) else kb_id
                db.add(AgentGraphKnowledgeBase(agent_id=agent_id, knowledge_base_id=kb_uuid))

    # Update child relationships if provided
    if req.is_set("child_ids"):
        await _set_child_relationships(db, agent_id, req.child_ids or [], user)

    await db.flush()

    # Reload
    result = await db.execute(
        select(Agent)
        .options(
            joinedload(Agent.model),
            selectinload(Agent.skills),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
            selectinload(Agent.children),
            selectinload(Agent.parents),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(Agent.id == agent_id)
    )
    agent = result.scalar_one()
    return _agent_to_dict(agent, user=user)


@router.delete("/api/admin/agents/{agent_id}")
async def admin_delete_agent(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == user.tenant_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if agent.created_by != user.id:
        raise HTTPException(status_code=403, detail="只有智能体创建者可以删除")

    # Clean up relationships (both directions)
    await db.execute(
        delete(AgentRelationship).where(
            (AgentRelationship.parent_id == agent_id)
            | (AgentRelationship.child_id == agent_id)
        )
    )

    # Clean up skill bindings
    await db.execute(
        AgentSkill.__table__.delete().where(
            AgentSkill.__table__.c.agent_id == agent_id
        )
    )

    # Clean up knowledge base bindings
    await db.execute(
        AgentKnowledgeBase.__table__.delete().where(
            AgentKnowledgeBase.__table__.c.agent_id == agent_id
        )
    )

    # Clean up graph knowledge base bindings
    await db.execute(
        AgentGraphKnowledgeBase.__table__.delete().where(
            AgentGraphKnowledgeBase.__table__.c.agent_id == agent_id
        )
    )

    await db.delete(agent)
    await db.flush()
    return {"message": "智能体已删除"}


# ---- User read-only ----


@router.get("/api/agents", response_model=list[AgentOut])
async def list_agents(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(Agent)
        .options(
            joinedload(Agent.model),
            selectinload(Agent.skills),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
            selectinload(Agent.children),
            selectinload(Agent.parents),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(Agent.is_active, _agent_visible_to(user))
        .order_by(Agent.created_at)
    )
    agents = result.scalars().all()
    return [_agent_to_dict(a, include_prompt=False, user=user) for a in agents]


@router.get("/api/agents/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(Agent)
        .options(
            joinedload(Agent.model),
            selectinload(Agent.skills),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
            selectinload(Agent.children),
            selectinload(Agent.parents),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(Agent.id == agent_id, Agent.is_active, _agent_visible_to(user))
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在")

    return _agent_to_dict(agent, include_prompt=False, user=user)


@router.get("/api/agents/{agent_id}/stats")
async def get_agent_stats(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get usage statistics for an agent (current user's sessions only)."""
    from aio_agent_platform.db.models import Message, Session

    visible = await db.scalar(
        select(func.count()).select_from(Agent).where(
            Agent.id == agent_id, _agent_visible_to(user)
        )
    )
    if not visible:
        raise HTTPException(status_code=404, detail="智能体不存在")

    # Total sessions for this agent owned by current user
    sessions_result = await db.execute(
        select(Session).where(
            Session.agent_id == agent_id,
            Session.user_id == user.id,
        )
    )
    sessions = sessions_result.scalars().all()
    session_ids = [s.id for s in sessions]
    total_sessions = len(sessions)

    # Last active time
    last_active_at = None
    if sessions:
        last_session = max(sessions, key=lambda s: s.updated_at)
        last_active_at = last_session.updated_at.isoformat()

    # Total messages across all sessions
    total_messages = 0
    if session_ids:
        msg_count_result = await db.execute(
            select(func.count()).select_from(Message).where(
                Message.session_id.in_(session_ids)
            )
        )
        total_messages = msg_count_result.scalar() or 0

    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "last_active_at": last_active_at,
    }


# ---- Hierarchy endpoints ----


@router.get("/api/agents/{agent_id}/children", response_model=list[AgentBrief])
async def get_agent_children(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Get all direct children of an agent."""
    result = await db.execute(
        select(Agent)
        .join(
            AgentRelationship,
            AgentRelationship.child_id == Agent.id,
        )
        .where(
            AgentRelationship.parent_id == agent_id,
            Agent.is_active,
            _agent_visible_to(user),
        )
        .order_by(Agent.created_at)
    )
    children = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "icon": c.icon,
            "is_active": c.is_active,
        }
        for c in children
    ]


@router.get("/api/agents/{agent_id}/tree")
async def get_agent_tree(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get the full hierarchy tree rooted at the specified agent."""
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.children),
            with_loader_criteria(Agent, _agent_visible_to(user), include_aliases=True),
        )
        .where(Agent.id == agent_id, _agent_visible_to(user))
    )
    root = result.scalar_one_or_none()
    if not root:
        raise HTTPException(status_code=404, detail="智能体不存在")

    return _build_tree(root, visited=set())


# ---- Helpers ----


async def _set_child_relationships(
    db: AsyncSession, parent_id: UUID, child_ids: list[str], user
) -> None:
    """Set child relationships for a parent agent (replaces existing)."""
    # Remove existing child relationships for this parent
    await db.execute(
        delete(AgentRelationship).where(AgentRelationship.parent_id == parent_id)
    )

    if not child_ids:
        return

    # Validate all child IDs exist
    child_uuids = []
    for cid in child_ids:
        child_uuid = UUID(cid) if isinstance(cid, str) else cid
        child_uuids.append(child_uuid)

    result = await db.execute(
        select(Agent.id).where(Agent.id.in_(child_uuids), _agent_visible_to(user))
    )
    existing_ids = set(result.scalars().all())

    missing = set(child_uuids) - existing_ids
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"子智能体不存在: {', '.join(str(m) for m in missing)}",
        )

    # Self-reference check
    if parent_id in child_uuids:
        raise HTTPException(status_code=400, detail="不能将智能体设为自己的子级")

    # Cycle detection: check if any child is an ancestor of parent
    for child_uuid in child_uuids:
        if await _would_create_cycle(db, parent_id, child_uuid):
            raise HTTPException(
                status_code=400,
                detail="不能形成循环的智能体层级关系",
            )

    # Create new relationships
    for child_uuid in child_uuids:
        db.add(AgentRelationship(parent_id=parent_id, child_id=child_uuid))


async def _would_create_cycle(
    db: AsyncSession, parent_id: UUID, child_id: UUID
) -> bool:
    """Check if adding parent->child would create a cycle.

    A cycle exists if child_id is an ancestor of parent_id
    (i.e., parent is already a descendant of child).
    """
    # BFS from parent_id upward through its ancestors
    visited = set()
    queue = [parent_id]

    while queue:
        current = queue.pop(0)
        if current == child_id:
            return True
        if current in visited:
            continue
        visited.add(current)

        # Get parents of current node
        result = await db.execute(
            select(AgentRelationship.parent_id).where(
                AgentRelationship.child_id == current
            )
        )
        parents = result.scalars().all()
        queue.extend(parents)

    return False


def _build_tree(agent: Agent, visited: set) -> dict:
    """Recursively build a hierarchy tree dict."""
    node = {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "icon": agent.icon,
        "is_active": agent.is_active,
        "children": [],
    }

    if agent.id in visited:
        return node  # Prevent infinite recursion on DAG cycles

    visited.add(agent.id)

    if agent.children:
        for child in agent.children:
            node["children"].append(_build_tree(child, visited))

    return node


def _agent_visible_to(user):
    return (
        (Agent.tenant_id == user.tenant_id)
        & or_(Agent.visibility == "tenant", Agent.created_by == user.id)
    )


async def _validate_knowledge_bases(
    db: AsyncSession, user, knowledge_base_ids: list[str]
) -> None:
    ids = [UUID(value) if isinstance(value, str) else value for value in knowledge_base_ids]
    if not ids:
        return
    result = await db.execute(
        select(KnowledgeBase.id).where(
            KnowledgeBase.id.in_(ids),
            KnowledgeBase.tenant_id == user.tenant_id,
            or_(KnowledgeBase.visibility == "tenant", KnowledgeBase.created_by == user.id),
        )
    )
    if set(result.scalars().all()) != set(ids):
        raise HTTPException(status_code=404, detail="知识库不存在或无权访问")


async def _validate_graph_knowledge_bases(
    db: AsyncSession, user, graph_knowledge_base_ids: list[str]
) -> None:
    ids = [UUID(value) if isinstance(value, str) else value for value in graph_knowledge_base_ids]
    if not ids:
        return
    result = await db.execute(
        select(GraphKnowledgeBase.id).where(
            GraphKnowledgeBase.id.in_(ids),
            GraphKnowledgeBase.tenant_id == user.tenant_id,
            or_(
                GraphKnowledgeBase.visibility == "tenant",
                GraphKnowledgeBase.created_by == user.id,
            ),
        )
    )
    if set(result.scalars().all()) != set(ids):
        raise HTTPException(status_code=404, detail="图谱知识库不存在或无权访问")


async def _validate_mcp_servers(db: AsyncSession, user, mcp_server_ids: list[str]) -> None:
    ids = [UUID(value) if isinstance(value, str) else value for value in mcp_server_ids]
    if not ids:
        return
    result = await db.execute(
        select(MCPServer.id).where(
            MCPServer.id.in_(ids),
            MCPServer.tenant_id == user.tenant_id,
        )
    )
    if set(result.scalars().all()) != set(ids):
        raise HTTPException(status_code=404, detail="MCP Server 不存在或无权访问")


def _agent_to_dict(agent: Agent, include_prompt: bool = True, user=None) -> dict:
    d = {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "icon": agent.icon,
        "model_id": agent.model_id,
        "model_name": agent.model.name if agent.model else None,
        "enabled_tools": agent.enabled_tools or [],
        "mcp_server_ids": agent.mcp_server_ids or [],
        "temperature": agent.temperature,
        "max_iterations": agent.max_iterations,
        "welcome_message": agent.welcome_message,
        "starter_prompts": agent.starter_prompts,
        "enable_memory_extraction": agent.enable_memory_extraction,
        "enable_retry": agent.enable_retry if agent.enable_retry is not None else True,
        "enable_auto_title": agent.enable_auto_title if agent.enable_auto_title is not None else True,
        "is_active": agent.is_active,
        "skill_ids": [str(s.id) for s in agent.skills] if agent.skills else [],
        "knowledge_base_ids": [str(kb.id) for kb in agent.knowledge_bases] if agent.knowledge_bases else [],
        "graph_knowledge_base_ids": [str(kb.id) for kb in agent.graph_knowledge_bases] if agent.graph_knowledge_bases else [],
        "parent_ids": [str(p.id) for p in agent.parents] if agent.parents else [],
        "child_ids": [str(c.id) for c in agent.children] if agent.children else [],
        "children_count": len(agent.children) if agent.children else 0,
        "tenant_id": agent.tenant_id,
        "created_by": agent.created_by,
        "visibility": agent.visibility or "tenant",
        "can_edit": bool(user and agent.created_by == user.id),
    }
    if include_prompt:
        d["system_prompt"] = agent.system_prompt
    return d
