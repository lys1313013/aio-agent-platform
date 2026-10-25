"""Isolated authentication regressions; no database or external service required."""

import asyncio
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aio_agent_platform.auth import password
from aio_agent_platform.auth.routes import LoginRequest, RegisterRequest
from aio_agent_platform.core.config import JWTSettings
from aio_agent_platform.interface.routes.settings import PasswordUpdate
from aio_agent_platform.interface.routes.users import UserAdminCreate, UserAdminUpdate


@pytest.mark.parametrize("value", ["a" * 73, "密" * 25])
@pytest.mark.parametrize("model,data,key", [
    (RegisterRequest, {"username": "tester", "email": "test@example.com"}, "password"),
    (LoginRequest, {"username_or_email": "tester"}, "password"),
    (UserAdminCreate, {"username": "tester", "email": "test@example.com", "tenant_ids": [uuid4()]}, "password"),
    (UserAdminUpdate, {}, "password"),
    (PasswordUpdate, {"current_password": "old-password"}, "new_password"),
    (PasswordUpdate, {"new_password": "new-password"}, "current_password"),
])
def test_password_schemas_reject_more_than_72_utf8_bytes(value, model, data, key):
    with pytest.raises(ValidationError):
        model(**data, **{key: value})


def test_unicode_password_boundary_and_no_silent_truncation():
    value = "密" * 24
    assert RegisterRequest(username="tester", email="test@example.com", password=value).password == value
    hashed = password.hash_password(value)
    assert password.verify_password(value, hashed)
    assert not password.verify_password(value + "x", hashed)
    with pytest.raises(ValueError, match="72"):
        password.hash_password(value + "x")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["hash", "verify"])
async def test_bcrypt_runs_outside_event_loop(monkeypatch, operation):
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()

    def blocking(*args):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(15)
        return "hashed" if operation == "hash" else True

    monkeypatch.setattr(password, f"{operation}_password", blocking)
    coroutine = password.async_hash_password("password") if operation == "hash" else password.async_verify_password("password", "hashed")
    task = asyncio.create_task(coroutine)
    try:
        async with asyncio.timeout(10):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        # This code can execute while bcrypt is still blocked in its worker.
        assert not task.done()
    finally:
        release.set()
    assert await task == ("hashed" if operation == "hash" else True)


@pytest.fixture
def clean_jwt_environment(monkeypatch):
    for name in ("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "ACCESS_TOKEN_EXPIRE_MINUTES", "JWT_REFRESH_TOKEN_EXPIRE_DAYS", "REFRESH_TOKEN_EXPIRE_DAYS"):
        monkeypatch.delenv(name, raising=False)


def test_jwt_safe_default(clean_jwt_environment):
    assert JWTSettings().access_token_expire_minutes == 15


def test_jwt_legacy_env_supported(clean_jwt_environment, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "20")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "2")
    assert JWTSettings().access_token_expire_minutes == 20
    assert JWTSettings().refresh_token_expire_days == 2


def test_jwt_prefixed_env_takes_priority(clean_jwt_environment, monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "100")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "10")
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "10")
    monkeypatch.setenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "3")
    assert JWTSettings().access_token_expire_minutes == 10
    assert JWTSettings().refresh_token_expire_days == 3
