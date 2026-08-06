"""Tests for GET /api/analytics/* endpoints.

These routes use partial-column selects (e.g. ``select(TokenUsageDaily.model,
func.sum(...))``). If such a select omits a column the handler later reads, the
row would lack the field and the endpoint would break. These tests seed rows and
assert the exact fields the handlers read are present in the response.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Agent, Message, Session, TokenUsageDaily, User
from aio_agent_platform.interface.api import app

TEST_USER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient, db_session: AsyncSession):
    """HTTP client with auth bypassed — returns the same user every time."""
    from aio_agent_platform.auth.dependencies import get_current_user

    user = User(
        id=TEST_USER_ID,
        username="analytics-tester",
        email="analytics@test.com",
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


@pytest.mark.asyncio
async def test_trend_returns_tokens_and_sessions(auth_client: AsyncClient, db_session: AsyncSession):
    """trend returns prompt/completion/total tokens and a session count per day."""
    today = date.today()
    db_session.add(
        TokenUsageDaily(
            user_id=TEST_USER_ID, date=today, model="test-model",
            prompt_tokens=10, completion_tokens=20, total_tokens=30, request_count=2,
        )
    )
    db_session.add(Session(user_id=TEST_USER_ID, title="trend-session"))
    await db_session.flush()

    resp = await auth_client.get(f"/api/analytics/trend?start={today}&end={today}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data, "trend returned no points"
    point = data[0]
    assert {"date", "prompt_tokens", "completion_tokens", "total_tokens", "sessions"} <= set(point)
    assert point["prompt_tokens"] == 10
    assert point["completion_tokens"] == 20
    assert point["total_tokens"] == 30
    assert point["sessions"] == 1


@pytest.mark.asyncio
async def test_distribution_by_model_returns_token_fields(auth_client: AsyncClient, db_session: AsyncSession):
    """distribution?by=model returns per-model token sums the handler reads."""
    today = date.today()
    db_session.add_all([
        TokenUsageDaily(
            user_id=TEST_USER_ID, date=today, model="model-a",
            prompt_tokens=1, completion_tokens=2, total_tokens=3, request_count=1,
        ),
        TokenUsageDaily(
            user_id=TEST_USER_ID, date=today, model="model-b",
            prompt_tokens=4, completion_tokens=5, total_tokens=9, request_count=2,
        ),
    ])
    await db_session.flush()

    resp = await auth_client.get(f"/api/analytics/distribution?by=model&start={today}&end={today}")
    assert resp.status_code == 200, resp.text
    by_key = {item["key"]: item for item in resp.json()}
    assert by_key["model-a"]["label"] == "model-a"
    assert by_key["model-a"]["total_tokens"] == 3
    assert by_key["model-a"]["request_count"] == 1
    assert by_key["model-b"]["total_tokens"] == 9
    assert by_key["model-b"]["request_count"] == 2


@pytest.mark.asyncio
async def test_distribution_by_agent_returns_sessions(auth_client: AsyncClient, db_session: AsyncSession):
    """distribution?by=agent returns per-agent session counts with resolved names."""
    today = date.today()
    agent_a = Agent(id=uuid.uuid4(), name="agent-a", is_active=True, created_by=TEST_USER_ID)
    agent_b = Agent(id=uuid.uuid4(), name="agent-b", is_active=True, created_by=TEST_USER_ID)
    db_session.add_all([agent_a, agent_b])
    db_session.add_all([
        Session(user_id=TEST_USER_ID, agent_id=agent_a.id, title="s1"),
        Session(user_id=TEST_USER_ID, agent_id=agent_a.id, title="s2"),
        Session(user_id=TEST_USER_ID, agent_id=agent_b.id, title="s3"),
    ])
    await db_session.flush()

    resp = await auth_client.get(f"/api/analytics/distribution?by=agent&start={today}&end={today}")
    assert resp.status_code == 200, resp.text
    by_key = {item["key"]: item for item in resp.json()}
    assert by_key[str(agent_a.id)]["label"] == "agent-a"
    assert by_key[str(agent_a.id)]["sessions"] == 2
    assert by_key[str(agent_b.id)]["label"] == "agent-b"
    assert by_key[str(agent_b.id)]["sessions"] == 1


@pytest.mark.asyncio
async def test_summary_counts(auth_client: AsyncClient, db_session: AsyncSession):
    """summary counts sessions/messages/tokens/requests for the user."""
    today = date.today()
    db_session.add(
        TokenUsageDaily(
            user_id=TEST_USER_ID, date=today, model="test-model",
            prompt_tokens=5, completion_tokens=5, total_tokens=10, request_count=1,
        )
    )
    db_session.add(Session(user_id=TEST_USER_ID, title="summary-session"))
    await db_session.flush()
    session_id = (await db_session.execute(
        select(Session.id).where(Session.user_id == TEST_USER_ID)
    )).scalar_one()
    db_session.add(Message(user_id=TEST_USER_ID, session_id=session_id, role="user", content="hi"))
    await db_session.flush()

    resp = await auth_client.get(f"/api/analytics/summary?start={today}&end={today}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sessions"] == 1
    assert data["messages"] == 1
    assert data["total_tokens"] == 10
    assert data["request_count"] == 1
