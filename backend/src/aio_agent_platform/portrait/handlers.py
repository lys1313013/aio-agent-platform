"""Portrait tool handlers; all reads and writes use a validated execution tenant."""

import json
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select

from aio_agent_platform.core.execution_scope import (
    ExecutionAccessError,
    execution_tenant_id,
    require_execution_user,
    resolve_execution_tenant,
)
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import UserProfile
from aio_agent_platform.portrait.service import PortraitUpdate, update_portrait


async def _handle(arguments: dict, user_id: str, *, read: bool) -> str:
    request = None if read else PortraitUpdate.model_validate(arguments)
    if read and arguments:
        raise ValueError("read_user_portrait does not accept arguments")
    uid = UUID(user_id)
    if execution_tenant_id(uid) is None:
        raise ExecutionAccessError("画像工具缺少执行租户上下文")
    token = current_user_id.set(user_id)
    try:
        async with get_session_factory()() as db:
            tenant_id = await resolve_execution_tenant(db, uid)
            await require_execution_user(db, uid, tenant_id)
            if read:
                portrait = await db.scalar(select(UserProfile.personal_portrait).where(
                    UserProfile.user_id == uid, UserProfile.tenant_id == tenant_id,
                ))
                result = {"action": "read", "portrait": portrait or ""}
            else:
                result = await update_portrait(db, uid, tenant_id, request)
        return json.dumps(result, ensure_ascii=False)
    finally:
        current_user_id.reset(token)


async def handle_read_user_portrait(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    return await _handle(arguments, user_id, read=True)


async def handle_update_user_portrait(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    return await _handle(arguments, user_id, read=False)


PORTRAIT_HANDLERS: dict[str, Callable] = {
    "read_user_portrait": handle_read_user_portrait,
    "update_user_portrait": handle_update_user_portrait,
}
