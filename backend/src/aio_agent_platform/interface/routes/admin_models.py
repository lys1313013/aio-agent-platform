"""Admin routes for managing LLM providers and models."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import LLMModel, LLMProvider

router = APIRouter(prefix="/api/admin/models", tags=["admin-models"])


# ---- Schemas ----


class ProviderOut(BaseModel):
    id: UUID
    name: str
    provider_type: str
    base_url: str | None = None
    has_api_key: bool = False
    is_active: bool

    model_config = {"from_attributes": True}


class ProviderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider_type: str = Field(..., pattern=r"^(openai|anthropic)$")
    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = None


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_type: str | None = Field(default=None, pattern=r"^(openai|anthropic)$")
    base_url: str | None = None
    api_key: str | None = None
    is_active: bool | None = None


class ModelOut(BaseModel):
    id: UUID
    provider_id: UUID
    provider_name: str = ""
    name: str
    model_name: str
    is_multimodal: bool
    is_default: bool
    is_active: bool

    model_config = {"from_attributes": True}


class ModelCreate(BaseModel):
    provider_id: UUID
    name: str = Field(..., min_length=1, max_length=128)
    model_name: str = Field(..., min_length=1, max_length=256)
    is_multimodal: bool = False


class ModelUpdate(BaseModel):
    provider_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    is_multimodal: bool | None = None
    is_active: bool | None = None


# ---- Providers ----


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.tenant_id == admin.tenant_id).order_by(LLMProvider.created_at)
    )
    providers = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "provider_type": p.provider_type,
            "base_url": p.base_url,
            "has_api_key": bool(p.api_key_encrypted),
            "is_active": p.is_active,
        }
        for p in providers
    ]


@router.post("/providers", response_model=ProviderOut)
async def create_provider(
    req: ProviderCreate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    provider = LLMProvider(
        tenant_id=admin.tenant_id,
        name=req.name,
        provider_type=req.provider_type,
        base_url=req.base_url,
        api_key_encrypted=req.api_key,  # TODO: encrypt before storing
    )
    db.add(provider)
    await db.flush()
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "has_api_key": bool(provider.api_key_encrypted),
        "is_active": provider.is_active,
    }


@router.put("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: UUID,
    req: ProviderUpdate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == provider_id, LLMProvider.tenant_id == admin.tenant_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")

    if req.name is not None:
        provider.name = req.name
    if req.provider_type is not None:
        provider.provider_type = req.provider_type
    if req.base_url is not None:
        provider.base_url = req.base_url
    if req.api_key is not None:
        provider.api_key_encrypted = req.api_key  # TODO: encrypt
    if req.is_active is not None:
        provider.is_active = req.is_active

    await db.flush()
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "has_api_key": bool(provider.api_key_encrypted),
        "is_active": provider.is_active,
    }


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == provider_id, LLMProvider.tenant_id == admin.tenant_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")

    await db.delete(provider)
    await db.flush()
    return {"message": "供应商已删除"}


# ---- Fetch remote models ----


class FetchModelsOut(BaseModel):
    models: list[str]


class BatchModelCreate(BaseModel):
    provider_id: UUID
    models: list[str] = Field(..., min_length=1)


@router.post("/providers/{provider_id}/fetch-models", response_model=FetchModelsOut)
async def fetch_remote_models(
    provider_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Fetch available models from the provider's /v1/models endpoint."""
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == provider_id, LLMProvider.tenant_id == admin.tenant_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")

    if not provider.base_url:
        raise HTTPException(status_code=400, detail="供应商未配置 API 地址")
    if not provider.api_key_encrypted:
        raise HTTPException(status_code=400, detail="供应商未配置 API 密钥")

    # Build URL: {base_url}/models
    base = provider.base_url.rstrip("/")
    url = f"{base}/models"

    headers = {"Authorization": f"Bearer {provider.api_key_encrypted}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"请求超时：{url}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"供应商 API 返回 {e.response.status_code}：{e.response.text[:200]}",
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"请求失败：{e!s}")

    # Parse response — OpenAI-compatible: {"data": [{"id": "gpt-4o", ...}]}
    model_ids: list[str] = []
    if isinstance(data, dict) and "data" in data:
        for item in data["data"]:
            if isinstance(item, dict) and "id" in item:
                model_ids.append(item["id"])
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "id" in item:
                model_ids.append(item["id"])
            elif isinstance(item, str):
                model_ids.append(item)

    return {"models": sorted(model_ids)}


@router.post("/batch-create", response_model=list[ModelOut])
async def batch_create_models(
    req: BatchModelCreate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Batch import models for a provider."""
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == req.provider_id, LLMProvider.tenant_id == admin.tenant_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")

    # Get existing model_names for this provider to avoid duplicates
    existing_result = await db.execute(
        select(LLMModel.model_name).where(LLMModel.provider_id == req.provider_id)
    )
    existing_names = set(existing_result.scalars().all())

    created: list[LLMModel] = []
    for model_name in req.models:
        if model_name in existing_names:
            continue
        model = LLMModel(
            tenant_id=admin.tenant_id,
            provider_id=req.provider_id,
            name=model_name,
            model_name=model_name,
        )
        db.add(model)
        created.append(model)

    if created:
        await db.flush()

        # If no default model exists, set the first one as default
        default_result = await db.execute(
            select(LLMModel).where(LLMModel.is_default, LLMModel.tenant_id == admin.tenant_id)
        )
        if not default_result.scalar_one_or_none() and created:
            created[0].is_default = True
            await db.flush()

    return [
        {
            "id": m.id,
            "provider_id": m.provider_id,
            "provider_name": provider.name,
            "name": m.name,
            "model_name": m.model_name,
            "is_multimodal": m.is_multimodal,
            "is_default": m.is_default,
            "is_active": m.is_active,
        }
        for m in created
    ]


# ---- Models ----


@router.get("", response_model=list[ModelOut])
async def list_models(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(
        select(LLMModel)
        .options(selectinload(LLMModel.provider))
        .where(LLMModel.tenant_id == admin.tenant_id)
        .order_by(LLMModel.created_at)
    )
    models = result.scalars().all()
    return [
        {
            "id": m.id,
            "provider_id": m.provider_id,
            "provider_name": m.provider.name if m.provider else "",
            "name": m.name,
            "model_name": m.model_name,
            "is_multimodal": m.is_multimodal,
            "is_default": m.is_default,
            "is_active": m.is_active,
        }
        for m in models
    ]


@router.post("", response_model=ModelOut)
async def create_model(
    req: ModelCreate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    # Verify provider exists
    result = await db.execute(
        select(LLMProvider).where(LLMProvider.id == req.provider_id, LLMProvider.tenant_id == admin.tenant_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="供应商不存在")

    model = LLMModel(
        tenant_id=admin.tenant_id,
        provider_id=req.provider_id,
        name=req.name,
        model_name=req.model_name,
        is_multimodal=req.is_multimodal,
    )
    db.add(model)
    await db.flush()

    # If this is the first model, make it default
    count_result = await db.execute(
        select(LLMModel).where(LLMModel.is_default, LLMModel.tenant_id == admin.tenant_id)
    )
    if not count_result.scalar_one_or_none():
        model.is_default = True
        await db.flush()

    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider_name": provider.name,
        "name": model.name,
        "model_name": model.model_name,
        "is_multimodal": model.is_multimodal,
        "is_default": model.is_default,
        "is_active": model.is_active,
    }


@router.put("/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: UUID,
    req: ModelUpdate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(LLMModel)
        .options(selectinload(LLMModel.provider))
        .where(LLMModel.id == model_id, LLMModel.tenant_id == admin.tenant_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")

    if req.provider_id is not None:
        prov_result = await db.execute(
            select(LLMProvider).where(LLMProvider.id == req.provider_id, LLMProvider.tenant_id == admin.tenant_id)
        )
        if not prov_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="供应商不存在")
        model.provider_id = req.provider_id
    if req.name is not None:
        model.name = req.name
    if req.model_name is not None:
        model.model_name = req.model_name
    if req.is_multimodal is not None:
        model.is_multimodal = req.is_multimodal
    if req.is_active is not None:
        model.is_active = req.is_active

    await db.flush()

    provider_name = ""
    if model.provider:
        provider_name = model.provider.name
    else:
        prov_result = await db.execute(
            select(LLMProvider).where(LLMProvider.id == model.provider_id, LLMProvider.tenant_id == admin.tenant_id)
        )
        prov = prov_result.scalar_one_or_none()
        if prov:
            provider_name = prov.name

    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider_name": provider_name,
        "name": model.name,
        "model_name": model.model_name,
        "is_multimodal": model.is_multimodal,
        "is_default": model.is_default,
        "is_active": model.is_active,
    }


@router.delete("/{model_id}")
async def delete_model(
    model_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(LLMModel).where(LLMModel.id == model_id, LLMModel.tenant_id == admin.tenant_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")

    was_default = model.is_default
    await db.delete(model)
    await db.flush()

    # If deleted model was default, try to set another active model as default
    if was_default:
        fallback = await db.execute(
            select(LLMModel)
            .where(LLMModel.is_active, LLMModel.tenant_id == admin.tenant_id)
            .limit(1)
        )
        fallback_model = fallback.scalar_one_or_none()
        if fallback_model:
            fallback_model.is_default = True
            await db.flush()

    return {"message": "模型已删除"}


@router.put("/{model_id}/default", response_model=ModelOut)
async def set_default_model(
    model_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(LLMModel)
        .options(selectinload(LLMModel.provider))
        .where(LLMModel.id == model_id, LLMModel.tenant_id == admin.tenant_id)
    )
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")

    # Clear all defaults for this tenant, set this one
    await db.execute(
        update(LLMModel).where(LLMModel.is_default, LLMModel.tenant_id == admin.tenant_id).values(is_default=False)
    )
    model.is_default = True
    await db.flush()

    provider_name = ""
    if model.provider:
        provider_name = model.provider.name
    else:
        prov_result = await db.execute(
            select(LLMProvider).where(LLMProvider.id == model.provider_id, LLMProvider.tenant_id == admin.tenant_id)
        )
        prov = prov_result.scalar_one_or_none()
        if prov:
            provider_name = prov.name

    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider_name": provider_name,
        "name": model.name,
        "model_name": model.model_name,
        "is_multimodal": model.is_multimodal,
        "is_default": model.is_default,
        "is_active": model.is_active,
    }
