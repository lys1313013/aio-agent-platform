"""add_tenant_id_to_memories

Revision ID: b7e91c2a4d83
Revises: f60ae3d51abf
Create Date: 2026-08-11 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7e91c2a4d83'
down_revision: str | None = 'f60ae3d51abf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TENANT_ID = '00000000-0000-0000-0000-000000000001'
TABLES = ('memories', 'daily_memories')


def upgrade() -> None:
    # 与 db/connection.py 的 create_all bootstrap 路径共存:列可能已存在
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    for table in TABLES:
        if table not in existing_tables:
            continue
        cols = {c['name'] for c in insp.get_columns(table)}
        if 'tenant_id' not in cols:
            op.add_column(
                table,
                sa.Column('tenant_id', sa.UUID(), nullable=True, comment='所属租户ID'),
            )
        # 从属用户回填,孤儿记录(用户已删)回退默认租户
        op.execute(
            f"UPDATE {table} m SET tenant_id = u.tenant_id"
            " FROM users u WHERE m.user_id = u.id AND m.tenant_id IS NULL"
        )
        op.execute(
            f"UPDATE {table} SET tenant_id = '{DEFAULT_TENANT_ID}'"
            " WHERE tenant_id IS NULL"
        )
        op.alter_column(table, 'tenant_id', nullable=False)
        op.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant ON {table} (tenant_id)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_tables = set(insp.get_table_names())

    for table in TABLES:
        if table not in existing_tables:
            continue
        cols = {c['name'] for c in insp.get_columns(table)}
        if 'tenant_id' not in cols:
            continue
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_tenant")
        op.drop_column(table, 'tenant_id')
