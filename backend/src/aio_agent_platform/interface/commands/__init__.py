"""Slash command system.

Importing this package registers all built-in commands and exposes the
dispatcher used by the chat endpoint to intercept command messages.
"""

# Import handler modules to trigger registration.
from . import handlers as _handlers  # noqa: F401
from .dispatcher import dispatch, dynamic_commands
from .models import Command, CommandArg, CommandContext, CommandResult
from .registry import CommandRegistry, command, registry

__all__ = [
    "Command",
    "CommandArg",
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "command",
    "dispatch",
    "dynamic_commands",
    "registry",
]
