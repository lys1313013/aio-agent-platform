"""Channel management routes — admin CRUD + user binding endpoints."""

from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import AdminUser, CurrentUser
from aio_agent_platform.channels.binding import (
    BindCodeError,
    BindCodeInvalid,
    BindCodeRateLimited,
    consume_bind_code,
)
from aio_agent_platform.channels.registry import get_channel_spec
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import (
    Agent,
    ChannelBindCode,
    ChannelBinding,
    ChannelConfig,
)

router = APIRouter(prefix="/api/channels", tags=["channels"])

_CHANNEL_LABELS = {"feishu": "飞书", "wecom": "企微", "wecom_bot": "企微机器人", "dingtalk": "钉钉"}


def _channel_label(channel_type: str) -> str:
    return _CHANNEL_LABELS.get(channel_type, channel_type)


def _validate_channel_mode(channel_type: str, mode: str) -> None:
    """Reject a mode the channel type doesn't support (e.g. WeCom is webhook-only)."""
    spec = get_channel_spec(channel_type)
    if mode not in spec.allowed_modes:
        label = _channel_label(channel_type)
        raise HTTPException(
            status_code=400,
            detail=f"{label}渠道仅支持 {'/'.join(spec.allowed_modes)} 连接模式",
        )


async def _verify_channel_credentials(
    channel_type: str, app_id: str, app_secret: str, extra_config: dict
) -> None:
    """Verify credentials through the channel-type spec; 400 on failure."""
    label = _channel_label(channel_type)
    spec = get_channel_spec(channel_type)
    if spec.verify_credentials is None:
        raise HTTPException(status_code=400, detail=f"{label}渠道不支持凭证校验")
    valid = await spec.verify_credentials(app_id, app_secret, extra_config)
    if not valid:
        raise HTTPException(status_code=400, detail=f"{label}凭证无效，请检查凭证配置")


def _validate_wecom_agentid(extra_config: dict) -> int:
    """Return the wecom app AgentID after asserting it's a positive integer.

    A missing / non-numeric / non-positive value is a config error (400), not
    a crash — the spec layer would otherwise ``int()``-raise a ValueError that
    surfaces as a 500.
    """
    agentid = (extra_config or {}).get("agentid")
    if agentid in (None, ""):
        raise HTTPException(status_code=400, detail="企微渠道缺少应用 AgentID")
    try:
        value = int(agentid)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="企微渠道 AgentID 必须为正整数")
    if value <= 0:
        raise HTTPException(status_code=400, detail="企微渠道 AgentID 必须为正整数")
    return value


# ---- Pydantic Schemas ----


class ChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    channel_type: str = Field(default="feishu", pattern=r"^(feishu|dingtalk|wecom|wecom_bot)$")
    agent_id: UUID
    app_id: str = Field(..., min_length=1, max_length=128)
    app_secret: str = Field(..., min_length=1)
    encrypt_key: str | None = None
    verification_token: str | None = None
    mode: str = Field(..., pattern=r"^(websocket|webhook)$")
    tool_blacklist: list[str] = Field(default_factory=list)
    enable_streaming: bool = True
    extra_config: dict = Field(default_factory=dict, description="渠道类型特有配置(如企微 agentid)")


class ChannelUpdate(BaseModel):
    name: str | None = None
    agent_id: UUID | None = None
    app_id: str | None = None
    app_secret: str | None = None
    encrypt_key: str | None = None
    verification_token: str | None = None
    mode: str | None = Field(default=None, pattern=r"^(websocket|webhook)$")
    tool_blacklist: list[str] | None = None
    enable_streaming: bool | None = None
    extra_config: dict | None = None


class ChannelOut(BaseModel):
    id: UUID
    channel_type: str
    name: str
    agent_id: UUID
    app_id: str
    mode: str
    status: str
    channel_key: str
    tool_blacklist: list[str] = []
    enable_streaming: bool = True
    extra_config: dict = {}
    last_error: str | None = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class ChannelBindingOut(BaseModel):
    id: UUID
    tenant_id: UUID
    external_id: str
    user_id: UUID
    bind_type: str
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class BindCodeRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class BindCodeResponse(BaseModel):
    message: str
    channel_id: UUID | None = None
    external_id: str | None = None


class WebhookURLOut(BaseModel):
    webhook_url: str


# ---- Helper ----


def _channel_to_dict(ch: ChannelConfig) -> dict:
    return {
        "id": ch.id,
        "channel_type": ch.channel_type,
        "name": ch.name,
        "agent_id": ch.agent_id,
        "app_id": ch.app_id,
        "mode": ch.mode,
        "status": ch.status,
        "channel_key": ch.channel_key,
        "tool_blacklist": ch.tool_blacklist or [],
        "enable_streaming": ch.enable_streaming,
        "extra_config": ch.extra_config or {},
        "last_error": ch.last_error,
        "created_at": ch.created_at.isoformat() if ch.created_at else "",
        "updated_at": ch.updated_at.isoformat() if ch.updated_at else "",
    }


def _binding_to_dict(b: ChannelBinding) -> dict:
    return {
        "id": b.id,
        "tenant_id": b.tenant_id,
        "external_id": b.external_id,
        "user_id": b.user_id,
        "bind_type": b.bind_type,
        "created_at": b.created_at.isoformat() if b.created_at else "",
        "updated_at": b.updated_at.isoformat() if b.updated_at else "",
    }


def _sensitive_config_changed(channel: ChannelConfig, req: ChannelUpdate) -> bool:
    """Return whether an update actually changes connection credentials or mode."""
    return any(
        (
            req.app_id is not None and req.app_id != channel.app_id,
            req.app_secret is not None
            and req.app_secret != channel.app_secret_encrypted,
            req.encrypt_key is not None
            and req.encrypt_key != channel.encrypt_key_encrypted,
            req.verification_token is not None
            and req.verification_token != channel.verification_token_encrypted,
            req.mode is not None and req.mode != channel.mode,
            req.extra_config is not None and req.extra_config != (channel.extra_config or {}),
        )
    )


# ---- Admin CRUD ----


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List all channels for the admin's tenant."""
    result = await db.execute(
        select(ChannelConfig)
        .where(ChannelConfig.tenant_id == user.tenant_id)
        .order_by(ChannelConfig.created_at.desc())
    )
    return [_channel_to_dict(ch) for ch in result.scalars().all()]


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(
    req: ChannelCreate,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new channel. Validates Feishu credentials before saving."""
    # Verify agent exists and belongs to tenant
    agent_result = await db.execute(
        select(Agent).where(
            Agent.id == req.agent_id,
            Agent.tenant_id == user.tenant_id,
            Agent.is_active,
        )
    )
    if agent_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Agent 不存在或无权限")

    # Validate credentials + mode through the channel-type spec.
    _validate_channel_mode(req.channel_type, req.mode)
    if req.channel_type == "wecom":
        _validate_wecom_agentid(req.extra_config or {})
        if not req.verification_token:
            raise HTTPException(status_code=400, detail="企微渠道必须配置回调 Token")
    await _verify_channel_credentials(
        req.channel_type, req.app_id, req.app_secret, req.extra_config
    )

    channel = ChannelConfig(
        tenant_id=user.tenant_id,
        channel_type=req.channel_type,
        name=req.name,
        agent_id=req.agent_id,
        app_id=req.app_id,
        app_secret_encrypted=req.app_secret,  # TODO: encrypt at rest
        encrypt_key_encrypted=req.encrypt_key,  # TODO: encrypt at rest
        verification_token_encrypted=req.verification_token,  # TODO: encrypt at rest
        mode=req.mode,
        status="disabled",
        channel_key=secrets.token_urlsafe(32),
        tool_blacklist=req.tool_blacklist,
        enable_streaming=req.enable_streaming,
        extra_config=req.extra_config,
        created_by=user.id,
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return _channel_to_dict(channel)


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: UUID,
    req: ChannelUpdate,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update channel configuration. Requires re-enable after mode/credential changes."""
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.id == channel_id,
            ChannelConfig.tenant_id == user.tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    sensitive_changed = _sensitive_config_changed(channel, req)

    if req.name is not None:
        channel.name = req.name
    if req.agent_id is not None:
        # Verify new agent exists
        agent_result = await db.execute(
            select(Agent).where(
                Agent.id == req.agent_id,
                Agent.tenant_id == user.tenant_id,
                Agent.is_active,
            )
        )
        if agent_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Agent 不存在或无权限")
        channel.agent_id = req.agent_id
    if req.app_id is not None:
        channel.app_id = req.app_id
    if req.app_secret is not None:
        channel.app_secret_encrypted = req.app_secret  # TODO: encrypt
    if req.encrypt_key is not None:
        channel.encrypt_key_encrypted = req.encrypt_key
    if req.verification_token is not None:
        channel.verification_token_encrypted = req.verification_token
    if req.mode is not None:
        channel.mode = req.mode
    if req.extra_config is not None:
        channel.extra_config = req.extra_config
    if req.tool_blacklist is not None:
        channel.tool_blacklist = req.tool_blacklist
    if req.enable_streaming is not None:
        channel.enable_streaming = req.enable_streaming

    # If credentials or mode actually changed while enabled, force re-enable.
    # Merely resubmitting the existing mode must not stop a working channel.
    if sensitive_changed and channel.status == "enabled":
        channel.status = "disabled"

    await db.commit()
    await db.refresh(channel)
    return _channel_to_dict(channel)


@router.post("/{channel_id}/enable", response_model=dict)
async def enable_channel(
    channel_id: UUID,
    request: Request,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Enable a channel and start its transport."""
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.id == channel_id,
            ChannelConfig.tenant_id == user.tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    conn_mgr = getattr(request.app.state, "channel_connection_manager", None)
    if conn_mgr is None:
        raise HTTPException(status_code=503, detail="渠道管理器未初始化")

    try:
        _validate_channel_mode(channel.channel_type, channel.mode)
        if channel.channel_type == "wecom":
            _validate_wecom_agentid(channel.extra_config or {})
            if not channel.verification_token_encrypted:
                raise HTTPException(status_code=400, detail="企微渠道必须配置回调 Token")
        await _verify_channel_credentials(
            channel.channel_type,
            channel.app_id,
            channel.app_secret_encrypted,
            channel.extra_config or {},
        )

        channel.status = "enabled"
        channel.last_error = None
        await db.commit()

        await conn_mgr.start_channel(channel)
        await db.refresh(channel)

        result_data = _channel_to_dict(channel)
        if channel.mode == "webhook":
            from aio_agent_platform.core.config import settings
            base_url = settings.server.server_url or f"http://localhost:{settings.server.port}"
            result_data["webhook_url"] = f"{base_url}/api/channels/webhook/{channel.channel_key}"

        return result_data
    except HTTPException:
        raise
    except Exception as e:
        channel.status = "error"
        channel.last_error = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"启用渠道失败: {e}")


@router.post("/{channel_id}/disable", response_model=ChannelOut)
async def disable_channel(
    channel_id: UUID,
    request: Request,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Disable a channel and release its connection resources."""
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.id == channel_id,
            ChannelConfig.tenant_id == user.tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    conn_mgr = getattr(request.app.state, "channel_connection_manager", None)
    if conn_mgr:
        await conn_mgr.stop_channel(channel_id)

    channel.status = "disabled"
    await db.commit()
    await db.refresh(channel)
    return _channel_to_dict(channel)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: UUID,
    request: Request,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a channel and clean up its bind codes and session mappings.

    Channel bindings are tenant-scoped and shared across channels, so they
    survive channel deletion.
    """
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.id == channel_id,
            ChannelConfig.tenant_id == user.tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    conn_mgr = getattr(request.app.state, "channel_connection_manager", None)
    if conn_mgr:
        await conn_mgr.stop_channel(channel_id)

    await db.execute(delete(ChannelBindCode).where(ChannelBindCode.channel_id == channel_id))
    await db.execute(delete(ChannelConfig).where(ChannelConfig.id == channel_id))
    await db.commit()


@router.get("/{channel_id}/bindings", response_model=list[ChannelBindingOut])
async def list_channel_bindings(
    channel_id: UUID,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List user bindings for the channel's tenant (bindings are tenant-scoped
    and shared by all channels in the tenant)."""
    # Verify channel belongs to tenant
    ch_result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.id == channel_id,
            ChannelConfig.tenant_id == user.tenant_id,
        )
    )
    channel = ch_result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(status_code=404, detail="渠道不存在")

    result = await db.execute(
        select(ChannelBinding)
        .where(ChannelBinding.tenant_id == channel.tenant_id)
        .order_by(ChannelBinding.created_at.desc())
    )
    return [_binding_to_dict(b) for b in result.scalars().all()]


# ---- User-facing binding endpoints ----

user_router = APIRouter(prefix="/api/channel-bindings", tags=["channel-bindings"])


@user_router.get("", response_model=list[ChannelBindingOut])
async def list_my_bindings(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List the current user's channel bindings."""
    result = await db.execute(
        select(ChannelBinding)
        .where(ChannelBinding.user_id == user.id)
        .order_by(ChannelBinding.created_at.desc())
    )
    return [_binding_to_dict(b) for b in result.scalars().all()]


@user_router.post("/bind", response_model=BindCodeResponse)
async def bind_with_code(
    req: BindCodeRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Consume a bind code to link a channel identity to the current user.

    The code is only valid within the tenant whose channel issued it.
    """
    try:
        await consume_bind_code(db, req.code, user.id, user.tenant_id)
        await db.commit()
        return {
            "message": "绑定成功，现在可以在当前租户的所有渠道与 Agent 对话",
            "channel_id": None,
            "external_id": None,
        }
    except BindCodeRateLimited as e:
        raise HTTPException(status_code=429, detail=str(e))
    except BindCodeInvalid as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BindCodeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@user_router.delete("/{binding_id}", status_code=204)
async def unbind(
    binding_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Unbind a channel. Shadow accounts are disabled; real accounts are unlinked."""
    result = await db.execute(
        select(ChannelBinding).where(
            ChannelBinding.id == binding_id,
            ChannelBinding.user_id == user.id,
        )
    )
    binding = result.scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="绑定记录不存在")

    from aio_agent_platform.channels.binding import unbind_external

    await unbind_external(db, binding.tenant_id, binding.external_id)
    await db.commit()
