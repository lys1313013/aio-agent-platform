"""Business decisions run against explicit mocked query results, without PostgreSQL."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Memory
from aio_agent_platform.memory.reconciliation import reconcile_memory
from aio_agent_platform.memory.service import MemoryService


@pytest.fixture
def db_session(monkeypatch):
    db = MagicMock(spec=AsyncSession)
    db.scalar.side_effect = [True, None]  # Lock granted; no exact duplicate.

    async def create(_db, user_id, layer, content, meta=None, agent_id=None):
        memory = Memory(id=uuid4(), user_id=user_id, layer=layer, content=content,
                        agent_id=agent_id, meta=meta or {}, updated_at=datetime.now(UTC))
        db.scalars.return_value = SimpleNamespace(all=lambda: [memory])
        return memory

    monkeypatch.setattr(MemoryService, "create_memory", AsyncMock(side_effect=create))
    return db


@pytest.fixture
def model(monkeypatch):
    provider = SimpleNamespace(complete=AsyncMock())
    factory = AsyncMock(return_value=provider)
    monkeypatch.setattr(
        "aio_agent_platform.memory.reconciliation.create_default_provider_for_user", factory
    )
    return provider


@pytest.mark.asyncio
async def test_exact_duplicate_keeps_details_and_merges_sources(db_session, model):
    uid = uuid4()
    original = await MemoryService.create_memory(
        db_session,
        uid,
        "L2",
        "英语初学者，使用扇贝背单词",
        meta={"tags": ["english"], "source_session": "s1"},
    )
    db_session.scalar.side_effect = [True, original]
    memory, action = await reconcile_memory(
        db_session,
        uid,
        "L2",
        "  英语初学者，使用扇贝背单词  ",
        meta={"tags": ["learning"], "source_session": "s2"},
    )
    assert action == "skipped" and memory.id == original.id
    assert memory.meta["source_session"] == ["s1", "s2"]
    assert memory.meta["tags"] == ["english", "learning"]
    model.complete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision,expected", [("skip", "skipped"), ("merge", "merged"), ("update", "updated")]
)
async def test_semantic_decisions_preserve_id_and_revision(db_session, model, decision, expected):
    uid = uuid4()
    old = await MemoryService.create_memory(
        db_session, uid, "L2", "英语初学者，使用扇贝背单词", meta={"source_session": "s1"}
    )
    combined = "英语初学者，语法较弱，使用扇贝背单词"
    model.complete.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "action": decision,
                "memory_id": str(old.id),
                "content": combined,
            }
        )
    )
    memory, action = await reconcile_memory(
        db_session, uid, "L2", "用户的语法基础较弱", meta={"source_session": "s2"}
    )
    assert memory.id == old.id and action == expected
    if decision == "skip":
        assert memory.content == "英语初学者，使用扇贝背单词"
        assert "revisions" not in memory.meta
    else:
        assert memory.content == combined
        assert memory.meta["revisions"][0]["content"] == "英语初学者，使用扇贝背单词"
        assert memory.meta["revisions"][0]["source_session"] == "s1"
        assert memory.search_vec == MemoryService._tokenize(combined)
    MemoryService.create_memory.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {"action": "create"},
        {"action": "merge", "memory_id": str(uuid4()), "content": "wrong target"},
        {"action": "delete"},
        {"action": "merge", "memory_id": 123, "content": "bad id"},
        [],
        "not json",
    ],
)
async def test_new_or_invalid_decision_never_overwrites(db_session, model, reply):
    uid = uuid4()
    old = await MemoryService.create_memory(db_session, uid, "L2", "只负责英语教学")
    model.complete.return_value = SimpleNamespace(content=json.dumps(reply))
    memory, action = await reconcile_memory(db_session, uid, "L2", "每次先翻译用户的话")
    assert memory.id != old.id and action == "created"
    assert old.content == "只负责英语教学"


@pytest.mark.asyncio
async def test_provider_failure_preserves_old_fact(db_session, model):
    uid = uuid4()
    old = await MemoryService.create_memory(db_session, uid, "L2", "喜欢咖啡")
    model.complete.side_effect = TimeoutError()
    memory, action = await reconcile_memory(db_session, uid, "L2", "喜欢拿铁咖啡")
    assert action == "created" and memory.id != old.id and old.content == "喜欢咖啡"


@pytest.mark.asyncio
async def test_empty_merged_content_is_rejected(db_session, model):
    uid = uuid4()
    old = await MemoryService.create_memory(db_session, uid, "L2", "原始事实")
    model.complete.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "action": "merge",
                "memory_id": str(old.id),
                "content": "  ",
            }
        )
    )
    _, action = await reconcile_memory(db_session, uid, "L2", "补充信息")
    assert action == "created" and old.content == "原始事实"


@pytest.mark.asyncio
async def test_revisions_bounded(db_session, model):
    uid = uuid4()
    old = await MemoryService.create_memory(
        db_session,
        uid,
        "L2",
        "原始事实",
        meta={"revisions": [{"content": str(i)} for i in range(10)]},
    )
    model.complete.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "action": "update",
                "memory_id": str(old.id),
                "content": "改变后的事实",
            }
        )
    )
    memory, _ = await reconcile_memory(db_session, uid, "L2", "用户明确改变了事实")
    assert len(memory.meta["revisions"]) == 10
    assert memory.meta["revisions"][0]["content"] == "1"
    assert memory.meta["revisions"][-1]["content"] == "原始事实"



async def test_tool_and_extraction_call_reconciliation_without_database(monkeypatch):
    from aio_agent_platform.memory.handlers import handle_memory_write

    uid, agent, session_id, tenant = uuid4(), uuid4(), uuid4(), uuid4()
    memory = Memory(id=uuid4(), content='英语初学者')
    db = MagicMock(spec=AsyncSession)
    db.__aenter__.return_value = db
    db.scalar.return_value = agent
    db.scalars.return_value = SimpleNamespace(all=lambda: [memory])
    factory = MagicMock(return_value=db)
    monkeypatch.setattr('aio_agent_platform.memory.handlers.get_session_factory', lambda: factory)
    monkeypatch.setattr('aio_agent_platform.memory.handlers._active_agent', AsyncMock(return_value=agent))
    monkeypatch.setattr('aio_agent_platform.db.connection.get_session_factory', lambda: factory)
    reconcile = AsyncMock(return_value=(memory, 'skipped'))
    monkeypatch.setattr('aio_agent_platform.memory.handlers.reconcile_memory', reconcile)
    monkeypatch.setattr('aio_agent_platform.memory.reconciliation.reconcile_memory', reconcile)
    result = await handle_memory_write({'layer': 'L2', 'content': '刚入门'}, str(uid), str(session_id))
    assert 'skipped' in result
    assert reconcile.call_args.args[:4] == (db, uid, 'L2', '刚入门')
    assert reconcile.call_args.kwargs['agent_id'] == agent
    reconcile.reset_mock()
    # Background authorization loads the source Session, then the Agent tenant,
    # then the active user/membership. A bare UUID is not a Session object.
    db.scalar.side_effect = [SimpleNamespace(agent_id=agent), tenant, SimpleNamespace(id=uid)]
    provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=json.dumps({
        'l2_memories': [{'content': '正在学英语'}], 'l3_summary': '',
    }))))
    from aio_agent_platform.core.execution_scope import execution_tenant_id

    async def get_provider(user_id):
        assert user_id == uid
        assert execution_tenant_id(uid) == tenant
        return provider

    monkeypatch.setattr('aio_agent_platform.memory.service.create_default_provider_for_user',
                        get_provider)
    rows = await MemoryService.extract_memories_from_conversation(uid, session_id, [], agent_id=agent)
    assert rows == [memory]
    reconcile.assert_awaited_once()
    assert reconcile.call_args.kwargs['content'] == '正在学英语'
    assert reconcile.call_args.kwargs['agent_id'] == agent
    assert '英语初学者' in provider.complete.call_args.kwargs['messages'][0].content
