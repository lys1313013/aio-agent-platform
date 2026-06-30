"""Knowledge retrieval tool handler for ToolExecutor._execute_direct() dispatch."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import structlog
from sqlalchemy import select

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import (
    AgentKnowledgeBase,
    KnowledgeBase,
    SystemConfig,
)
from aio_agent_platform.knowledge.ragflow_client import retrieve

logger = structlog.get_logger()


async def handle_knowledge_retrieval(
    arguments: dict, user_id: str, session_id: str, **kwargs
) -> str:
    """Handle knowledge_retrieval tool call — search bound knowledge bases via RAGFlow."""
    query = arguments.get("query", "")
    top_k = min(arguments.get("top_k", 5), 20)

    logger.info(
        "knowledge_retrieval_called",
        query=query,
        top_k=top_k,
        user_id=user_id,
        session_id=session_id,
        raw_arguments=arguments,
    )

    if not query:
        logger.warning("knowledge_retrieval_empty_query")
        return "Error: query parameter is required"

    agent_id = current_agent_id.get(None)
    if not agent_id:
        logger.warning("knowledge_retrieval_no_agent_context")
        return "Error: no agent context available for knowledge retrieval"

    logger.info("knowledge_retrieval_agent_resolved", agent_id=agent_id)

    factory = get_session_factory()
    async with factory() as db:
        # 1. 查询 agent 绑定的活跃知识库 → dataset_ids
        kb_result = await db.execute(
            select(KnowledgeBase.dataset_id, KnowledgeBase.name)
            .join(
                AgentKnowledgeBase,
                AgentKnowledgeBase.knowledge_base_id == KnowledgeBase.id,
            )
            .where(
                AgentKnowledgeBase.agent_id == UUID(agent_id),
                KnowledgeBase.is_active == True,
            )
        )
        kb_rows = kb_result.all()

        if not kb_rows:
            logger.warning(
                "knowledge_retrieval_no_knowledge_bases",
                agent_id=agent_id,
            )
            return "No knowledge bases configured for this agent."

        dataset_ids = [r.dataset_id for r in kb_rows]
        kb_name_map = {r.dataset_id: r.name for r in kb_rows}

        logger.info(
            "knowledge_retrieval_datasets_loaded",
            agent_id=agent_id,
            dataset_count=len(dataset_ids),
            datasets=[
                {"dataset_id": did, "name": kb_name_map[did]} for did in dataset_ids
            ],
        )

        # 2. 读取 system_config 中的 RAGFlow 配置
        config_result = await db.execute(
            select(SystemConfig).where(
                SystemConfig.key.in_(["ragflow_base_url", "ragflow_api_key"])
            )
        )
        config = {r.key: r.value for r in config_result.scalars().all()}
        base_url = config.get("ragflow_base_url", "")
        api_key = config.get("ragflow_api_key", "")

    if not base_url:
        logger.error("knowledge_retrieval_missing_base_url")
        return "Error: RAGFlow base_url is not configured. Please set it in admin settings."
    if not api_key:
        logger.error("knowledge_retrieval_missing_api_key")
        return "Error: RAGFlow api_key is not configured. Please set it in admin settings."

    logger.info(
        "knowledge_retrieval_ragflow_request",
        base_url=base_url,
        dataset_ids=dataset_ids,
        query=query,
        top_k=top_k,
    )

    # 3. 调用 RAGFlow 检索
    try:
        records = await retrieve(
            base_url=base_url,
            api_key=api_key,
            dataset_ids=dataset_ids,
            query=query,
            top_k=top_k,
        )
    except Exception as e:
        logger.error(
            "knowledge_retrieval_ragflow_error",
            error=str(e),
            error_type=type(e).__name__,
            dataset_ids=dataset_ids,
            query=query,
        )
        return f"Error retrieving from knowledge base: {e}"

    logger.info(
        "knowledge_retrieval_ragflow_response",
        record_count=len(records),
        records=[
            {
                "title": r.get("title", "Unknown"),
                "score": r.get("score", 0),
                "dataset_id": r.get("dataset_id", ""),
                "content_length": len(r.get("content", "")),
            }
            for r in records
        ],
    )

    if not records:
        logger.info(
            "knowledge_retrieval_no_results",
            query=query,
            dataset_ids=dataset_ids,
        )
        return "No relevant information found in the knowledge base."

    # 4. 格式化输出
    parts = [f"Found {len(records)} relevant chunks from knowledge base:\n"]
    for i, rec in enumerate(records, 1):
        title = rec.get("title", "Unknown")
        score = rec.get("score", 0)
        content = rec.get("content", "")
        did = rec.get("dataset_id", "")
        kb_name = kb_name_map.get(did, did)
        parts.append(
            f"{i}. [{kb_name}/{title}] (score: {score:.2f})\n"
            f"   {content[:1500]}"
        )

    logger.info(
        "knowledge_retrieval_completed",
        query=query,
        chunks_returned=len(records),
    )

    return "\n\n".join(parts)


# Registry mapping tool_name -> handler function
KNOWLEDGE_HANDLERS: dict[str, Callable] = {
    "knowledge_retrieval": handle_knowledge_retrieval,
}
