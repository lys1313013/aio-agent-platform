"""Channel binding — shadow account creation, bind-code lifecycle, account merge.

Flow overview:
  1. External user sends first message → ``ensure_binding`` creates a shadow
     account and a ``channel_bindings`` row of type ``shadow``.
  2. User sends ``/bind`` → ``issue_bind_code`` writes a 6-digit code valid
     for 10 minutes. Rate-limited to 3 per minute per external_id.
  3. Web user submits the code via ``consume_bind_code``. On success the
     shadow account's sessions are re-assigned to the real account, the
     binding row flips to type ``bound`` and the shadow user is disabled.
"""

from __future__ import annotations

import random
import string
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db import Session as ChatSession
from aio_agent_platform.db.models import (
    ChannelBindCode,
    ChannelBinding,
    User,
)

logger = structlog.get_logger()

BIND_CODE_TTL_MINUTES = 10
BIND_CODE_RATE_LIMIT_PER_MINUTE = 3
SHADOW_USERNAME_PREFIX = "feishu_"
# Feishu open_ids are like "ou_xxxxx". Strip the prefix to keep usernames short.


def _shadow_username(external_id: str) -> str:
    safe = external_id.replace("ou_", "", 1) if external_id.startswith("ou_") else external_id
    return f"{SHADOW_USERNAME_PREFIX}{safe}"


def _generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))


async def ensure_shadow_user(
    db: AsyncSession,
    channel_id: UUID,
    external_id: str,
    tenant_id: UUID,
) -> tuple[UUID, str]:
    """Return (user_id, bind_type) for an external user, creating a shadow
    account if none exists. Idempotent.
    """
    result = await db.execute(
        select(ChannelBinding).where(
            ChannelBinding.channel_id == channel_id,
            ChannelBinding.external_id == external_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        # Validate that the linked user still exists and is usable.
        uresult = await db.execute(select(User).where(User.id == existing.user_id))
        if uresult.scalar_one_or_none():
            return existing.user_id, existing.bind_type
        # Linked user was deleted — drop the stale binding and fall through.
        await db.execute(
            select(ChannelBinding).where(ChannelBinding.id == existing.id).with_for_update()
        )  # ensure row lock isn't needed; delete by PK.
        from sqlalchemy import delete as sql_delete
        await db.execute(sql_delete(ChannelBinding).where(ChannelBinding.id == existing.id))
        await db.flush()

    # Create shadow user + binding.
    username = _shadow_username(external_id)
    # Collisions are unlikely (different open_ids) but append a nonce if taken.
    taken = await db.execute(select(User.id).where(User.username == username))
    if taken.scalar_one_or_none():
        nonce = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        username = f"{username}_{nonce}"

    shadow_user = User(
        username=username,
        email=f"{username}@channels.internal",
        password_hash="!shadow-no-login!",  # unusable — no password auth path accepts this
        tenant_id=tenant_id,
        role="user",
        is_active=True,
        is_shadow=True,
    )
    db.add(shadow_user)
    await db.flush()  # populate shadow_user.id

    binding = ChannelBinding(
        channel_id=channel_id,
        external_id=external_id,
        user_id=shadow_user.id,
        bind_type="shadow",
    )
    db.add(binding)
    await db.flush()

    logger.info(
        "shadow_user_created",
        channel_id=str(channel_id),
        external_id=external_id,
        shadow_user_id=str(shadow_user.id),
    )
    return shadow_user.id, "shadow"


async def issue_bind_code(
    db: AsyncSession,
    channel_id: UUID,
    external_id: str,
) -> tuple[str, datetime]:
    """Generate a 6-digit bind code for this external user.

    Raises RateLimitError if they've requested more than the allowed count in
    the past minute.
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(minutes=1)
    result = await db.execute(
        select(ChannelBindCode).where(
            ChannelBindCode.channel_id == channel_id,
            ChannelBindCode.external_id == external_id,
            ChannelBindCode.created_at >= window_start,
        )
    )
    recent = result.scalars().all()
    if len(recent) >= BIND_CODE_RATE_LIMIT_PER_MINUTE:
        raise BindCodeRateLimited("绑定码请求过于频繁，请稍后再试")

    # Invalidate any prior unused codes for this user (one active code at a time).
    from sqlalchemy import update as sql_update
    await db.execute(
        sql_update(ChannelBindCode)
        .where(
            ChannelBindCode.channel_id == channel_id,
            ChannelBindCode.external_id == external_id,
            ChannelBindCode.used_at.is_(None),
        )
        .values(used_at=now)  # mark as consumed to prevent reuse
    )

    code = _generate_code()
    expires_at = now + timedelta(minutes=BIND_CODE_TTL_MINUTES)
    record = ChannelBindCode(
        code=code,
        channel_id=channel_id,
        external_id=external_id,
        expires_at=expires_at,
    )
    db.add(record)
    await db.flush()
    return code, expires_at


async def consume_bind_code(
    db: AsyncSession,
    code: str,
    real_user_id: UUID,
) -> UUID:
    """Consume a bind code and merge the shadow account into ``real_user_id``.

    Returns the shadow user_id that was merged (for logging). Raises
    BindCodeInvalid on any failure (expired / already used / unknown code).
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(ChannelBindCode).where(
            ChannelBindCode.code == code,
            ChannelBindCode.used_at.is_(None),
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise BindCodeInvalid("绑定码不存在或已被使用")
    if record.expires_at < now:
        raise BindCodeInvalid("绑定码已过期")

    # Locate the binding row for this external user.
    bresult = await db.execute(
        select(ChannelBinding).where(
            ChannelBinding.channel_id == record.channel_id,
            ChannelBinding.external_id == record.external_id,
        )
    )
    binding = bresult.scalar_one_or_none()
    if binding is None:
        raise BindCodeInvalid("绑定记录不存在")

    shadow_user_id = binding.user_id
    if shadow_user_id == real_user_id:
        raise BindCodeInvalid("该渠道已绑定到当前账号")

    # 1. Reassign the shadow user's sessions to the real user.
    await db.execute(
        update(ChatSession)
        .where(ChatSession.user_id == shadow_user_id)
        .values(user_id=real_user_id)
    )

    # 2. Flip the binding to 'bound' pointing at the real user.
    binding.user_id = real_user_id
    binding.bind_type = "bound"

    # 3. Mark the code as consumed.
    record.used_by = real_user_id
    record.used_at = now

    # 4. Disable the shadow user (keep row for audit, prevent login).
    await db.execute(
        update(User).where(User.id == shadow_user_id).values(is_active=False)
    )

    await db.flush()

    logger.info(
        "bind_code_consumed",
        channel_id=str(record.channel_id),
        external_id=record.external_id,
        real_user_id=str(real_user_id),
        shadow_user_id=str(shadow_user_id),
    )
    return shadow_user_id


async def unbind_external(
    db: AsyncSession,
    channel_id: UUID,
    external_id: str,
) -> None:
    """Remove the binding for an external user. If the linked account is a
    shadow account, also disable it. Real accounts are unlinked but kept.
    """
    result = await db.execute(
        select(ChannelBinding).where(
            ChannelBinding.channel_id == channel_id,
            ChannelBinding.external_id == external_id,
        )
    )
    binding = result.scalar_one_or_none()
    if binding is None:
        return

    if binding.bind_type == "shadow":
        await db.execute(
            update(User).where(User.id == binding.user_id).values(is_active=False)
        )

    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(ChannelBinding).where(ChannelBinding.id == binding.id))
    await db.flush()


# --- Exceptions ---


class BindCodeError(Exception):
    """Base error for bind-code operations."""


class BindCodeRateLimited(BindCodeError):
    pass


class BindCodeInvalid(BindCodeError):
    pass
