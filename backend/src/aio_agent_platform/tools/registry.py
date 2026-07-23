"""Tool registry — defines Tool dataclass and ToolRegistry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Tool:
    """A tool the agent can invoke."""

    name: str
    description: str
    parameters: dict  # JSON Schema
    requires_sandbox: bool
    permission_level: str = "read"  # read | write | dangerous
    timeout: int = 60


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict]:
        """Export tools in OpenAI function-calling format for LLM injection."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]
