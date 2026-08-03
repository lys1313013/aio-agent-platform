"""Admin system-config routes — global key-value settings."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.core.auto_title import (
    DEFAULT_PROMPT,
    load_auto_title_config,
    save_auto_title_config,
)
from aio_agent_platform.db.connection import get_db

router = APIRouter(prefix="/api/admin/system-config", tags=["system-config"])


class AutoTitleConfigOut(BaseModel):
    model_id: UUID | None = None
    prompt: str
    default_prompt: str


class AutoTitleConfigUpdate(BaseModel):
    model_id: UUID | None = None
    prompt: str = Field(default="", max_length=4000)


@router.get("/auto-title", response_model=AutoTitleConfigOut)
async def get_auto_title_config(
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    config = await load_auto_title_config(db)
    return {
        "model_id": config.model_id,
        "prompt": config.prompt,
        "default_prompt": DEFAULT_PROMPT,
    }


@router.put("/auto-title", response_model=AutoTitleConfigOut)
async def update_auto_title_config(
    req: AutoTitleConfigUpdate,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    prompt = req.prompt.strip() or DEFAULT_PROMPT
    await save_auto_title_config(db, model_id=req.model_id, prompt=prompt)
    return {
        "model_id": req.model_id,
        "prompt": prompt,
        "default_prompt": DEFAULT_PROMPT,
    }
