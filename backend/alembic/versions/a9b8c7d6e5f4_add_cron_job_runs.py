"""add cron_job_runs

定时任务运行日志表：记录每次执行的状态、耗时、输出与错误。

Revision ID: a9b8c7d6e5f4
Revises: a1b2c3d4e5f6
Create Date: 2026-08-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: str | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'cron_job_runs',
        sa.Column('id', PG_UUID(as_uuid=True), primary_key=True),
        sa.Column('job_id', PG_UUID(as_uuid=True), nullable=False, comment='关联定时任务ID'),
        sa.Column('user_id', PG_UUID(as_uuid=True), nullable=False, comment='任务所属用户ID'),
        sa.Column('status', sa.String(32), server_default='running', nullable=False, comment='运行状态: running/success/failed'),
        sa.Column('session_id', PG_UUID(as_uuid=True), nullable=True, comment='本次执行创建的会话ID'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='开始执行时间'),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True, comment='结束时间'),
        sa.Column('duration_ms', sa.Integer(), nullable=True, comment='执行耗时(毫秒)'),
        sa.Column('output', sa.Text(), nullable=True, comment='执行输出'),
        sa.Column('error', sa.Text(), nullable=True, comment='错误信息'),
        comment='定时任务运行日志表',
    )
    op.create_index('idx_cron_job_runs_job', 'cron_job_runs', ['job_id', 'started_at'])


def downgrade() -> None:
    op.drop_index('idx_cron_job_runs_job', table_name='cron_job_runs')
    op.drop_table('cron_job_runs')
