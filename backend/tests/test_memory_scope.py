"""Scope regression tests using real PostgreSQL storage and retrieval."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.models import Memory, Session
from aio_agent_platform.memory.daily import DailyMemoryService, local_today, run_daily_consolidation
from aio_agent_platform.memory.handlers import handle_memory_read, handle_memory_write
from aio_agent_platform.memory.service import MemoryService


@pytest.fixture
def factory(monkeypatch, engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr('aio_agent_platform.db.connection._async_session_factory', factory)
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['*', '用户喜欢咖啡'])
async def test_retrieval_and_prompt_scope(db_session, query):
    user, other_user, a, b = (uuid4() for _ in range(4))
    for uid, agent in [(user, None), (user, a), (user, b), (other_user, a)]:
        for layer in ('L1', 'L2', 'L3'):
            await MemoryService.create_memory(db_session, uid, layer, '用户喜欢咖啡', agent_id=agent)
        await DailyMemoryService.upsert(db_session, uid, local_today(), '每日摘要', agent_id=agent)
    for agent, allowed in [(None, {None}), (a, {None, a}), (b, {None, b})]:
        results = await MemoryService.search_memories(db_session, user, query, agent_id=agent, top_k=30)
        assert len(results) == 3 * len(allowed)
        assert {m.agent_id for m, _ in results} == allowed
        prompt = await MemoryService.get_memories_for_prompt(db_session, user, query, agent_id=agent, top_k=20)
        for key in ('l1_memories', 'l2_memories', 'l3_memories', 'daily_memories'):
            assert {m.agent_id for m in prompt[key]} == allowed
            assert all(m.user_id == user for m in prompt[key])
    # Missing-date fallback must obey scope too.
    rows = await DailyMemoryService.get_for_prompt(db_session, user, '2020年1月1日', agent_id=a)
    assert {m.agent_id for m in rows} == {None, a}


@pytest.mark.asyncio
async def test_dedupe_only_within_same_scope(db_session):
    user, a, b = uuid4(), uuid4(), uuid4()
    ids = set()
    for agent in (None, a, b):
        first, action = await MemoryService.create_or_update_memory(db_session, user, 'L2', '用户喜欢咖啡', agent_id=agent)
        assert action == 'created'
        second, action = await MemoryService.create_or_update_memory(db_session, user, 'L2', '用户喜欢咖啡', agent_id=agent)
        assert action == 'updated'
        assert first.id == second.id
        ids.add(first.id)
    assert len(ids) == 3


@pytest.mark.asyncio
async def test_daily_update_and_delete_are_scoped(db_session):
    user, a, b = uuid4(), uuid4(), uuid4()
    for agent in (None, a, b):
        await DailyMemoryService.upsert(db_session, user, local_today(), '原始内容', agent_id=agent)
    await DailyMemoryService.upsert(db_session, user, local_today(), 'A更新', agent_id=a)
    assert (await DailyMemoryService.get_by_date(db_session, user, local_today(), agent_id=b)).content == '原始内容'
    await DailyMemoryService.delete_by_date(db_session, user, local_today(), agent_id=a)
    assert await DailyMemoryService.get_by_date(db_session, user, local_today(), agent_id=a) is None
    assert await DailyMemoryService.get_by_date(db_session, user, local_today()) is not None
    assert await DailyMemoryService.get_by_date(db_session, user, local_today(), agent_id=b) is not None


@pytest.mark.asyncio
async def test_tools_use_child_context_and_session_fallback(db_session, factory):
    user, a, child = uuid4(), uuid4(), uuid4()
    session = Session(user_id=user, agent_id=a)
    db_session.add(session)
    await db_session.commit()
    # Channels without a ContextVar still save to the session's agent.
    token = current_agent_id.set(None)
    try:
        await handle_memory_write({'layer': 'L1', 'content': 'parent-secret'}, str(user), str(session.id))
        child_token = current_agent_id.set(str(child))
        try:
            await handle_memory_write({'layer': 'L1', 'content': 'child-secret'}, str(user), str(session.id))
            await handle_memory_write({'layer': 'L1', 'content': 'shared-fact', 'scope': 'user'}, str(user), str(session.id))
            result = await handle_memory_read({'query': '*'}, str(user), str(session.id))
            assert 'child-secret' in result and 'shared-fact' in result
            assert 'parent-secret' not in result
        finally:
            current_agent_id.reset(child_token)
        result = await handle_memory_read({'query': '*'}, str(user), str(session.id))
        assert 'parent-secret' in result and 'shared-fact' in result
        assert 'child-secret' not in result
    finally:
        current_agent_id.reset(token)


@pytest.mark.asyncio
async def test_extraction_and_daily_cron_preserve_scope(db_session, factory, monkeypatch):
    user, a, b = uuid4(), uuid4(), uuid4()
    sessions = []
    for agent in (a, b):
        session = Session(user_id=user, agent_id=agent, title=str(agent))
        db_session.add(session)
        sessions.append(session)
    await db_session.commit()
    provider = SimpleNamespace(complete=AsyncMock(return_value=SimpleNamespace(content=json.dumps({
        'l2_memories': [{'content': '用户喜欢咖啡'}], 'l3_summary': 'A专属摘要',
    }))))
    monkeypatch.setattr('aio_agent_platform.memory.service.create_default_provider_for_user', AsyncMock(return_value=provider))
    created = await MemoryService.extract_memories_from_conversation(user, sessions[0].id, [])
    assert len(created) == 2 and all(m.agent_id == a for m in created)
    db_session.add(Memory(user_id=user, agent_id=b, layer='L3', content='B专属摘要'))
    await db_session.commit()
    prompts = []
    async def writer(uid, prompt):
        prompts.append(prompt)
        assert not ('A专属摘要' in prompt and 'B专属摘要' in prompt)
        return ('每日结果', [])
    monkeypatch.setattr(DailyMemoryService, '_run_writer', writer)
    result = await run_daily_consolidation(local_today())
    assert result['consolidated'] == 2
    assert len(prompts) == 2
    for session in sessions:
        daily = await DailyMemoryService.get_by_date(db_session, user, local_today(), agent_id=session.agent_id)
        assert daily.source_session_ids == [str(session.id)]
    assert await DailyMemoryService.get_by_date(db_session, user, local_today()) is None


@pytest.mark.asyncio
async def test_management_api_scope_and_promotion(client, db_session):
    from aio_agent_platform.auth.dependencies import get_current_user
    from aio_agent_platform.db.models import DEFAULT_TENANT_ID, Agent
    from aio_agent_platform.interface.api import app

    user = SimpleNamespace(id=uuid4(), tenant_id=DEFAULT_TENANT_ID)
    agent = Agent(name='scope-test', created_by=user.id, tenant_id=user.tenant_id)
    foreign = Agent(name='other-tenant', created_by=user.id, tenant_id=uuid4())
    db_session.add_all([agent, foreign])
    await db_session.flush()
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        base = '/api/memories'
        response = await client.post(base, json={'layer': 'L2', 'content': 'agent-fact', 'agent_id': str(agent.id)})
        assert response.status_code == 201, response.text
        memory_id = response.json()['id']
        assert response.json()['agent_id'] == str(agent.id)
        assert (await client.get(base)).json()['total'] == 0
        assert (await client.get(base, params={'agent_id': str(agent.id)})).json()['total'] == 1
        assert (await client.get(base + '/stats', params={'agent_id': str(agent.id)})).json()['L2'] == 1
        assert (await client.get(base + '/search', params={'q': '*', 'agent_id': str(agent.id)})).json()[0]['id'] == memory_id
        unchanged = await client.put(f'{base}/{memory_id}', json={'content': 'updated'})
        assert unchanged.json()['agent_id'] == str(agent.id)
        promoted = await client.put(f'{base}/{memory_id}', json={'agent_id': None})
        assert promoted.json()['agent_id'] is None
        assert (await client.get(base)).json()['total'] == 1
        assert (await client.get(base, params={'agent_id': str(agent.id)})).json()['total'] == 0
        rejected = await client.post(base, json={'layer': 'L2', 'content': 'invalid', 'agent_id': str(foreign.id)})
        assert rejected.status_code == 404
        for scope in (None, agent.id):
            await DailyMemoryService.upsert(db_session, user.id, local_today(), 'daily', agent_id=scope)
        scoped = await client.get(base + '/daily', params={'agent_id': str(agent.id)})
        assert len(scoped.json()) == 1 and scoped.json()[0]['agent_id'] == str(agent.id)
        deleted = await client.delete(f'{base}/daily/{local_today()}', params={'agent_id': str(agent.id)})
        assert deleted.status_code == 204
        assert len((await client.get(base + '/daily')).json()) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)
