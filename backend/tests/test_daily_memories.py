"""Tests for daily memory service — date parsing, CRUD, prompt selection, consolidation."""

from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Memory, Session
from aio_agent_platform.memory import daily as daily_module
from aio_agent_platform.memory.daily import (
    DailyMemoryService,
    day_range_utc,
    extract_dates,
    local_today,
)

TODAY = date(2026, 8, 10)


class TestExtractDates:
    def test_explicit_full_date(self):
        assert extract_dates("2026年8月9日我们聊了什么", TODAY) == [date(2026, 8, 9)]

    def test_explicit_iso_date(self):
        assert extract_dates("2026-08-09 的进展", TODAY) == [date(2026, 8, 9)]

    def test_month_day_without_year(self):
        assert extract_dates("8月9日做了什么", TODAY) == [date(2026, 8, 9)]

    def test_future_month_day_falls_back_to_last_year(self):
        assert extract_dates("12月25日", TODAY) == [date(2025, 12, 25)]

    def test_relative_dates(self):
        assert extract_dates("昨天讨论的内容", TODAY) == [date(2026, 8, 9)]
        assert extract_dates("前天", TODAY) == [date(2026, 8, 8)]
        assert extract_dates("今天", TODAY) == [TODAY]

    def test_invalid_date_ignored(self):
        assert extract_dates("13月40日", TODAY) == []

    def test_no_date(self):
        assert extract_dates("随便聊聊", TODAY) == []

    def test_dedup(self):
        assert extract_dates("昨天和8月9日", TODAY) == [date(2026, 8, 9)]


class TestDayRangeUtc:
    def test_bounds(self):
        start, end = day_range_utc(date(2026, 8, 10))
        assert start.utcoffset() == timedelta(hours=8)
        assert (end - start) == timedelta(days=1)


@pytest.mark.asyncio
class TestDailyMemoryCrud:
    async def test_upsert_insert_then_update(self, db_session: AsyncSession):
        user_id = uuid4()
        day = date(2026, 8, 10)

        created = await DailyMemoryService.upsert(
            db_session, user_id, day, content="第一天内容", highlights=[{"type": "event", "text": "a"}]
        )
        assert created.content == "第一天内容"
        assert created.search_vec

        updated = await DailyMemoryService.upsert(
            db_session, user_id, day, content="覆盖后的内容"
        )
        assert updated.id == created.id
        assert updated.content == "覆盖后的内容"
        # highlights 未传时保留原值
        assert updated.highlights == [{"type": "event", "text": "a"}]

        fetched = await DailyMemoryService.get_by_date(db_session, user_id, day)
        assert fetched is not None and fetched.content == "覆盖后的内容"

    async def test_user_isolation(self, db_session: AsyncSession):
        day = date(2026, 8, 10)
        await DailyMemoryService.upsert(db_session, uuid4(), day, content="用户A")
        other = await DailyMemoryService.get_by_date(db_session, uuid4(), day)
        assert other is None

    async def test_list_range(self, db_session: AsyncSession):
        user_id = uuid4()
        for i in range(5):
            await DailyMemoryService.upsert(
                db_session, user_id, date(2026, 8, 6) + timedelta(days=i), content=f"第{i}天"
            )
        all_items = await DailyMemoryService.list_range(db_session, user_id)
        assert len(all_items) == 5
        assert all_items[0].date == date(2026, 8, 10)  # 倒序

        ranged = await DailyMemoryService.list_range(
            db_session, user_id, start=date(2026, 8, 8), end=date(2026, 8, 9)
        )
        assert [m.date for m in ranged] == [date(2026, 8, 9), date(2026, 8, 8)]

    async def test_delete_by_date(self, db_session: AsyncSession):
        user_id = uuid4()
        day = date(2026, 8, 10)
        await DailyMemoryService.upsert(db_session, user_id, day, content="x")
        assert await DailyMemoryService.delete_by_date(db_session, user_id, day) is True
        assert await DailyMemoryService.delete_by_date(db_session, user_id, day) is False


@pytest.mark.asyncio
class TestGetForPrompt:
    async def test_default_recent_days(self, db_session: AsyncSession):
        user_id = uuid4()
        today = local_today()
        await DailyMemoryService.upsert(db_session, user_id, today, content="今天")
        await DailyMemoryService.upsert(db_session, user_id, today - timedelta(days=1), content="昨天")
        await DailyMemoryService.upsert(db_session, user_id, today - timedelta(days=5), content="五天前")

        result = await DailyMemoryService.get_for_prompt(db_session, user_id, "随便聊聊")
        assert [m.content for m in result] == ["今天", "昨天"]

    async def test_mentioned_date_exact_lookup(self, db_session: AsyncSession):
        user_id = uuid4()
        today = local_today()
        target = today - timedelta(days=7)
        await DailyMemoryService.upsert(db_session, user_id, target, content="七天前")
        await DailyMemoryService.upsert(db_session, user_id, today, content="今天")

        message = f"{target.month}月{target.day}日我们讨论了什么"
        result = await DailyMemoryService.get_for_prompt(db_session, user_id, message)
        assert [m.content for m in result] == ["七天前"]

    async def test_mentioned_date_missing_falls_back_to_recent(self, db_session: AsyncSession):
        user_id = uuid4()
        today = local_today()
        await DailyMemoryService.upsert(db_session, user_id, today, content="今天")

        result = await DailyMemoryService.get_for_prompt(db_session, user_id, "前天干了啥")
        assert [m.content for m in result] == ["今天"]


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


def _fake_provider(payload):
    """Build an async stand-in for create_default_provider_for_user."""

    async def _resolve(uid, temperature=0.3):
        return _FakeProvider(payload)

    return _resolve


class _FakeProvider:
    def __init__(self, payload: dict):
        self._payload = payload

    async def complete(self, messages, max_tokens):
        return _FakeResponse(json.dumps(self._payload, ensure_ascii=False))


@pytest.fixture
def use_test_session_factory(monkeypatch, engine):
    """Point the global session factory at the per-session test engine.

    consolidate_day / append_session_summary open their own sessions via
    db.connection.get_session_factory(), whose cached engine binds to the first
    event loop that touched it — pytest-asyncio gives each test a fresh loop.
    """
    from sqlalchemy.orm import sessionmaker

    import aio_agent_platform.db.connection as connection

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(connection, "_async_session_factory", factory)
    return factory


@pytest.mark.asyncio
class TestConsolidateDay:
    async def _seed_day_activity(self, db_session: AsyncSession, user_id, day: date):
        start, _ = day_range_utc(day)
        session = Session(id=uuid4(), user_id=user_id, title="测试会话")
        session.created_at = start
        session.updated_at = start
        db_session.add(session)
        memory = Memory(
            id=uuid4(),
            user_id=user_id,
            layer="L3",
            content="用户完成了每日记忆功能的设计讨论",
            search_vec="用户 完成 每日 记忆 功能 设计 讨论",
            meta={},
        )
        memory.created_at = start
        memory.updated_at = start
        db_session.add(memory)
        await db_session.commit()
        return session

    async def test_consolidate_writes_daily_memory(
        self, db_session: AsyncSession, monkeypatch, use_test_session_factory
    ):
        user_id = uuid4()
        day = date(2026, 8, 9)
        session = await self._seed_day_activity(db_session, user_id, day)

        payload = {
            "content": "- 完成了每日记忆功能的设计讨论",
            "highlights": [{"type": "event", "text": "设计每日记忆功能"}],
        }
        monkeypatch.setattr(
            daily_module,
            "create_default_provider_for_user",
            _fake_provider(payload),
        )

        result = await DailyMemoryService.consolidate_day(user_id, day)
        assert result is not None
        assert result.date == day
        assert "每日记忆" in result.content
        assert result.highlights == [{"type": "event", "text": "设计每日记忆功能"}]
        assert str(session.id) in result.source_session_ids

    async def test_consolidate_skips_day_without_sessions(
        self, db_session: AsyncSession, monkeypatch, use_test_session_factory
    ):
        monkeypatch.setattr(
            daily_module,
            "create_default_provider_for_user",
            _fake_provider({"content": "x", "highlights": []}),
        )
        result = await DailyMemoryService.consolidate_day(uuid4(), date(2026, 8, 9))
        assert result is None

    async def test_consolidate_empty_content_returns_none(
        self, db_session: AsyncSession, monkeypatch, use_test_session_factory
    ):
        user_id = uuid4()
        day = date(2026, 8, 9)
        await self._seed_day_activity(db_session, user_id, day)

        monkeypatch.setattr(
            daily_module,
            "create_default_provider_for_user",
            _fake_provider({"content": "", "highlights": []}),
        )
        result = await DailyMemoryService.consolidate_day(user_id, day)
        assert result is None
        assert await DailyMemoryService.get_by_date(db_session, user_id, day) is None


@pytest.mark.asyncio
class TestAppendSessionSummary:
    async def test_append_creates_record_when_none(
        self, db_session: AsyncSession, monkeypatch, use_test_session_factory
    ):
        user_id = uuid4()
        session_id = uuid4()
        monkeypatch.setattr(
            daily_module,
            "create_default_provider_for_user",
            _fake_provider(
                {
                    "content": "- 讨论了每日记忆方案",
                    "highlights": [{"type": "event", "text": "讨论每日记忆方案"}],
                }
            ),
        )

        result = await DailyMemoryService.append_session_summary(
            user_id, session_id, "用户讨论了每日记忆方案设计"
        )
        assert result is not None
        assert result.date == local_today()
        assert "每日记忆" in result.content
        assert result.source_session_ids == [str(session_id)]

    async def test_append_merges_into_existing(
        self, db_session: AsyncSession, monkeypatch, use_test_session_factory
    ):
        user_id = uuid4()
        today = local_today()
        existing = await DailyMemoryService.upsert(
            db_session,
            user_id,
            today,
            content="- 已有的事项",
            source_session_ids=["old-session"],
        )
        await db_session.commit()

        monkeypatch.setattr(
            daily_module,
            "create_default_provider_for_user",
            _fake_provider(
                {"content": "- 已有的事项\n- 新增的事项", "highlights": []}
            ),
        )

        new_session = uuid4()
        result = await DailyMemoryService.append_session_summary(
            user_id, new_session, "用户又聊了新内容"
        )
        assert result is not None
        assert result.id == existing.id
        assert "新增的事项" in result.content
        assert result.source_session_ids == ["old-session", str(new_session)]

    async def test_append_empty_summary_noop(
        self, db_session: AsyncSession, monkeypatch, use_test_session_factory
    ):
        called = False

        async def _resolve(uid, temperature=0.3):
            nonlocal called
            called = True
            return _FakeProvider({"content": "x", "highlights": []})

        monkeypatch.setattr(
            daily_module, "create_default_provider_for_user", _resolve
        )
        result = await DailyMemoryService.append_session_summary(uuid4(), uuid4(), "   ")
        assert result is None
        assert called is False  # 空调用不应触发 LLM
