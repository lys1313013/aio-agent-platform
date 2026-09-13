"""New channels start immediately and retain actionable startup failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from aio_agent_platform.interface.routes import channels


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["websocket", "webhook"])
@pytest.mark.parametrize("startup_error", [None, "transport unavailable"])
async def test_create_starts_saved_channel(monkeypatch, mode, startup_error):
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    req = channels.ChannelCreate(
        name="test channel", agent_id=uuid4(), app_id="test_app",
        app_secret="test_secret", mode=mode,
    )
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    manager = SimpleNamespace(start_channel=AsyncMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        channel_connection_manager=manager,
    )))
    monkeypatch.setattr(channels, "_verify_channel_credentials", AsyncMock())

    async def start(channel):
        db.commit.assert_awaited_once()
        assert channel.status == "enabled"
        assert channel.tenant_id == user.tenant_id
        if startup_error:
            raise RuntimeError(startup_error)

    manager.start_channel.side_effect = start
    result = await channels.create_channel(req, request, user, db)
    saved = db.add.call_args.args[0]
    manager.start_channel.assert_awaited_once_with(saved)
    assert result["status"] == ("error" if startup_error else "enabled")
    assert result["last_error"] == startup_error
    if mode == "webhook" and not startup_error:
        assert result["webhook_url"].endswith(f"/api/channels/webhook/{saved.channel_key}")
    else:
        assert "webhook_url" not in result
    assert db.commit.await_count == (2 if startup_error else 1)


@pytest.mark.asyncio
async def test_create_without_manager_does_not_save():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    db = MagicMock()
    req = channels.ChannelCreate(
        name="test channel", agent_id=uuid4(), app_id="test_app",
        app_secret="test_secret", mode="websocket",
    )
    with pytest.raises(HTTPException) as exc:
        await channels.create_channel(req, request, SimpleNamespace(), db)
    assert exc.value.status_code == 503
    db.add.assert_not_called()
