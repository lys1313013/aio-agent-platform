"""Durable Web chat execution and replay."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chat_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("assistant_message_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("resumed_from", pg.UUID(as_uuid=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("request", pg.JSONB(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chat_runs_session_id", "chat_runs", ["session_id"])
    op.create_index("ix_chat_runs_user_id", "chat_runs", ["user_id"])
    op.create_index("uq_chat_runs_active_session", "chat_runs", ["session_id"], unique=True,
                    postgresql_where=sa.text("status = 'running'"))
    op.create_table(
        "chat_run_events",
        sa.Column("run_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
    )
    for table in ("chat_runs", "chat_run_events"):
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY user_isolation ON {table} USING "
                   "(user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)")


def downgrade():
    op.drop_table("chat_run_events")
    op.drop_table("chat_runs")
