"""Test fixtures for agent tests."""

import asyncio
import configparser
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Provide required settings so app modules import cleanly in environments
# without a .env (e.g. CI). DB-dependent tests skip when unreachable.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://agent_user:changeme@localhost:5435/aio_agent_platform"
)
os.environ.setdefault("JWT_SECRET", "ci-test-secret-key-0123456789abcdef")
os.environ.setdefault("STORAGE_ACCESS_KEY", "ci-test-access-key")
os.environ.setdefault("STORAGE_SECRET_KEY", "ci-test-secret-key")

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from aio_agent_platform.db.models import Base
from aio_agent_platform.interface.api import app


def get_test_database_url():
    """Get database URL from DATABASE_URL env var or alembic.ini."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        config = configparser.ConfigParser()
        config.read('alembic.ini')
        url = config.get('alembic', 'sqlalchemy.url', fallback=None)
        if url:
            return url
    except Exception:
        pass
    return None


def _guard_test_db(database_url: str) -> None:
    """拒绝在非回环地址上执行 TRUNCATE，防止误伤远程生产库。"""
    host = (make_url(database_url).host or "").lower()
    if host not in ("localhost", "127.0.0.1"):
        raise RuntimeError(
            f"refusing to truncate non-loopback test database host: {host}"
        )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    """Create the test database engine and schema once per session.

    Skips DB-dependent tests when no database URL is configured or the
    database is unreachable (e.g. CI without a PostgreSQL service).
    """
    database_url = get_test_database_url()
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    try:
        async with asyncio.timeout(5):
            async with engine.connect():
                pass
    except Exception:
        await engine.dispose()
        pytest.skip(f"test database not reachable: {database_url}")
    async with engine.begin() as conn:
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for each test, with all tables truncated first."""
    _guard_test_db(engine.url.render_as_string(hide_password=True))
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables}"))
    async_session = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an HTTP client for testing."""
    async def override_get_db():
        yield db_session

    from aio_agent_platform.db.connection import get_db
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as test_client:
        yield test_client

    app.dependency_overrides.clear()
