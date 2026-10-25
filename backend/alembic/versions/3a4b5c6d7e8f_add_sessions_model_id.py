"""add sessions model_id

Revision ID: 3a4b5c6d7e8f
Revises: 2b9374942e23
Create Date: 2026-08-06 17:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3a4b5c6d7e8f'
down_revision: str | None = '2b9374942e23'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("model_id", sa.UUID(), nullable=True, comment="会话级模型覆盖(缺省跟随智能体模型)"),
    )


def downgrade() -> None:
    op.drop_column("sessions", "model_id")
