"""drop user_pets level/exp (移除宠物升级功能)

Revision ID: f3b0c1d2e4a5
Revises: a9b8c7d6e5f4
Create Date: 2026-08-06

"""
from alembic import op
import sqlalchemy as sa

revision: str = "f3b0c1d2e4a5"
down_revision: str | None = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("user_pets", "level")
    op.drop_column("user_pets", "exp")


def downgrade() -> None:
    op.add_column(
        "user_pets",
        sa.Column("level", sa.Integer(), server_default="1", nullable=False, comment="等级"),
    )
    op.add_column(
        "user_pets",
        sa.Column("exp", sa.Integer(), server_default="0", nullable=False, comment="累计经验"),
    )
