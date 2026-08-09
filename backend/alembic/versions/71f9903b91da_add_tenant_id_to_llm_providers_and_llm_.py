"""add tenant_id to llm_providers and llm_models

Revision ID: 71f9903b91da
Revises: b2c3d4e5f7a8
Create Date: 2026-08-09 00:00:19.709313
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71f9903b91da'
down_revision: Union[str, None] = 'b2c3d4e5f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT = '00000000-0000-0000-0000-000000000001'


def upgrade() -> None:
    # llm_providers
    op.add_column('llm_providers', sa.Column('tenant_id', sa.UUID(), nullable=True))
    op.execute(f"UPDATE llm_providers SET tenant_id = '{DEFAULT_TENANT}' WHERE tenant_id IS NULL")
    op.alter_column('llm_providers', 'tenant_id', nullable=False)
    op.create_index('idx_llm_providers_tenant', 'llm_providers', ['tenant_id'])

    # llm_models
    op.add_column('llm_models', sa.Column('tenant_id', sa.UUID(), nullable=True))
    op.execute(f"UPDATE llm_models SET tenant_id = '{DEFAULT_TENANT}' WHERE tenant_id IS NULL")
    op.alter_column('llm_models', 'tenant_id', nullable=False)
    op.drop_index('idx_llm_models_default', table_name='llm_models')
    op.create_index(
        'idx_llm_models_tenant_default', 'llm_models',
        ['tenant_id', 'is_default'],
        postgresql_where=sa.text('is_default'),
    )


def downgrade() -> None:
    op.drop_index('idx_llm_models_tenant_default', table_name='llm_models')
    op.create_index(
        'idx_llm_models_default', 'llm_models',
        ['is_default'],
        postgresql_where=sa.text('true'),
    )
    op.drop_column('llm_models', 'tenant_id')
    op.drop_index('idx_llm_providers_tenant', table_name='llm_providers')
    op.drop_column('llm_providers', 'tenant_id')
