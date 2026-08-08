"""Graph retrieval tool handler — search bound graph knowledge bases via subgraph expansion."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import (
    AgentGraphKnowledgeBase,
    GraphKnowledgeBase,
    User,
)
from aio_agent_platform.graph_knowledge.retrieval import retrieve_subgraph

logger = structlog.get_logger(__name__)


async def handle_graph_retrieval(
    arguments: dict, user_id: str, session_id: str, **kwargs
) -> str:
    """Handle graph_retrieval tool call — subgraph retrieval over bound graph KBs."""
    query = arguments.get("query", "")
    max_depth = min(int(arguments.get("max_depth", 2)), 5)

    if not query:
        return "Error: query parameter is required"

    agent_id = current_agent_id.get(None)
    if not agent_id:
        return "Error: no agent context available for graph retrieval"

    factory = get_session_factory()
    async with factory() as db:
        tenant_id = await db.scalar(select(User.tenant_id).where(User.id == UUID(user_id)))
        if not tenant_id:
            return "Error: user tenant not found"

        kb_result = await db.execute(
            select(GraphKnowledgeBase.id, GraphKnowledgeBase.name)
            .join(
                AgentGraphKnowledgeBase,
                AgentGraphKnowledgeBase.knowledge_base_id == GraphKnowledgeBase.id,
            )
            .where(
                AgentGraphKnowledgeBase.agent_id == UUID(agent_id),
                GraphKnowledgeBase.is_active,
                GraphKnowledgeBase.tenant_id == tenant_id,
                or_(
                    GraphKnowledgeBase.visibility == "tenant",
                    GraphKnowledgeBase.created_by == UUID(user_id),
                ),
            )
        )
        kb_rows = kb_result.all()

        if not kb_rows:
            logger.warning("graph_retrieval_no_kbs", agent_id=agent_id)
            return "No graph knowledge bases configured for this agent."

        all_entities: dict[str, dict] = {}
        all_rels: list[dict] = []
        for kb_id, _ in kb_rows:
            try:
                result = await retrieve_subgraph(db, kb_id, query, max_depth=max_depth)
            except Exception as e:
                logger.warning(
                    "graph_retrieval_kb_failed",
                    kb_id=str(kb_id),
                    error=str(e),
                )
                continue
            for ent in result["entities"]:
                all_entities.setdefault(ent["id"], ent)
            for rel in result["relationships"]:
                all_rels.append(rel)

        if not all_entities and not all_rels:
            logger.info("graph_retrieval_no_results", query=query, agent_id=agent_id)
            return "No matching entities or relationships found in the knowledge graph."

        parts = [
            f"Found {len(all_entities)} entities and {len(all_rels)} relationships "
            f"from the knowledge graph:"
        ]
        parts.append("Entities:")
        for e in all_entities.values():
            desc = f" — {e['description']}" if e.get("description") else ""
            parts.append(f"  - {e['name']} ({e['type']}){desc}")
        if all_rels:
            parts.append("Relationships:")
            for r in all_rels:
                desc = f" ({r['description']})" if r.get("description") else ""
                parts.append(
                    f"  - {r['source']} --[{r['relation_type']}]--> {r['target']}{desc}"
                )

        logger.info(
            "graph_retrieval_completed",
            query=query,
            entities=len(all_entities),
            relationships=len(all_rels),
            kbs=[str(kb_id) for kb_id, _ in kb_rows],
        )
        return "\n".join(parts)


# Registry mapping tool_name -> handler function
GRAPH_HANDLERS: dict[str, Callable] = {
    "graph_retrieval": handle_graph_retrieval,
}
