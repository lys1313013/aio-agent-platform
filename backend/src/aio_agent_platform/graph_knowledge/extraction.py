"""LLM entity/relationship extraction pipeline for graph knowledge bases.

Documents are split into chunks; each chunk is sent to the LLM which returns a
JSON payload of entities + relationships. Results are deduplicated by normalized
entity name (within a knowledge base) and by (source, target, relation_type).

Extraction runs as a fire-and-forget background task: it creates its own DB
session, so it never blocks the request that triggered it.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import func, select

from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import (
    GraphChunk,
    GraphEntity,
    GraphExtractionJob,
    GraphRelationship,
)

logger = structlog.get_logger(__name__)

# Knowledge bases with an extraction running in this process (prevents duplicate
# concurrent runs that would fight over the same entities).
_running_kbs: set[UUID] = set()

_EXTRACTION_PROMPT = """你是知识图谱构建助手。从给定的文档片段中抽取实体与关系，严格输出 JSON，不要输出其他文字。

输出格式：
{
  "entities": [
    {"name": "实体名称", "type": "实体类型(人物/组织/产品/概念/事件/地点/其他)", "description": "一句话描述"}
  ],
  "relationships": [
    {"source": "来源实体名称", "target": "目标实体名称", "relation_type": "关系类型(如 负责/属于/开发/依赖/位于)", "description": "关系描述", "confidence": 0.9}
  ]
}

规则：
1. 只抽取片段中明确提到的信息，不要编造。
2. relationships 中的 source/target 必须是 entities 中列出的实体名称。
3. confidence 为 0-1 的浮点数，表示对该关系的把握程度。
4. 每个实体名称唯一；不同片段中的同一实体请保持名称一致。

文档片段：
"""


def normalize_name(name: str) -> str:
    """Normalize an entity name for dedup: collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", name or "").strip().lower()


async def _get_default_provider(tenant_id: UUID):
    """Load the tenant's default LLM provider. Returns None if unavailable."""
    try:
        from sqlalchemy.orm import selectinload

        from aio_agent_platform.db.models import LLMModel
        from aio_agent_platform.llm import LLMMessage, create_provider

        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(LLMModel)
                .options(selectinload(LLMModel.provider))
                .where(LLMModel.is_default, LLMModel.is_active, LLMModel.tenant_id == tenant_id)
                .limit(1)
            )
            model = result.scalar_one_or_none()
        if not model or not model.provider:
            return None, None
        provider = create_provider(
            provider=model.provider.provider_type,
            model=model.model_name,
            base_url=model.provider.base_url,
            api_key=model.provider.api_key_encrypted,
            temperature=0.3,
        )
        return provider, LLMMessage
    except Exception:
        logger.exception("graph_extraction_default_provider_failed")
        return None, None


def _parse_llm_json(raw: str) -> dict:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("LLM 输出不是合法 JSON")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是对象")
    return data


async def _extract_from_chunk(provider, llm_message_cls, chunk_text: str) -> dict:
    response = await provider.complete(
        messages=[llm_message_cls(role="user", content=_EXTRACTION_PROMPT + chunk_text)],
        max_tokens=3000,
    )
    return _parse_llm_json(response.content or "")


async def _load_existing_entities(db, kb_id: UUID) -> dict[str, UUID]:
    result = await db.execute(
        select(GraphEntity.name_norm, GraphEntity.id).where(
            GraphEntity.knowledge_base_id == kb_id
        )
    )
    existing: dict[str, UUID] = {}
    for norm, entity_id in result.all():
        existing[norm] = entity_id
    return existing


async def _load_existing_relationship_keys(db, kb_id: UUID) -> set[str]:
    result = await db.execute(
        select(
            GraphRelationship.source_entity_id,
            GraphRelationship.target_entity_id,
            GraphRelationship.relation_type,
        ).where(GraphRelationship.knowledge_base_id == kb_id)
    )
    return {f"{s}|{t}|{rt}" for s, t, rt in result.all()}


async def _upsert_extraction_result(
    db,
    kb_id: UUID,
    data: dict,
    chunk_id: UUID,
    created_by: UUID,
    existing_entities: dict[str, UUID],
    existing_rel_keys: set[str],
) -> tuple[int, int]:
    """Write entities & relationships from one chunk, dedup on the fly. Returns (new_entities, new_rels)."""
    entity_ids = dict(existing_entities)  # per-chunk view; merged back after
    new_entities = 0
    for item in data.get("entities") or []:
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 128:
            continue
        norm = normalize_name(name)
        if norm in existing_entities:
            entity_ids[norm] = existing_entities[norm]
            continue
        entity = GraphEntity(
            knowledge_base_id=kb_id,
            name=name,
            name_norm=norm,
            type=str(item.get("type") or "其他")[:64],
            description=str(item.get("description") or "").strip() or None,
            source_chunk_id=chunk_id,
            created_by=created_by,
        )
        db.add(entity)
        await db.flush()
        existing_entities[norm] = entity.id
        entity_ids[norm] = entity.id
        new_entities += 1

    new_rels = 0
    for rel in data.get("relationships") or []:
        source = str(rel.get("source") or "").strip()
        target = str(rel.get("target") or "").strip()
        rel_type = str(rel.get("relation_type") or "").strip()
        if not source or not target or not rel_type:
            continue
        source_id = entity_ids.get(normalize_name(source))
        target_id = entity_ids.get(normalize_name(target))
        if not source_id or not target_id or source_id == target_id:
            continue
        key = f"{source_id}|{target_id}|{rel_type}"
        if key in existing_rel_keys:
            continue
        existing_rel_keys.add(key)
        try:
            confidence = max(0.0, min(1.0, float(rel.get("confidence") or 0.8)))
        except (TypeError, ValueError):
            confidence = 0.8
        db.add(
            GraphRelationship(
                knowledge_base_id=kb_id,
                source_entity_id=source_id,
                target_entity_id=target_id,
                relation_type=rel_type,
                description=str(rel.get("description") or "").strip() or None,
                confidence=confidence,
                source_chunk_id=chunk_id,
                created_by=created_by,
            )
        )
        new_rels += 1

    return new_entities, new_rels


async def _execute_job(db, job: GraphExtractionJob) -> None:
    job.status = "running"
    job.error = None
    await db.commit()

    # Background task writes as the job creator (RLS context).
    current_user_id.set(str(job.created_by))
    await db.execute(select(func.set_config("app.current_user_id", str(job.created_by), True)))

    # Resolve tenant from the knowledge base
    from aio_agent_platform.db.models import GraphKnowledgeBase
    kb_result = await db.execute(
        select(GraphKnowledgeBase.tenant_id).where(GraphKnowledgeBase.id == job.knowledge_base_id)
    )
    tenant_id = kb_result.scalar_one_or_none()
    if tenant_id is None:
        job.status = "failed"
        job.error = "知识库不存在"
        job.finished_at = datetime.now(UTC)
        await db.commit()
        return

    provider, llm_message_cls = await _get_default_provider(tenant_id)
    if provider is None:
        job.status = "failed"
        job.error = "没有可用的默认模型，请先在模型管理中配置"
        job.finished_at = datetime.now(UTC)
        await db.commit()
        return

    chunks_result = await db.execute(
        select(GraphChunk)
        .where(GraphChunk.knowledge_base_id == job.knowledge_base_id)
        .order_by(GraphChunk.seq)
    )
    chunks = list(chunks_result.scalars().all())
    job.total_chunks = len(chunks)
    await db.commit()

    existing_entities = await _load_existing_entities(db, job.knowledge_base_id)
    existing_rel_keys = await _load_existing_relationship_keys(db, job.knowledge_base_id)

    total_entities = 0
    total_rels = 0
    failed_chunks = 0
    for idx, chunk in enumerate(chunks, start=1):
        try:
            data = await _extract_from_chunk(provider, llm_message_cls, chunk.content)
            new_e, new_r = await _upsert_extraction_result(
                db,
                job.knowledge_base_id,
                data,
                chunk.id,
                job.created_by,
                existing_entities,
                existing_rel_keys,
            )
            total_entities += new_e
            total_rels += new_r
        except Exception as exc:
            failed_chunks += 1
            logger.warning(
                "graph_extraction_chunk_failed",
                job_id=str(job.id),
                chunk_id=str(chunk.id),
                error=str(exc),
            )
        job.processed_chunks = idx
        job.entities_found = total_entities
        job.relationships_found = total_rels
        await db.commit()

    if chunks and failed_chunks == len(chunks):
        job.status = "failed"
        job.error = f"全部 {failed_chunks} 个分块抽取失败，请检查模型配置后重试"
    else:
        job.status = "done"
    job.finished_at = datetime.now(UTC)
    await db.commit()
    logger.info(
        "graph_extraction_job_done",
        job_id=str(job.id),
        entities=total_entities,
        relationships=total_rels,
        failed_chunks=failed_chunks,
    )


async def _run_extraction_job(job_id: UUID) -> None:
    try:
        factory = get_session_factory()
        async with factory() as db:
            job = await db.get(GraphExtractionJob, job_id)
            if not job:
                return
            if job.knowledge_base_id in _running_kbs:
                job.status = "failed"
                job.error = "该知识库已有抽取任务运行中"
                job.finished_at = datetime.now(UTC)
                await db.commit()
                return
            _running_kbs.add(job.knowledge_base_id)
            try:
                await _execute_job(db, job)
            finally:
                _running_kbs.discard(job.knowledge_base_id)
    except Exception:
        logger.exception("graph_extraction_job_failed", job_id=str(job_id))
        try:
            factory = get_session_factory()
            async with factory() as db:
                job = await db.get(GraphExtractionJob, job_id)
                if job:
                    job.status = "failed"
                    job.error = "抽取过程发生未知错误"
                    job.finished_at = datetime.now(UTC)
                    await db.commit()
        except Exception:
            logger.exception("graph_extraction_job_mark_failed_failed", job_id=str(job_id))


_background_tasks: set[asyncio.Task] = set()


def start_extraction_job(job_id: UUID) -> None:
    """Fire-and-forget: schedule the extraction job on the running event loop."""
    task = asyncio.create_task(_run_extraction_job(job_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
