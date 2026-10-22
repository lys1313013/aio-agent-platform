"""Public model listing — active LLM models visible to regular users."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.llm.catalog import list_available_models

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
    return [ModelOut.model_validate(model) for model in await list_available_models(db, user.tenant_id)]
