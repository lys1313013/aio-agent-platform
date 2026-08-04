"""add_pet_agent_and_actions

Revision ID: a1b2c3d4e5f6
Revises: e6f8a1b4c5d6
Create Date: 2026-08-04 12:00:00.000000

宠物绑定智能体 + 动作系统：
- pet_packages: default_agent_id(包级默认人设), actions(动作目录)
- user_pets: agent_id(实例级绑定), action_aliases(实例级动作名覆盖), state_mapping(实例级状态映射覆盖)
- sessions: source(会话来源 chat/pet)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e6f8a1b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pet_packages', sa.Column(
        'default_agent_id', postgresql.UUID(as_uuid=True), nullable=True,
        comment='包级默认人设智能体ID(领养者未绑定时生效)',
    ))
    op.add_column('pet_packages', sa.Column(
        'actions', postgresql.JSONB(), nullable=True,
        comment='动作目录 {row: {name, state}}，上传时自动生成，创建人可改',
    ))

    op.add_column('user_pets', sa.Column(
        'agent_id', postgresql.UUID(as_uuid=True), nullable=True,
        comment='实例级绑定智能体ID(优先级高于包级默认)',
    ))
    op.add_column('user_pets', sa.Column(
        'action_aliases', postgresql.JSONB(), nullable=True,
        comment='实例级动作名覆盖 {row: name}，优先级高于包级动作目录',
    ))
    op.add_column('user_pets', sa.Column(
        'state_mapping', postgresql.JSONB(), nullable=True,
        comment='实例级状态→行 覆盖 {state: row}，优先级高于包级 row_mapping',
    ))

    op.add_column('sessions', sa.Column(
        'source', sa.String(16), nullable=False, server_default='chat',
        comment='会话来源: chat(常规)/pet(宠物闲聊)',
    ))
    op.add_column('sessions', sa.Column(
        'pet_id', postgresql.UUID(as_uuid=True), nullable=True,
        comment='宠物闲聊会话关联的用户宠物ID',
    ))


def downgrade() -> None:
    op.drop_column('sessions', 'pet_id')
    op.drop_column('sessions', 'source')
    op.drop_column('user_pets', 'state_mapping')
    op.drop_column('user_pets', 'action_aliases')
    op.drop_column('user_pets', 'agent_id')
    op.drop_column('pet_packages', 'actions')
    op.drop_column('pet_packages', 'default_agent_id')
