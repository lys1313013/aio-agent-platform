"""Platform tenant and tenant-user management routes."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import SuperAdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import (
    Agent,
    KnowledgeBase,
    Tenant,
    TenantMembership,
    User,
    UserProfile,
)

router = APIRouter(prefix="/api/admin/tenants", tags=["admin-tenants"])


class TenantOut(BaseModel):
    id: UUID
    name: str
    slug: str
    is_active: bool
    users_count: int = 0
    agents_count: int = 0
    knowledge_bases_count: int = 0
    created_at: datetime


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=2, max_length=64)


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    slug: str | None = Field(default=None, min_length=2, max_length=64)
    is_active: bool | None = None


class TenantUserOut(BaseModel):
    id: UUID
    tenant_id: UUID
    username: str
    email: str
    display_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime


class TenantMemberAssign(BaseModel):
    user_ids: list[UUID] = Field(..., min_length=1)


def _normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise HTTPException(
            status_code=422,
            detail="租户标识只能包含小写字母、数字和连字符",
        )
    return slug


async def _get_tenant_or_404(db: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")
    return tenant


async def _ensure_slug_available(
    db: AsyncSession, slug: str, exclude_id: UUID | None = None
) -> None:
    query = select(Tenant.id).where(Tenant.slug == slug)
    if exclude_id:
        query = query.where(Tenant.id != exclude_id)
    if await db.scalar(query):
        raise HTTPException(status_code=409, detail="租户标识已存在")


@router.get("", response_model=list[TenantOut])
async def list_tenants(
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(
            Tenant,
            select(func.count(TenantMembership.user_id))
            .where(TenantMembership.tenant_id == Tenant.id)
            .correlate(Tenant)
            .scalar_subquery(),
            select(func.count(Agent.id))
            .where(Agent.tenant_id == Tenant.id)
            .correlate(Tenant)
            .scalar_subquery(),
            select(func.count(KnowledgeBase.id))
            .where(KnowledgeBase.tenant_id == Tenant.id)
            .correlate(Tenant)
            .scalar_subquery(),
        ).order_by(Tenant.created_at)
    )
    return [
        {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "is_active": tenant.is_active,
            "users_count": users_count,
            "agents_count": agents_count,
            "knowledge_bases_count": knowledge_bases_count,
            "created_at": tenant.created_at,
        }
        for tenant, users_count, agents_count, knowledge_bases_count in result.all()
    ]


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    req: TenantCreate,
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    slug = _normalize_slug(req.slug)
    await _ensure_slug_available(db, slug)
    tenant = Tenant(name=req.name.strip(), slug=slug)
    db.add(tenant)
    await db.flush()
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "is_active": tenant.is_active,
        "users_count": 0,
        "agents_count": 0,
        "knowledge_bases_count": 0,
        "created_at": tenant.created_at,
    }


@router.put("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: UUID,
    req: TenantUpdate,
    superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant = await _get_tenant_or_404(db, tenant_id)
    if req.name is not None:
        tenant.name = req.name.strip()
    if req.slug is not None:
        slug = _normalize_slug(req.slug)
        await _ensure_slug_available(db, slug, tenant.id)
        tenant.slug = slug
    if req.is_active is not None:
        if tenant.id == superadmin.tenant_id and not req.is_active:
            raise HTTPException(status_code=400, detail="不能停用自己所属的租户")
        tenant.is_active = req.is_active
    await db.flush()

    counts = await db.execute(
        select(
            select(func.count(TenantMembership.user_id))
            .where(TenantMembership.tenant_id == tenant.id)
            .scalar_subquery(),
            select(func.count(Agent.id)).where(Agent.tenant_id == tenant.id).scalar_subquery(),
            select(func.count(KnowledgeBase.id))
            .where(KnowledgeBase.tenant_id == tenant.id)
            .scalar_subquery(),
        )
    )
    users_count, agents_count, knowledge_bases_count = counts.one()
    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "is_active": tenant.is_active,
        "users_count": users_count,
        "agents_count": agents_count,
        "knowledge_bases_count": knowledge_bases_count,
        "created_at": tenant.created_at,
    }


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: UUID,
    superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    tenant = await _get_tenant_or_404(db, tenant_id)
    if tenant.id == superadmin.tenant_id:
        raise HTTPException(status_code=400, detail="不能删除自己所属的租户")
    resources = await db.scalar(
        select(func.count())
        .select_from(TenantMembership)
        .where(TenantMembership.tenant_id == tenant.id)
    )
    if resources:
        raise HTTPException(status_code=409, detail="租户仍有用户，不能删除")
    agent_count = await db.scalar(select(func.count()).select_from(Agent).where(Agent.tenant_id == tenant.id))
    kb_count = await db.scalar(
        select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant.id)
    )
    if agent_count or kb_count:
        raise HTTPException(status_code=409, detail="租户仍有智能体或知识库，不能删除")
    await db.delete(tenant)


@router.get("/{tenant_id}/users", response_model=list[TenantUserOut])
async def list_tenant_users(
    tenant_id: UUID,
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    await _get_tenant_or_404(db, tenant_id)
    result = await db.execute(
        select(User, UserProfile.display_name)
        .join(TenantMembership, TenantMembership.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(User.created_at)
    )
    return [
        {
            "id": user.id,
            "tenant_id": user.tenant_id,
            "username": user.username,
            "email": user.email,
            "display_name": display_name,
            "role": user.role,
            "is_active": user.is_active,
            "created_at": user.created_at,
        }
        for user, display_name in result.all()
    ]


@router.put("/{tenant_id}/users")
async def assign_tenant_users(
    tenant_id: UUID,
    req: TenantMemberAssign,
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant = await _get_tenant_or_404(db, tenant_id)
    if not tenant.is_active:
        raise HTTPException(status_code=400, detail="不能向已停用租户添加成员")
    result = await db.execute(select(User.id).where(User.id.in_(req.user_ids)))
    existing_user_ids = set(result.scalars().all())
    missing = set(req.user_ids) - existing_user_ids
    if missing:
        raise HTTPException(status_code=404, detail="部分用户不存在")
    existing_result = await db.execute(
        select(TenantMembership.user_id).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id.in_(req.user_ids),
        )
    )
    already_member_ids = set(existing_result.scalars().all())
    for user_id in existing_user_ids - already_member_ids:
        db.add(TenantMembership(tenant_id=tenant.id, user_id=user_id))
    await db.flush()
    return {"message": "租户成员已添加", "added_count": len(existing_user_ids - already_member_ids)}


@router.delete("/{tenant_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tenant_user(
    tenant_id: UUID,
    user_id: UUID,
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    await _get_tenant_or_404(db, tenant_id)
    user = await db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    memberships_result = await db.execute(
        select(TenantMembership).where(TenantMembership.user_id == user.id)
    )
    memberships = memberships_result.scalars().all()
    if not any(item.tenant_id == tenant_id for item in memberships):
        raise HTTPException(status_code=404, detail="用户不属于该租户")
    if len(memberships) == 1:
        raise HTTPException(status_code=409, detail="用户必须至少属于一个租户")
    if user.tenant_id == tenant_id:
        other_ids = [item.tenant_id for item in memberships if item.tenant_id != tenant_id]
        replacement = await db.scalar(
            select(Tenant.id).where(
                Tenant.id.in_(other_ids),
                Tenant.is_active,
            ).limit(1)
        )
        if not replacement:
            raise HTTPException(status_code=409, detail="用户没有其他已启用租户可切换")
        user.tenant_id = replacement
    await db.execute(
        delete(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
        )
    )
