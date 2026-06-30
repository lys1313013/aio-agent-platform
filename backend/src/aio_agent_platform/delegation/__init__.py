"""Delegation module for multi-agent task dispatching."""

from aio_agent_platform.delegation.handler import DELEGATION_HANDLERS, handle_delegate_task

__all__ = ["handle_delegate_task", "DELEGATION_HANDLERS"]
