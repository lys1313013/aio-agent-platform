"""Confirmation manager — manages user confirmation requests during agent execution."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class PendingConfirmation:
    """A confirmation request waiting for user response."""

    id: str
    session_id: str
    user_id: str
    question: str
    mode: str  # single_select | multi_select | free_input | approve | table_input
    options: list[dict[str, Any]]
    context: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    response: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    timeout_seconds: int = 300
    table_schema: dict[str, Any] | None = None


class ConfirmationManager:
    """
    Manages pending confirmation requests across all active sessions.

    Thread-safe via asyncio (single-threaded event loop).
    Confirmations are stored in memory only — no database persistence.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}

    def create_confirmation(
        self,
        session_id: str,
        user_id: str,
        question: str,
        mode: str,
        options: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        table_schema: dict[str, Any] | None = None,
    ) -> PendingConfirmation:
        """Create a new confirmation request (non-blocking)."""
        ctx = context or {}
        timeout = ctx.get("timeout_seconds", 300)

        confirmation = PendingConfirmation(
            id=str(uuid.uuid4()),
            session_id=session_id,
            user_id=user_id,
            question=question,
            mode=mode,
            options=options or [],
            context=ctx,
            timeout_seconds=timeout,
            table_schema=table_schema,
        )
        self._pending[confirmation.id] = confirmation
        logger.info(
            "Confirmation created",
            confirmation_id=confirmation.id,
            session_id=session_id,
            mode=mode,
        )
        return confirmation

    async def wait_for_response(
        self, confirmation_id: str
    ) -> PendingConfirmation | None:
        """Wait for user response (blocking, no timeout)."""
        confirmation = self._pending.get(confirmation_id)
        if not confirmation:
            return None

        try:
            await confirmation.event.wait()
            return confirmation
        finally:
            self._pending.pop(confirmation_id, None)

    def resolve_confirmation(
        self, confirmation_id: str, response: dict[str, Any]
    ) -> bool:
        """Resolve a pending confirmation with user's response."""
        confirmation = self._pending.get(confirmation_id)
        if not confirmation:
            logger.warning(
                "Confirmation not found or already resolved",
                confirmation_id=confirmation_id,
            )
            return False

        confirmation.response = response
        confirmation.event.set()
        logger.info(
            "Confirmation resolved",
            confirmation_id=confirmation_id,
            status=response.get("status"),
        )
        return True

    def get_pending(self, session_id: str) -> list[PendingConfirmation]:
        """Get all pending confirmations for a session."""
        return [
            c for c in self._pending.values() if c.session_id == session_id
        ]


# Global singleton
confirmation_manager = ConfirmationManager()
