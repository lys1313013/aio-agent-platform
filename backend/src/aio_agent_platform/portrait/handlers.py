"""User portrait tool handler for ToolExecutor._execute_direct() dispatch."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select

from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import PortraitVersion, UserProfile


async def handle_update_user_portrait(
    arguments: dict, user_id: str, session_id: str, **kwargs
) -> str:
    """Handle update_user_portrait tool call — update the user's personal portrait."""
    portrait = arguments.get("portrait", "")

    uid = UUID(user_id)

    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        # Look up tenant_id from the user record
        from aio_agent_platform.db.models import User
        user_result = await db.execute(select(User.tenant_id).where(User.id == uid))
        tenant_id = user_result.scalar_one_or_none()
        if not tenant_id:
            return "用户不存在"

        result = await db.execute(
            select(UserProfile).where(
                UserProfile.user_id == uid,
                UserProfile.tenant_id == tenant_id,
            )
        )
        profile = result.scalar_one_or_none()

        if not profile:
            profile = UserProfile(user_id=uid, tenant_id=tenant_id)
            db.add(profile)

        # Save a version snapshot before overwriting
        if profile.personal_portrait and profile.personal_portrait.strip():
            db.add(PortraitVersion(
                user_id=uid,
                tenant_id=tenant_id,
                content=profile.personal_portrait,
                source="ai",
            ))

        profile.personal_portrait = portrait if portrait.strip() else None
        await db.commit()

    if portrait.strip():
        return f"已更新个人画像（{len(portrait)} 字符）"
    else:
        return "已清除个人画像"


PORTRAIT_HANDLERS: dict[str, Callable] = {
    "update_user_portrait": handle_update_user_portrait,
}
