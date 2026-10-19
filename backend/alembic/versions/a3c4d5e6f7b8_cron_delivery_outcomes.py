"""Separate scheduled execution results from notification outcomes."""

from alembic import op

revision = "a3c4d5e6f7b8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    # Bootstrap databases may already have these columns from startup migrations.
    op.execute(
        "ALTER TABLE cron_job_runs ADD COLUMN IF NOT EXISTS "
        "delivery_status VARCHAR(32) NOT NULL DEFAULT 'unknown'"
    )
    op.execute("ALTER TABLE cron_job_runs ADD COLUMN IF NOT EXISTS delivery_reason TEXT")
    op.execute("ALTER TABLE cron_job_runs ADD COLUMN IF NOT EXISTS delivery_error TEXT")


def downgrade():
    for name in ("delivery_error", "delivery_reason", "delivery_status"):
        op.drop_column("cron_job_runs", name)
