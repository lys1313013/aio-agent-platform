"""merge portrait tenant isolation and graph knowledge base

Revision ID: 111deac801c6
Revises: a1b2c3d4e5f7, b2c3d4e5f6a7
Create Date: 2026-08-08 22:31:38.129701
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '111deac801c6'
down_revision: Union[str, None] = ('a1b2c3d4e5f7', 'b2c3d4e5f6a7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
