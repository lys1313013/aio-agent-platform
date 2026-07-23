"""Knowledge base management routes + RAGFlow global settings."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import AgentKnowledgeBase, KnowledgeBase, SystemConfig
from aio_agent_platform.knowledge.ragflow_client import RagflowError, retrieve, test_connection

router = APIRouter(tags=["knowledge"])


# ---- Schemas ----


class KnowledgeBaseOut(BaseModel):
    id: UUID
    name: str
    dataset_id: str
    description: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    dataset_id: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    is_active: bool = True


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    is_active: bool | None = None


class RagflowSettingsOut(BaseModel):
    base_url: str = ""
    has_api_key: bool = False


class RagflowSettingsUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class TestResultOut(BaseModel):
    success: bool
    message: str
    records_count: int = 0


class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)


class RetrievalRecord(BaseModel):
    content: str
    score: float
    title: str | None = None
    metadata: dict | None = None


class RetrievalResult(BaseModel):
    success: bool
    records: list[RetrievalRecord] = []
    message: str | None = None
    query_time_ms: float = 0.0


# ---- Knowledge Base CRUD ----


@router.get("/api/admin/knowledge-bases", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases(
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    result = await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at))
    items = result.scalars().all()
    return [
        {
            "id": kb.id,
            "name": kb.name,
            "dataset_id": kb.dataset_id,
            "description": kb.description,
            "is_active": kb.is_active,
        }
        for kb in items
    ]


@router.post("/api/admin/knowledge-bases", response_model=KnowledgeBaseOut)
async def create_knowledge_base(
    req: KnowledgeBaseCreate,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = KnowledgeBase(
        name=req.name,
        dataset_id=req.dataset_id,
        description=req.description,
        is_active=req.is_active,
    )
    db.add(kb)
    await db.flush()
    return {
        "id": kb.id,
        "name": kb.name,
        "dataset_id": kb.dataset_id,
        "description": kb.description,
        "is_active": kb.is_active,
    }


@router.put("/api/admin/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: UUID,
    req: KnowledgeBaseUpdate,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if req.name is not None:
        kb.name = req.name
    if req.dataset_id is not None:
        kb.dataset_id = req.dataset_id
    if req.description is not None:
        kb.description = req.description
    if req.is_active is not None:
        kb.is_active = req.is_active

    await db.flush()
    return {
        "id": kb.id,
        "name": kb.name,
        "dataset_id": kb.dataset_id,
        "description": kb.description,
        "is_active": kb.is_active,
    }


@router.delete("/api/admin/knowledge-bases/{kb_id}")
async def delete_knowledge_base(
    kb_id: UUID,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # Clean up agent bindings
    await db.execute(
        AgentKnowledgeBase.__table__.delete().where(
            AgentKnowledgeBase.__table__.c.knowledge_base_id == kb_id
        )
    )

    await db.delete(kb)
    await db.flush()
    return {"message": "知识库已删除"}


@router.post("/api/admin/knowledge-bases/{kb_id}/test", response_model=TestResultOut)
async def test_knowledge_base(
    kb_id: UUID,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Test connection by querying RAGFlow with a dummy query."""
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # Load RAGFlow config
    config_result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_(["ragflow_base_url", "ragflow_api_key"])
        )
    )
    config = {r.key: r.value for r in config_result.scalars().all()}
    base_url = config.get("ragflow_base_url", "")
    api_key = config.get("ragflow_api_key", "")

    if not base_url:
        return {"success": False, "message": "RAGFlow base_url 未配置", "records_count": 0}
    if not api_key:
        return {"success": False, "message": "RAGFlow api_key 未配置", "records_count": 0}

    try:
        records_count = await test_connection(
            base_url=base_url,
            api_key=api_key,
            dataset_id=kb.dataset_id,
        )
        return {
            "success": True,
            "message": f"连接成功，返回 {records_count} 条结果",
            "records_count": records_count,
        }
    except RagflowError as e:
        return {"success": False, "message": str(e), "records_count": 0}
    except Exception as e:
        return {"success": False, "message": f"未知错误：{e}", "records_count": 0}


@router.post("/api/admin/knowledge-bases/{kb_id}/retrieval", response_model=RetrievalResult)
async def retrieval_knowledge_base(
    kb_id: UUID,
    req: RetrievalRequest,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Test retrieval by querying RAGFlow with a custom query."""
    import time

    start = time.time()

    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # Load RAGFlow config
    config_result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_(["ragflow_base_url", "ragflow_api_key"])
        )
    )
    config = {r.key: r.value for r in config_result.scalars().all()}
    base_url = config.get("ragflow_base_url", "")
    api_key = config.get("ragflow_api_key", "")

    if not base_url:
        return {"success": False, "message": "RAGFlow base_url 未配置", "records": []}
    if not api_key:
        return {"success": False, "message": "RAGFlow api_key 未配置", "records": []}

    try:
        records = await retrieve(
            base_url=base_url,
            api_key=api_key,
            dataset_ids=[kb.dataset_id],
            query=req.query,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
        )
        elapsed_ms = (time.time() - start) * 1000

        # Transform records to match RetrievalRecord schema
        transformed = []
        for r in records:
            transformed.append({
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
                "title": r.get("title"),
                "metadata": r.get("metadata"),
            })

        return {
            "success": True,
            "records": transformed,
            "query_time_ms": round(elapsed_ms, 2),
        }
    except RagflowError as e:
        return {"success": False, "message": str(e), "records": []}
    except Exception as e:
        return {"success": False, "message": f"检索失败：{e}", "records": []}


# ---- RAGFlow Global Settings ----


@router.get("/api/admin/settings/ragflow", response_model=RagflowSettingsOut)
async def get_ragflow_settings(
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_(["ragflow_base_url", "ragflow_api_key"])
        )
    )
    config = {r.key: r.value for r in result.scalars().all()}
    return {
        "base_url": config.get("ragflow_base_url", ""),
        "has_api_key": bool(config.get("ragflow_api_key", "")),
    }


@router.put("/api/admin/settings/ragflow", response_model=RagflowSettingsOut)
async def update_ragflow_settings(
    req: RagflowSettingsUpdate,
    _admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    updates: dict[str, str] = {}
    if req.base_url is not None:
        updates["ragflow_base_url"] = req.base_url
    if req.api_key is not None:
        updates["ragflow_api_key"] = req.api_key

    for key, value in updates.items():
        # Upsert
        result = await db.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        )
        config = result.scalar_one_or_none()
        if config:
            config.value = value
        else:
            db.add(SystemConfig(key=key, value=value))

    await db.flush()

    # Reload
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_(["ragflow_base_url", "ragflow_api_key"])
        )
    )
    config_map = {r.key: r.value for r in result.scalars().all()}
    return {
        "base_url": config_map.get("ragflow_base_url", ""),
        "has_api_key": bool(config_map.get("ragflow_api_key", "")),
    }
