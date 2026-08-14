"""add extra_config to channel_configs

渠道类型特有配置（如企微应用 agentid）存入 JSONB，避免为每个渠道新增敏感列。

Revision ID: a9f3c5e7d2b1
Revises: b7e91c2a4d83
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'a9f3c5e7d2b1'
down_revision: Union[str, None] = 'b7e91c2a4d83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'channel_configs',
        sa.Column(
            'extra_config',
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment='渠道类型特有配置(如企微 agentid)',
        ),
    )


def downgrade() -> None:
    op.drop_column('channel_configs', 'extra_config')
