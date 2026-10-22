"""Allow a scheduled task to override its agent model."""

from alembic import op

revision = "b4d5e6f7a8b9"
down_revision = "a3c4d5e6f7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE cron_jobs ADD COLUMN IF NOT EXISTS model_id UUID")


def downgrade():
    op.drop_column("cron_jobs", "model_id")
