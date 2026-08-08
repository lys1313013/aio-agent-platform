"""add hook tables (Hook 机制)

Revision ID: 9a0b1c2d3e4f
Revises: 6b09c3d8bdb8
Create Date: 2026-08-08

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "9a0b1c2d3e4f"
down_revision: str | None = "6b09c3d8bdb8"
branch_labels = None
depends_on = None


def _uuid(**kwargs) -> UUID:
    return UUID(as_uuid=True, **kwargs)


def upgrade() -> None:
    op.create_table(
        "hooks",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("tenant_id", _uuid(), nullable=True),
        sa.Column("created_by", _uuid(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(16), server_default="tenant", nullable=False),
        sa.Column("agent_id", _uuid(), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("action_type", sa.String(16), server_default="webhook", nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), server_default=sa.text("5000"), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("visibility", sa.String(16), server_default="tenant", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="Hook配置表",
    )
    op.create_index("idx_hooks_tenant_event", "hooks", ["tenant_id", "event"])
    op.create_index("idx_hooks_agent_event", "hooks", ["agent_id", "event"])

    op.create_table(
        "hook_executions",
        sa.Column("id", _uuid(), nullable=False),
        sa.Column("hook_id", _uuid(), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("trace_id", _uuid(), nullable=True),
        sa.Column("session_id", _uuid(), nullable=True),
        sa.Column("user_id", _uuid(), nullable=True),
        sa.Column("tenant_id", _uuid(), nullable=True),
        sa.Column("agent_id", _uuid(), nullable=True),
        sa.Column("action_type", sa.String(16), nullable=False),
        sa.Column("target", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), server_default="success", nullable=False),
        sa.Column("duration_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("response_preview", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        comment="Hook执行日志表",
    )
    op.create_index("idx_hook_executions_tenant_created", "hook_executions", ["tenant_id", "created_at"])
    op.create_index("idx_hook_executions_hook_created", "hook_executions", ["hook_id", "created_at"])


def downgrade() -> None:
    op.drop_table("hook_executions")
    op.drop_table("hooks")
