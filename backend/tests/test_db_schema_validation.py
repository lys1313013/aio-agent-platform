"""Database schema validation tests.

These tests ensure every SQLAlchemy model matches the actual database schema,
in both directions:

- model columns must exist in the database (missing -> every query on that
  column crashes with "column does not exist", e.g. a forgotten migration)
- database columns must exist on the model (extra -> a pending DROP migration)

Parameterized over all tables so new models/columns are covered automatically.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Base


async def _db_column_names(db: AsyncSession, table_name: str) -> set[str]:
    result = await db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :t"
        ),
        {"t": table_name},
    )
    return {row[0] for row in result.fetchall()}


@pytest.mark.parametrize(
    "table", [t for t in Base.metadata.sorted_tables], ids=lambda t: t.name
)
class TestDatabaseSchemaValidation:
    @pytest.mark.asyncio
    async def test_model_columns_exist_in_db(self, db_session: AsyncSession, table):
        """Every model column must exist in the database (no missing migration)."""
        model_columns = {col.name for col in table.columns}
        db_columns = await _db_column_names(db_session, table.name)

        missing = model_columns - db_columns
        assert not missing, (
            f"table '{table.name}' is missing model columns: {sorted(missing)}. "
            "Did you forget to create and run a migration? "
            "These queries would crash with 'column does not exist'."
        )

    @pytest.mark.asyncio
    async def test_no_stale_db_columns(self, db_session: AsyncSession, table):
        """Every database column must exist on the model (no pending drop)."""
        model_columns = {col.name for col in table.columns}
        db_columns = await _db_column_names(db_session, table.name)

        stale = db_columns - model_columns
        assert not stale, (
            f"table '{table.name}' has database columns not on the model: "
            f"{sorted(stale)}. A drop migration is pending."
        )
