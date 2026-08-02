"""Channel binding — external-user resolution, bind-code lifecycle, account merge.

Flow overview:
  1. External user sends first message → ``resolve_external_user`` finds no
     live binding → the pipeline replies with a bind-code guide instead of
     creating a shadow account.
  2. User sends ``/bind`` → ``issue_bind_code`` writes a 6-digit code valid
     for 10 minutes. Rate-limited to 3 per minute per external_id.
  3. Web user submits the code via ``consume_bind_code``. On success a
     ``bound`` binding is created; legacy shadow accounts are merged into the
     real account (sessions re-assigned) and disabled.
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


def _generate_code() -> str:
    return "".join(random.choices(string.digits, k=6))


async def resolve_external_user(
    db: AsyncSession,
    channel_id: UUID,
    external_id: str,
) -> tuple[UUID | None, str]:
    """Return (user_id, bind_type) for an external user.

    Never creates a ``User`` row. Returns ``(None, "unbound")`` when the
    external user has no live binding — the pipeline then guides them through
    the bind-code flow instead of creating a shadow account. A stale legacy
    ``shadow`` binding also resolves as unbound so the user is nudged to bind
    their real account.
    """
    result = await db.execute(
        select(ChannelBinding).where(
            ChannelBinding.channel_id == channel_id,
            ChannelBinding.external_id == external_id,
        )
    )
    binding = result.scalar_one_or_none()
    if binding is None or binding.bind_type != "bound":
        return None, "unbound"
    user = await db.scalar(select(User).where(User.id == binding.user_id))
    if user is None or not user.is_active:
        return None, "unbound"
    return binding.user_id, "bound"


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
) -> UUID | None:
    """Consume a bind code and link the external user to ``real_user_id``.

    Creates a ``bound`` ``ChannelBinding`` on first link; merges a legacy
    shadow account (re-assigning its sessions and disabling it) if one exists.
    Returns the shadow user_id that was merged, or None. Raises
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
    binding = await db.scalar(
        select(ChannelBinding).where(
            ChannelBinding.channel_id == record.channel_id,
            ChannelBinding.external_id == record.external_id,
        )
    )

    merged_shadow_id: UUID | None = None
    if binding is not None and binding.bind_type == "bound":
        if binding.user_id == real_user_id:
            raise BindCodeInvalid("该渠道已绑定到当前账号")
        raise BindCodeInvalid("该渠道已绑定到其他账号，请先在 Web 端解绑")

    if binding is not None:
        # Legacy shadow binding: reassign its sessions to the real user,
        # disable the shadow account (keep row for audit), flip the binding.
        shadow_user_id = binding.user_id
        await db.execute(
            update(ChatSession)
            .where(ChatSession.user_id == shadow_user_id)
            .values(user_id=real_user_id)
        )
        binding.user_id = real_user_id
        binding.bind_type = "bound"
        await db.execute(
            update(User).where(User.id == shadow_user_id).values(is_active=False)
        )
        merged_shadow_id = shadow_user_id
    else:
        # First-time link — no shadow account was ever created.
        db.add(
            ChannelBinding(
                channel_id=record.channel_id,
                external_id=record.external_id,
                user_id=real_user_id,
                bind_type="bound",
            )
        )

    record.used_by = real_user_id
    record.used_at = now
    await db.flush()

    logger.info(
        "bind_code_consumed",
        channel_id=str(record.channel_id),
        external_id=record.external_id,
        real_user_id=str(real_user_id),
        merged_shadow_id=str(merged_shadow_id) if merged_shadow_id else None,
    )
    return merged_shadow_id


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
