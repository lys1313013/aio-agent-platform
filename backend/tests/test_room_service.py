"""Room business rules with mocked I/O; transaction guarantees live in test_rooms."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import ChatRoom, ChatRoomTask
from aio_agent_platform.rooms import service
from aio_agent_platform.rooms.schemas import RoomRetry, RoomSend


@pytest.fixture
def env(monkeypatch):
    db = MagicMock(spec=AsyncSession)
    db.scalar.return_value = None
    roster = [SimpleNamespace(id=uuid4(), agent_id=uuid4(), is_active=True,
                              name=name, description=name) for name in ('产品', '架构')]
    room = ChatRoom(id=uuid4(), goal='评审', default_member_id=roster[0].id,
                    revision=0, message_sequence=0, is_archived=False)
    monkeypatch.setattr(service, 'require_idle', AsyncMock())
    monkeypatch.setattr(service, 'members', AsyncMock(return_value=roster))
    monkeypatch.setattr(service, 'load_agent', AsyncMock(return_value=object()))
    return SimpleNamespace(db=db, room=room, roster=roster,
                           user=SimpleNamespace(id=uuid4(), tenant_id=uuid4()))


async def test_duplicate_request_reuses_run_without_writing(env):
    req = RoomSend(request_id=uuid4(), message='评审')
    run, created = await service.submit_run(env.db, env.room, env.user, req)
    assert created
    env.db.reset_mock()
    env.db.scalar.return_value = run
    result, created = await service.submit_run(env.db, env.room, env.user, req)
    assert result is run and not created
    env.db.add.assert_not_called()
    env.db.flush.assert_not_awaited()
    with pytest.raises(HTTPException) as error:
        await service.submit_run(env.db, env.room, env.user,
                                 req.model_copy(update={'message': '另一条消息'}))
    assert error.value.status_code == 409


@pytest.mark.parametrize('mode', ['mentions', 'all'])
async def test_targets_schedule_in_order_and_filter_unavailable_agents(env, mode):
    env.roster.append(SimpleNamespace(id=uuid4(), agent_id=uuid4(), is_active=True,
                                     name='测试', description='测试'))
    service.load_agent.side_effect = [object(), None, object()] if mode == 'all' else [object()]
    req = RoomSend(request_id=uuid4(), message='评审', mode=mode,
                   member_ids=[env.roster[2].id] if mode == 'mentions' else [])
    run, created = await service.submit_run(env.db, env.room, env.user, req)
    tasks = [c.args[0] for c in env.db.add.call_args_list if isinstance(c.args[0], ChatRoomTask)]
    expected = [env.roster[0].id, env.roster[2].id] if mode == 'all' else [env.roster[2].id]
    assert created and [t.member_id for t in tasks] == expected
    assert [t.position for t in tasks] == list(range(len(expected)))
    assert run.input['member_ids'] == list(map(str, expected))


async def test_unavailable_selected_member_rejected_without_writes(env):
    service.load_agent.return_value = None
    with pytest.raises(HTTPException) as error:
        await service.submit_run(env.db, env.room, env.user,
                                 RoomSend(request_id=uuid4(), message='评审'))
    assert error.value.status_code == 422
    env.db.add.assert_not_called()


async def test_archived_room_rejects_submission(env):
    env.room.is_archived = True
    with pytest.raises(HTTPException) as error:
        await service.submit_run(env.db, env.room, env.user,
                                 RoomSend(request_id=uuid4(), message='评审'))
    assert error.value.status_code == 409
    env.db.add.assert_not_called()


async def test_retry_requires_acknowledgement_and_preserves_context(env, monkeypatch):
    snapshot = {'history': ['original boundary']}
    task = SimpleNamespace(id=uuid4(), status='failed', message_id=uuid4(),
                           run_id=uuid4(), member_id=env.roster[0].id, context_snapshot=snapshot)
    env.db.scalar.return_value = task
    message = SimpleNamespace(payload={'tool_calls': [{'name': 'write_file'}]})
    original = SimpleNamespace(input={'message': '评审', 'mode': 'all'})
    env.db.get.side_effect = [message, message, original]
    submit = AsyncMock(return_value=('retry', True))
    monkeypatch.setattr(service, 'submit_run', submit)
    with pytest.raises(HTTPException) as error:
        await service.retry_task(env.db, env.room, env.user, task.id, RoomRetry(request_id=uuid4()))
    assert error.value.status_code == 409
    submit.assert_not_awaited()
    result = await service.retry_task(env.db, env.room, env.user, task.id,
                                     RoomRetry(request_id=uuid4(), acknowledge_side_effects=True))
    assert result == ('retry', True)
    req = submit.call_args.args[3]
    assert req.mode == 'mentions' and req.member_ids == [task.member_id]
    assert submit.call_args.kwargs['retry'].context_snapshot is snapshot


@pytest.mark.parametrize('stale', [False, True])
async def test_stale_recovery_preserves_partial_output_and_completed_tasks(env, monkeypatch, stale):
    run = SimpleNamespace(id=uuid4(), status='running',
                          heartbeat_at=service.now() - timedelta(seconds=200 if stale else 0))
    monkeypatch.setattr(service, 'active_run', AsyncMock(return_value=run))
    tasks = [SimpleNamespace(status=status, message_id=uuid4(), confirmation={'pending': True})
             for status in ['running', 'queued', 'completed']]
    env.db.scalars.return_value = SimpleNamespace(all=lambda: tasks)
    message = SimpleNamespace(status='running', content='部分结果')
    env.db.get.return_value = message
    await service.expire_stale_run(env.db, env.room)
    assert run.status == ('interrupted' if stale else 'running')
    assert [t.status for t in tasks] == (['failed', 'failed', 'completed'] if stale
                                       else ['running', 'queued', 'completed'])
    assert message.content == '部分结果'
    if stale:
        assert tasks[0].confirmation is None and message.status == 'failed'
    else:
        env.db.flush.assert_not_awaited()
