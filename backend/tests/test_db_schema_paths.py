"""Real PostgreSQL checks for new, upgraded and legacy-bootstrap schemas."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aio_agent_platform.db.connection import _local_alembic_heads
from aio_agent_platform.db.models import Base
from aio_agent_platform.db.schema import database_schema_state
from alembic import command

pytestmark = pytest.mark.postgres
PREVIOUS_HEAD = "b4d5e6f7a8b9"
RLS_TABLES = (
    "chat_runs",
    "chat_run_events",
    "memory_changes",
    "memory_versions",
    "memory_organize_plans",
)


def migrate(connection, revision="head"):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


async def create_schema(conn, prefix):
    schema = f"{prefix}_{uuid4().hex}"
    await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    return schema


async def bootstrap_snapshot(conn):
    # Simulate the previous known bootstrap head, including its missing RLS.
    await conn.run_sync(Base.metadata.create_all)
    await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    await conn.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": PREVIOUS_HEAD})


async def snapshot(conn, schema):
    queries = {
        "columns": """SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
            a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname=:schema AND c.relkind='r' AND c.relname<>'alembic_version'
            ORDER BY c.relname,a.attname""",
        "indexes": """SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname=:schema AND tablename<>'alembic_version'
            ORDER BY tablename,indexname""",
        "constraints": """SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid)
            FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname=:schema AND t.relname<>'alembic_version'
            ORDER BY t.relname,c.conname""",
        "rls": """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=:schema AND c.relkind='r' AND c.relname<>'alembic_version'
            ORDER BY c.relname""",
        "policies": """SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
            FROM pg_policies WHERE schemaname=:schema ORDER BY tablename,policyname""",
    }
    result = {}
    for name, query in queries.items():
        rows = (await conn.execute(text(query), {"schema": schema})).all()
        result[name] = [
            tuple(
                value.replace(f"{schema}.", "") if isinstance(value, str) else value
                for value in row
            )
            for row in rows
        ]
    return result


async def test_fresh_upgrade_and_bootstrap_repair_have_identical_schema(engine):
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            snapshots = []
            for mode in ("fresh", "upgrade", "bootstrap"):
                schema = await create_schema(conn, mode)
                if mode == "upgrade":
                    await conn.run_sync(migrate, PREVIOUS_HEAD)
                elif mode == "bootstrap":
                    await bootstrap_snapshot(conn)
                await conn.run_sync(migrate)
                assert await conn.run_sync(database_schema_state, _local_alembic_heads()) == "current"
                snapshots.append(await snapshot(conn, schema))
            assert snapshots[0] == snapshots[1] == snapshots[2]
        finally:
            await transaction.rollback()


@pytest.mark.parametrize("bootstrap", [False, True])
async def test_rls_is_enforced_for_non_owner_role(engine, bootstrap):
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            schema = await create_schema(conn, "rls")
            if bootstrap:
                await bootstrap_snapshot(conn)
            else:
                await conn.run_sync(migrate)
            users = [uuid4(), uuid4()]
            for user_id in users:
                rows = {
                    "chat_runs": {
                        "session_id": uuid4(),
                        "user_id": user_id,
                        "assistant_message_id": uuid4(),
                        "owner": "test",
                        "request": {},
                    },
                    "chat_run_events": {
                        "run_id": uuid4(),
                        "sequence": 1,
                        "user_id": user_id,
                        "payload": {},
                    },
                    "memory_changes": {
                        "user_id": user_id,
                        "kind": "test",
                        "before": {},
                        "after": {},
                    },
                    "memory_versions": {
                        "memory_id": uuid4(),
                        "version": 1,
                        "user_id": user_id,
                        "kind": "test",
                        "snapshot": {},
                    },
                    "memory_organize_plans": {
                        "user_id": user_id,
                        "layer": "L1",
                        "groups": [],
                        "originals": {},
                    },
                }
                for table, row in rows.items():
                    await conn.execute(Base.metadata.tables[table].insert().values(**row))
            if bootstrap:
                # Existing data survives the repair; only the missing policies change.
                await conn.run_sync(migrate)
            role = f"schema_reader_{uuid4().hex}"
            await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS'))
            await conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            await conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"'))
            await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            for user_id in users:
                await conn.execute(
                    text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": str(user_id)},
                )
                for table in RLS_TABLES:
                    visible = (
                        (await conn.execute(text(f'SELECT user_id FROM "{table}"'))).scalars().all()
                    )
                    assert visible == [user_id]
            await conn.execute(text("SELECT set_config('app.current_user_id', '', true)"))
            for table in RLS_TABLES:
                assert await conn.scalar(text(f'SELECT count(*) FROM "{table}"')) == 0
        finally:
            await transaction.rollback()


async def test_unversioned_partial_database_is_rejected(engine):
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await create_schema(conn, "unknown")
            await conn.execute(text("CREATE TABLE cron_jobs (id INTEGER)"))
            assert await conn.run_sync(database_schema_state, _local_alembic_heads()) == "legacy"
            with pytest.raises(RuntimeError, match="未标记版本"):
                await conn.run_sync(migrate)
            assert await conn.scalar(text("SELECT to_regclass('users')")) is None
        finally:
            await transaction.rollback()


async def test_explicit_alembic_rejects_unknown_historical_ownership(engine):
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await create_schema(conn, "ownership")
            await bootstrap_snapshot(conn)
            # Legacy nullable ownership must fail before any migration changes the schema.
            await conn.execute(text("ALTER TABLE cron_jobs ALTER COLUMN tenant_id DROP NOT NULL"))
            await conn.execute(
                Base.metadata.tables["cron_jobs"]
                .insert()
                .values(
                    user_id=uuid4(),
                    tenant_id=None,
                    name="historical",
                    cron_expr="0 0 * * *",
                    message="do nothing",
                    is_active=False,
                )
            )
            with pytest.raises(RuntimeError, match="无法确定租户"):
                await conn.run_sync(migrate)
            assert (
                await conn.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_HEAD
            )
            assert await conn.scalar(text("SELECT tenant_id FROM cron_jobs")) is None
        finally:
            await transaction.rollback()


async def test_schema_repair_does_not_fabricate_historical_timestamps(engine):
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await create_schema(conn, "timestamps")
            await conn.run_sync(migrate, PREVIOUS_HEAD)
            await conn.execute(
                text("""INSERT INTO memory_changes
                (id,user_id,kind,before,after,created_at)
                VALUES (:id,:uid,'test','{}','{}',NULL)"""),
                {"id": uuid4(), "uid": uuid4()},
            )
            # PostgreSQL rolls the failed repair back to the savepoint, keeping
            # both the historical NULL and its old revision unchanged.
            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.run_sync(migrate)
            assert (
                await conn.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_HEAD
            )
            assert await conn.scalar(text("SELECT created_at FROM memory_changes")) is None
        finally:
            await transaction.rollback()
