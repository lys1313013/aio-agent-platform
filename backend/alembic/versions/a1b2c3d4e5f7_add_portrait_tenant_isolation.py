"""add tenant_id to user_profiles and portrait_versions

Revision ID: a1b2c3d4e5f7
Revises: f3b0c1d2e4a5
Create Date: 2026-08-08

"""
from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f3b0c1d2e4a5"
branch_labels = None
depends_on = None

DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
    )
    op.execute(
        """UPDATE user_profiles up SET tenant_id = u.tenant_id
           FROM users u WHERE up.user_id = u.id AND up.tenant_id IS NULL"""
    )
    op.execute(
        f"UPDATE user_profiles SET tenant_id = '{DEFAULT_TENANT_ID}' WHERE tenant_id IS NULL"
    )
    op.alter_column("user_profiles", "tenant_id", nullable=False)
    op.create_index("idx_user_profiles_tenant", "user_profiles", ["tenant_id"])

    op.add_column(
        "portrait_versions",
        sa.Column("tenant_id", sa.UUID(), nullable=True),
    )
    op.execute(
        """UPDATE portrait_versions pv SET tenant_id = u.tenant_id
           FROM users u WHERE pv.user_id = u.id AND pv.tenant_id IS NULL"""
    )
    op.execute(
        f"UPDATE portrait_versions SET tenant_id = '{DEFAULT_TENANT_ID}' WHERE tenant_id IS NULL"
    )
    op.alter_column("portrait_versions", "tenant_id", nullable=False)
    op.create_index("idx_portrait_versions_tenant", "portrait_versions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("idx_portrait_versions_tenant", table_name="portrait_versions")
    op.drop_column("portrait_versions", "tenant_id")
    op.drop_index("idx_user_profiles_tenant", table_name="user_profiles")
    op.drop_column("user_profiles", "tenant_id")
