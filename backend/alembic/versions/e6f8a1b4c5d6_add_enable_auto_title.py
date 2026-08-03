"""add enable_auto_title to agents

Revision ID: e6f8a1b4c5d6
Revises: d5e7f9a2b3c4
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa

revision = "e6f8a1b4c5d6"
down_revision = "d5e7f9a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "enable_auto_title",
            sa.Boolean(),
            server_default="true",
            nullable=False,
            comment="是否自动总结会话标题",
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "enable_auto_title")
