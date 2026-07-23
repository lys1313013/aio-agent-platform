"""User settings routes — profile and config CRUD."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db import PortraitVersion, User, UserConfig, UserProfile
from aio_agent_platform.db.connection import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ---- Schemas ----


class ProfileOut(BaseModel):
    username: str
    email: str
    display_name: str | None = None

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    username: str | None = None
    email: str | None = None


class PersonalPortraitOut(BaseModel):
    personal_portrait: str | None = None


class PersonalPortraitUpdate(BaseModel):
    personal_portrait: str | None = None


class PortraitVersionOut(BaseModel):
    id: str
    content: str | None = None
    source: str
    created_at: str

    model_config = {"from_attributes": True}


class PortraitVersionListOut(BaseModel):
    versions: list[PortraitVersionOut]


class PasswordUpdate(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class SecurityConfigOut(BaseModel):
    trust_level: str = "ask_dangerous"


class SecurityConfigUpdate(BaseModel):
    trust_level: str = Field(
        default="ask_dangerous", pattern="^(ask_always|ask_dangerous|auto_all)$"
    )


class MemoryConfigOut(BaseModel):
    top_k: int = 5
    compress_threshold: int = 50


class MemoryConfigUpdate(BaseModel):
    top_k: int | None = Field(default=None, ge=1, le=20)
    compress_threshold: int | None = Field(default=None, ge=10, le=200)


# ---- Helpers ----


async def _get_or_create_config(db: AsyncSession, user_id: UUID) -> UserConfig:
    result = await db.execute(select(UserConfig).where(UserConfig.user_id == user_id))
    config = result.scalar_one_or_none()
    if not config:
        config = UserConfig(user_id=user_id)
        db.add(config)
        await db.flush()
    return config


async def _get_or_create_profile(db: AsyncSession, user_id: UUID) -> UserProfile:
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if not profile:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        await db.flush()
    return profile


# ---- Profile ----


@router.get("/profile", response_model=ProfileOut)
async def get_profile(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    profile = await _get_or_create_profile(db, user.id)
    return {
        "username": user.username,
        "email": user.email,
        "display_name": profile.display_name,
    }


@router.put("/profile", response_model=ProfileOut)
async def update_profile(
    req: ProfileUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    profile = await _get_or_create_profile(db, user.id)

    if req.display_name is not None:
        profile.display_name = req.display_name
    if req.username is not None:
        # Check uniqueness
        result = await db.execute(
            select(User).where(User.username == req.username, User.id != user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="用户名已存在")
        user.username = req.username
    if req.email is not None:
        result = await db.execute(
            select(User).where(User.email == req.email, User.id != user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="邮箱已被使用")
        user.email = req.email

    await db.flush()
    return {
        "username": user.username,
        "email": user.email,
        "display_name": profile.display_name,
    }


# ---- Personal Portrait ----


@router.get("/personal-portrait", response_model=PersonalPortraitOut)
async def get_personal_portrait(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    profile = await _get_or_create_profile(db, user.id)
    return {"personal_portrait": profile.personal_portrait}


@router.put("/personal-portrait", response_model=PersonalPortraitOut)
async def update_personal_portrait(
    req: PersonalPortraitUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    profile = await _get_or_create_profile(db, user.id)

    # Save a version snapshot before overwriting
    if profile.personal_portrait and profile.personal_portrait.strip():
        db.add(PortraitVersion(
            user_id=user.id,
            content=profile.personal_portrait,
            source="manual",
        ))

    profile.personal_portrait = req.personal_portrait
    await db.flush()
    return {"personal_portrait": profile.personal_portrait}


# ---- Portrait Version History ----


@router.get("/personal-portrait/versions", response_model=PortraitVersionListOut)
async def list_portrait_versions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(PortraitVersion)
        .where(PortraitVersion.user_id == user.id)
        .order_by(PortraitVersion.created_at.desc())
        .limit(50)
    )
    versions = result.scalars().all()
    return {
        "versions": [
            PortraitVersionOut(
                id=str(v.id),
                content=v.content,
                source=v.source,
                created_at=v.created_at.isoformat(),
            )
            for v in versions
        ]
    }


@router.get("/personal-portrait/versions/{version_id}", response_model=PortraitVersionOut)
async def get_portrait_version(
    version_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PortraitVersionOut:
    result = await db.execute(
        select(PortraitVersion).where(
            PortraitVersion.id == version_id,
            PortraitVersion.user_id == user.id,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="版本不存在")
    return PortraitVersionOut(
        id=str(version.id),
        content=version.content,
        source=version.source,
        created_at=version.created_at.isoformat(),
    )


@router.post("/personal-portrait/versions/{version_id}/restore")
async def restore_portrait_version(
    version_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Restore the portrait to a historical version (saves current as snapshot first)."""
    result = await db.execute(
        select(PortraitVersion).where(
            PortraitVersion.id == version_id,
            PortraitVersion.user_id == user.id,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise HTTPException(status_code=404, detail="版本不存在")

    profile = await _get_or_create_profile(db, user.id)

    # Save current as snapshot before restoring
    if profile.personal_portrait and profile.personal_portrait.strip():
        db.add(PortraitVersion(
            user_id=user.id,
            content=profile.personal_portrait,
            source="manual",
        ))

    profile.personal_portrait = version.content
    await db.flush()
    return {"personal_portrait": profile.personal_portrait}


# ---- Password ----


@router.put("/password")
async def update_password(
    req: PasswordUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from aio_agent_platform.auth.password import hash_password, verify_password

    if not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")

    user.password_hash = hash_password(req.new_password)
    await db.flush()
    return {"message": "密码已更新"}


# ---- Security Config ----


@router.get("/security", response_model=SecurityConfigOut)
async def get_security_config(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    config = await _get_or_create_config(db, user.id)
    return {"trust_level": config.trust_level}


@router.put("/security", response_model=SecurityConfigOut)
async def update_security_config(
    req: SecurityConfigUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    config = await _get_or_create_config(db, user.id)
    config.trust_level = req.trust_level
    await db.flush()
    return {"trust_level": config.trust_level}


# ---- Memory Config ----


@router.get("/memory", response_model=MemoryConfigOut)
async def get_memory_config(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    config = await _get_or_create_config(db, user.id)
    extra = config.extra or {}
    return {
        "top_k": config.memory_top_k,
        "compress_threshold": extra.get("compress_threshold", 50),
    }


@router.put("/memory", response_model=MemoryConfigOut)
async def update_memory_config(
    req: MemoryConfigUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    config = await _get_or_create_config(db, user.id)

    if req.top_k is not None:
        config.memory_top_k = req.top_k
    if req.compress_threshold is not None:
        extra = dict(config.extra or {})
        extra["compress_threshold"] = req.compress_threshold
        config.extra = extra

    await db.flush()
    extra = config.extra or {}
    return {
        "top_k": config.memory_top_k,
        "compress_threshold": extra.get("compress_threshold", 50),
    }
