"""make user_profiles primary key composite (user_id, tenant_id)

Revision ID: b2c3d4e5f7a8
Revises: 111deac801c6
Create Date: 2026-08-08

"""
from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f7a8"
down_revision: str | None = "111deac801c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("user_profiles_pkey", "user_profiles", type_="primary")
    op.create_primary_key(
        "user_profiles_pkey", "user_profiles", ["user_id", "tenant_id"]
    )


def downgrade() -> None:
    op.drop_constraint("user_profiles_pkey", "user_profiles", type_="primary")
    op.create_primary_key("user_profiles_pkey", "user_profiles", ["user_id"])
