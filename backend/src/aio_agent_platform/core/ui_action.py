"""UI action manager — manages pending in-browser (frontend) tool executions.

 Mirrors ConfirmationManager (core/confirmation.py): pure in-memory, single-process
 deployment constraint. Key differences:
 - wait MUST time out (a browser action cannot hang for 5 minutes like a user
   confirmation can);
 - pending entries are popped in `finally` (covers asyncio.CancelledError —
   SSE disconnect cancels the whole agent run);
 - waiting is chunked so the AgentLoop can yield heartbeats and let the SSE
   generator drain the event queue;
 - per-session caches (dangerous_refs / page_context) and a hallucinated-ref
   circuit breaker live here too, refreshed by every frontend respond payload.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger()

#: Consecutive stale_ref/element_not_found failures before the circuit breaker trips.
REF_FAILURE_BREAKER_THRESHOLD = 3
#: Chunk length for the heartbeat-friendly wait loop (seconds).
WAIT_CHUNK_SECONDS = 15


@dataclass
class PendingUiAction:
    """A frontend (in-browser) action waiting for execution result."""

    id: str
    session_id: str
    user_id: str
    tool_call_id: str
    action: str  # ui_navigate / ui_click / ui_input / ...
    args: dict[str, Any]
    risk: str = "write"  # read | write | dangerous
    confirmed: bool = False  # backend confirmation flow passed (frontend re-check credential)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    timeout_seconds: int = 60


class UiActionManager:
    """
    Manages pending frontend UI actions across active sessions.

    Single-threaded asyncio; in-memory only — no database persistence.
    """

    def __init__(self) -> None:
        self._pending: dict[str, PendingUiAction] = {}
        # session_id -> {"snapshot_version": int|None, "dangerous_refs": list[str],
        #                "page_context": dict|None}
        self._session_ctx: dict[str, dict[str, Any]] = {}
        # session_id -> consecutive stale_ref/element_not_found count
        self._ref_failures: dict[str, int] = {}

    # ---- lifecycle ----

    def create_action(
        self,
        session_id: str,
        user_id: str,
        tool_call_id: str,
        action: str,
        args: dict[str, Any],
        risk: str = "write",
        confirmed: bool = False,
        timeout_seconds: int = 60,
    ) -> PendingUiAction | None:
        """Create a pending action. Returns None if the session already has one
        (one pending action per session — prevents concurrent driving)."""
        for p in self._pending.values():
            if p.session_id == session_id:
                logger.warning(
                    "ui_action_session_busy",
                    session_id=session_id,
                    existing_action_id=p.id,
                    new_action=action,
                )
                return None

        pending = PendingUiAction(
            id=str(uuid.uuid4()),
            session_id=session_id,
            user_id=user_id,
            tool_call_id=tool_call_id,
            action=action,
            args=args,
            risk=risk,
            confirmed=confirmed,
            timeout_seconds=timeout_seconds,
        )
        self._pending[pending.id] = pending
        logger.info(
            "ui_action_created",
            action_id=pending.id,
            session_id=session_id,
            action=action,
            risk=risk,
            confirmed=confirmed,
        )
        return pending

    async def wait_chunk(self, action_id: str, chunk: float = WAIT_CHUNK_SECONDS) -> str:
        """Wait one chunk. Returns "resolved" | "timeout" | "waiting".

        "waiting" means the chunk elapsed without a result — the caller should
        yield a heartbeat (letting SSE drain) and call again.
        """
        pending = self._pending.get(action_id)
        if not pending:
            return "resolved"
        elapsed = (datetime.now(UTC) - pending.created_at).total_seconds()
        remaining = pending.timeout_seconds - elapsed
        if remaining <= 0:
            return "timeout"
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=min(chunk, remaining))
            return "resolved"
        except TimeoutError:
            elapsed = (datetime.now(UTC) - pending.created_at).total_seconds()
            return "timeout" if elapsed >= pending.timeout_seconds else "waiting"

    def discard(self, action_id: str) -> dict[str, Any] | None:
        """Pop the pending entry (call in finally — covers CancelledError) and
        return its result payload (None if never resolved)."""
        pending = self._pending.pop(action_id, None)
        return pending.result if pending else None

    def resolve(self, action_id: str, result: dict[str, Any]) -> bool:
        """Resolve a pending action with the frontend's execution result.

        Also refreshes the per-session context cache from the respond payload
        (the only uplink channel available mid-run).
        """
        pending = self._pending.get(action_id)
        if not pending:
            logger.warning(
                "ui_action_not_found_or_resolved", action_id=action_id
            )
            return False

        pending.result = result
        pending.event.set()
        self.update_session_context(
            pending.session_id,
            snapshot_version=result.get("snapshot_version"),
            dangerous_refs=result.get("dangerous_refs"),
            page_context=result.get("page_context"),
        )
        logger.info(
            "ui_action_resolved",
            action_id=action_id,
            session_id=pending.session_id,
            status=result.get("status"),
        )
        return True

    def cancel_session(self, session_id: str, error: str = "cancelled") -> int:
        """Cancel all pending actions for a session. Returns count cancelled."""
        targets = [p for p in self._pending.values() if p.session_id == session_id]
        for p in targets:
            p.result = {"status": "cancelled", "error": error}
            p.event.set()
        if targets:
            logger.info(
                "ui_actions_cancelled", session_id=session_id, count=len(targets)
            )
        return len(targets)

    def get(self, action_id: str) -> PendingUiAction | None:
        return self._pending.get(action_id)

    def get_pending(self, session_id: str) -> list[PendingUiAction]:
        return [p for p in self._pending.values() if p.session_id == session_id]

    # ---- per-session context cache (dangerous gate) ----

    def update_session_context(
        self,
        session_id: str,
        *,
        snapshot_version: int | None = None,
        dangerous_refs: list[str] | None = None,
        page_context: dict[str, Any] | None = None,
    ) -> None:
        ctx = self._session_ctx.setdefault(session_id, {})
        if snapshot_version is not None:
            ctx["snapshot_version"] = snapshot_version
        if dangerous_refs is not None:
            ctx["dangerous_refs"] = dangerous_refs
        if page_context is not None:
            ctx["page_context"] = page_context

    def get_session_context(self, session_id: str) -> dict[str, Any] | None:
        return self._session_ctx.get(session_id)

    # ---- hallucinated-ref circuit breaker ----

    def record_ref_failure(self, session_id: str) -> int:
        """Record one stale_ref/element_not_found failure; return the new count."""
        count = self._ref_failures.get(session_id, 0) + 1
        self._ref_failures[session_id] = count
        return count

    def reset_ref_failures(self, session_id: str) -> None:
        self._ref_failures.pop(session_id, None)

    def ref_failures(self, session_id: str) -> int:
        return self._ref_failures.get(session_id, 0)


# Global singleton
ui_action_manager = UiActionManager()
