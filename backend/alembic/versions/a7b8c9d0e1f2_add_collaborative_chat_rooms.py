"""Add collaborative chat rooms and durable member executions.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_rooms",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("default_member_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("message_sequence", sa.BigInteger(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("summary_sequence", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_chat_rooms_owner", "chat_rooms", ["tenant_id", "user_id", "updated_at"])
    op.create_table(
        "chat_room_members",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("icon", sa.String(128)),
        sa.Column("description", sa.Text()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("room_id", "agent_id", name="uq_room_member_agent"),
    )
    op.create_table(
        "chat_room_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("input", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("stop_requested", sa.Boolean(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("room_id", "request_id", name="uq_room_run_request"),
    )
    op.create_index("uq_room_active_run", "chat_room_runs", ["room_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running', 'stopping')"))
    op.create_table(
        "chat_room_tasks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", pg.UUID(as_uuid=True)),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("context_snapshot", pg.JSONB()),
        sa.Column("retry_of", pg.UUID(as_uuid=True)),
        sa.Column("confirmation", pg.JSONB()),
        sa.Column("confirmation_response", pg.JSONB()),
        sa.Column("token_usage", pg.JSONB()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "position", name="uq_room_task_position"),
    )
    op.create_table(
        "chat_room_messages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True)),
        sa.Column("member_id", pg.UUID(as_uuid=True)),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("icon", sa.String(128)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reply_to_id", pg.UUID(as_uuid=True)),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("room_id", "sequence", name="uq_room_message_sequence"),
    )


def downgrade() -> None:
    for table in ("chat_room_messages", "chat_room_tasks", "chat_room_runs",
                  "chat_room_members", "chat_rooms"):
        op.drop_table(table)
