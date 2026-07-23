"""Confirmation routes — REST API for responding to user confirmation requests."""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.core.confirmation import confirmation_manager

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
    """Submit a response to a pending confirmation request."""
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
) -> list[PendingConfirmationResponse]:
    """Get all pending confirmation requests for a session (for page refresh recovery)."""
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
