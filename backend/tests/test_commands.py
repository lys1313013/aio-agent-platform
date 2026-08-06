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
    Message,
    Skill,
    User,
)
from aio_agent_platform.interface.api import app
from aio_agent_platform.interface.commands import dispatch
from aio_agent_platform.interface.commands.models import CommandContext
from aio_agent_platform.interface.commands.parser import ParseError, parse_command
from aio_agent_platform.interface.commands.registry import registry

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
