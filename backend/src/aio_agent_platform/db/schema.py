"""Schema ownership checks shared by startup and explicit Alembic migrations."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

# These tables require an explicit, historically reliable execution tenant.
_TENANT_TABLES = (
    "users",
    "knowledge_bases",
    "mcp_servers",
    "agents",
    "user_profiles",
    "portrait_versions",
    "cron_jobs",
    "cron_job_runs",
    "memories",
    "daily_memories",
    "llm_providers",
    "llm_models",
    "channel_configs",
    "channel_bindings",
    "channel_bind_codes",
)


def database_schema_state(conn: Connection, heads: set[str]) -> str:
    tables = set(inspect(conn).get_table_names())
    if "alembic_version" in tables:
        revisions = set(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
        if revisions:
            return "current" if revisions == heads else "behind"
        tables.remove("alembic_version")
    return "legacy" if tables else "empty"


def require_known_tenant_ownership(conn: Connection) -> None:
    """Never run historical backfills over rows without explicit tenant ownership."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for table in _TENANT_TABLES:
        if table not in tables:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        condition = " WHERE tenant_id IS NULL" if "tenant_id" in columns else ""
        # Identifiers are fixed in the allowlist above.
        ambiguous = conn.scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}"{condition})'))
        if ambiguous:
            raise RuntimeError(
                f"{table} 存在无法确定租户的历史记录，已停止迁移。"
                "请先备份并单独审核所属对象，显式处理归属后再迁移；"
                "禁止按用户当前租户或公共租户自动回填。"
            )
