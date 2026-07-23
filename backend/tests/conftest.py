"""Test fixtures for agent tests."""

import asyncio
import os
from collections.abc import AsyncGenerator
import configparser

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


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def engine():
    """Create a test database engine for each test.

    Skips the test when no database URL is configured or the database
    is unreachable (e.g. CI without a PostgreSQL service).
    """
    database_url = get_test_database_url()
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    engine = create_async_engine(
        database_url,
        echo=False,
        pool_size=1,
        max_overflow=0,
    )
    try:
        async with asyncio.timeout(5):
            async with engine.connect():
                pass
    except Exception:
        await engine.dispose()
        pytest.skip(f"test database not reachable: {database_url}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for each test."""
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
