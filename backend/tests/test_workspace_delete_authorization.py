"""Workspace deletion must authorize before irreversible object-storage operations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from aio_agent_platform.auth.dependencies import get_current_user
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.workspaces import routes


@pytest.mark.parametrize("workspace_kind", ["not_owned", "missing", "default", "owned"])
async def test_delete_authorizes_before_accessing_storage(monkeypatch, workspace_kind):
    user_id, workspace_id = uuid4(), uuid4()
    workspace = (
        SimpleNamespace(id=workspace_id, is_default=workspace_kind == "default")
        if workspace_kind in {"default", "owned"}
        else None
    )
    db = AsyncMock()
    db.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=workspace))
    storage = Mock()
    storage_factory = Mock(return_value=storage)
    monkeypatch.setattr(routes, "_get_workspace_storage", storage_factory)

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)

    async def database():
        yield db

    app.dependency_overrides[get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(f"/api/workspaces/{workspace_id}")

    # Exercise the real service query and verify it scopes lookup to the caller.
    query = db.execute.call_args.args[0]
    assert set(query.compile().params.values()) == {workspace_id, user_id}
    if workspace_kind == "owned":
        assert response.status_code == 204
        db.delete.assert_awaited_once_with(workspace)
        db.flush.assert_awaited_once()
        storage.delete_workspace.assert_called_once_with(str(workspace_id))
    else:
        assert response.status_code == (400 if workspace_kind == "default" else 404)
        db.delete.assert_not_awaited()
        db.flush.assert_not_awaited()
        storage_factory.assert_not_called()
        storage.delete_workspace.assert_not_called()
