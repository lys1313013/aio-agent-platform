"""Password hashing with bcrypt."""

from typing import Annotated

import bcrypt
from anyio import CapacityLimiter, to_thread
from pydantic import AfterValidator, Field


def validate_password_bytes(password: str) -> str:
    """bcrypt's limit is bytes, not Unicode characters; never silently truncate."""
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
    return password


Password = Annotated[str, Field(max_length=72), AfterValidator(validate_password_bytes)]
NewPassword = Annotated[Password, Field(min_length=8)]
_password_limiter = CapacityLimiter(4)


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    validate_password_bytes(password)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash."""
    if len(password.encode("utf-8")) > 72:
        return False
    return bcrypt.checkpw(password.encode(), hashed.encode())


async def async_hash_password(password: str) -> str:
    """Bound CPU concurrency and keep bcrypt off the request event loop."""
    return await to_thread.run_sync(hash_password, password, limiter=_password_limiter)


async def async_verify_password(password: str, hashed: str) -> bool:
    return await to_thread.run_sync(verify_password, password, hashed, limiter=_password_limiter)
