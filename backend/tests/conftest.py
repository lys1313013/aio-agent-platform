"""Test fixtures for agent tests."""

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


# Never inherit the application's DATABASE_URL or alembic.ini. Even tests that
# import services directly must not connect to a developer's business database.
def _guard_test_db(database_url: str) -> None:
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    if (
        parsed.get_backend_name() != "postgresql"
        or (parsed.host or "").lower() not in {"localhost", "127.0.0.1", "::1"}
        or not (parsed.database or "").endswith("_test")
        or parsed.query
    ):
        raise RuntimeError(
            "TEST_DATABASE_URL must point to a loopback PostgreSQL database "
            "ending in _test, without connection query overrides"
        )


if _test_database_url := os.environ.get("TEST_DATABASE_URL"):
    _guard_test_db(_test_database_url)
os.environ["DATABASE_URL"] = _test_database_url or (
    "postgresql+asyncpg://test:test@127.0.0.1:1/unconfigured_test"
)
os.environ.setdefault("JWT_SECRET", "ci-test-secret-key-0123456789abcdef")
os.environ.setdefault("STORAGE_ACCESS_KEY", "ci-test-access-key")
os.environ.setdefault("STORAGE_SECRET_KEY", "ci-test-secret-key")

# Application imports must follow the test connection override above.
from aio_agent_platform.db.models import Base  # noqa: E402
from aio_agent_platform.interface.api import app  # noqa: E402


def get_test_database_url():
    """Only explicitly opted-in disposable test databases may be used."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        _guard_test_db(url)
    return url


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(request):
    """Create the test database engine and schema once per session.

    Skips DB-dependent tests when no database URL is configured or the
    database is unreachable (e.g. CI without a PostgreSQL service).
    """
    database_url = get_test_database_url()
    if not database_url and request.config.getoption("--run-postgres"):
        pytest.fail("PostgreSQL integration tests require TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL not configured")
    _guard_test_db(database_url)
    engine = create_async_engine(database_url, echo=False, poolclass=NullPool)
    try:
        async with asyncio.timeout(5):
            async with engine.connect():
                pass
    except Exception:
        await engine.dispose()
        if request.config.getoption("--run-postgres"):
            pytest.fail("PostgreSQL integration test database is unreachable")
        pytest.skip("disposable test database not reachable")
    # Exercise the production migration chain; create_all would hide missing DDL/RLS.
    from pathlib import Path

    from alembic.config import Config

    from alembic import command

    def migrate(connection):
        backend_dir = Path(__file__).resolve().parents[1]
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "alembic"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    async with engine.begin() as conn:
        await conn.run_sync(migrate)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession]:
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
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
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


def pytest_addoption(parser):
    parser.addoption("--run-postgres", action="store_true", default=False,
                     help="Include explicitly marked PostgreSQL integration tests")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-postgres"):
        return
    selected, deferred = [], []
    for item in items:
        (deferred if item.get_closest_marker("postgres") else selected).append(item)
    items[:] = selected
    if deferred:
        config.hook.pytest_deselected(items=deferred)
