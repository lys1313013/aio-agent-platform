"""Confirmation routes — REST API for responding to user confirmation requests."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.core.confirmation import confirmation_manager
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Session

logger = structlog.get_logger()

router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])


# ---- Schemas ----


class ConfirmResponseRequest(BaseModel):
    """Request body for responding to a confirmation."""

    status: str  # "approved" | "rejected" | "modified"
    selected_options: list[str] | None = None
    user_input: str | None = None
    table_data: list[dict] | None = None


class ConfirmResponseResponse(BaseModel):
    """Response after submitting a confirmation."""

    success: bool
    message: str = ""


class PendingConfirmationResponse(BaseModel):
    """A pending confirmation request."""

    id: str
    question: str
    mode: str
    options: list[dict]
    context: dict
    created_at: str
    timeout_seconds: int
    table_schema: dict | None = None


# ---- Routes ----


@router.post("/{confirmation_id}/respond", response_model=ConfirmResponseResponse)
async def respond_to_confirmation(
    confirmation_id: str,
    body: ConfirmResponseRequest,
    user: CurrentUser,
) -> ConfirmResponseResponse:
    """Submit a response to a pending confirmation request.

    Only the user the confirmation was issued to may respond — otherwise a
    third party could approve/reject another user's potentially destructive
    agent actions.
    """
    confirmation = confirmation_manager.get(confirmation_id)
    if not confirmation or confirmation.user_id != str(user.id):
        raise HTTPException(
            status_code=404,
            detail="Confirmation not found or already resolved",
        )

    resolved = confirmation_manager.resolve_confirmation(
        confirmation_id,
        response={
            "status": body.status,
            "selected_options": body.selected_options or [],
            "user_input": body.user_input,
            "table_data": body.table_data,
        },
    )
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail="Confirmation not found or already resolved",
        )
    return ConfirmResponseResponse(success=True, message="Confirmation submitted")


@router.get("/sessions/{session_id}/pending")
async def get_pending_confirmations(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PendingConfirmationResponse]:
    """Get all pending confirmation requests for a session (for page refresh recovery)."""
    # Only the session owner may list its confirmations.
    owned = await db.scalar(
        select(Session.id).where(
            Session.id == session_id, Session.user_id == user.id
        )
    )
    if not owned:
        raise HTTPException(status_code=404, detail="Session not found")

    pending = confirmation_manager.get_pending(str(session_id))
    return [
        PendingConfirmationResponse(
            id=c.id,
            question=c.question,
            mode=c.mode,
            options=c.options,
            context=c.context,
            created_at=c.created_at.isoformat(),
            timeout_seconds=c.timeout_seconds,
            table_schema=c.table_schema,
        )
        for c in pending
    ]
