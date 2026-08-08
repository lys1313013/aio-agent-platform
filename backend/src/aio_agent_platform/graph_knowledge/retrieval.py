"""Subgraph retrieval for graph knowledge bases.

Flow: entity linking (pg_trgm on normalized entity names) -> recursive-CTE
BFS expansion up to ``max_depth`` hops -> serialize as entities + relationships.

Only ``approved`` entities/relationships are served, so unverified LLM
extractions never reach agents.
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import GraphEntity, GraphRelationship
from aio_agent_platform.graph_knowledge.extraction import normalize_name
from aio_agent_platform.memory.service import MemoryService

logger = structlog.get_logger(__name__)

DEFAULT_TOP_K_ENTITIES = 5
DEFAULT_MAX_DEPTH = 2
MAX_EDGES = 200


async def link_entities(
    db: AsyncSession,
    kb_id: UUID,
    query: str,
    top_k: int = DEFAULT_TOP_K_ENTITIES,
) -> list[GraphEntity]:
    """Find approved entities matching the query (pg_trgm + substring)."""
    tokenized = MemoryService._tokenize(query)
    tokens = [t for t in tokenized.split() if t][:5]

    filters = [func.similarity(GraphEntity.name_norm, tokenized) > 0.05]
    for tok in tokens:
        filters.append(GraphEntity.name_norm.ilike(f"%{normalize_name(tok)}%"))

    stmt = (
        select(GraphEntity)
        .where(
            GraphEntity.knowledge_base_id == kb_id,
            GraphEntity.status == "approved",
            or_(*filters),
        )
        .order_by(func.similarity(GraphEntity.name_norm, tokenized).desc())
        .limit(top_k)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def expand_subgraph(
    db: AsyncSession,
    kb_id: UUID,
    seed_entity_ids: list[UUID],
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[dict[UUID, GraphEntity], list[dict]]:
    """Expand from seed entities via recursive CTE, return (entities, edges)."""
    if not seed_entity_ids:
        return {}, []

    rel = GraphRelationship.__table__
    depth_col = sa.literal(1).label("depth")

    base = (
        sa.select(
            rel.c.id,
            rel.c.source_entity_id,
            rel.c.target_entity_id,
            rel.c.relation_type,
            rel.c.description,
            rel.c.confidence,
            depth_col,
        )
        .where(
            rel.c.knowledge_base_id == kb_id,
            rel.c.status == "approved",
            rel.c.source_entity_id.in_(seed_entity_ids),
        )
    )

    cte = base.cte(name="subgraph", recursive=True)
    recursive_part = (
        sa.select(
            rel.c.id,
            rel.c.source_entity_id,
            rel.c.target_entity_id,
            rel.c.relation_type,
            rel.c.description,
            rel.c.confidence,
            (cte.c.depth + 1).label("depth"),
        )
        .join(cte, rel.c.source_entity_id == cte.c.target_entity_id)
        .where(
            rel.c.knowledge_base_id == kb_id,
            rel.c.status == "approved",
            cte.c.depth < max_depth,
        )
    )
    expanded = cte.union_all(recursive_part)

    result = await db.execute(sa.select(expanded).order_by(expanded.c.depth))
    rows = result.all()

    # Dedup edges (keep shallowest), cap total.
    seen: set[UUID] = set()
    edges: list[dict] = []
    for row in rows:
        if len(edges) >= MAX_EDGES:
            break
        edge_id = row.id
        if edge_id in seen:
            continue
        seen.add(edge_id)
        edges.append(
            {
                "id": row.id,
                "source_entity_id": row.source_entity_id,
                "target_entity_id": row.target_entity_id,
                "relation_type": row.relation_type,
                "description": row.description,
                "confidence": row.confidence,
                "depth": row.depth,
            }
        )

    # Load entity details for all touched nodes.
    node_ids = set(seed_entity_ids)
    for edge in edges:
        node_ids.add(edge["source_entity_id"])
        node_ids.add(edge["target_entity_id"])
    entities: dict[UUID, GraphEntity] = {}
    if node_ids:
        en_result = await db.execute(
            select(GraphEntity).where(
                GraphEntity.knowledge_base_id == kb_id,
                GraphEntity.id.in_(node_ids),
            )
        )
        entities = {e.id: e for e in en_result.scalars().all()}

    return entities, edges


def serialize_subgraph(
    entities: dict[UUID, GraphEntity],
    edges: list[dict],
    seed_ids: list[UUID] | None = None,
) -> dict:
    """Serialize a subgraph into a readable structure for agents / UI."""
    seed_set = set(seed_ids or ())
    entity_out = []
    for entity in entities.values():
        entity_out.append(
            {
                "id": str(entity.id),
                "name": entity.name,
                "type": entity.type,
                "description": entity.description,
                "is_seed": entity.id in seed_set,
            }
        )
    rel_out = []
    for edge in edges:
        source = entities.get(edge["source_entity_id"])
        target = entities.get(edge["target_entity_id"])
        rel_out.append(
            {
                "id": str(edge["id"]),
                "source": source.name if source else str(edge["source_entity_id"]),
                "relation_type": edge["relation_type"],
                "target": target.name if target else str(edge["target_entity_id"]),
                "confidence": edge["confidence"],
                "description": edge["description"],
                "depth": edge["depth"],
            }
        )
    return {"entities": entity_out, "relationships": rel_out}


async def retrieve_subgraph(
    db: AsyncSession,
    kb_id: UUID,
    query: str,
    top_k_entities: int = DEFAULT_TOP_K_ENTITIES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict:
    """End-to-end retrieval: link entities, expand subgraph, serialize."""
    seeds = await link_entities(db, kb_id, query, top_k=top_k_entities)
    seed_ids = [e.id for e in seeds]
    entities, edges = await expand_subgraph(db, kb_id, seed_ids, max_depth=max_depth)
    result = serialize_subgraph(entities, edges, seed_ids)
    result["seed_entities"] = [
        {"id": str(e.id), "name": e.name, "type": e.type} for e in seeds
    ]
    logger.info(
        "graph_subgraph_retrieved",
        kb_id=str(kb_id),
        query=query,
        seeds=len(seeds),
        entities=len(result["entities"]),
        relationships=len(result["relationships"]),
    )
    return result
