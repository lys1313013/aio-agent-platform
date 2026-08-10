"""Graph knowledge base management routes (知识图谱)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.graph_knowledge import mineru, service
from aio_agent_platform.graph_knowledge.ocr_jobs import start_ocr_job
from aio_agent_platform.graph_knowledge.parsing import (
    DocumentParseError,
    ScannedPDFError,
    extract_text,
)
from aio_agent_platform.graph_knowledge.service import serialize_kb

router = APIRouter(tags=["graph-knowledge"])

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


# ---- Schemas ----


class GraphKBCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    is_active: bool = True
    visibility: str = Field(default="tenant", pattern="^(tenant|private)$")


class GraphKBUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    is_active: bool | None = None
    visibility: str | None = Field(default=None, pattern="^(tenant|private)$")


class DocumentCreate(BaseModel):
    title: str | None = Field(default=None, max_length=256)
    content: str = Field(..., min_length=1)
    source_type: str = Field(default="text", pattern="^(text|upload)$")


class EntityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    type: str | None = Field(default=None, max_length=64)
    description: str | None = None


class RelationshipUpdate(BaseModel):
    relation_type: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ApproveRequest(BaseModel):
    ids: list[UUID] = Field(..., min_length=1)


class GraphRetrieveRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k_entities: int = Field(default=5, ge=1, le=20)
    max_depth: int = Field(default=2, ge=1, le=5)


# ---- Knowledge base CRUD ----


@router.get("/api/admin/graph-knowledge-bases")
async def list_graph_kbs(
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    return await service.list_kbs(db, admin)


@router.post("/api/admin/graph-knowledge-bases")
async def create_graph_kb(
    req: GraphKBCreate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.create_kb(
        db,
        admin,
        name=req.name,
        description=req.description,
        is_active=req.is_active,
        visibility=req.visibility,
    )
    await db.commit()
    return serialize_kb(kb, admin)


@router.put("/api/admin/graph-knowledge-bases/{kb_id}")
async def update_graph_kb(
    kb_id: UUID,
    req: GraphKBUpdate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    if kb.created_by != admin.id:
        raise HTTPException(status_code=403, detail="只有创建者可以修改")
    await service.update_kb(
        db,
        kb,
        name=req.name,
        description=req.description,
        is_active=req.is_active,
        visibility=req.visibility,
    )
    await db.commit()
    return serialize_kb(kb, admin)


@router.delete("/api/admin/graph-knowledge-bases/{kb_id}")
async def delete_graph_kb(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    if kb.created_by != admin.id:
        raise HTTPException(status_code=403, detail="只有创建者可以删除")
    await service.delete_kb(db, kb)
    await db.commit()
    return {"message": "图谱知识库已删除"}


# ---- Documents ----


@router.get("/api/admin/graph-knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    return await service.list_documents(db, kb_id, admin)


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/documents")
async def add_document(
    kb_id: UUID,
    req: DocumentCreate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    doc = await service.add_document(
        db,
        kb,
        admin,
        title=req.title or "未命名文档",
        content=req.content,
        source_type=req.source_type,
    )
    await db.commit()
    return {
        "id": doc.id,
        "title": doc.title,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
    }


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/documents/upload")
async def upload_document(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    title: str | None = Form(None),
) -> dict:
    """Upload a document file (.md/.txt/.html/.pdf/.docx) and parse it into text."""
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大(最大 {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )

    filename = file.filename or "document"
    doc_title = (title or "").strip() or filename.rsplit(".", 1)[0]
    try:
        content = extract_text(filename, data)
    except ScannedPDFError:
        if not mineru.is_configured():
            raise HTTPException(
                status_code=415,
                detail="扫描版 PDF 需要通过 MinerU 远程解析,请配置 MINERU_API_TOKEN",
            ) from None
        doc = await service.create_pending_document(db, kb, admin, title=doc_title[:256])
        await db.commit()
        start_ocr_job(doc.id, filename, data)
        return {
            "id": doc.id,
            "title": doc.title,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
        }
    except DocumentParseError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    if not content.strip():
        raise HTTPException(status_code=400, detail="未从文件中提取到文本内容")

    doc = await service.add_document(
        db,
        kb,
        admin,
        title=doc_title[:256],
        content=content,
        source_type="upload",
    )
    await db.commit()
    return {
        "id": doc.id,
        "title": doc.title,
        "status": doc.status,
        "chunk_count": doc.chunk_count,
    }


@router.get("/api/admin/graph-knowledge-bases/{kb_id}/documents/{doc_id}/chunks")
async def list_chunks(
    kb_id: UUID,
    doc_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    if not await service.get_kb(db, kb_id, admin):
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    return await service.list_chunks(db, doc_id, admin)


@router.delete("/api/admin/graph-knowledge-bases/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: UUID,
    doc_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    deleted = await service.delete_document(db, kb, doc_id)
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"message": "文档已删除"}


# ---- Extraction jobs ----


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/extract")
async def trigger_extraction(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    job, started = await service.create_extraction_job(db, kb, admin)
    if not started:
        raise HTTPException(status_code=409, detail="已有抽取任务运行中，请等待完成后再试")
    return {"job_id": job.id, "status": job.status}


@router.get("/api/admin/graph-knowledge-bases/{kb_id}/jobs")
async def list_jobs(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    if not await service.get_kb(db, kb_id, admin):
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    return await service.list_jobs(db, kb_id, admin)


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/jobs/{job_id}/retry")
async def retry_job(
    kb_id: UUID,
    job_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    from aio_agent_platform.db.models import GraphExtractionJob

    result = await db.execute(
        select(GraphExtractionJob).where(
            GraphExtractionJob.id == job_id,
            GraphExtractionJob.knowledge_base_id == kb.id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="抽取任务不存在")
    new_job, started = await service.create_extraction_job(db, kb, admin)
    if not started:
        raise HTTPException(status_code=409, detail="已有抽取任务运行中，请等待完成后再试")
    return {"job_id": new_job.id, "status": new_job.status}


# ---- Entities ----


@router.get("/api/admin/graph-knowledge-bases/{kb_id}/entities")
async def list_entities(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    if not await service.get_kb(db, kb_id, admin):
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    items, total = await service.list_entities(db, kb_id, status=status, limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.put("/api/admin/graph-knowledge-bases/{kb_id}/entities/{entity_id}")
async def update_entity(
    kb_id: UUID,
    entity_id: UUID,
    req: EntityUpdate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    try:
        entity = await service.update_entity(
            db,
            kb,
            entity_id,
            name=req.name,
            type=req.type,
            description=req.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not entity:
        raise HTTPException(status_code=404, detail="实体不存在")
    await db.commit()
    return service.serialize_entity(entity)


@router.delete("/api/admin/graph-knowledge-bases/{kb_id}/entities/{entity_id}")
async def delete_entity(
    kb_id: UUID,
    entity_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    deleted = await service.delete_entity(db, kb, entity_id)
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="实体不存在")
    return {"message": "实体已删除"}


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/entities/approve")
async def approve_entities(
    kb_id: UUID,
    req: ApproveRequest,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    count = await service.approve_entities(db, kb, req.ids)
    await db.commit()
    return {"approved": count}


# ---- Relationships ----


@router.get("/api/admin/graph-knowledge-bases/{kb_id}/relationships")
async def list_relationships(
    kb_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    if not await service.get_kb(db, kb_id, admin):
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    items, total = await service.list_relationships(
        db, kb_id, status=status, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.put("/api/admin/graph-knowledge-bases/{kb_id}/relationships/{rel_id}")
async def update_relationship(
    kb_id: UUID,
    rel_id: UUID,
    req: RelationshipUpdate,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    rel = await service.update_relationship(
        db,
        kb,
        rel_id,
        relation_type=req.relation_type,
        description=req.description,
        confidence=req.confidence,
    )
    if not rel:
        raise HTTPException(status_code=404, detail="关系不存在")
    await db.commit()
    return {
        "id": rel.id,
        "relation_type": rel.relation_type,
        "description": rel.description,
        "confidence": rel.confidence,
        "status": rel.status,
    }


@router.delete("/api/admin/graph-knowledge-bases/{kb_id}/relationships/{rel_id}")
async def delete_relationship(
    kb_id: UUID,
    rel_id: UUID,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    deleted = await service.delete_relationship(db, kb, rel_id)
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="关系不存在")
    return {"message": "关系已删除"}


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/relationships/approve")
async def approve_relationships(
    kb_id: UUID,
    req: ApproveRequest,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    kb = await service.get_kb(db, kb_id, admin)
    if not kb:
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    count = await service.approve_relationships(db, kb, req.ids)
    await db.commit()
    return {"approved": count}


@router.post("/api/admin/graph-knowledge-bases/{kb_id}/retrieve")
async def retrieve_graph_kb(
    kb_id: UUID,
    req: GraphRetrieveRequest,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Test subgraph retrieval: entity linking + multi-hop expansion."""
    import time

    start = time.time()
    if not await service.get_kb(db, kb_id, admin):
        raise HTTPException(status_code=404, detail="图谱知识库不存在")
    from aio_agent_platform.graph_knowledge.retrieval import retrieve_subgraph

    result = await retrieve_subgraph(
        db,
        kb_id,
        req.query,
        top_k_entities=req.top_k_entities,
        max_depth=req.max_depth,
    )
    result["query_time_ms"] = round((time.time() - start) * 1000, 2)
    return result
