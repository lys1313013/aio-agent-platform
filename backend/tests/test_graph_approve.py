"""Tests for graph knowledge approve cascade behavior."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import (
    GraphEntity,
    GraphKnowledgeBase,
    GraphRelationship,
    Tenant,
)
from aio_agent_platform.graph_knowledge import service


async def _make_graph(db: AsyncSession) -> tuple[Tenant, GraphKnowledgeBase, GraphEntity, GraphEntity, GraphRelationship]:
    tenant = Tenant(id=uuid4(), name=f"t-{uuid4().hex[:8]}", slug=uuid4().hex[:8])
    db.add(tenant)
    await db.flush()
    kb = GraphKnowledgeBase(
        id=uuid4(), tenant_id=tenant.id, name="kb", created_by=uuid4()
    )
    db.add(kb)
    await db.flush()
    e1 = GraphEntity(
        id=uuid4(), knowledge_base_id=kb.id, name="A", name_norm="a",
        type="概念", created_by=uuid4(),
    )
    e2 = GraphEntity(
        id=uuid4(), knowledge_base_id=kb.id, name="B", name_norm="b",
        type="概念", created_by=uuid4(),
    )
    db.add_all([e1, e2])
    await db.flush()
    rel = GraphRelationship(
        id=uuid4(), knowledge_base_id=kb.id,
        source_entity_id=e1.id, target_entity_id=e2.id,
        relation_type="属于", created_by=uuid4(),
    )
    db.add(rel)
    await db.flush()
    return tenant, kb, e1, e2, rel


@pytest.mark.asyncio
async def test_approve_relationships_cascades_to_entities(db_session: AsyncSession):
    """采纳关系时应级联采纳两端 pending 实体，否则图谱视图看不到该关系。"""
    _, kb, e1, e2, rel = await _make_graph(db_session)

    count = await service.approve_relationships(db_session, kb, [rel.id])
    assert count == 1

    statuses = (
        await db_session.execute(
            select(GraphEntity.status).where(GraphEntity.id.in_([e1.id, e2.id]))
        )
    ).scalars().all()
    assert statuses == ["approved", "approved"]


@pytest.mark.asyncio
async def test_approve_relationships_keeps_other_entities_pending(db_session: AsyncSession):
    """不相关的实体不受影响。"""
    _, kb, _, _, rel = await _make_graph(db_session)
    other = GraphEntity(
        id=uuid4(), knowledge_base_id=kb.id, name="C", name_norm="c",
        type="概念", created_by=uuid4(),
    )
    db_session.add(other)
    await db_session.flush()

    await service.approve_relationships(db_session, kb, [rel.id])

    status = (
        await db_session.execute(
            select(GraphEntity.status).where(GraphEntity.id == other.id)
        )
    ).scalar_one()
    assert status == "pending"
