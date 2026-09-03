"""Tests for portal endpoints — /api/portal/agents sanitized read-only views."""

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Agent, User
from aio_agent_platform.interface.api import app

TEST_USER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient, db_session: AsyncSession):
    """HTTP client with auth bypassed — returns the same user every time."""
    from aio_agent_platform.auth.dependencies import get_current_user

    user = User(
        id=TEST_USER_ID,
        username="portal-tester",
        email="portal@test.com",
        password_hash="fake",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    yield client
    # client fixture clears dependency_overrides on teardown


@pytest.mark.asyncio
async def test_portal_agents_only_exposes_safe_fields(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Portal list must not leak system_prompt / tools / model config."""
    agent = Agent(
        id=uuid.uuid4(),
        name="portal-agent",
        description="desc",
        system_prompt="SECRET PROMPT",
        is_active=True,
        created_by=TEST_USER_ID,
        welcome_message="hi",
        starter_prompts=[{"label": "你好", "icon": "smile"}],
    )
    db_session.add(agent)
    await db_session.flush()

    resp = await auth_client.get("/api/portal/agents")
    assert resp.status_code == 200
    data = [a for a in resp.json() if a["name"] == "portal-agent"]
    assert len(data) == 1
    item = data[0]
    assert item["welcome_message"] == "hi"
    assert item["starter_prompts"] == [{"label": "你好", "icon": "smile"}]
    for forbidden in ("system_prompt", "enabled_tools", "model_id", "model_name", "temperature"):
        assert forbidden not in item, f"portal response leaks field: {forbidden}"
    assert "SECRET PROMPT" not in resp.text


@pytest.mark.asyncio
async def test_portal_agents_visibility_filter(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Private agents of other users are invisible; inactive agents excluded."""
    other_user = User(
        id=uuid.uuid4(),
        username="portal-other",
        email="portal-other@test.com",
        password_hash="fake",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.flush()

    mine_private = Agent(
        id=uuid.uuid4(), name="mine-private", is_active=True,
        visibility="private", created_by=TEST_USER_ID,
    )
    other_private = Agent(
        id=uuid.uuid4(), name="other-private", is_active=True,
        visibility="private", created_by=other_user.id,
    )
    tenant_agent = Agent(
        id=uuid.uuid4(), name="tenant-visible", is_active=True,
        visibility="tenant", created_by=other_user.id,
    )
    inactive = Agent(
        id=uuid.uuid4(), name="inactive-agent", is_active=False,
        visibility="tenant", created_by=TEST_USER_ID,
    )
    db_session.add_all([mine_private, other_private, tenant_agent, inactive])
    await db_session.flush()

    resp = await auth_client.get("/api/portal/agents")
    names = {a["name"] for a in resp.json()}
    assert "mine-private" in names
    assert "tenant-visible" in names
    assert "other-private" not in names
    assert "inactive-agent" not in names


@pytest.mark.asyncio
async def test_portal_get_agent_by_id(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    agent = Agent(
        id=uuid.uuid4(), name="portal-get", is_active=True, created_by=TEST_USER_ID,
    )
    db_session.add(agent)
    await db_session.flush()

    resp = await auth_client.get(f"/api/portal/agents/{agent.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "portal-get"
    assert "system_prompt" not in resp.json()

    assert (await auth_client.get(f"/api/portal/agents/{uuid.uuid4()}")).status_code == 404
