"""Public model listing — active LLM models visible to regular users."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import LLMModel

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelOut(BaseModel):
    id: UUID
    name: str
    provider: str | None = None
    is_default: bool = False


@router.get("", response_model=list[ModelOut])
async def list_models(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ModelOut]:
    result = await db.execute(
        select(LLMModel)
        .options(selectinload(LLMModel.provider))
        .where(LLMModel.is_active, LLMModel.tenant_id == user.tenant_id)
        .order_by(LLMModel.is_default.desc(), LLMModel.name)
    )
    models = list(result.scalars().all())
    return [
        ModelOut(
            id=m.id,
            name=m.name,
            provider=m.provider.name if m.provider else None,
            is_default=m.is_default,
        )
        for m in models
    ]
