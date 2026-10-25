"""Destructive test fixtures must never fall back to application databases."""

from unittest.mock import MagicMock

import pytest

from tests import conftest


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://user:password@localhost:5435/aio_agent_platform",
    "postgresql+asyncpg://user:password@example.com/aio_test",
    "postgresql+asyncpg://user:password@localhost/postgres",
    "postgresql+asyncpg://user:password@localhost/aio_test?host=example.com",
    "sqlite+aiosqlite:///aio_test",
])
def test_rejects_unsafe_database_targets(url):
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        conftest._guard_test_db(url)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_accepts_explicit_loopback_test_database(host):
    conftest._guard_test_db(f"postgresql+asyncpg://test:test@{host}/aio_test")


def test_business_database_url_is_never_a_fallback(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost/business")
    assert conftest.get_test_database_url() is None


async def test_guard_runs_before_engine_creation_or_migration(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://user:password@localhost/business")
    create_engine = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(conftest, "create_async_engine", create_engine)
    request = MagicMock()
    generator = conftest.engine.__wrapped__(request)
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL"):
        await anext(generator)
    create_engine.assert_not_called()
