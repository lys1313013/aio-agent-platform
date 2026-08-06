"""Command metadata endpoint — powers the frontend slash-command menu."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.interface.commands.dispatcher import dynamic_commands
from aio_agent_platform.interface.commands.registry import registry

router = APIRouter(prefix="/api/commands", tags=["commands"])


@router.get("")
async def list_commands(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Return commands visible to the current user (including dynamic skills)."""
    cmds = registry.list_for(user)
    dyn = await dynamic_commands(db, str(user.id))
    known = {c.name for c in cmds}
    items = [*cmds, *[d for d in dyn if d.name not in known]]
    return [
        {
            "name": c.name,
            "aliases": c.aliases,
            "group": c.group,
            "desc": c.desc,
            "usage": c.usage_text,
            "args": [
                {
                    "name": a.name,
                    "required": a.required,
                    "variadic": a.variadic,
                    "choices": a.choices,
                    "hint": a.hint,
                }
                for a in c.args
            ],
            "dynamic": c.dynamic,
        }
        for c in items
    ]
