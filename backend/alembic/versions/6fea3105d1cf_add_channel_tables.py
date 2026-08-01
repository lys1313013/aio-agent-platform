"""add_channel_tables

Revision ID: 6fea3105d1cf
Revises:
Create Date: 2026-07-28 16:56:19.301784
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6fea3105d1cf'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_shadow column to users table
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

    # Create channel_configs table
    op.create_table(
        'channel_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False, comment='所属租户ID'),
        sa.Column('channel_type', sa.String(32), nullable=False, server_default='feishu', comment='渠道类型'),
        sa.Column('name', sa.String(128), nullable=False, comment='渠道显示名称'),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False, comment='绑定的 Agent ID'),
        sa.Column('app_id', sa.String(128), nullable=False, comment='飞书 App ID'),
        sa.Column('app_secret_encrypted', sa.Text(), nullable=False, comment='App Secret(加密存储)'),
        sa.Column('encrypt_key_encrypted', sa.Text(), nullable=True, comment='Event Encrypt Key(仅 webhook)'),
        sa.Column('verification_token_encrypted', sa.Text(), nullable=True, comment='Verification Token(仅 webhook)'),
        sa.Column('mode', sa.String(16), nullable=False, comment='连接模式: websocket/webhook'),
        sa.Column('status', sa.String(16), nullable=False, server_default='disabled', comment='状态'),
        sa.Column('channel_key', sa.String(64), nullable=False, unique=True, comment='Webhook URL 随机串'),
        sa.Column('tool_blacklist', postgresql.JSONB(), nullable=False, server_default='[]', comment='工具黑名单'),
        sa.Column('last_error', sa.Text(), nullable=True, comment='最近错误'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False, comment='创建人'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        comment='IM 渠道配置表',
    )
    op.create_index('idx_channel_configs_tenant', 'channel_configs', ['tenant_id'])
    op.create_index('idx_channel_configs_agent', 'channel_configs', ['agent_id'])

    # Create channel_bindings table
    op.create_table(
        'channel_bindings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=False, comment='渠道 ID'),
        sa.Column('external_id', sa.String(128), nullable=False, comment='外部用户 ID(飞书 open_id)'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False, comment='平台 user ID'),
        sa.Column('bind_type', sa.String(16), nullable=False, server_default='shadow', comment='绑定类型'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('channel_id', 'external_id', name='uq_channel_binding_external'),
        comment='渠道用户绑定表',
    )
    op.create_index('idx_channel_bindings_user', 'channel_bindings', ['user_id'])
    op.create_index('idx_channel_bindings_channel', 'channel_bindings', ['channel_id'])

    # Create channel_bind_codes table
    op.create_table(
        'channel_bind_codes',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('code', sa.String(8), nullable=False, comment='6 位数字绑定码'),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=False, comment='渠道 ID'),
        sa.Column('external_id', sa.String(128), nullable=False, comment='外部用户 ID'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, comment='过期时间'),
        sa.Column('used_by', postgresql.UUID(as_uuid=True), nullable=True, comment='消费该码的 user ID'),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True, comment='消费时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        comment='渠道绑定码表',
    )
    op.create_index('idx_bind_codes_code', 'channel_bind_codes', ['code'])
    op.create_index('idx_bind_codes_channel_external', 'channel_bind_codes', ['channel_id', 'external_id'])

    # Create channel_session_mappings table
    op.create_table(
        'channel_session_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=False, comment='渠道 ID'),
        sa.Column('chat_id', sa.String(128), nullable=False, comment='飞书 chat_id'),
        sa.Column('external_id', sa.String(128), nullable=False, comment='外部用户 ID'),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=False, comment='平台 session ID'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true', comment='是否活跃'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        comment='渠道会话映射表',
    )
    # Partial unique index for active mappings
    op.create_index(
        'uq_channel_session_active',
        'channel_session_mappings',
        ['channel_id', 'chat_id', 'external_id'],
        unique=True,
        postgresql_where=sa.text('is_active = true'),
    )
    op.create_index('idx_channel_session_session', 'channel_session_mappings', ['session_id'])


def downgrade() -> None:
    op.drop_table('channel_session_mappings')
    op.drop_table('channel_bind_codes')
    op.drop_table('channel_bindings')
    op.drop_table('channel_configs')
    op.drop_column('users', 'is_shadow')
