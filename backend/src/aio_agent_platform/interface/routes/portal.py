"""Portal routes — 用户端对话门户的精简只读接口。

与 /api/agents 的区别：只返回门户展示所需字段（名称/描述/图标/欢迎语/快捷提问），
不暴露 system_prompt、工具配置、模型等实现细节。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent

router = APIRouter(tags=["portal"])


class PortalAgentOut(BaseModel):
    """门户可见的 agent 精简信息（脱敏）。"""

    id: UUID
    name: str
    description: str | None = None
    icon: str = "robot"
    welcome_message: str | None = None
    starter_prompts: list[dict] | None = None


def _portal_visible_to(user):
    return (
        (Agent.tenant_id == user.tenant_id)
        & or_(Agent.visibility == "tenant", Agent.created_by == user.id)
    )


def _to_portal_out(agent: Agent) -> PortalAgentOut:
    return PortalAgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        icon=agent.icon,
        welcome_message=agent.welcome_message,
        starter_prompts=agent.starter_prompts,
    )


@router.get("/api/portal/agents", response_model=list[PortalAgentOut])
async def list_portal_agents(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PortalAgentOut]:
    result = await db.execute(
        select(Agent)
        .where(Agent.is_active, _portal_visible_to(user))
        .order_by(Agent.created_at)
    )
    return [_to_portal_out(a) for a in result.scalars().all()]


@router.get("/api/portal/agents/{agent_id}", response_model=PortalAgentOut)
async def get_portal_agent(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PortalAgentOut:
    result = await db.execute(
        select(Agent).where(
            Agent.id == agent_id, Agent.is_active, _portal_visible_to(user)
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="智能体不存在或无权访问")
    return _to_portal_out(agent)
