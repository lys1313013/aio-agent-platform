"""RemoteToolExecutor — builds and sends HTTP requests for remote tools."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import structlog

from aio_agent_platform.tools.remote.manager import RemoteToolConfig, RemoteToolManager
from aio_agent_platform.tools.remote.template import render_body, render_url

logger = structlog.get_logger()


class RemoteToolError(Exception):
    """Raised when a remote tool call fails."""


class RemoteToolExecutor:
    """Executes remote tool calls by building HTTP requests from config + arguments."""

    def __init__(self, manager: RemoteToolManager) -> None:
        self._manager = manager

    async def call(self, tool_name: str, arguments: dict) -> str:
        """Execute a remote tool call and return the response as a string.

        Flow:
        1. Get tool config from manager
        2. Render URL (path variable substitution)
        3. Build query params (for GET/DELETE, remaining args go to query)
        4. Build headers (static + auth)
        5. Build request body (template interpolation, for POST/PUT/PATCH)
        6. Send HTTP request
        7. Extract response via JSONPath (if configured)
        8. Return as string
        """
        config = self._manager.get_config(tool_name)
        if config is None:
            raise RemoteToolError(f"Remote tool not found: {tool_name}")

        # 1. URL template rendering
        url, remaining_args = render_url(config.url_template, arguments)

        # 2. Build query params
        query_params = self._build_query_params(config, remaining_args)

        # 3. Build headers
        headers = self._build_headers(config)

        # 4. Build body (only for methods that support it)
        body = self._build_body(config, remaining_args)

        # 5. Send request
        logger.info(
            "remote_tool_call",
            tool=tool_name,
            method=config.method,
            url=url,
        )

        try:
            async with httpx.AsyncClient(timeout=config.timeout, max_redirects=3) as client:
                response = await client.request(
                    method=config.method,
                    url=url,
                    params=query_params or None,
                    headers=headers,
                    json=body if body is not None else None,
                    follow_redirects=True,
                )
        except httpx.TimeoutException:
            raise RemoteToolError(
                f"Remote tool '{tool_name}' timed out after {config.timeout}s"
            )
        except httpx.RequestError as e:
            raise RemoteToolError(f"Remote tool '{tool_name}' request failed: {e}")

        # 6. Check response status
        if response.status_code >= 400:
            raise RemoteToolError(
                f"Remote tool '{tool_name}' returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        # 7. Parse and extract response
        try:
            data = response.json()
        except Exception:
            return response.text

        if config.response_extract:
            data = self._extract_response(data, config.response_extract)

        # 8. Serialize to string
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _build_query_params(
        self, config: RemoteToolConfig, remaining_args: dict
    ) -> dict[str, Any]:
        """Build query parameters for the request.

        - If query_params mapping is configured, use it.
        - For GET/DELETE: remaining args (not consumed by URL template) go to query.
        - For POST/PUT/PATCH: remaining args go to body (handled separately).
        """
        query: dict[str, Any] = {}

        if config.query_params:
            # Explicit mapping: tool_param_name → query_param_name
            for tool_param, query_param in config.query_params.items():
                if tool_param in remaining_args:
                    query[query_param] = remaining_args[tool_param]
        elif config.method.upper() in ("GET", "DELETE"):
            # Auto: all remaining args become query params
            query = {k: v for k, v in remaining_args.items()}

        return query

    def _build_headers(self, config: RemoteToolConfig) -> dict[str, str]:
        """Build request headers — static headers + auth headers."""
        headers: dict[str, str] = {}

        # Static headers
        if config.headers:
            headers.update(config.headers)

        # Auth headers
        auth_headers = self._build_auth_headers(config.auth_type, config.auth_config)
        headers.update(auth_headers)

        # Ensure Content-Type for POST/PUT/PATCH with body
        if config.method.upper() in ("POST", "PUT", "PATCH"):
            headers.setdefault("Content-Type", "application/json")

        return headers

    def _build_auth_headers(
        self, auth_type: str, auth_config: dict | None
    ) -> dict[str, str]:
        """Build authentication headers based on auth_type."""
        if auth_type == "none" or not auth_config:
            return {}

        if auth_type == "bearer":
            token = auth_config.get("token", "")
            return {"Authorization": f"Bearer {token}"}

        if auth_type == "api_key":
            header_name = auth_config.get("header_name", "X-API-Key")
            key = auth_config.get("key", "")
            return {header_name: key}

        if auth_type == "basic":
            username = auth_config.get("username", "")
            password = auth_config.get("password", "")
            credentials = base64.b64encode(
                f"{username}:{password}".encode()
            ).decode()
            return {"Authorization": f"Basic {credentials}"}

        if auth_type == "custom_header":
            custom = auth_config.get("headers", {})
            return {str(k): str(v) for k, v in custom.items()}

        return {}

    def _build_body(
        self, config: RemoteToolConfig, remaining_args: dict
    ) -> dict | list | None:
        """Build request body for POST/PUT/PATCH methods."""
        if config.method.upper() not in ("POST", "PUT", "PATCH"):
            return None

        if config.body_template:
            return render_body(config.body_template, remaining_args)

        # No template: put all remaining args directly as body
        if remaining_args:
            return remaining_args

        return None

    def _extract_response(self, data: Any, jsonpath_expr: str) -> Any:
        """Extract value from response using simple dotted-path JSONPath.

        Supports:
        - `$` — entire response
        - `$.key` — top-level field
        - `$.key.nested` — nested field
        - `$.key[0]` — array index
        - `$.key[*].name` — map over array elements
        """
        if jsonpath_expr == "$":
            return data

        # Strip leading "$."
        path = jsonpath_expr.lstrip("$").lstrip(".")

        current = data
        for part in _split_path(path):
            if current is None:
                return None

            if part == "[*]":
                # Wildcard: map over all elements
                if isinstance(current, list):
                    current = current  # keep list, next part applies to each
                continue

            if part.endswith("[*]"):
                # Field with wildcard: e.g. "items[*]"
                field_name = part[:-3]
                if isinstance(current, dict) and field_name in current:
                    current = current[field_name]
                else:
                    return None
                continue

            if part.startswith("[") and part.endswith("]"):
                # Array index
                try:
                    idx = int(part[1:-1])
                    if isinstance(current, list) and 0 <= idx < len(current):
                        current = current[idx]
                    else:
                        return None
                except ValueError:
                    return None
                continue

            # Regular field access
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list):
                # After wildcard, apply to each element
                current = [
                    item.get(part) if isinstance(item, dict) else None
                    for item in current
                ]
            else:
                return None

        return current


def _split_path(path: str) -> list[str]:
    """Split a dotted JSONPath into parts, preserving array indices.

    Example: "choices[0].message.content" → ["choices", "[0]", "message", "content"]
    Example: "data.items[*].name" → ["data", "items[*]", "name"]
    """
    parts: list[str] = []
    current = ""

    i = 0
    while i < len(path):
        ch = path[i]
        if ch == ".":
            if current:
                parts.append(current)
                current = ""
        elif ch == "[":
            if current:
                parts.append(current)
                current = ""
            # Read until ]
            bracket_end = path.index("]", i)
            parts.append(path[i : bracket_end + 1])
            i = bracket_end + 1
            continue
        else:
            current += ch
        i += 1

    if current:
        parts.append(current)

    return parts
