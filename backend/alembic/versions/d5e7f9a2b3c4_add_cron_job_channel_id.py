"""add channel_id to cron_jobs

定时任务结果可推送到指定 IM 渠道（如飞书）。

Revision ID: d5e7f9a2b3c4
Revises: c8e2f4a1b7d9
Create Date: 2026-08-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# revision identifiers, used by Alembic.
revision: str = 'd5e7f9a2b3c4'
down_revision: Union[str, None] = 'c8e2f4a1b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'cron_jobs',
        sa.Column('channel_id', PG_UUID(as_uuid=True), nullable=True, comment='结果推送渠道ID(关联 channel_configs)'),
    )


def downgrade() -> None:
    op.drop_column('cron_jobs', 'channel_id')
