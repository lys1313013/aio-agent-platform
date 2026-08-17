"""add tenant_id to cron_job_runs

运行记录表缺 tenant_id 列（2026-08-09 模型加入但未生成迁移，导致按迁移链建库时
运行记录写入/查询报 column does not exist）。仅按 alembic 迁移链建库的环境需要；
由 create_all 建库的库已有该列，勿执行本迁移。

Revision ID: c1d2e3f4a5b6
Revises: a9f3c5e7d2b1
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'a9f3c5e7d2b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'cron_job_runs',
        sa.Column(
            'tenant_id',
            PG_UUID(as_uuid=True),
            nullable=False,
            # 存量记录回填到默认租户；后续写入由模型 Python default 覆盖
            server_default=sa.text("'00000000-0000-0000-0000-000000000001'::uuid"),
            comment='所属租户ID',
        ),
    )


def downgrade() -> None:
    op.drop_column('cron_job_runs', 'tenant_id')
