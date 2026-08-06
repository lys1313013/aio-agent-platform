"""add mcp_servers tenant_id

Revision ID: 2b9374942e23
Revises: f3b0c1d2e4a5
Create Date: 2026-08-06 16:46:15.296831
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b9374942e23'
down_revision: Union[str, None] = 'f3b0c1d2e4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column(
            "tenant_id",
            sa.UUID(),
            nullable=False,
            server_default="00000000-0000-0000-0000-000000000001",
            comment="所属租户ID",
        ),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "tenant_id")
