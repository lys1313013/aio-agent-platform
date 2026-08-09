"""add channel enable_streaming

Revision ID: 8d2f4a6b7c9e
Revises: 71f9903b91da
Create Date: 2026-08-09

"""

import sqlalchemy as sa

from alembic import op

revision = "8d2f4a6b7c9e"
down_revision = "71f9903b91da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_configs",
        sa.Column(
            "enable_streaming",
            sa.Boolean(),
            server_default="true",
            nullable=False,
            comment="是否启用渠道流式回复",
        ),
    )


def downgrade() -> None:
    op.drop_column("channel_configs", "enable_streaming")
