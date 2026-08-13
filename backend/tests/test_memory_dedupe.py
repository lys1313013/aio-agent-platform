"""Tests for memory write dedupe — create_or_update_memory merges near-duplicates.

Verifies that repeated writes of the same/similar fact update the existing
memory instead of piling up copies, and that layer/user isolation and the
threshold are honored.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Memory
from aio_agent_platform.memory.service import MemoryService


async def _count(db: AsyncSession, user_id, layer: str) -> int:
    result = await db.execute(
        select(func.count()).select_from(Memory).where(
            Memory.user_id == user_id, Memory.layer == layer
        )
    )
    return result.scalar_one()


@pytest.mark.asyncio
class TestCreateOrUpdateMemory:
    async def test_identical_content_updates_in_place(self, db_session: AsyncSession):
        user_id = uuid4()
        mem1, action1 = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户喜欢喝咖啡"
        )
        assert action1 == "created"

        mem2, action2 = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户喜欢喝咖啡"
        )
        assert action2 == "updated"
        assert mem2.id == mem1.id
        assert await _count(db_session, user_id, "L2") == 1

    async def test_similar_content_merges(self, db_session: AsyncSession):
        user_id = uuid4()
        mem1, _ = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户最喜欢的饮品是拿铁咖啡"
        )
        mem2, action = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户喜欢的饮品是拿铁咖啡"
        )
        assert action == "updated"
        assert mem2.id == mem1.id
        assert mem2.content == "用户喜欢的饮品是拿铁咖啡"
        assert await _count(db_session, user_id, "L2") == 1

    async def test_different_content_creates_new(self, db_session: AsyncSession):
        user_id = uuid4()
        mem1, action1 = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户喜欢喝咖啡"
        )
        mem2, action2 = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户从事软件开发工作"
        )
        assert action1 == "created"
        assert action2 == "created"
        assert mem2.id != mem1.id
        assert await _count(db_session, user_id, "L2") == 2

    async def test_below_threshold_creates_new(self, db_session: AsyncSession):
        user_id = uuid4()
        await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户喜欢喝咖啡"
        )
        # 相似但不完全相同 → 实际相似度 < 1.0, 阈值 0.999 下应走新建
        _, action = await MemoryService.create_or_update_memory(
            db_session,
            user_id,
            "L2",
            "用户喜欢喝美式咖啡",
            dedupe_threshold=0.999,
        )
        assert action == "created"
        assert await _count(db_session, user_id, "L2") == 2

    async def test_different_layer_does_not_merge(self, db_session: AsyncSession):
        user_id = uuid4()
        l2, _ = await MemoryService.create_or_update_memory(
            db_session, user_id, "L2", "用户喜欢喝咖啡"
        )
        l1, action = await MemoryService.create_or_update_memory(
            db_session, user_id, "L1", "用户喜欢喝咖啡"
        )
        assert action == "created"
        assert l1.id != l2.id

    async def test_different_user_does_not_merge(self, db_session: AsyncSession):
        await MemoryService.create_or_update_memory(
            db_session, uuid4(), "L2", "用户喜欢喝咖啡"
        )
        mem2, action = await MemoryService.create_or_update_memory(
            db_session, uuid4(), "L2", "用户喜欢喝咖啡"
        )
        assert action == "created"
        assert await _count(db_session, mem2.user_id, "L2") == 1

    async def test_meta_tags_merge_and_dedup(self, db_session: AsyncSession):
        user_id = uuid4()
        mem1, _ = await MemoryService.create_or_update_memory(
            db_session,
            user_id,
            "L2",
            "用户喜欢喝咖啡",
            meta={"tags": ["偏好", "饮食"], "source_session": "s1"},
        )
        mem2, action = await MemoryService.create_or_update_memory(
            db_session,
            user_id,
            "L2",
            "用户喜欢喝咖啡",
            meta={"tags": ["偏好", "咖啡"], "source_session": "s2"},
        )
        assert action == "updated"
        assert mem2.id == mem1.id
        assert mem2.meta["tags"] == ["偏好", "饮食", "咖啡"]
        assert mem2.meta["source_session"] == ["s1", "s2"]

    async def test_create_memory_keeps_plain_append_semantics(
        self, db_session: AsyncSession
    ):
        """create_memory 保持纯新增语义, 不受去重逻辑影响 (显式创建路径)."""
        user_id = uuid4()
        await MemoryService.create_memory(db_session, user_id, "L2", "用户喜欢喝咖啡")
        await MemoryService.create_memory(db_session, user_id, "L2", "用户喜欢喝咖啡")
        assert await _count(db_session, user_id, "L2") == 2
