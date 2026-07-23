"""JWT token handling."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel

from aio_agent_platform.core.config import settings


class TokenPayload(BaseModel):
    """JWT payload."""

    sub: str  # user_id
    role: str | None = None  # user | admin (only in access tokens)
    type: str  # access | refresh
    jti: str | None = None  # unique token ID (refresh tokens only)
    exp: int | None = None
    iat: int | None = None


class TokenPair(BaseModel):
    """Access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def create_access_token(user_id: UUID, role: str) -> str:
    """Create a short-lived access token (15 min default)."""
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=settings.jwt.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)


def create_refresh_token(user_id: UUID) -> str:
    """Create a long-lived refresh token (7 days default)."""
    now = datetime.now(UTC)
    expire = now + timedelta(days=settings.jwt.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": str(uuid4()),  # Unique ID to prevent hash collisions
        "exp": expire,
        "iat": now,
    }
    return jwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)


def decode_token(token: str) -> TokenPayload:
    """Decode and validate a JWT token. Raises on invalid/expired."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt.secret,
            algorithms=[settings.jwt.algorithm],
        )
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError as e:
        raise TokenExpiredError("Token has expired") from e
    except jwt.InvalidTokenError as e:
        raise InvalidTokenError("Invalid token") from e


class TokenError(Exception):
    """Base token error."""


class TokenExpiredError(TokenError):
    """Token has expired."""


class InvalidTokenError(TokenError):
    """Token is invalid."""
