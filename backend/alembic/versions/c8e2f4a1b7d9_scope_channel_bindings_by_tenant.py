"""scope_channel_bindings_by_tenant

Channel bindings become tenant-scoped: an external user binds once per tenant
and the binding is shared by every channel in that tenant. Bind codes also
record their tenant so consumption can be validated without joining back to
channel_configs.

Revision ID: c8e2f4a1b7d9
Revises: b7f1a2c3d4e5
Create Date: 2026-08-03 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c8e2f4a1b7d9'
down_revision: Union[str, None] = 'b7f1a2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_TENANT = '00000000-0000-0000-0000-000000000001'


def upgrade() -> None:
    # --- channel_bind_codes: record tenant at issuance ---
    op.add_column(
        'channel_bind_codes',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True, comment='所属租户ID'),
    )
    op.execute(
        f"""
        UPDATE channel_bind_codes cbc
        SET tenant_id = COALESCE(cc.tenant_id, '{_DEFAULT_TENANT}'::uuid)
        FROM (SELECT id, tenant_id FROM channel_configs) cc
        WHERE cc.id = cbc.channel_id
        """
    )
    op.execute(
        f"""
        UPDATE channel_bind_codes
        SET tenant_id = '{_DEFAULT_TENANT}'::uuid
        WHERE tenant_id IS NULL
        """
    )
    op.alter_column('channel_bind_codes', 'tenant_id', nullable=False)

    # --- channel_bindings: re-key from channel to tenant ---
    op.add_column(
        'channel_bindings',
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=True, comment='所属租户ID'),
    )
    op.execute(
        f"""
        UPDATE channel_bindings cb
        SET tenant_id = COALESCE(cc.tenant_id, '{_DEFAULT_TENANT}'::uuid)
        FROM (SELECT id, tenant_id FROM channel_configs) cc
        WHERE cc.id = cb.channel_id
        """
    )
    op.execute(
        f"""
        UPDATE channel_bindings
        SET tenant_id = '{_DEFAULT_TENANT}'::uuid
        WHERE tenant_id IS NULL
        """
    )
    # Dedupe: one row per (tenant_id, external_id). Prefer bound over shadow,
    # then the most recently updated row.
    op.execute(
        """
        DELETE FROM channel_bindings a
        USING channel_bindings b
        WHERE a.tenant_id = b.tenant_id
          AND a.external_id = b.external_id
          AND a.id <> b.id
          AND (
              (a.bind_type <> 'bound' AND b.bind_type = 'bound')
              OR (a.bind_type = b.bind_type AND a.updated_at < b.updated_at)
              OR (a.bind_type = b.bind_type AND a.updated_at = b.updated_at AND a.id < b.id)
          )
        """
    )
    op.alter_column('channel_bindings', 'tenant_id', nullable=False)

    op.drop_constraint('uq_channel_binding_external', 'channel_bindings', type_='unique')
    op.drop_index('idx_channel_bindings_channel', table_name='channel_bindings')
    op.drop_column('channel_bindings', 'channel_id')
    op.create_unique_constraint(
        'uq_channel_binding_external', 'channel_bindings', ['tenant_id', 'external_id']
    )
    op.create_index('idx_channel_bindings_tenant', 'channel_bindings', ['tenant_id'])


def downgrade() -> None:
    op.drop_index('idx_channel_bindings_tenant', table_name='channel_bindings')
    op.drop_constraint('uq_channel_binding_external', 'channel_bindings', type_='unique')
    op.add_column(
        'channel_bindings',
        sa.Column('channel_id', postgresql.UUID(as_uuid=True), nullable=True, comment='渠道 ID'),
    )
    # Original channel mapping is unrecoverable; fall back to any channel of
    # the tenant so the column can be made NOT NULL again.
    op.execute(
        """
        UPDATE channel_bindings cb
        SET channel_id = (
            SELECT id FROM channel_configs cc
            WHERE cc.tenant_id = cb.tenant_id
            LIMIT 1
        )
        """
    )
    op.execute("DELETE FROM channel_bindings WHERE channel_id IS NULL")
    op.alter_column('channel_bindings', 'channel_id', nullable=False)
    op.create_unique_constraint(
        'uq_channel_binding_external', 'channel_bindings', ['channel_id', 'external_id']
    )
    op.create_index('idx_channel_bindings_channel', 'channel_bindings', ['channel_id'])
    op.drop_column('channel_bindings', 'tenant_id')

    op.drop_column('channel_bind_codes', 'tenant_id')
