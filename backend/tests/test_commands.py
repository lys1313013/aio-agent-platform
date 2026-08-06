"""Slash command system tests — parser, dispatcher, and endpoints."""

import asyncio
import json
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import get_current_user
from aio_agent_platform.core.chat import load_conversation_history
from aio_agent_platform.db import Session
from aio_agent_platform.db.models import (
    Agent,
    LLMModel,
    LLMProvider,
    Memory,
    Message,
    Skill,
    User,
)
from aio_agent_platform.interface.api import app
from aio_agent_platform.interface.commands import dispatch
from aio_agent_platform.interface.commands.models import CommandContext
from aio_agent_platform.interface.commands.parser import ParseError, parse_command
from aio_agent_platform.interface.commands.registry import registry
from aio_agent_platform.memory.service import MemoryService

# ---- Parser (no DB) ----


def test_parse_quoted_cron_expression():
    cmd = registry.get("cron")
    args = parse_command("/cron create '0 9 * * *' 每天发日报", cmd)
    assert args == {"action": "create", "param": "0 9 * * *", "message": "每天发日报"}


def test_parse_variadic_arg_joins_rest():
    cmd = registry.get("remember")
    args = parse_command("/remember 我喜欢在 早上 工作", cmd)
    assert args["content"] == "我喜欢在 早上 工作"


def test_parse_missing_required_raises():
    cmd = registry.get("remember")
    with pytest.raises(ParseError):
        parse_command("/remember", cmd)


def test_parse_bad_choice_raises():
    cmd = registry.get("cron")
    with pytest.raises(ParseError):
        parse_command("/cron nonsense", cmd)


def test_aliases_resolve_to_same_command():
    assert registry.get("reset") is registry.get("new")
    assert registry.get("clear") is registry.get("new")


# ---- Dispatcher (unknown command degrades gracefully, no DB needed) ----


def test_dispatch_unknown_command_degrades():
    class FakeUser:
        role = "user"

    async def run():
        ctx = CommandContext(user=FakeUser(), user_id="x", db=None, raw="/definitely-not-a-command")
        result = await dispatch(ctx)
        assert "未知命令" in result.content

    asyncio.run(run())


# ---- API tests (real local test DB on :5435) ----


@pytest_asyncio.fixture
async def command_user(client: AsyncClient, db_session: AsyncSession):
    user = User(
        id=uuid4(),
        username="cmd-tester",
        email="cmd@test.com",
        password_hash="fake",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    try:
        yield client, db_session, user
    finally:
        app.dependency_overrides.clear()


async def test_api_commands_lists_builtin(command_user):
    client, _db, _user = command_user
    resp = await client.get("/api/commands")
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()}
    assert {"help", "cron", "memory", "sessions", "skill"} <= names


async def test_api_commands_includes_dynamic_skill(command_user):
    client, db_session, user = command_user
    db_session.add(
        Skill(user_id=user.id, name="my-custom-skill", is_active=True, category="general")
    )
    await db_session.flush()
    resp = await client.get("/api/commands")
    names = {c["name"] for c in resp.json()}
    assert "my-custom-skill" in names


async def test_api_commands_only_active_models(command_user):
    client, db_session, _user = command_user
    provider = LLMProvider(name="p", provider_type="openai")
    db_session.add(provider)
    await db_session.flush()
    db_session.add_all([
        LLMModel(provider_id=provider.id, name="active-model", model_name="a", is_active=True, is_default=True),
        LLMModel(provider_id=provider.id, name="inactive-model", model_name="b", is_active=False),
    ])
    await db_session.flush()
    resp = await client.get("/api/models")
    assert resp.status_code == 200
    names = {m["name"] for m in resp.json()}
    assert "active-model" in names
    assert "inactive-model" not in names


async def test_patch_session_agent_id(command_user):
    client, db_session, user = command_user
    agent = Agent(
        id=uuid4(),
        tenant_id=user.tenant_id,
        created_by=user.id,
        name="a1",
        visibility="tenant",
        is_active=True,
    )
    session = Session(user_id=user.id, title="s")
    db_session.add_all([agent, session])
    await db_session.flush()

    resp = await client.patch(
        f"/api/sessions/{session.id}", json={"agent_id": str(agent.id)}
    )
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == str(agent.id)


async def test_chat_stream_persists_system_not_in_history(command_user):
    client, db_session, _user = command_user
    async with client.stream("POST", "/api/chat/stream", json={"message": "/whoami"}) as resp:
        lines = [line async for line in resp.aiter_lines()]
    payloads = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    cmd_result = next(p for p in payloads if p["type"] == "command_result")
    assert "当前用户" in cmd_result["content"]

    session_id = UUID(cmd_result["session_id"])
    roles = (
        await db_session.execute(
            select(Message.role).where(Message.session_id == session_id)
        )
    ).scalars().all()
    assert "user" in roles
    assert "system" in roles

    history, _ = await load_conversation_history(db_session, session_id)
    history_roles = {getattr(m, "role", None) for m in history}
    assert "system" not in history_roles


async def test_chat_stream_command_result(command_user):
    client, _db, _user = command_user
    async with client.stream("POST", "/api/chat/stream", json={"message": "/help"}) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines()]
    payloads = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    types = [p["type"] for p in payloads]
    assert "command_result" in types
    assert "done" in types
    result = next(p for p in payloads if p["type"] == "command_result")
    assert "可用命令" in result["content"]


# ---- New Tier 1 + Tier 2 command parsing (no DB) ----


def test_parse_forget_keyword_variadic():
    cmd = registry.get("forget")
    args = parse_command("/forget 早上的 会议记录", cmd)
    assert args["id"] == "早上的 会议记录"


def test_parse_delegate_role_task():
    cmd = registry.get("delegate")
    args = parse_command("/delegate 数据分析师 分析 上季度 销量", cmd)
    assert args["role"] == "数据分析师"
    assert args["task"] == "分析 上季度 销量"


def test_parse_model_switch():
    cmd = registry.get("model")
    args = parse_command("/model gpt-4o", cmd)
    assert args["name"] == "gpt-4o"


def test_parse_switch_uuid():
    cmd = registry.get("switch")
    args = parse_command(f"/switch {uuid4()}", cmd)
    assert UUID(args["id"])


def test_parse_export_json():
    cmd = registry.get("export")
    args = parse_command("/export json", cmd)
    assert args["format"] == "json"


# ---- Registration / permission visibility ----


async def test_api_commands_new_user_commands_visible_admin_hidden(command_user):
    client, _db, _user = command_user
    resp = await client.get("/api/commands")
    names = {c["name"] for c in resp.json()}
    assert {"context", "compact", "usage", "export", "tools", "delegate", "switch", "resume"} <= names
    assert not ({"mcp", "reload-mcp", "reload-skills", "status"} & names)


@pytest_asyncio.fixture
async def admin_user(client: AsyncClient, db_session: AsyncSession):
    user = User(
        id=uuid4(),
        username="cmd-admin",
        email="cmd-admin@test.com",
        password_hash="fake",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    try:
        yield client, db_session, user
    finally:
        app.dependency_overrides.clear()


async def test_api_commands_admin_sees_admin_commands(admin_user):
    client, _db, _user = admin_user
    resp = await client.get("/api/commands")
    names = {c["name"] for c in resp.json()}
    assert {"mcp", "reload-mcp", "reload-skills", "status"} <= names


# ---- Behavior via dispatcher (real local test DB) ----


def make_ctx(db, user, session, raw):
    return CommandContext(
        user=user,
        user_id=str(user.id),
        db=db,
        raw=raw,
        session_id=str(session.id),
        session=session,
    )


async def test_forget_keyword_deletes_matching_memory(command_user):
    _client, db, user = command_user
    await MemoryService.create_memory(db, user.id, "L2", "我喜欢在早上工作")
    await MemoryService.create_memory(db, user.id, "L2", "猫粮补货")
    session = Session(user_id=user.id, title="s")
    db.add(session)
    await db.flush()

    result = await dispatch(make_ctx(db, user, session, "/forget 早上"))
    assert "已删除" in result.content

    remaining = (
        await db.execute(select(Memory).where(Memory.user_id == user.id))
    ).scalars().all()
    contents = [m.content for m in remaining]
    assert "猫粮补货" in contents
    assert "早上" not in contents


async def test_forget_by_uuid_still_works(command_user):
    _client, db, user = command_user
    mem = await MemoryService.create_memory(db, user.id, "L2", "待删除的记忆")
    session = Session(user_id=user.id, title="s")
    db.add(session)
    await db.flush()

    result = await dispatch(make_ctx(db, user, session, f"/forget {mem.id}"))
    assert "已删除记忆" in result.content
    remaining = (
        await db.execute(select(Memory).where(Memory.user_id == user.id))
    ).scalars().all()
    assert remaining == []


async def test_model_switch_sets_session_model(command_user):
    _client, db, user = command_user
    provider = LLMProvider(name="p", provider_type="openai")
    db.add(provider)
    await db.flush()
    model = LLMModel(provider_id=provider.id, name="m1", model_name="m1", is_active=True)
    db.add(model)
    await db.flush()
    session = Session(user_id=user.id, title="s")
    db.add(session)
    await db.flush()

    result = await dispatch(make_ctx(db, user, session, "/model m1"))
    assert "已切换到模型" in result.content
    await db.refresh(session)
    assert session.model_id == model.id

    view = await dispatch(make_ctx(db, user, session, "/model"))
    assert "← 当前" in view.content
    assert "m1" in view.content


async def test_export_returns_session_messages(command_user):
    _client, db, user = command_user
    session = Session(user_id=user.id, title="s")
    db.add(session)
    await db.flush()
    db.add_all([
        Message(session_id=session.id, user_id=user.id, role="user", content="你好"),
        Message(session_id=session.id, user_id=user.id, role="assistant", content="你好！"),
    ])
    await db.flush()

    result = await dispatch(make_ctx(db, user, session, "/export"))
    assert "用户" in result.content
    assert "助手" in result.content
    assert "你好" in result.content

    json_result = await dispatch(make_ctx(db, user, session, "/export json"))
    payload = json.loads(json_result.content)
    assert [m["role"] for m in payload] == ["user", "assistant"]


async def test_context_and_usage_commands(command_user):
    _client, db, user = command_user
    session = Session(user_id=user.id, title="s")
    db.add(session)
    await db.flush()
    db.add(Message(session_id=session.id, user_id=user.id, role="user", content="hi"))
    await db.flush()

    res = await dispatch(make_ctx(db, user, session, "/context"))
    assert "上下文占用" in res.content
    assert "可用窗口" in res.content

    res2 = await dispatch(make_ctx(db, user, session, "/usage"))
    assert "消息总数" in res2.content


async def test_switch_and_resume_return_switch_session_id(command_user):
    _client, db, user = command_user
    s1 = Session(user_id=user.id, title="one")
    s2 = Session(user_id=user.id, title="two")
    db.add_all([s1, s2])
    await db.flush()

    res = await dispatch(make_ctx(db, user, s1, f"/switch {s2.id}"))
    assert res.data.get("switch_session_id") == str(s2.id)

    res2 = await dispatch(make_ctx(db, user, s1, "/resume"))
    assert res2.data.get("switch_session_id") == str(s2.id)
