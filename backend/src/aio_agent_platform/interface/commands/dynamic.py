"""Dynamic skill commands — each user skill auto-registers as a slash command."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from aio_agent_platform.db.models import Skill

from .models import Command, CommandArg, CommandContext, CommandResult


async def run_skill(ctx: CommandContext, skill_name: str, user_input: str | None = None) -> CommandResult:
    """Expand a skill's content for display / direct use as context."""
    skill = await ctx.db.scalar(
        select(Skill).where(
            Skill.user_id == UUID(ctx.user_id),
            Skill.name == skill_name,
            Skill.is_active,
        )
    )
    if not skill:
        return CommandResult(content=f"技能 `{skill_name}` 不存在或已停用。")

    parts = [
        f"## {skill.name}",
        f"**分类：** {skill.category} | **版本：** {skill.version}",
    ]
    if skill.description:
        parts.append(f"**描述：** {skill.description}")
    if skill.content:
        parts.append(f"\n---\n\n{skill.content}")
    if user_input:
        parts.append(f"\n---\n\n**输入：** {user_input}")
    parts.append("\n> 技能内容已展开，可直接作为执行上下文。")
    return CommandResult(content="\n".join(parts))


def _make_handler(skill_name: str):
    async def handler(ctx: CommandContext) -> CommandResult:
        return await run_skill(ctx, skill_name, ctx.args.get("input"))

    return handler


async def find_skill_command(db, user_id: str, name: str) -> Command | None:
    """Return a dynamic Command bound to the matching skill, if any."""
    skill = await db.scalar(
        select(Skill).where(
            Skill.user_id == UUID(user_id),
            Skill.name == name,
            Skill.is_active,
        )
    )
    if not skill:
        return None
    return Command(
        name=skill.name,
        handler=_make_handler(skill.name),
        group="技能",
        desc=skill.description or f"运行技能 {skill.name}",
        dynamic=True,
        args=[CommandArg(name="input", required=False, variadic=True, hint="要交给技能执行的输入")],
    )


async def list_skill_names(db, user_id: str) -> list[str]:
    result = await db.execute(
        select(Skill.name).where(
            Skill.user_id == UUID(user_id),
            Skill.is_active,
        )
    )
    return list(result.scalars().all())
