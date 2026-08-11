"""add_daily_memories

Revision ID: f60ae3d51abf
Revises: 8d2f4a6b7c9e
Create Date: 2026-08-10 20:03:05.513284
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f60ae3d51abf'
down_revision: Union[str, None] = '8d2f4a6b7c9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The app runs Base.metadata.create_all on startup (db/connection.py), so the
    # table may already exist on databases that were never alembic-managed.
    bind = op.get_bind()
    if 'daily_memories' in sa.inspect(bind).get_table_names():
        return

    op.create_table(
        'daily_memories',
        sa.Column('id', sa.UUID(), nullable=False, comment='主键ID'),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='关联用户ID'),
        sa.Column('date', sa.Date(), nullable=False, comment='记忆所属日期(用户本地日,东八区)'),
        sa.Column('content', sa.Text(), nullable=False, comment='当日记忆正文(Markdown)'),
        sa.Column('highlights', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]', comment='结构化要点(JSON): [{type, text}]'),
        sa.Column('source_session_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]', comment='贡献的会话ID列表(JSON)'),
        sa.Column('search_vec', sa.Text(), nullable=True, comment='搜索向量(用于pg_trgm检索)'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'date', name='uq_daily_memories_user_date'),
        comment='每日记忆表(一人一天一条)',
    )
    op.create_index('idx_daily_memories_user_date', 'daily_memories', ['user_id', 'date'])


def downgrade() -> None:
    bind = op.get_bind()
    if 'daily_memories' not in sa.inspect(bind).get_table_names():
        return
    op.drop_index('idx_daily_memories_user_date', table_name='daily_memories')
    op.drop_table('daily_memories')
