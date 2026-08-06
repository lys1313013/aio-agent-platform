"""Explicit delegation slash command."""

from __future__ import annotations

from uuid import UUID, uuid4

from aio_agent_platform.core.agent import DelegationContext
from aio_agent_platform.core.chat import resolve_workspace
from aio_agent_platform.core.config import settings
from aio_agent_platform.delegation.handler import handle_delegate_task

from ..models import CommandArg, CommandContext, CommandResult
from ..registry import command


@command(
    "delegate",
    group="智能体",
    desc="显式将任务委派给指定角色子智能体",
    args=[
        CommandArg(name="role", required=True, hint="子智能体角色/名称"),
        CommandArg(name="task", required=True, variadic=True, hint="任务描述"),
    ],
)
async def cmd_delegate(ctx: CommandContext) -> CommandResult:
    if ctx.tool_executor is None:
        return CommandResult(content="工具执行器不可用，无法委派任务。")
    if not ctx.session_id:
        return CommandResult(content="当前没有会话，请先开始对话。")

    role = ctx.args["role"]
    task = ctx.args["task"]

    workspace_id = workspace_slug = None
    if ctx.session is not None:
        workspace_id, workspace_slug = await resolve_workspace(
            ctx.db, ctx.session, UUID(ctx.user_id)
        )

    delegation = DelegationContext(
        parent_agent_id=(ctx.session.agent_id if ctx.session and ctx.session.agent_id else uuid4()),
        delegation_depth=0,
        max_depth=settings.agent.max_delegation_depth,
        event_queue=None,
        workspace_id=workspace_id,
        workspace_slug=workspace_slug,
    )
    text = await handle_delegate_task(
        {"role_name": role, "role_description": role, "task": task},
        user_id=ctx.user_id,
        session_id=ctx.session_id,
        delegation=delegation,
        tool_executor=ctx.tool_executor,
    )
    return CommandResult(content=text)
