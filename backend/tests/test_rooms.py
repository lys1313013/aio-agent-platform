"""Room contract tests. PostgreSQL tests use a disposable, explicitly named DB.

ROOM_TEST_DATABASE_URL defaults to the local temporary test container, never .env.
No existing application tables are truncated; each test owns an isolated schema.
"""

import asyncio
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aio_agent_platform.auth.dependencies import get_current_user
from aio_agent_platform.core.agent import AgentLoop
from aio_agent_platform.core.confirmation import ConfirmationManager, confirmation_manager
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import (
    Agent,
    Base,
    ChatRoomRun,
    Tenant,
    TenantMembership,
    User,
)
from aio_agent_platform.interface.routes import chat, rooms, sessions
from aio_agent_platform.llm import LLMChunk, LLMResponse, ToolCall
from aio_agent_platform.rooms import runtime, service
from aio_agent_platform.rooms.schemas import RoomCreate, RoomSend
from aio_agent_platform.tools.executor import ToolResult
from aio_agent_platform.tools.registry import Tool, ToolRegistry


def test_mention_routing_deduplicates_ids_and_never_parses_text():
    a, b = uuid4(), uuid4()
    roster = [SimpleNamespace(id=a, is_active=True), SimpleNamespace(id=b, is_active=True)]
    req = RoomSend(request_id=uuid4(), message="@B", mode="mentions", member_ids=[b, a, b])
    assert service.select_targets(req, roster, a) == [b, a]
    assert service.select_targets(RoomSend(request_id=uuid4(), message="@B"), roster, a) == [a]
    with pytest.raises(HTTPException):
        service.select_targets(req, roster[:1], a)


def test_invalid_command_combinations():
    with pytest.raises(ValidationError):
        RoomSend(request_id=uuid4(), message="hello", mode="all", member_ids=[uuid4()])
    with pytest.raises(ValidationError):
        RoomSend(request_id=uuid4(), message=" ")
    agent_id = uuid4()
    with pytest.raises(ValidationError):
        RoomCreate(goal="review", agent_ids=[agent_id, agent_id])


def test_attachments_reject_cross_user_and_path_traversal():
    owner, room_id = uuid4(), uuid4()
    req = RoomSend(request_id=uuid4(), attachments=[dict(
        key=f"chat-attachments/{uuid4()}/{room_id}/image.png", mime="image/png", size=1, filename="image.png",
    )])
    with pytest.raises(HTTPException) as error:
        service.validate_attachments(req, SimpleNamespace(id=room_id), SimpleNamespace(id=owner))
    assert error.value.status_code == 403
    req = RoomSend(request_id=uuid4(), file_attachments=[dict(
        file_id="a", filename="f", mime="text/plain", size=1, workspace_path="uploads/../secret",
    )])
    with pytest.raises(HTTPException):
        service.validate_attachments(req, SimpleNamespace(id=room_id), SimpleNamespace(id=owner))


async def test_confirmation_cannot_be_answered_twice():
    manager = ConfirmationManager()
    item = manager.create_confirmation("session", "user", "Continue?", "approve")
    assert manager.resolve_confirmation(item.id, {"status": "rejected"})
    assert not manager.resolve_confirmation(item.id, {"status": "approved"})
    assert (await manager.wait_for_response(item.id)).response["status"] == "rejected"


def test_unknown_usage_is_not_zero_and_partial_messages_keep_provenance():
    meter = runtime.MeteredProvider(SimpleNamespace(), uuid4())
    meter.record({"total_tokens": 12})
    meter.record(None)
    assert meter.usage is None
    msg = SimpleNamespace(content="可能的结论", status="failed", payload={}, name="架构助手", sequence=4)
    record = runtime.public_record(msg)
    assert "架构助手" in record["content"] and "不能视为最终结论" in record["content"]


@pytest.fixture
async def room_env(monkeypatch):
    url = os.environ.get("ROOM_TEST_DATABASE_URL", "postgresql+asyncpg://room_test:room_test@127.0.0.1:55439/room_test")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or parsed.database != "room_test":
        pytest.fail("Room tests require a loopback database named room_test")
    admin = create_async_engine(url, connect_args={"timeout": 3})
    schema = "room_test_" + uuid4().hex
    try:
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:
        await admin.dispose()
        pytest.skip(f"Disposable room test database unavailable: {type(exc).__name__}")
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(runtime, "get_session_factory", lambda: factory)
    monkeypatch.setattr(rooms, "get_session_factory", lambda: factory)
    monkeypatch.setattr(runtime, "record_llm_usage", lambda *a: None)
    import aio_agent_platform.core.agent as agent_module
    monkeypatch.setattr(agent_module, "record_llm_usage", lambda *a: None)
    monkeypatch.setattr(agent_module, "get_hook_manager", lambda: MagicMock())
    monkeypatch.setattr(agent_module, "get_recorder", lambda: MagicMock())
    monkeypatch.setattr(AgentLoop, "_finalize_trace", lambda *a, **kw: None)
    async with factory() as db:
        tenant = Tenant(id=uuid4(), name="room-test", slug="room-test")
        user = User(id=uuid4(), tenant_id=tenant.id, username="room-user", email="room@example.test", password_hash="test", is_active=True)
        agents = [Agent(id=uuid4(), tenant_id=tenant.id, created_by=user.id, name=name,
                        system_prompt=name, description=name + "专业职责", is_active=True,
                        enabled_tools=["AskUserQuestion", "write_file", "delegate_task"])
                  for name in ("产品", "架构", "测试")]
        db.add_all([tenant, user, *agents, TenantMembership(user_id=user.id, tenant_id=tenant.id)])
        await db.commit()
    registry = ToolRegistry()
    for name in ("AskUserQuestion", "write_file", "delegate_task"):
        registry.register(Tool(name=name, description=name, parameters={}, requires_sandbox=False))
    executor = SimpleNamespace(registry=registry, executed=[], mcp_manager=None, remote_manager=None)

    async def execute(**kwargs):
        executor.executed.append(kwargs["tool_name"])
        return ToolResult(kwargs["tool_call_id"], kwargs["tool_name"], kwargs["arguments"], "written", True)
    executor.execute = execute
    app = FastAPI()
    app.state.tool_executor = executor
    for router in (rooms.router, sessions.router, chat.router):
        app.include_router(router)
    actor = {"user": user}
    app.dependency_overrides[get_current_user] = lambda: actor["user"]

    async def test_db():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
    app.dependency_overrides[get_db] = test_db
    scripts = {}
    calls = []

    class Provider:
        model = "room-fixture"
        supports_vision = False

        def __init__(self, agent_id):
            self.agent_id = agent_id
            self.iteration = 0

        async def complete(self, messages, **kwargs):
            return LLMResponse(content="[保留来源的历史摘要]", usage={"total_tokens": 3})

        async def stream(self, messages, tools=None):
            calls.append({"agent_id": self.agent_id, "messages": messages, "tools": tools})
            self.iteration += 1
            script = scripts.get(self.agent_id)
            if script:
                async for chunk in script(self.iteration):
                    yield chunk
            else:
                yield LLMChunk(type="text_delta", content="成员回答 @其他成员")
                yield LLMChunk(type="done", usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})

    async def build(executor, prompt, db, **kwargs):
        aid = UUID(runtime.current_agent_id.get())
        return AgentLoop(Provider(aid), executor, system_prompt=prompt, max_iterations=3,
                         event_queue=kwargs["event_queue"], allowed_tools=kwargs["allowed_tools"],
                         workspace_id=kwargs["workspace_id"], workspace_slug=kwargs["workspace_slug"])
    monkeypatch.setattr(runtime, "build_agent_loop", build)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        env = SimpleNamespace(client=client, app=app, factory=factory, user=user,
                              agents=agents, actor=actor, scripts=scripts, calls=calls, executor=executor)
        yield env
    await runtime.shutdown()
    confirmation_manager._pending.clear()
    await engine.dispose()
    async with admin.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


async def new_room(env):
    response = await env.client.post("/api/rooms", json={"goal": "需求评审", "agent_ids": [str(a.id) for a in env.agents]})
    assert response.status_code == 201, response.text
    return response.json()


async def wait_state(env, room_id, predicate, timeout=8):
    async with asyncio.timeout(timeout):
        while True:
            response = await env.client.get(f"/api/rooms/{room_id}")
            assert response.status_code == 200, response.text
            state = response.json()
            if predicate(state):
                return state
            await asyncio.sleep(0.03)


async def test_single_mention_history_usage_and_direct_route_guard(room_env):
    env = room_env
    room = await new_room(env)
    target = room["members"][1]["id"]
    response = await env.client.post(f"/api/rooms/{room['id']}/runs", json={
        "request_id": str(uuid4()), "message": "请评审", "mode": "mentions", "member_ids": [target],
    })
    assert response.status_code == 202, response.text
    state = await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] not in service.ACTIVE_RUNS)
    assert state["runs"][0]["status"] == "completed", state
    assert len(env.calls) == 1 and env.calls[0]["agent_id"] == env.agents[1].id
    assert state["tasks"][0]["token_usage"]["total_tokens"] == 5
    assistant = next(m for m in state["messages"] if m["role"] == "assistant")
    assert assistant["member_id"] == target and assistant["name"] == "架构"
    assert not (await env.client.get('/api/sessions')).json()
    assert (await env.client.delete(f"/api/sessions/{room['id']}")).status_code == 404
    assert (await env.client.post('/api/chat/stream', json={"session_id": room["id"], "message": "bypass"})).status_code == 404


async def test_sequential_context_and_summary_only_one_member(room_env):
    env = room_env
    room = await new_room(env)
    response = await env.client.post(f"/api/rooms/{room['id']}/runs", json={
        "request_id": str(uuid4()), "message": "一起评审", "mode": "all",
    })
    assert response.status_code == 202
    state = await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] == "completed")
    assert [call["agent_id"] for call in env.calls] == [a.id for a in env.agents]
    second_context = "\n".join(str(m.content) for m in env.calls[1]["messages"])
    assert "[产品，消息 #" in second_context and "成员回答" in second_context
    assert all(t["function"]["name"] != "delegate_task" for call in env.calls for t in call["tools"])
    quote = next(m for m in state["messages"] if m["role"] == "assistant")
    response = await env.client.post(f"/api/rooms/{room['id']}/runs", json={
        "request_id": str(uuid4()), "mode": "summary", "member_ids": [room["members"][2]["id"]], "reply_to_id": quote["id"],
    })
    assert response.status_code == 202
    await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] == "completed")
    assert len(env.calls) == 4 and env.calls[-1]["tools"] == []


async def test_concurrent_submissions_are_serialized_and_idempotent(room_env, monkeypatch):
    env = room_env
    monkeypatch.setattr(rooms, "start_run", lambda *a: None)
    room = await new_room(env)
    body = {"request_id": str(uuid4()), "message": "same request"}
    responses = await asyncio.gather(*[env.client.post(f"/api/rooms/{room['id']}/runs", json=body) for _ in range(2)])
    assert [r.status_code for r in responses] == [202, 202]
    assert responses[0].json()["run_id"] == responses[1].json()["run_id"]
    assert (await env.client.post(f"/api/rooms/{room['id']}/runs", json={**body, "message": "different"})).status_code == 409
    assert (await env.client.post(f"/api/rooms/{room['id']}/runs", json={**body, "request_id": str(uuid4())})).status_code == 409
    assert (await env.client.patch(f"/api/rooms/{room['id']}", json={"goal": "change"})).status_code == 409
    state = (await env.client.get(f"/api/rooms/{room['id']}")).json()
    assert len(state["runs"]) == 1 and len([m for m in state["messages"] if m["role"] == "user"]) == 1


async def test_stop_preserves_partial_output_and_never_starts_next_member(room_env):
    env = room_env
    async def slow(_iteration):
        yield LLMChunk(type="text_delta", content="部分结果")
        await asyncio.Event().wait()
    env.scripts[env.agents[0].id] = slow
    room = await new_room(env)
    run = (await env.client.post(f"/api/rooms/{room['id']}/runs", json={"request_id": str(uuid4()), "message": "go", "mode": "all"})).json()
    await wait_state(env, room["id"], lambda s: any(t["status"] == "running" for t in s["tasks"]))
    assert (await env.client.post(f"/api/rooms/{room['id']}/runs/{run['run_id']}/stop")).status_code == 200
    state = await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] == "stopped")
    assert len(env.calls) == 1
    assert [t["status"] for t in state["tasks"]] == ["stopped", "cancelled", "cancelled"]
    assert any(m["content"] == "部分结果" for m in state["messages"])


async def test_confirmations_block_queue_and_duplicate_or_late_answers_fail(room_env):
    env = room_env
    async def ask(iteration):
        if iteration == 1:
            yield LLMChunk(type="tool_call_start", tool_call=ToolCall("ask1", "AskUserQuestion", {}),
                           argument_delta=json.dumps({"question": "继续？", "mode": "free_input"}))
        else:
            yield LLMChunk(type="text_delta", content="已处理答复")
        yield LLMChunk(type="done", usage={"total_tokens": 5})
    env.scripts[env.agents[0].id] = ask
    room = await new_room(env)
    await env.client.post(f"/api/rooms/{room['id']}/runs", json={"request_id": str(uuid4()), "message": "go", "mode": "all"})
    state = await wait_state(env, room["id"], lambda s: any(t["status"] == "waiting_user" for t in s["tasks"]))
    assert len(env.calls) == 1
    task = state["tasks"][0]
    body = {"confirmation_id": task["confirmation"]["confirmation_id"], "status": "approved", "user_input": "继续"}
    path = f"/api/rooms/{room['id']}/tasks/{task['id']}/respond"
    assert (await env.client.post(path, json=body)).status_code == 200
    assert (await env.client.post(path, json=body)).status_code == 409
    state = await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] == "completed")
    assert len(env.calls) == 4
    assert (await env.client.post(path, json=body)).status_code == 409


async def test_failed_tool_retry_requires_acknowledgement_and_keeps_old_context(room_env):
    env = room_env
    async def fail_after_tool(iteration):
        if iteration > 1:
            raise ValueError("provider secret must not reach browser")
        yield LLMChunk(type="tool_call_start", tool_call=ToolCall("write1", "write_file", {}), argument_delta='{"path":"a"}')
        yield LLMChunk(type="done", usage={"total_tokens": 4})
    env.scripts[env.agents[0].id] = fail_after_tool
    room = await new_room(env)
    await env.client.post(f"/api/rooms/{room['id']}/runs", json={"request_id": str(uuid4()), "message": "go", "mode": "all"})
    state = await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] == "partial")
    task = next(t for t in state["tasks"] if t["status"] == "failed")
    assert "secret" not in json.dumps(state)
    path = f"/api/rooms/{room['id']}/tasks/{task['id']}/retry"
    req = {"request_id": str(uuid4())}
    assert (await env.client.post(path, json=req)).status_code == 409
    env.scripts.clear()
    assert (await env.client.post(path, json={**req, "acknowledge_side_effects": True})).status_code == 202
    await wait_state(env, room["id"], lambda s: s["runs"][0]["status"] == "completed")
    retry_context = "\n".join(str(m.content) for m in env.calls[-1]["messages"])
    assert "[架构，消息 #" not in retry_context


async def test_owner_tenant_and_agent_visibility_checks(room_env):
    env = room_env
    room = await new_room(env)
    original = env.actor["user"]
    env.actor["user"] = SimpleNamespace(id=uuid4(), tenant_id=original.tenant_id)
    assert (await env.client.get(f"/api/rooms/{room['id']}")).status_code == 404
    env.actor["user"] = SimpleNamespace(id=original.id, tenant_id=uuid4())
    assert (await env.client.get(f"/api/rooms/{room['id']}")).status_code == 404
    env.actor["user"] = original
    async with env.factory() as db:
        agent = await db.get(Agent, env.agents[0].id)
        agent.visibility = "private"
        agent.created_by = uuid4()
        await db.commit()
    assert (await env.client.post(f"/api/rooms/{room['id']}/runs", json={"request_id": str(uuid4()), "message": "go"})).status_code == 422
    response = await env.client.post('/api/rooms', json={"goal": "x", "agent_ids": [str(env.agents[0].id)]})
    assert response.status_code == 404


async def test_stale_run_recovery_and_membership_history(room_env, monkeypatch):
    env = room_env
    monkeypatch.setattr(rooms, "start_run", lambda *a: None)
    room = await new_room(env)
    result = (await env.client.post(f"/api/rooms/{room['id']}/runs", json={"request_id": str(uuid4()), "message": "go"})).json()
    async with env.factory() as db:
        run = await db.get(ChatRoomRun, UUID(result["run_id"]))
        run.heartbeat_at = service.now() - timedelta(seconds=service.LEASE_SECONDS + 1)
        await db.commit()
    state = (await env.client.get(f"/api/rooms/{room['id']}")).json()
    assert state["runs"][0]["status"] == "interrupted"
    assert state["tasks"][0]["status"] == "failed"
    response = await env.client.patch(f"/api/rooms/{room['id']}", json={
        "agent_ids": [str(a.id) for a in env.agents[1:]], "default_agent_id": str(env.agents[1].id),
    })
    assert response.status_code == 200, response.text
    assert any("移除：产品" in m["content"] for m in response.json()["messages"])
    assert len([m for m in response.json()["members"] if m["is_active"]]) == 2
