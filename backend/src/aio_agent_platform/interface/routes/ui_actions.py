"""UI action routes — REST API for frontend (in-browser) tool execution results.

See docs/22-浏览器页面自动化/02-技术方案.md. Mirrors routes/confirmations.py.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.auth.jwt_handler import TokenPayload, decode_token
from aio_agent_platform.core.confirmation import confirmation_manager
from aio_agent_platform.core.ui_action import ui_action_manager
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Session

logger = structlog.get_logger()

router = APIRouter(prefix="/api/ui-actions", tags=["ui-actions"])

_optional_security = HTTPBearer(auto_error=False)


async def _user_id_from_header_or_query(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_security)
    ],
    token: Annotated[str | None, Query()] = None,
) -> UUID:
    """Auth for beacon/unload paths: Authorization header OR ?token= query param.

    sendBeacon cannot set headers, so tab-unload cancel_all passes the access
    token as a query parameter. Only the user id is extracted (ownership checks
    compare against Session.user_id / pending.user_id).
    """
    raw = credentials.credentials if credentials else token
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload: TokenPayload = decode_token(raw)
        if payload.type != "access":
            raise ValueError("wrong token type")
        return UUID(payload.sub)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from e


# ---- Schemas ----


class UiActionRespondRequest(BaseModel):
    """Request body for reporting a frontend action's execution result."""

    status: str  # "ok" | "error" | "cancelled"
    result: dict[str, Any] | None = None
    error: str | None = None
    warning: str | None = None
    # Session context refresh (the only uplink channel mid-run)
    snapshot_version: int | None = None
    dangerous_refs: list[str] | None = None
    delta_snapshot: str | None = None
    page_context: dict[str, Any] | None = None
    # ui_screenshot: base64 image payload (never persisted, never forwarded in events)
    image: str | None = None


class UiActionRespondResponse(BaseModel):
    success: bool
    message: str = ""


class PendingUiActionResponse(BaseModel):
    id: str
    tool_call_id: str
    action: str
    args: dict[str, Any]
    risk: str
    created_at: str


# ---- Routes ----


@router.post("/{action_id}/respond", response_model=UiActionRespondResponse)
async def respond_to_ui_action(
    action_id: str,
    body: UiActionRespondRequest,
    user: CurrentUser,
) -> UiActionRespondResponse:
    """Submit the execution result of a pending frontend UI action.

    Only the user the action was issued to may respond.
    """
    pending = ui_action_manager.get(action_id)
    if not pending or pending.user_id != str(user.id):
        logger.info(
            "ui_action_respond_rejected",
            action_id=action_id,
            found=bool(pending),
        )
        raise HTTPException(
            status_code=404,
            detail="UI action not found or already resolved",
        )

    payload: dict[str, Any] = body.model_dump(exclude_none=True)
    logger.info(
        "ui_action_respond",
        action_id=action_id,
        action=pending.action,
        status=payload.get("status"),
        error=payload.get("error"),
    )
    resolved = ui_action_manager.resolve(action_id, payload)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail="UI action not found or already resolved",
        )
    return UiActionRespondResponse(success=True, message="UI action result submitted")


@router.get("/sessions/{session_id}/pending")
async def get_pending_ui_actions(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PendingUiActionResponse]:
    """List pending UI actions for a session.

    Race-condition fallback only: an SSE disconnect cancels the agent run and
    its finally-block pops pendings, so this normally returns empty.
    """
    owned = await db.scalar(
        select(Session.id).where(
            Session.id == session_id, Session.user_id == user.id
        )
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Session not found")

    return [
        PendingUiActionResponse(
            id=p.id,
            tool_call_id=p.tool_call_id,
            action=p.action,
            args=p.args,
            risk=p.risk,
            created_at=p.created_at.isoformat(),
        )
        for p in ui_action_manager.get_pending(str(session_id))
    ]


@router.post("/sessions/{session_id}/cancel_all")
async def cancel_all_ui_actions(
    session_id: UUID,
    user_id: Annotated[UUID, Depends(_user_id_from_header_or_query)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, int]:
    """Cancel all pending UI actions AND pending confirmations for a session.

    Called on SSE reconnect / tab unload (via sendBeacon — hence the query-token
    auth fallback). Confirmations are cancelled too, otherwise a confirmation
    card orphaned by an aborted ui flow would hang until the run ends.
    """
    owned = await db.scalar(
        select(Session.id).where(
            Session.id == session_id, Session.user_id == user_id
        )
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Session not found")

    cancelled = ui_action_manager.cancel_session(str(session_id))
    # Resolve orphaned confirmations for the same session as rejected.
    confirmations_cancelled = 0
    for c in confirmation_manager.get_pending(str(session_id)):
        if confirmation_manager.resolve_confirmation(
            c.id, {"status": "rejected", "user_input": "session cancelled"}
        ):
            confirmations_cancelled += 1

    return {"cancelled": cancelled, "confirmations_cancelled": confirmations_cancelled}
