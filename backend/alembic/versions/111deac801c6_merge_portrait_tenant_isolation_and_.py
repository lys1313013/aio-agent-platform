"""merge portrait tenant isolation and graph knowledge base

Revision ID: 111deac801c6
Revises: a1b2c3d4e5f7, b2c3d4e5f6a7
Create Date: 2026-08-08 22:31:38.129701
"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '111deac801c6'
down_revision: str | None = ('a1b2c3d4e5f7', 'b2c3d4e5f6a7')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
