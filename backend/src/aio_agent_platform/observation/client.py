"""Langfuse client initialization and management."""

from __future__ import annotations

import asyncio
import contextvars

import structlog
from langfuse import Langfuse
from langfuse._client.span import LangfuseSpan

from aio_agent_platform.core.config import settings

logger = structlog.get_logger(__name__)

_client: Langfuse | None = None

# Context variable for the current root observation (set per-request)
_current_observation: contextvars.ContextVar[LangfuseSpan | None] = contextvars.ContextVar(
    "langfuse_current_observation", default=None
)


def init_langfuse() -> Langfuse | None:
    """Initialize Langfuse with the SDK's background batch exporter if configured."""
    global _client
    cfg = settings.langfuse
    if not cfg.enabled or not cfg.secret_key or not cfg.public_key:
        logger.info("langfuse_disabled")
        return None

    _client = Langfuse(
        secret_key=cfg.secret_key,
        public_key=cfg.public_key,
        host=cfg.base_url,
    )
    logger.info("langfuse_initialized", base_url=cfg.base_url)
    return _client


def get_langfuse_client() -> Langfuse | None:
    """Get the current Langfuse client."""
    return _client


def set_current_observation(obs: LangfuseSpan | None) -> None:
    """Set the current root observation for linking child observations."""
    _current_observation.set(obs)


def get_current_observation() -> LangfuseSpan | None:
    """Get the current root observation."""
    return _current_observation.get(None)


async def shutdown_langfuse() -> None:
    """Drain the SDK's background queues without blocking the event loop."""
    global _client
    client, _client = _client, None
    if client is not None:
        try:
            # shutdown() already flushes pending data and joins SDK threads.
            # Await it only at service shutdown, never at the end of a chat turn.
            await asyncio.to_thread(client.shutdown)
            logger.info("langfuse_shutdown")
        except Exception:
            logger.exception("langfuse_shutdown_failed")
