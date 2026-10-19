"""FastAPI auth dependencies."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.jwt_handler import TokenExpiredError, TokenPayload, decode_token
from aio_agent_platform.db import User
from aio_agent_platform.db.connection import current_user_id, get_db

security = HTTPBearer()


async def get_execution_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Extract user from JWT and set RLS context variable."""
    try:
        payload: TokenPayload = decode_token(credentials.credentials)
    except TokenExpiredError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    if payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    # Set RLS context variable
    current_user_id.set(payload.sub)

    # Identity authentication is independent of the mutable UI tenant.
    try:
        user_id = UUID(payload.sub)
    except ValueError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e
    user = await db.scalar(select(User).where(User.id == user_id, User.is_active))
    if user is None:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def get_current_user(
    user: Annotated[User, Depends(get_execution_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Management/new-object entry points additionally require the selected tenant."""
    from aio_agent_platform.core.execution_scope import ExecutionAccessError, require_execution_user

    try:
        return await require_execution_user(db, user.id, user.tenant_id)
    except ExecutionAccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require the current user to be an admin."""
    if user.role not in {"admin", "superadmin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


async def require_superadmin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require the current user to be a platform super administrator."""
    if user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super administrator access required",
        )
    return user


# Type aliases for dependency injection
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
SuperAdminUser = Annotated[User, Depends(require_superadmin)]
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Object execution callers must validate membership against the object itself.
ExecutionUser = Annotated[User, Depends(get_execution_user)]
