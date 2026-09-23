"""Skill mutation receipts, provenance and full version snapshots."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("provenance", "verification"):
        op.add_column("skills", sa.Column(name, postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("skill_versions", sa.Column("snapshot", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.create_table("skill_mutations",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", sa.String(256), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("skill_mutations")
    op.drop_column("skill_versions", "snapshot")
    op.drop_column("skills", "verification")
    op.drop_column("skills", "provenance")
