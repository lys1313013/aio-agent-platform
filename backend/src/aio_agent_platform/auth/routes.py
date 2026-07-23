"""Auth routes: register, login, refresh, logout."""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.jwt_handler import (
    TokenPair,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from aio_agent_platform.auth.password import hash_password, verify_password
from aio_agent_platform.core.config import settings
from aio_agent_platform.db import (
    RefreshToken,
    Tenant,
    TenantMembership,
    User,
    UserConfig,
    UserProfile,
)
from aio_agent_platform.db.connection import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    tenant_name: str | None = Field(default=None, min_length=1, max_length=128)


class LoginRequest(BaseModel):
    username_or_email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


def _hash_token(token: str) -> str:
    """Hash a refresh token with SHA-256 for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def _store_refresh_token(
    db: AsyncSession, user_id: UUID, token: str, expire_days: int
) -> None:
    """Persist a refresh token hash to the database."""
    expires_at = datetime.now(UTC) + timedelta(days=expire_days)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_token(token),
            expires_at=expires_at,
        )
    )
    await db.flush()


# ---- Register ----


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    # Check duplicate
    existing = await db.execute(
        select(User).where((User.username == req.username) | (User.email == req.email))
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already registered",
        )

    # Determine if this is the first user (make admin)
    total_users = await db.execute(select(User))
    is_first_user = len(total_users.scalars().all()) == 0

    # Public registration creates an isolated tenant. Joining an existing tenant
    # must go through an administrator-controlled membership flow.
    tenant = Tenant(
        name=req.tenant_name or f"{req.username} 的租户",
        slug=f"{req.username.lower()[:40]}-{uuid4().hex[:12]}",
    )
    db.add(tenant)
    await db.flush()

    # Create user
    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        role="superadmin" if is_first_user else "user",
        tenant_id=tenant.id,
    )
    db.add(user)
    await db.flush()
    db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id))

    # Create profile + config
    db.add(UserProfile(user_id=user.id))
    db.add(UserConfig(user_id=user.id))
    await db.flush()

    # Issue tokens
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)

    await _store_refresh_token(db, user.id, refresh, settings.jwt.refresh_token_expire_days)

    return TokenPair(access_token=access, refresh_token=refresh)


# ---- Login ----


@router.post("/login", response_model=TokenPair)
async def login(
    req: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    result = await db.execute(
        select(User).options(selectinload(User.tenant)).where(
            (User.username == req.username_or_email) | (User.email == req.username_or_email)
        )
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if not user.is_active or not user.tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)

    # Persist refresh token
    await _store_refresh_token(db, user.id, refresh, settings.jwt.refresh_token_expire_days)

    return TokenPair(access_token=access, refresh_token=refresh)


# ---- Refresh ----


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    req: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    try:
        payload = decode_token(req.refresh_token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        ) from e

    if payload.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a refresh token",
        )

    # Verify token hash exists and not expired
    token_hash = _hash_token(req.refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.expires_at > datetime.now(UTC),
        )
    )
    token_record = result.scalar_one_or_none()
    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or expired",
        )

    # Fetch user
    user_result = await db.execute(
        select(User).options(selectinload(User.tenant)).where(User.id == UUID(payload.sub))
    )
    user = user_result.scalar_one()
    if not user.is_active or not user.tenant.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    # Delete old refresh token (rotate)
    await db.execute(delete(RefreshToken).where(RefreshToken.id == token_record.id))

    # Issue new tokens
    access = create_access_token(user.id, user.role)
    new_refresh = create_refresh_token(user.id)

    # Persist new refresh token
    await _store_refresh_token(db, user.id, new_refresh, settings.jwt.refresh_token_expire_days)

    return TokenPair(access_token=access, refresh_token=new_refresh)


# ---- Logout ----


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    req: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Revoke a refresh token."""
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        return  # Silently succeed on invalid token

    token_hash = _hash_token(req.refresh_token)
    await db.execute(
        delete(RefreshToken).where(
            RefreshToken.user_id == UUID(payload.sub),
            RefreshToken.token_hash == token_hash,
        )
    )
