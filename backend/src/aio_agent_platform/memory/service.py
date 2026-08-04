"""MemoryService — CRUD, search, and auto-extraction for the 3-layer memory system."""

from __future__ import annotations

import json
import re
from uuid import UUID

import rjieba
import structlog
from sqlalchemy import delete, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Memory

logger = structlog.get_logger()


class MemoryService:
    """
    Stateless memory service — all methods take an explicit db session and user_id.

    Layers:
        L1 (常驻上下文): Loaded every conversation, full injection.
        L2 (长期记忆):   Retrieved by relevance (pg_trgm similarity), Top-K.
        L3 (情景记忆):   Daily summaries, retrieved by relevance + recency.
    """

    # ---- Tokenization ----

    @staticmethod
    def _tokenize(text: str) -> str:
        """Tokenize text with jieba, filter noise tokens, return space-separated string."""
        tokens = rjieba.cut(text)
        tokens = [
            t.strip()
            for t in tokens
            if t.strip() and not t.strip().isspace() and any(c.isalnum() for c in t.strip())
        ]
        return " ".join(tokens)

    # ---- CRUD ----

    @staticmethod
    async def list_memories(
        db: AsyncSession,
        user_id: UUID,
        layer: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Memory]:
        """List memories for a user, optionally filtered by layer."""
        stmt = (
            select(Memory)
            .where(Memory.user_id == user_id)
            .order_by(Memory.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if layer:
            stmt = stmt.where(Memory.layer == layer)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_memory(
        db: AsyncSession,
        memory_id: UUID,
        user_id: UUID,
    ) -> Memory | None:
        """Get a single memory by ID with ownership check."""
        result = await db.execute(
            select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_memory(
        db: AsyncSession,
        user_id: UUID,
        layer: str,
        content: str,
        meta: dict | None = None,
    ) -> Memory:
        """Create a new memory. Auto-generates search_vec via jieba tokenization."""
        search_vec = MemoryService._tokenize(content)
        memory = Memory(
            user_id=user_id,
            layer=layer,
            content=content,
            search_vec=search_vec,
            meta=meta or {},
        )
        db.add(memory)
        await db.flush()
        await db.refresh(memory)  # re-fetch server-generated timestamps to avoid MissingGreenlet
        return memory

    @staticmethod
    async def update_memory(
        db: AsyncSession,
        memory_id: UUID,
        user_id: UUID,
        content: str | None = None,
        layer: str | None = None,
        meta: dict | None = None,
    ) -> Memory | None:
        """Update an existing memory. Re-tokenizes search_vec if content changes."""
        memory = await MemoryService.get_memory(db, memory_id, user_id)
        if not memory:
            return None

        if content is not None and content != memory.content:
            memory.content = content
            memory.search_vec = MemoryService._tokenize(content)
        if layer is not None:
            memory.layer = layer
        if meta is not None:
            memory.meta = meta

        await db.flush()
        await db.refresh(memory)  # re-fetch server-generated updated_at to avoid MissingGreenlet
        return memory

    @staticmethod
    async def delete_memory(
        db: AsyncSession,
        memory_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Delete a memory. Returns True if deleted, False if not found."""
        memory = await MemoryService.get_memory(db, memory_id, user_id)
        if not memory:
            return False
        await db.delete(memory)
        await db.flush()
        return True

    @staticmethod
    async def delete_memories(
        db: AsyncSession,
        user_id: UUID,
        memory_ids: list[UUID],
    ) -> int:
        """Batch delete memories owned by a user. Returns number deleted."""
        if not memory_ids:
            return 0
        result = await db.execute(
            delete(Memory).where(Memory.user_id == user_id, Memory.id.in_(memory_ids))
        )
        await db.flush()
        return result.rowcount or 0

    # ---- Search ----

    @staticmethod
    async def search_memories(
        db: AsyncSession,
        user_id: UUID,
        query: str,
        layers: list[str] | None = None,
        top_k: int = 5,
        threshold: float = 0.1,
    ) -> list[tuple[Memory, float]]:
        """
        Search memories using pg_trgm similarity on jieba-tokenized search_vec.

        Returns list of (Memory, similarity_score) tuples sorted by relevance desc.

        Wildcard queries ('*', '%', or whitespace-only) return all memories
        sorted by most recently created.
        """
        stripped_query = query.strip()
        is_wildcard = not stripped_query or stripped_query in ("*", "%")

        if is_wildcard:
            # Browse-all mode: return all memories without similarity filter
            stmt = (
                select(Memory, literal(1.0).label("score"))
                .where(Memory.user_id == user_id)
                .order_by(Memory.created_at.desc())
                .limit(top_k)
            )
            if layers:
                stmt = stmt.where(Memory.layer.in_(layers))

            result = await db.execute(stmt)
            return [(row.Memory, 1.0) for row in result]

        tokenized_query = MemoryService._tokenize(query)
        if not tokenized_query:
            return []

        sim_score = func.similarity(Memory.search_vec, tokenized_query).label("score")

        stmt = (
            select(Memory, sim_score)
            .where(
                Memory.user_id == user_id,
                Memory.search_vec.isnot(None),
                func.similarity(Memory.search_vec, tokenized_query) > threshold,
            )
            .order_by(sim_score.desc())
            .limit(top_k)
        )
        if layers:
            stmt = stmt.where(Memory.layer.in_(layers))

        result = await db.execute(stmt)
        return [(row.Memory, float(row.score)) for row in result]

    # ---- Chat Integration ----

    @staticmethod
    async def get_memories_for_prompt(
        db: AsyncSession,
        user_id: UUID,
        user_message: str,
        top_k: int = 5,
    ) -> dict:
        """
        Get memories to inject into the system prompt.

        Returns dict:
            l1_memories: list[Memory] — all L1 memories (always loaded)
            l2_memories: list[Memory] — top-K by relevance
            l3_memories: list[Memory] — top-K by relevance + recency
        """
        # L1: Load ALL L1 memories (always present)
        l1_result = await db.execute(
            select(Memory)
            .where(Memory.user_id == user_id, Memory.layer == "L1")
            .order_by(Memory.created_at.desc())
        )
        l1_memories = list(l1_result.scalars().all())

        # L2: Search by relevance, top-K
        l2_results = await MemoryService.search_memories(
            db, user_id, user_message, layers=["L2"], top_k=top_k
        )
        l2_memories = [m for m, _score in l2_results]

        # L3: Relevance + recency (half relevant, half recent, deduplicated)
        half_k = max(top_k // 2, 1)
        l3_relevant = await MemoryService.search_memories(
            db, user_id, user_message, layers=["L3"], top_k=half_k
        )
        l3_recent_result = await db.execute(
            select(Memory)
            .where(Memory.user_id == user_id, Memory.layer == "L3")
            .order_by(Memory.created_at.desc())
            .limit(half_k)
        )
        l3_recent = list(l3_recent_result.scalars().all())

        # Deduplicate by ID
        seen_ids: set[UUID] = set()
        l3_memories: list[Memory] = []
        for m, _score in l3_relevant:
            if m.id not in seen_ids:
                l3_memories.append(m)
                seen_ids.add(m.id)
        for m in l3_recent:
            if m.id not in seen_ids:
                l3_memories.append(m)
                seen_ids.add(m.id)

        return {
            "l1_memories": l1_memories,
            "l2_memories": l2_memories,
            "l3_memories": l3_memories[:top_k],
        }

    # ---- Auto Extraction ----

    @staticmethod
    async def extract_memories_from_conversation(
        user_id: UUID,
        session_id: UUID,
        messages: list[dict],
    ) -> list[Memory]:
        """
        Background task: use LLM to extract noteworthy info from a conversation.

        Creates its own DB session and LLM provider internally.
        Errors are logged but never propagated.
        """
        try:
            # 1. Render extraction prompt
            from aio_agent_platform.core.prompt import _env

            template = _env.get_template("memory_writer.j2")
            prompt_text = template.render(messages=messages)

            # 2. Call LLM (non-streaming, low temperature for structured extraction)
            from sqlalchemy.orm import selectinload

            from aio_agent_platform.db.connection import get_session_factory as _get_session_factory
            from aio_agent_platform.db.models import LLMModel
            from aio_agent_platform.llm import LLMMessage, create_provider

            # Query default model from DB
            _factory = _get_session_factory()
            async with _factory() as _db:
                _result = await _db.execute(
                    select(LLMModel)
                    .options(selectinload(LLMModel.provider))
                    .where(LLMModel.is_default, LLMModel.is_active)
                    .limit(1)
                )
                _model = _result.scalar_one_or_none()

            if not _model or not _model.provider:
                logger.warning("没有可用的默认模型，跳过记忆提取")
                return []

            provider = create_provider(
                provider=_model.provider.provider_type,
                model=_model.model_name,
                base_url=_model.provider.base_url,
                api_key=_model.provider.api_key_encrypted,
                temperature=0.3,
            )
            response = await provider.complete(
                messages=[LLMMessage(role="user", content=prompt_text)],
                max_tokens=2000,
            )

            # 3. Parse structured output (JSON)
            raw_content = response.content or ""
            # Strip markdown code fences if LLM includes them
            raw_content = re.sub(r"^```(?:json)?\s*", "", raw_content.strip())
            raw_content = re.sub(r"\s*```$", "", raw_content.strip())

            extracted = json.loads(raw_content)

            # 4. Write to DB
            from aio_agent_platform.db.connection import current_user_id, get_session_factory

            factory = get_session_factory()
            async with factory() as db:
                # Set RLS context for background task (set_config supports parameterized queries)
                current_user_id.set(str(user_id))
                await db.execute(
                    select(func.set_config("app.current_user_id", str(user_id), True))
                )

                created: list[Memory] = []

                for item in extracted.get("l2_memories", []):
                    content = item.get("content", "").strip()
                    if not content:
                        continue
                    mem = await MemoryService.create_memory(
                        db,
                        user_id,
                        "L2",
                        content=content,
                        meta={
                            "tags": item.get("tags", []),
                            "source_session": str(session_id),
                        },
                    )
                    created.append(mem)

                summary = extracted.get("l3_summary", "").strip()
                if summary:
                    mem = await MemoryService.create_memory(
                        db,
                        user_id,
                        "L3",
                        content=summary,
                        meta={"source_session": str(session_id)},
                    )
                    created.append(mem)

                await db.commit()
                logger.info(
                    "memory_extraction_complete",
                    count=len(created),
                    session_id=str(session_id),
                )
                return created

        except Exception as e:
            logger.error(
                "memory_extraction_failed",
                error=str(e),
                session_id=str(session_id),
                exc_info=True,
            )
            return []
