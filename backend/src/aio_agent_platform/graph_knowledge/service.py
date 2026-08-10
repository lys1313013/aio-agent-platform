"""GraphKnowledgeService — CRUD, documents, extraction jobs, entity/relationship review.

Stateless service: all methods take an explicit db session and acting user.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import (
    AgentGraphKnowledgeBase,
    GraphChunk,
    GraphDocument,
    GraphEntity,
    GraphExtractionJob,
    GraphKnowledgeBase,
    GraphRelationship,
)
from aio_agent_platform.graph_knowledge.chunking import chunk_text
from aio_agent_platform.graph_knowledge.extraction import start_extraction_job

logger = structlog.get_logger(__name__)

_RUNNING_STATUSES = ("pending", "running")


def kb_visible_to(user) -> object:
    return (
        (GraphKnowledgeBase.tenant_id == user.tenant_id)
        & or_(
            GraphKnowledgeBase.visibility == "tenant",
            GraphKnowledgeBase.created_by == user.id,
        )
    )


# ---- Knowledge base CRUD ----


async def get_kb(
    db: AsyncSession, kb_id: UUID, user
) -> GraphKnowledgeBase | None:
    result = await db.execute(
        select(GraphKnowledgeBase).where(
            GraphKnowledgeBase.id == kb_id, kb_visible_to(user)
        )
    )
    return result.scalar_one_or_none()


async def list_kbs(db: AsyncSession, user) -> list[dict]:
    result = await db.execute(
        select(GraphKnowledgeBase)
        .where(kb_visible_to(user))
        .order_by(GraphKnowledgeBase.created_at.desc())
    )
    kbs = list(result.scalars().all())
    ids = [kb.id for kb in kbs]
    if not ids:
        return []

    async def _counts(model, column) -> dict[UUID, int]:
        result = await db.execute(
            select(column, func.count()).where(column.in_(ids)).group_by(column)
        )
        counts: dict[UUID, int] = {}
        for kb_id, count in result.all():
            counts[kb_id] = count
        return counts

    entity_counts = await _counts(GraphEntity, GraphEntity.knowledge_base_id)
    rel_counts = await _counts(GraphRelationship, GraphRelationship.knowledge_base_id)
    doc_counts = await _counts(GraphDocument, GraphDocument.knowledge_base_id)

    return [
        {
            "id": kb.id,
            "name": kb.name,
            "description": kb.description,
            "is_active": kb.is_active,
            "visibility": kb.visibility,
            "tenant_id": kb.tenant_id,
            "created_by": kb.created_by,
            "can_edit": kb.created_by == user.id,
            "entity_count": entity_counts.get(kb.id, 0),
            "relationship_count": rel_counts.get(kb.id, 0),
            "document_count": doc_counts.get(kb.id, 0),
            "created_at": kb.created_at,
        }
        for kb in kbs
    ]


def serialize_kb(kb: GraphKnowledgeBase, user) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "is_active": kb.is_active,
        "visibility": kb.visibility,
        "tenant_id": kb.tenant_id,
        "created_by": kb.created_by,
        "can_edit": kb.created_by == user.id,
        "created_at": kb.created_at,
    }


async def create_kb(
    db: AsyncSession,
    user,
    *,
    name: str,
    description: str | None,
    is_active: bool = True,
    visibility: str = "tenant",
) -> GraphKnowledgeBase:
    kb = GraphKnowledgeBase(
        name=name,
        description=description,
        is_active=is_active,
        visibility=visibility,
        tenant_id=user.tenant_id,
        created_by=user.id,
    )
    db.add(kb)
    await db.flush()
    return kb


async def update_kb(
    db: AsyncSession,
    kb: GraphKnowledgeBase,
    *,
    name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
    visibility: str | None = None,
) -> None:
    if name is not None:
        kb.name = name
    if description is not None:
        kb.description = description
    if is_active is not None:
        kb.is_active = is_active
    if visibility is not None:
        kb.visibility = visibility
    await db.flush()


async def delete_kb(db: AsyncSession, kb: GraphKnowledgeBase) -> None:
    kb_id = kb.id
    await db.execute(
        delete(AgentGraphKnowledgeBase).where(
            AgentGraphKnowledgeBase.knowledge_base_id == kb_id
        )
    )
    await db.execute(
        delete(GraphExtractionJob).where(GraphExtractionJob.knowledge_base_id == kb_id)
    )
    await db.execute(
        delete(GraphRelationship).where(GraphRelationship.knowledge_base_id == kb_id)
    )
    await db.execute(
        delete(GraphEntity).where(GraphEntity.knowledge_base_id == kb_id)
    )
    chunk_ids = (
        await db.execute(
            select(GraphChunk.id).where(GraphChunk.knowledge_base_id == kb_id)
        )
    ).scalars().all()
    await db.execute(
        delete(GraphChunk).where(GraphChunk.knowledge_base_id == kb_id)
    )
    await db.execute(
        delete(GraphDocument).where(GraphDocument.knowledge_base_id == kb_id)
    )
    # Silence unused warning; chunk_ids reserved for future provenance cleanup.
    _ = chunk_ids
    await db.delete(kb)
    await db.flush()


# ---- Documents ----


async def list_documents(db: AsyncSession, kb_id: UUID, user) -> list[dict]:
    if not await get_kb(db, kb_id, user):
        return []
    result = await db.execute(
        select(GraphDocument)
        .where(GraphDocument.knowledge_base_id == kb_id)
        .order_by(GraphDocument.created_at.desc())
    )
    return [
        {
            "id": doc.id,
            "title": doc.title,
            "source_type": doc.source_type,
            "status": doc.status,
            "chunk_count": doc.chunk_count,
            "content": doc.content,
            "created_by": doc.created_by,
            "created_at": doc.created_at,
        }
        for doc in result.scalars().all()
    ]


async def add_document(
    db: AsyncSession,
    kb: GraphKnowledgeBase,
    user,
    *,
    title: str,
    content: str,
    source_type: str = "text",
) -> GraphDocument:
    chunks = chunk_text(content)
    doc = GraphDocument(
        knowledge_base_id=kb.id,
        title=title,
        content=content,
        source_type=source_type,
        status="chunked" if chunks else "failed",
        chunk_count=len(chunks),
        created_by=user.id,
    )
    db.add(doc)
    await db.flush()
    for seq, chunk_content in enumerate(chunks):
        db.add(
            GraphChunk(
                document_id=doc.id,
                knowledge_base_id=kb.id,
                seq=seq,
                content=chunk_content,
            )
        )
    await db.flush()
    return doc


async def create_pending_document(
    db: AsyncSession,
    kb: GraphKnowledgeBase,
    user,
    *,
    title: str,
) -> GraphDocument:
    """Create a placeholder document for async parsing (e.g. scanned PDF via MinerU).

    Content/chunks are filled in by the background OCR job (ocr_jobs.py).
    """
    doc = GraphDocument(
        knowledge_base_id=kb.id,
        title=title,
        content="",
        source_type="upload",
        status="parsing",
        chunk_count=0,
        created_by=user.id,
    )
    db.add(doc)
    await db.flush()
    return doc


async def delete_document(
    db: AsyncSession, kb: GraphKnowledgeBase, doc_id: UUID
) -> bool:
    result = await db.execute(
        select(GraphDocument).where(
            GraphDocument.id == doc_id,
            GraphDocument.knowledge_base_id == kb.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        return False
    chunk_ids = (
        await db.execute(
            select(GraphChunk.id).where(GraphChunk.document_id == doc_id)
        )
    ).scalars().all()
    await db.execute(
        delete(GraphChunk).where(GraphChunk.document_id == doc_id)
    )
    if chunk_ids:
        await db.execute(
            GraphEntity.__table__.update()
            .where(GraphEntity.source_chunk_id.in_(chunk_ids))
            .values(source_chunk_id=None)
        )
        await db.execute(
            GraphRelationship.__table__.update()
            .where(GraphRelationship.source_chunk_id.in_(chunk_ids))
            .values(source_chunk_id=None)
        )
    await db.delete(doc)
    await db.flush()
    return True


async def list_chunks(db: AsyncSession, doc_id: UUID, user) -> list[dict]:
    result = await db.execute(
        select(GraphChunk)
        .where(GraphChunk.document_id == doc_id)
        .order_by(GraphChunk.seq)
    )
    return [
        {
            "id": chunk.id,
            "seq": chunk.seq,
            "content": chunk.content,
        }
        for chunk in result.scalars().all()
    ]


# ---- Extraction jobs ----


async def list_jobs(db: AsyncSession, kb_id: UUID, user) -> list[dict]:
    if not await get_kb(db, kb_id, user):
        return []
    result = await db.execute(
        select(GraphExtractionJob)
        .where(GraphExtractionJob.knowledge_base_id == kb_id)
        .order_by(GraphExtractionJob.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": job.id,
            "status": job.status,
            "total_chunks": job.total_chunks,
            "processed_chunks": job.processed_chunks,
            "entities_found": job.entities_found,
            "relationships_found": job.relationships_found,
            "error": job.error,
            "created_by": job.created_by,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
        }
        for job in result.scalars().all()
    ]


async def create_extraction_job(
    db: AsyncSession,
    kb: GraphKnowledgeBase,
    user,
) -> tuple[GraphExtractionJob | None, bool]:
    """Create and launch an extraction job. Returns (job, started).

    Refuses to start when a job is already pending/running for this KB.
    """
    running = await db.execute(
        select(GraphExtractionJob).where(
            GraphExtractionJob.knowledge_base_id == kb.id,
            GraphExtractionJob.status.in_(_RUNNING_STATUSES),
        )
    )
    if running.scalar_one_or_none():
        return None, False

    job = GraphExtractionJob(
        knowledge_base_id=kb.id,
        status="pending",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()
    await db.commit()
    start_extraction_job(job.id)
    return job, True


# ---- Entities & relationships review ----


def serialize_entity(entity: GraphEntity) -> dict:
    return {
        "id": entity.id,
        "name": entity.name,
        "type": entity.type,
        "description": entity.description,
        "status": entity.status,
        "source_chunk_id": entity.source_chunk_id,
        "created_at": entity.created_at,
    }


def serialize_relationship(
    rel: GraphRelationship, source_name: str | None, target_name: str | None
) -> dict:
    return {
        "id": rel.id,
        "source_entity_id": rel.source_entity_id,
        "target_entity_id": rel.target_entity_id,
        "source_name": source_name,
        "target_name": target_name,
        "relation_type": rel.relation_type,
        "description": rel.description,
        "confidence": rel.confidence,
        "status": rel.status,
        "source_chunk_id": rel.source_chunk_id,
        "created_at": rel.created_at,
    }


async def list_entities(
    db: AsyncSession, kb_id: UUID, status: str | None = None, limit: int = 100, offset: int = 0
) -> tuple[list[dict], int]:
    stmt = select(GraphEntity).where(GraphEntity.knowledge_base_id == kb_id)
    count_stmt = select(func.count()).select_from(GraphEntity).where(
        GraphEntity.knowledge_base_id == kb_id
    )
    if status:
        stmt = stmt.where(GraphEntity.status == status)
        count_stmt = count_stmt.where(GraphEntity.status == status)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(GraphEntity.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return [serialize_entity(e) for e in result.scalars().all()], total


async def update_entity(
    db: AsyncSession,
    kb: GraphKnowledgeBase,
    entity_id: UUID,
    *,
    name: str | None = None,
    type: str | None = None,
    description: str | None = None,
) -> GraphEntity | None:
    result = await db.execute(
        select(GraphEntity).where(
            GraphEntity.id == entity_id, GraphEntity.knowledge_base_id == kb.id
        )
    )
    entity = result.scalar_one_or_none()
    if not entity:
        return None
    if name is not None and name.strip() and name != entity.name:
        from aio_agent_platform.graph_knowledge.extraction import normalize_name

        norm = normalize_name(name)
        existing = await db.execute(
            select(GraphEntity.id).where(
                GraphEntity.knowledge_base_id == kb.id,
                GraphEntity.name_norm == norm,
                GraphEntity.id != entity.id,
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"实体 '{name}' 已存在")
        entity.name = name.strip()
        entity.name_norm = norm
    if type is not None:
        entity.type = type[:64]
    if description is not None:
        entity.description = description or None
    await db.flush()
    return entity


async def delete_entity(db: AsyncSession, kb: GraphKnowledgeBase, entity_id: UUID) -> bool:
    result = await db.execute(
        select(GraphEntity).where(
            GraphEntity.id == entity_id, GraphEntity.knowledge_base_id == kb.id
        )
    )
    entity = result.scalar_one_or_none()
    if not entity:
        return False
    await db.execute(
        delete(GraphRelationship).where(
            or_(
                GraphRelationship.source_entity_id == entity_id,
                GraphRelationship.target_entity_id == entity_id,
            )
        )
    )
    await db.delete(entity)
    await db.flush()
    return True


async def approve_entities(
    db: AsyncSession, kb: GraphKnowledgeBase, ids: list[UUID]
) -> int:
    result = await db.execute(
        GraphEntity.__table__.update()
        .where(
            GraphEntity.id.in_(ids),
            GraphEntity.knowledge_base_id == kb.id,
        )
        .values(status="approved")
    )
    await db.flush()
    return result.rowcount or 0


async def list_relationships(
    db: AsyncSession, kb_id: UUID, status: str | None = None, limit: int = 100, offset: int = 0
) -> tuple[list[dict], int]:
    stmt = (
        select(GraphRelationship)
        .where(GraphRelationship.knowledge_base_id == kb_id)
    )
    count_stmt = select(func.count()).select_from(GraphRelationship).where(
        GraphRelationship.knowledge_base_id == kb_id
    )
    if status:
        stmt = stmt.where(GraphRelationship.status == status)
        count_stmt = count_stmt.where(GraphRelationship.status == status)
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(GraphRelationship.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    rels = list(result.scalars().all())

    entity_ids = set()
    for rel in rels:
        entity_ids.add(rel.source_entity_id)
        entity_ids.add(rel.target_entity_id)
    names: dict[UUID, str] = {}
    if entity_ids:
        en = await db.execute(
            select(GraphEntity.id, GraphEntity.name).where(GraphEntity.id.in_(entity_ids))
        )
        for eid, name in en.all():
            names[eid] = name

    return [
        serialize_relationship(rel, names.get(rel.source_entity_id), names.get(rel.target_entity_id))
        for rel in rels
    ], total


async def update_relationship(
    db: AsyncSession,
    kb: GraphKnowledgeBase,
    rel_id: UUID,
    *,
    relation_type: str | None = None,
    description: str | None = None,
    confidence: float | None = None,
) -> GraphRelationship | None:
    result = await db.execute(
        select(GraphRelationship).where(
            GraphRelationship.id == rel_id,
            GraphRelationship.knowledge_base_id == kb.id,
        )
    )
    rel = result.scalar_one_or_none()
    if not rel:
        return None
    if relation_type is not None:
        rel.relation_type = relation_type[:64]
    if description is not None:
        rel.description = description or None
    if confidence is not None:
        rel.confidence = max(0.0, min(1.0, confidence))
    await db.flush()
    return rel


async def delete_relationship(db: AsyncSession, kb: GraphKnowledgeBase, rel_id: UUID) -> bool:
    result = await db.execute(
        select(GraphRelationship).where(
            GraphRelationship.id == rel_id,
            GraphRelationship.knowledge_base_id == kb.id,
        )
    )
    rel = result.scalar_one_or_none()
    if not rel:
        return False
    await db.delete(rel)
    await db.flush()
    return True


async def approve_relationships(
    db: AsyncSession, kb: GraphKnowledgeBase, ids: list[UUID]
) -> int:
    result = await db.execute(
        GraphRelationship.__table__.update()
        .where(
            GraphRelationship.id.in_(ids),
            GraphRelationship.knowledge_base_id == kb.id,
        )
        .values(status="approved")
    )
    # 级联采纳关系两端的实体，否则图谱视图(默认只显示已采纳)看不到这些关系
    rel_filter = [
        GraphRelationship.id.in_(ids),
        GraphRelationship.knowledge_base_id == kb.id,
    ]
    entity_ids = select(GraphRelationship.source_entity_id).where(*rel_filter).union(
        select(GraphRelationship.target_entity_id).where(*rel_filter)
    )
    await db.execute(
        GraphEntity.__table__.update()
        .where(
            GraphEntity.id.in_(entity_ids),
            GraphEntity.knowledge_base_id == kb.id,
            GraphEntity.status == "pending",
        )
        .values(status="approved")
    )
    await db.flush()
    return result.rowcount or 0
