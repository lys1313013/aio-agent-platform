"""Read-only model discovery using the validated execution tenant."""

import json
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from aio_agent_platform.core.execution_scope import (
    ExecutionAccessError,
    execution_tenant_id,
    require_execution_user,
)
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.llm.catalog import list_available_models


class ListModelsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def handle_list_models(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    ListModelsInput.model_validate(arguments)
    uid = UUID(user_id)
    tenant_id = execution_tenant_id(uid)
    if tenant_id is None:
        raise ExecutionAccessError("缺少已校验的执行租户，无法查询可用模型")
    factory = get_session_factory()
    async with factory() as db:
        await db.execute(select(func.set_config("app.current_user_id", user_id, True)))
        await require_execution_user(db, uid, tenant_id)
        models = await list_available_models(db, tenant_id)
    return json.dumps({"tenant_id": str(tenant_id), "models": models}, ensure_ascii=False, default=str)
