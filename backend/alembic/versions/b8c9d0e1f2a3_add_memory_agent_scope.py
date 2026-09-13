"""Add user/agent scope to memories and daily summaries.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows stay shared: do not guess ownership of historical summaries.
    for table in ("memories", "daily_memories"):
        op.add_column(table, sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("idx_memories_user_agent_layer", "memories", ["user_id", "agent_id", "layer"])
    op.drop_constraint("uq_daily_memories_user_date", "daily_memories", type_="unique")
    op.create_index("uq_daily_memories_shared_date", "daily_memories", ["user_id", "date"],
                    unique=True, postgresql_where=sa.text("agent_id IS NULL"))
    op.create_index("uq_daily_memories_agent_date", "daily_memories", ["user_id", "agent_id", "date"],
                    unique=True, postgresql_where=sa.text("agent_id IS NOT NULL"))


def downgrade():
    # Refuse a lossy downgrade if multiple scopes already exist for the same day.
    op.create_unique_constraint("uq_daily_memories_user_date", "daily_memories", ["user_id", "date"])
    op.drop_index("uq_daily_memories_agent_date", table_name="daily_memories")
    op.drop_index("uq_daily_memories_shared_date", table_name="daily_memories")
    op.drop_index("idx_memories_user_agent_layer", table_name="memories")
    for table in ("daily_memories", "memories"):
        op.drop_column(table, "agent_id")
