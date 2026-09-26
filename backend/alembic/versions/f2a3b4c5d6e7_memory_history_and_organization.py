"""Full memory versions, reversible changes and confirmed organization plans."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "memories", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_table(
        "memory_changes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("before", pg.JSONB(), nullable=False),
        sa.Column("after", pg.JSONB(), nullable=False),
        sa.Column("undone_by", pg.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "memory_versions",
        sa.Column("memory_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("change_id", pg.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_memory_versions_change_id", "memory_versions", ["change_id"])
    op.execute("""INSERT INTO memory_versions (memory_id, version, user_id, kind, snapshot, created_at)
        SELECT id, 1, user_id, 'baseline',
               (to_jsonb(memories) - 'search_vec') ||
               jsonb_build_object('exists', true, 'metadata', COALESCE(metadata, '{}'::jsonb)), now()
        FROM memories""")
    op.create_table(
        "memory_organize_plans",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", pg.UUID(as_uuid=True)),
        sa.Column("layer", sa.String(4), nullable=False),
        sa.Column("groups", pg.JSONB(), nullable=False),
        sa.Column("originals", pg.JSONB(), nullable=False),
        sa.Column("applied_change_id", pg.UUID(as_uuid=True)),
        sa.Column("applied_digest", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for table in ("memory_changes", "memory_versions", "memory_organize_plans"):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY user_isolation ON {table} USING "
            "(user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
        )


def downgrade():
    for table in ("memory_organize_plans", "memory_versions", "memory_changes"):
        op.drop_table(table)
    op.drop_column("memories", "version")
