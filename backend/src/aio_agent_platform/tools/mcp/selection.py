"""Shared MCP tool-selection policy helpers."""

from collections.abc import Collection
from uuid import UUID


def is_mcp_tool_allowed(
    *,
    server_id: UUID | None,
    full_name: str,
    allowed_server_ids: Collection[str] | None,
    enabled_tools: Collection[str] | None,
    all_tools_server_ids: Collection[str] = (),
) -> bool:
    """Return whether an MCP tool is enabled for an agent.

    A server in ``all_tools_server_ids`` follows the upstream tool catalogue,
    so newly discovered tools are enabled without rewriting ``enabled_tools``.
    """
    normalized_server_id = str(server_id) if server_id is not None else None
    if allowed_server_ids is not None and normalized_server_id not in allowed_server_ids:
        return False
    if normalized_server_id in all_tools_server_ids:
        return True
    return enabled_tools is None or full_name in enabled_tools
