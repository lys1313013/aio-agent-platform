"""Tests for GET /api/agents/{agent_id}/stats endpoint.

Covers the regression where missing `func` import in agents.py caused
a NameError at runtime, returning 500 instead of actual stats.
"""

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Agent, Message, Session, User
from aio_agent_platform.interface.api import app


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient, db_session: AsyncSession):
    """HTTP client with auth bypassed — returns the same user every time."""
    from aio_agent_platform.auth.dependencies import get_current_user

    # Create a deterministic test user
    user = User(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        username="stats-tester",
        email="stats@test.com",
        password_hash="fake",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    yield client
    # client fixture clears dependency_overrides on teardown

TEST_USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.mark.asyncio
async def test_stats_returns_200_for_valid_agent(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Stats endpoint returns 200 — catches missing-import crashes (500)."""
    agent = Agent(id=uuid.uuid4(), name="stats-agent", is_active=True, created_by=TEST_USER_ID)
    db_session.add(agent)
    await db_session.flush()

    resp = await auth_client.get(f"/api/agents/{agent.id}/stats")
    assert resp.status_code == 200, (
        f"Expected 200, got {resp.status_code}: {resp.text}. "
        "Likely a NameError or missing import in the stats handler."
    )


@pytest.mark.asyncio
async def test_stats_returns_correct_shape(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Response contains total_sessions, total_messages, last_active_at."""
    agent = Agent(id=uuid.uuid4(), name="stats-shape-agent", is_active=True, created_by=TEST_USER_ID)
    db_session.add(agent)
    await db_session.flush()

    resp = await auth_client.get(f"/api/agents/{agent.id}/stats")
    data = resp.json()

    assert "total_sessions" in data
    assert "total_messages" in data
    assert "last_active_at" in data
    assert isinstance(data["total_sessions"], int)
    assert isinstance(data["total_messages"], int)


@pytest.mark.asyncio
async def test_stats_counts_sessions_and_messages(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Counts match the actual sessions and messages for this user + agent."""
    user_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    agent = Agent(id=uuid.uuid4(), name="stats-count-agent", is_active=True, created_by=TEST_USER_ID)
    db_session.add(agent)
    await db_session.flush()

    # Create 2 sessions with 3 messages total
    s1 = Session(id=uuid.uuid4(), user_id=user_id, agent_id=agent.id, title="s1")
    s2 = Session(id=uuid.uuid4(), user_id=user_id, agent_id=agent.id, title="s2")
    db_session.add_all([s1, s2])
    await db_session.flush()

    for sid in [s1.id, s1.id, s2.id]:
        db_session.add(Message(
            id=uuid.uuid4(),
            session_id=sid,
            user_id=user_id,
            role="user",
            content="hello",
        ))
    await db_session.flush()

    resp = await auth_client.get(f"/api/agents/{agent.id}/stats")
    data = resp.json()

    assert data["total_sessions"] == 2
    assert data["total_messages"] == 3
    assert data["last_active_at"] is not None


@pytest.mark.asyncio
async def test_stats_excludes_other_users_sessions(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Stats only count the current user's sessions, not other users'."""
    user_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    other_user = User(
        id=uuid.uuid4(),
        username="other-user",
        email="other@test.com",
        password_hash="fake",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.flush()

    agent = Agent(id=uuid.uuid4(), name="stats-isolation-agent", is_active=True, created_by=TEST_USER_ID)
    db_session.add(agent)
    await db_session.flush()

    # Current user: 1 session
    db_session.add(Session(
        id=uuid.uuid4(), user_id=user_id, agent_id=agent.id, title="mine",
    ))
    # Other user: 2 sessions
    for i in range(2):
        db_session.add(Session(
            id=uuid.uuid4(), user_id=other_user.id, agent_id=agent.id, title=f"theirs-{i}",
        ))
    await db_session.flush()

    resp = await auth_client.get(f"/api/agents/{agent.id}/stats")
    data = resp.json()

    assert data["total_sessions"] == 1, (
        f"Expected 1 (only current user's sessions), got {data['total_sessions']}"
    )


@pytest.mark.asyncio
async def test_stats_zero_sessions(
    auth_client: AsyncClient,
    db_session: AsyncSession,
):
    """Agent with no sessions returns zeros and null last_active_at."""
    agent = Agent(id=uuid.uuid4(), name="stats-empty-agent", is_active=True, created_by=TEST_USER_ID)
    db_session.add(agent)
    await db_session.flush()

    resp = await auth_client.get(f"/api/agents/{agent.id}/stats")
    data = resp.json()

    assert data["total_sessions"] == 0
    assert data["total_messages"] == 0
    assert data["last_active_at"] is None
