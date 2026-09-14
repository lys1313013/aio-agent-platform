"""Semantic decisions use a fake model; persistence/locks use real PostgreSQL."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aio_agent_platform.db.models import Memory
from aio_agent_platform.memory.reconciliation import reconcile_memory
from aio_agent_platform.memory.service import MemoryService


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
    assert await db_session.scalar(select(func.count()).select_from(Memory)) == 1


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
async def test_candidate_scope_and_forged_target(db_session, model):
    uid, other, agent, foreign_agent = (uuid4() for _ in range(4))
    allowed = await MemoryService.create_memory(db_session, uid, "L2", "目标事实", agent_id=agent)
    foreign = []
    for user, scope, layer in [
        (other, agent, "L2"),
        (uid, foreign_agent, "L2"),
        (uid, None, "L2"),
        (uid, agent, "L1"),
    ]:
        foreign.append(
            await MemoryService.create_memory(db_session, user, layer, "目标事实", agent_id=scope)
        )
    model.complete.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "action": "update",
                "memory_id": str(foreign[0].id),
                "content": "越权覆盖",
            }
        )
    )
    _, action = await reconcile_memory(db_session, uid, "L2", "补充事实", agent_id=agent)
    assert action == "created"
    payload = json.loads(model.complete.call_args.kwargs["messages"][1].content)
    assert [m["id"] for m in payload["existing"]] == [str(allowed.id)]
    assert all(m.content == "目标事实" for m in foreign)


@pytest.mark.asyncio
async def test_concurrent_writes_into_empty_scope(db_session, engine, model):
    uid = uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    started = asyncio.Event()

    async def first():
        async with factory() as db:
            result = await reconcile_memory(db, uid, "L2", "英语初学者")
            started.set()
            await asyncio.sleep(0.15)
            await db.commit()
            return result

    async def second():
        await started.wait()
        async with factory() as db:
            result = await reconcile_memory(db, uid, "L2", "英语初学者")
            await db.commit()
            return result

    (a, action_a), (b, action_b) = await asyncio.gather(first(), second())
    assert a.id == b.id and (action_a, action_b) == ("created", "skipped")
    assert await db_session.scalar(select(func.count()).select_from(Memory)) == 1
    model.complete.assert_not_called()


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


@pytest.mark.asyncio
async def test_tool_and_extraction_share_semantic_policy(db_session, engine, model, monkeypatch):
    from aio_agent_platform.db.models import Session
    from aio_agent_platform.memory.handlers import handle_memory_write

    uid, agent = uuid4(), uuid4()
    session = Session(user_id=uid, agent_id=agent, title="英语学习")
    db_session.add(session)
    old = await MemoryService.create_memory(
        db_session, uid, "L2", "英语初学者，使用扇贝", agent_id=agent
    )
    await db_session.commit()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("aio_agent_platform.db.connection._async_session_factory", factory)
    model.complete.return_value = SimpleNamespace(
        content=json.dumps(
            {
                "action": "skip",
                "memory_id": str(old.id),
            }
        )
    )
    result = await handle_memory_write(
        {"layer": "L2", "content": "用户英语刚入门"},
        str(uid),
        str(session.id),
    )
    assert "skipped" in result
    extractor = SimpleNamespace(
        complete=AsyncMock(
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "l2_memories": [{"content": "正在入门学习英语"}],
                        "l3_summary": "",
                    }
                )
            )
        )
    )
    monkeypatch.setattr(
        "aio_agent_platform.memory.service.create_default_provider_for_user",
        AsyncMock(return_value=extractor),
    )
    rows = await MemoryService.extract_memories_from_conversation(uid, session.id, [])
    assert len(rows) == 1 and rows[0].id == old.id
    assert "英语初学者，使用扇贝" in extractor.complete.call_args.kwargs["messages"][0].content
    assert await db_session.scalar(select(func.count()).select_from(Memory)) == 1
