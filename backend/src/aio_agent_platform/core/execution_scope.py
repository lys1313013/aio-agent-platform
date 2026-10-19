"""Tenant context pinned by a validated background execution, never by the UI."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.models import Agent, Tenant, TenantMembership, User


class ExecutionAccessError(ValueError):
    """Execution ownership or membership is no longer valid."""


async def require_execution_user(db: AsyncSession, user_id: UUID, tenant_id: UUID) -> User:
    """Check actual membership, without changing the user's UI selection."""
    user = await db.scalar(
        select(User).options(joinedload(User.tenant)).join(TenantMembership, TenantMembership.user_id == User.id)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(User.id == user_id, User.is_active, Tenant.id == tenant_id, Tenant.is_active)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise ExecutionAccessError("用户已停用、租户已停用或用户已不属于执行租户")
    return user


async def require_agent_tenant(db: AsyncSession, user_id: UUID, agent_id: UUID) -> UUID:
    """Resolve an owned execution object, never the user's mutable UI tenant."""
    from sqlalchemy import or_

    tenant_id = await db.scalar(select(Agent.tenant_id).where(
        Agent.id == agent_id, Agent.is_active,
        or_(Agent.visibility == "tenant", Agent.created_by == user_id),
    ))
    if tenant_id is None:
        raise ExecutionAccessError("智能体不存在、已停用或无权访问")
    await require_execution_user(db, user_id, tenant_id)
    return tenant_id


async def resolve_background_tenant(
    db: AsyncSession, user_id: UUID, *, tenant_id: UUID | None = None,
    agent_id: UUID | None = None, session_id: UUID | None = None,
) -> UUID:
    """Validate the source of delayed work. Unknown historical ownership fails closed."""
    from aio_agent_platform.db.models import Session

    if session_id is not None:
        session = await db.scalar(select(Session).where(
            Session.id == session_id, Session.user_id == user_id,
        ))
        if session is None:
            raise ExecutionAccessError("来源会话不存在或无权访问")
        if agent_id is not None and session.agent_id != agent_id:
            raise ExecutionAccessError("来源会话的智能体已改变")
        agent_id = session.agent_id
    if agent_id is not None:
        owned_tenant = await require_agent_tenant(db, user_id, agent_id)
        if tenant_id is not None and owned_tenant != tenant_id:
            raise ExecutionAccessError("来源对象与执行租户不一致")
        return owned_tenant
    tenant_id = tenant_id or execution_tenant_id(user_id)
    if tenant_id is None:
        raise ExecutionAccessError("无法确定历史执行对象的租户，跳过后台处理")
    await require_execution_user(db, user_id, tenant_id)
    return tenant_id


@dataclass(frozen=True)
class ExecutionScope:
    user_id: UUID
    tenant_id: UUID


_execution_scope: ContextVar[ExecutionScope | None] = ContextVar(
    "execution_scope", default=None
)


def execution_tenant_id(user_id: UUID) -> UUID | None:
    scope = _execution_scope.get()
    if scope is None:
        return None
    if scope.user_id != user_id:
        raise ValueError("执行上下文与用户不匹配")
    return scope.tenant_id


@contextmanager
def bind_execution_scope(user_id: UUID, tenant_id: UUID, *, agent_id: UUID | None = None):
    """Caller must validate tenant membership before binding this scope."""
    token = _execution_scope.set(ExecutionScope(user_id, tenant_id))
    agent_token = current_agent_id.set(str(agent_id) if agent_id is not None else None)
    try:
        yield
    finally:
        current_agent_id.reset(agent_token)
        _execution_scope.reset(token)


async def resolve_execution_tenant(db: AsyncSession, user_id: UUID) -> UUID | None:
    pinned = execution_tenant_id(user_id)
    if pinned is not None:
        return pinned
    result = await db.execute(select(User.tenant_id).where(User.id == user_id))
    return result.scalar_one_or_none()
