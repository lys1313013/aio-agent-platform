"""add mcp all-tools selection policy

Revision ID: d4e5f6a7b8c9
Revises: b437b570e6d8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "b437b570e6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "mcp_all_tools_server_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
            comment="自动启用全部工具的MCP服务器ID列表(JSON)",
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "mcp_all_tools_server_ids")
