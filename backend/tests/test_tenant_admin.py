"""Super administrator and tenant-management tests."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aio_agent_platform.auth.dependencies import require_admin, require_superadmin
from aio_agent_platform.interface.routes.tenants import (
    TenantCreate,
    TenantMemberAssign,
    _normalize_slug,
)
from aio_agent_platform.interface.routes.users import UserAdminCreate, UserAdminUpdate


@pytest.mark.asyncio
async def test_superadmin_satisfies_admin_and_superadmin_dependencies() -> None:
    user = SimpleNamespace(role="superadmin")
    assert await require_admin(user) is user
    assert await require_superadmin(user) is user


@pytest.mark.asyncio
async def test_admin_cannot_access_superadmin_dependency() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_superadmin(SimpleNamespace(role="admin"))
    assert exc.value.status_code == 403


def test_tenant_slug_normalization_and_validation() -> None:
    assert _normalize_slug(" Example-Tenant ") == "example-tenant"
    with pytest.raises(HTTPException):
        _normalize_slug("示例 租户")


def test_tenant_and_user_schema_role_limits() -> None:
    assert TenantCreate(name="示例租户", slug="example").slug == "example"
    assert UserAdminCreate(
        username="alice",
        email="alice@example.com",
        password="password123",
        role="admin",
        tenant_ids=["00000000-0000-0000-0000-000000000001"],
    ).role == "admin"
    assert UserAdminUpdate(role="superadmin").role == "superadmin"
    with pytest.raises(ValidationError):
        UserAdminUpdate(role="owner")


def test_tenant_user_update_supports_profile_and_password_changes() -> None:
    update = UserAdminUpdate(
        username="alice-new",
        email="alice-new@example.com",
        display_name="Alice",
        password="new-password",
    )
    assert update.username == "alice-new"
    assert update.display_name == "Alice"
    assert update.password == "new-password"


def test_tenant_member_assignment_requires_users() -> None:
    with pytest.raises(ValidationError):
        TenantMemberAssign(user_ids=[])
