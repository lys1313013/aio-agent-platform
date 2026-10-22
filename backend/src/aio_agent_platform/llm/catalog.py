"""Tenant-scoped catalog shared by model discovery and scheduled execution."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import LLMModel, LLMProvider


def available_models_query(tenant_id: UUID):
    # Select only discovery metadata, never provider credentials or endpoints.
    return (
        select(
            LLMModel.id, LLMModel.name, LLMModel.model_name,
            LLMProvider.name.label("provider"), LLMModel.is_default, LLMModel.is_multimodal,
        )
        .select_from(LLMModel)
        .join(LLMProvider, LLMProvider.id == LLMModel.provider_id)
        .where(
            LLMModel.tenant_id == tenant_id, LLMModel.is_active,
            LLMProvider.tenant_id == tenant_id, LLMProvider.is_active,
        )
    )


async def list_available_models(db: AsyncSession, tenant_id: UUID) -> list[dict]:
    result = await db.execute(
        available_models_query(tenant_id).order_by(
            LLMModel.is_default.desc(), LLMModel.name, LLMModel.id,
        )
    )
    return [dict(row) for row in result.mappings()]
