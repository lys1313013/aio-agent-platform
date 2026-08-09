"""Platform user management routes for super administrators."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import SuperAdminUser
from aio_agent_platform.auth.password import hash_password
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Tenant, TenantMembership, User, UserConfig, UserProfile

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


class UserAdminOut(BaseModel):
    id: UUID
    username: str
    email: str
    display_name: str | None = None
    role: str
    is_active: bool
    active_tenant_id: UUID
    tenant_ids: list[UUID]
    created_at: datetime


class UserAdminCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    role: str = Field(default="user", pattern="^(user|admin|superadmin)$")
    tenant_ids: list[UUID] = Field(..., min_length=1)
    active_tenant_id: UUID | None = None


class UserAdminUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: str | None = Field(default=None, pattern="^(user|admin|superadmin)$")
    is_active: bool | None = None


def _user_to_dict(user: User, display_name: str | None) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "display_name": display_name,
        "role": user.role,
        "is_active": user.is_active,
        "active_tenant_id": user.tenant_id,
        "tenant_ids": [membership.tenant_id for membership in user.memberships],
        "created_at": user.created_at,
    }


async def _validate_tenants(
    db: AsyncSession, tenant_ids: list[UUID]
) -> list[UUID]:
    unique_ids = list(dict.fromkeys(tenant_ids))
    result = await db.execute(
        select(Tenant.id).where(Tenant.id.in_(unique_ids), Tenant.is_active)
    )
    existing_ids = set(result.scalars().all())
    if existing_ids != set(unique_ids):
        raise HTTPException(status_code=404, detail="部分租户不存在或已停用")
    return unique_ids


@router.get("", response_model=list[UserAdminOut])
async def list_users(
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(User, UserProfile.display_name)
        .options(selectinload(User.memberships))
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(User.is_shadow.is_(False))
        .order_by(User.created_at)
    )
    return [_user_to_dict(user, display_name) for user, display_name in result.all()]


@router.post("", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    req: UserAdminCreate,
    _superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    duplicate = await db.scalar(
        select(User.id).where(or_(User.username == req.username, User.email == req.email))
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="用户名或邮箱已存在")
    tenant_ids = await _validate_tenants(db, req.tenant_ids)
    active_tenant_id = req.active_tenant_id or tenant_ids[0]
    if active_tenant_id not in tenant_ids:
        raise HTTPException(status_code=400, detail="当前租户必须包含在所属租户中")

    user = User(
        username=req.username,
        email=str(req.email),
        password_hash=hash_password(req.password),
        role=req.role,
        tenant_id=active_tenant_id,
    )
    db.add(user)
    await db.flush()
    for tenant_id in tenant_ids:
        db.add(TenantMembership(tenant_id=tenant_id, user_id=user.id))
    profile = UserProfile(user_id=user.id, tenant_id=active_tenant_id, display_name=req.display_name)
    db.add(profile)
    db.add(UserConfig(user_id=user.id))
    await db.flush()
    await db.refresh(user, attribute_names=["memberships"])
    return _user_to_dict(user, profile.display_name)


@router.put("/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: UUID,
    req: UserAdminUpdate,
    superadmin: SuperAdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(User).options(selectinload(User.memberships)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.id == superadmin.id and (req.role is not None or req.is_active is False):
        raise HTTPException(status_code=400, detail="不能修改自己的角色或激活状态")
    if req.username is not None and req.username != user.username:
        if await db.scalar(
            select(User.id).where(User.username == req.username, User.id != user.id)
        ):
            raise HTTPException(status_code=409, detail="用户名已存在")
        user.username = req.username
    if req.email is not None and req.email != user.email:
        if await db.scalar(
            select(User.id).where(User.email == req.email, User.id != user.id)
        ):
            raise HTTPException(status_code=409, detail="邮箱已存在")
        user.email = str(req.email)
    if req.password is not None:
        user.password_hash = hash_password(req.password)
    if req.role is not None:
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    profile = await db.scalar(
        select(UserProfile).where(
            UserProfile.user_id == user.id,
            UserProfile.tenant_id == user.tenant_id,
        )
    )
    if not profile:
        profile = UserProfile(user_id=user.id, tenant_id=user.tenant_id)
        db.add(profile)
    if "display_name" in req.model_fields_set:
        profile.display_name = req.display_name
    await db.flush()
    return _user_to_dict(user, profile.display_name)
