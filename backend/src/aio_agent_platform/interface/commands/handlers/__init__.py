"""Slash command handlers. Importing this package registers all commands."""

# Importing each handler module triggers @command registration.
from . import agent as _agent  # noqa: F401
from . import schedule as _schedule  # noqa: F401
from . import session as _session  # noqa: F401
from . import skills_memory as _skills_memory  # noqa: F401
from . import system as _system  # noqa: F401
