"""extend sessions.source comment with trigger sources

会话来源枚举扩展：在原有 chat/pet 基础上追加 api(接口触发)/cron(定时任务)/
feishu/wecom/wecom_bot(渠道触发)。仅更新列 comment，无数据与结构变更，
历史 chat 数据即界面触发，无需回填。

Revision ID: e9d8c7b6a5f4
Revises: c1d2e3f4a5b6
Create Date: 2026-08-16
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e9d8c7b6a5f4'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'sessions',
        'source',
        existing_type=sa.String(16),
        existing_nullable=False,
        comment="会话来源: chat(界面触发)/pet(宠物闲聊)/api(接口触发)/cron(定时任务)/feishu(飞书)/wecom(企微)/wecom_bot(企微机器人)",
    )


def downgrade() -> None:
    op.alter_column(
        'sessions',
        'source',
        existing_type=sa.String(16),
        existing_nullable=False,
        comment="会话来源: chat(常规)/pet(宠物闲聊)",
    )
