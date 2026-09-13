"""Restore channel identity scope without guessing legacy binding ownership.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_bindings", sa.Column("channel_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    # Only restore a unique source supported by a successfully consumed code.
    # Unknown/ambiguous legacy rows remain intact with NULL channel_id and are
    # excluded from runtime resolution. Users can bind again to a chosen channel.
    op.execute("""
        UPDATE channel_bindings b SET channel_id = source.channel_id
        FROM (
            SELECT b.id, (array_agg(DISTINCT c.channel_id))[1] AS channel_id
            FROM channel_bindings b
            JOIN channel_bind_codes c ON c.tenant_id = b.tenant_id
                AND c.external_id = b.external_id AND c.used_by = b.user_id
                AND c.used_at IS NOT NULL
            JOIN channel_configs ch ON ch.id = c.channel_id AND ch.tenant_id = b.tenant_id
            GROUP BY b.id HAVING count(DISTINCT c.channel_id) = 1
        ) source WHERE b.id = source.id
    """)
    op.drop_constraint("uq_channel_binding_external", "channel_bindings", type_="unique")
    op.create_unique_constraint(
        "uq_channel_binding_external",
        "channel_bindings",
        ["tenant_id", "channel_id", "external_id"],
    )
    op.create_index("idx_channel_bindings_channel", "channel_bindings", ["channel_id"])


def downgrade() -> None:
    # Never discard channel-specific identities to make the old uniqueness fit.
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text("""
        SELECT 1 FROM channel_bindings GROUP BY tenant_id, external_id
        HAVING count(*) > 1 LIMIT 1
    """)
    ).first()
    if duplicates:
        raise RuntimeError("Cannot downgrade channel bindings without losing identities")
    op.drop_index("idx_channel_bindings_channel", table_name="channel_bindings")
    op.drop_constraint("uq_channel_binding_external", "channel_bindings", type_="unique")
    op.create_unique_constraint(
        "uq_channel_binding_external", "channel_bindings", ["tenant_id", "external_id"]
    )
    op.drop_column("channel_bindings", "channel_id")
