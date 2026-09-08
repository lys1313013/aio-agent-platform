"""add message file changes

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "file_changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="本轮工作区文件变更(JSON)",
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "file_changes")
