"""Tests for agent models and API endpoints."""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Agent


class TestAgentModel:
    """Test Agent SQLAlchemy model."""

    @pytest.mark.asyncio
    async def test_agent_model_has_temperature_field(self, db_session: AsyncSession):
        """Verify Agent model has temperature field."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()

        agent = Agent(
            id=agent_id,
            name="Test Agent",
            temperature=0.8,
            created_by=user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db_session.add(agent)
        await db_session.flush()

        # Query back
        result = await db_session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        retrieved = result.scalar_one()

        assert retrieved.temperature == 0.8

    @pytest.mark.asyncio
    async def test_agent_model_has_welcome_message_field(self, db_session: AsyncSession):
        """Verify Agent model has welcome_message field."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()

        agent = Agent(
            id=agent_id,
            name="Test Agent",
            welcome_message="Hello! How can I help you?",
            created_by=user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db_session.add(agent)
        await db_session.flush()

        # Query back
        result = await db_session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        retrieved = result.scalar_one()

        assert retrieved.welcome_message == "Hello! How can I help you?"

    @pytest.mark.asyncio
    async def test_agent_model_temperature_can_be_null(self, db_session: AsyncSession):
        """Verify temperature field accepts null (use global default)."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()

        agent = Agent(
            id=agent_id,
            name="Test Agent",
            temperature=None,  # Use global default
            created_by=user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db_session.add(agent)
        await db_session.flush()

        result = await db_session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        retrieved = result.scalar_one()

        assert retrieved.temperature is None

    @pytest.mark.asyncio
    async def test_agent_model_welcome_message_can_be_null(self, db_session: AsyncSession):
        """Verify welcome_message field accepts null (no welcome message)."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()

        agent = Agent(
            id=agent_id,
            name="Test Agent",
            welcome_message=None,
            created_by=user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db_session.add(agent)
        await db_session.flush()

        result = await db_session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        retrieved = result.scalar_one()

        assert retrieved.welcome_message is None

    @pytest.mark.asyncio
    async def test_agent_model_all_new_fields_together(self, db_session: AsyncSession):
        """Verify all new fields work together."""
        agent_id = uuid.uuid4()
        user_id = uuid.uuid4()

        agent = Agent(
            id=agent_id,
            name="Full Featured Agent",
            description="An agent with all features",
            temperature=0.5,
            welcome_message="Welcome to my agent!",
            created_by=user_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db_session.add(agent)
        await db_session.flush()

        result = await db_session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        retrieved = result.scalar_one()

        assert retrieved.name == "Full Featured Agent"
        assert retrieved.description == "An agent with all features"
        assert retrieved.temperature == 0.5
        assert retrieved.welcome_message == "Welcome to my agent!"
