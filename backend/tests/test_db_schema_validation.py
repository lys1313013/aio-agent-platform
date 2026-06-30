"""Database schema validation tests.

These tests ensure that SQLAlchemy models match the actual database schema.
This prevents the issue where code references columns that don't exist yet
because migrations haven't been run.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Agent, Base


class TestDatabaseSchemaValidation:
    """Validate that models match the database schema."""

    @pytest.mark.asyncio
    async def test_agents_table_has_temperature_column(self, db_session: AsyncSession):
        """Verify agents table has temperature column in database."""
        # Use raw SQL to inspect table - avoids transaction issues
        result = await db_session.execute(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'agents' AND column_name = 'temperature'
            """)
        )
        columns = result.fetchall()

        assert len(columns) > 0, (
            "agents table is missing 'temperature' column. "
            "Did you forget to run 'uv run alembic upgrade head'?"
        )

    @pytest.mark.asyncio
    async def test_agents_table_has_welcome_message_column(self, db_session: AsyncSession):
        """Verify agents table has welcome_message column in database."""
        result = await db_session.execute(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'agents' AND column_name = 'welcome_message'
            """)
        )
        columns = result.fetchall()

        assert len(columns) > 0, (
            "agents table is missing 'welcome_message' column. "
            "Did you forget to run 'uv run alembic upgrade head'?"
        )

    @pytest.mark.asyncio
    async def test_all_model_columns_exist_in_database(self, db_session: AsyncSession):
        """Verify all Agent model columns exist in the database."""
        # Get columns from the model
        model_columns = {col.name for col in Agent.__table__.columns}

        # Get columns from the database
        result = await db_session.execute(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'agents'
            """)
        )
        db_columns = {row[0] for row in result.fetchall()}

        missing_columns = model_columns - db_columns

        assert not missing_columns, (
            f"Agent model has columns that don't exist in database: {missing_columns}. "
            "Did you forget to create and run a migration?"
        )

    @pytest.mark.asyncio
    async def test_no_pending_migrations_for_model_columns(self, db_session: AsyncSession):
        """Ensure model columns don't exceed database schema (detect missing migrations)."""
        # Get all columns from Agent model
        model_columns = {col.name for col in Agent.__table__.columns}

        # Get all columns from database
        result = await db_session.execute(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'agents'
            """)
        )
        db_columns = {row[0] for row in result.fetchall()}

        # Model should not have columns that database doesn't have
        # (this would indicate a missing migration)
        extra_model_columns = model_columns - db_columns

        assert not extra_model_columns, (
            f"Agent model defines columns {extra_model_columns} that don't exist in database. "
            "You need to create a migration for these columns."
        )
