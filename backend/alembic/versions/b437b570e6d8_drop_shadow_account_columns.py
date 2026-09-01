"""drop shadow account columns

移除影子账号遗留字段：users.is_shadow、channel_bindings.bind_type。
渠道绑定新流程不再创建影子账号，存量绑定均为已绑定真实账号。

Revision ID: b437b570e6d8
Revises: e9d8c7b6a5f4
Create Date: 2026-09-01 16:38:43.706316
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b437b570e6d8'
down_revision: Union[str, None] = 'e9d8c7b6a5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('users', 'is_shadow')
    op.drop_column('channel_bindings', 'bind_type')


def downgrade() -> None:
    op.add_column(
        'channel_bindings',
        sa.Column(
            'bind_type',
            sa.String(length=16),
            nullable=False,
            server_default='bound',
            comment='绑定类型: shadow(影子账号) / bound(已关联真实账号)',
        ),
    )
    op.add_column(
        'users',
        sa.Column(
            'is_shadow',
            sa.Boolean(),
            nullable=False,
            server_default='false',
            comment='是否影子账号(由渠道自动创建，不可登录 Web 端)',
        ),
    )
