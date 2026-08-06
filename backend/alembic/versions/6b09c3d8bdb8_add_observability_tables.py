"""add observability tables

Revision ID: 6b09c3d8bdb8
Revises: 3a4b5c6d7e8f
Create Date: 2026-08-06 22:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6b09c3d8bdb8'
down_revision: Union[str, None] = '3a4b5c6d7e8f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 观测明细表/预聚合表（llm_call_logs / tool_call_logs / agent_trace_logs /
    # tool_usage_daily / performance_daily）由 init_db 的 create_all 自动创建，
    # 这里只做已有表的结构变更。
    op.add_column(
        'token_usage_daily',
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False, server_default='0', comment='缓存命中令牌数'),
    )
    op.add_column(
        'token_usage_daily',
        sa.Column('cache_creation_tokens', sa.Integer(), nullable=False, server_default='0', comment='缓存写入令牌数'),
    )


def downgrade() -> None:
    op.drop_column('token_usage_daily', 'cache_creation_tokens')
    op.drop_column('token_usage_daily', 'cache_read_tokens')
