"""add_pet_tables

Revision ID: b7f1a2c3d4e5
Revises: 6fea3105d1cf
Create Date: 2026-08-02 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7f1a2c3d4e5'
down_revision: Union[str, None] = '6fea3105d1cf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pet_packages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()'), comment='主键ID'),
        sa.Column('name', sa.String(128), nullable=False, comment='宠物标识(pet.json 的 id)'),
        sa.Column('display_name', sa.String(256), nullable=False, comment='展示名称'),
        sa.Column('description', sa.Text(), nullable=True, comment='描述'),
        sa.Column('kind', sa.String(32), nullable=True, comment='pet.json 的 kind(可选)'),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False, comment='创建人ID'),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False, comment='归属租户ID'),
        sa.Column('visibility', sa.String(16), nullable=False, server_default='private', comment='可见性: private/tenant/public/official'),
        sa.Column('status', sa.String(16), nullable=False, server_default='active', comment='状态: active/taken_down'),
        sa.Column('manifest', postgresql.JSONB(), nullable=False, comment='pet.json 原文(JSON)'),
        sa.Column('row_mapping', postgresql.JSONB(), nullable=False, server_default='{}', comment='平台状态→精灵图行号 映射'),
        sa.Column('frame_width', sa.Integer(), nullable=False, comment='帧宽(px)'),
        sa.Column('frame_height', sa.Integer(), nullable=False, comment='帧高(px)'),
        sa.Column('col_count', sa.Integer(), nullable=False, comment='列数(每行帧数)'),
        sa.Column('row_count', sa.Integer(), nullable=False, comment='行数(动画数)'),
        sa.Column('spritesheet_key', sa.String(512), nullable=False, comment='精灵图对象存储key'),
        sa.Column('package_key', sa.String(512), nullable=False, comment='原始zip对象存储key(导出用)'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        comment='宠物包表(Codex格式兼容)',
    )
    op.create_index('idx_pet_packages_tenant_visibility', 'pet_packages', ['tenant_id', 'visibility'])
    op.create_index('idx_pet_packages_owner', 'pet_packages', ['owner_id'])

    op.create_table(
        'user_pets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('uuid_generate_v4()'), comment='主键ID'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False, comment='用户ID'),
        sa.Column('package_id', postgresql.UUID(as_uuid=True), nullable=False, comment='宠物包ID'),
        sa.Column('level', sa.Integer(), nullable=False, server_default='1', comment='等级'),
        sa.Column('exp', sa.Integer(), nullable=False, server_default='0', comment='累计经验'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false', comment='是否当前激活'),
        sa.Column('adopted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='领养时间'),
        sa.UniqueConstraint('user_id', 'package_id', name='uq_user_pets_user_package'),
        comment='用户宠物表',
    )
    op.create_index('idx_user_pets_user', 'user_pets', ['user_id'])
    op.create_index(
        'uq_user_pets_active', 'user_pets', ['user_id'], unique=True,
        postgresql_where=sa.text('is_active = true'),
    )

    op.create_table(
        'pet_exp_logs',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True, comment='主键ID'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False, comment='用户ID'),
        sa.Column('pet_id', postgresql.UUID(as_uuid=True), nullable=False, comment='用户宠物ID'),
        sa.Column('delta', sa.Integer(), nullable=False, comment='经验变动值'),
        sa.Column('reason', sa.String(32), nullable=False, comment='来源: task_complete/tool_call/daily_first/interact'),
        sa.Column('ref_id', sa.String(64), nullable=True, comment='关联ID(如session_id)'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        comment='宠物经验流水表',
    )
    op.create_index('idx_pet_exp_logs_user_date', 'pet_exp_logs', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('idx_pet_exp_logs_user_date', table_name='pet_exp_logs')
    op.drop_table('pet_exp_logs')
    op.drop_index('uq_user_pets_active', table_name='user_pets')
    op.drop_index('idx_user_pets_user', table_name='user_pets')
    op.drop_table('user_pets')
    op.drop_index('idx_pet_packages_owner', table_name='pet_packages')
    op.drop_index('idx_pet_packages_tenant_visibility', table_name='pet_packages')
    op.drop_table('pet_packages')
