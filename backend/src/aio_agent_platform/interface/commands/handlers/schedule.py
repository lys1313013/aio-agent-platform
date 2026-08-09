"""Scheduled task (cron) commands."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from aio_agent_platform.cron_jobs.scheduler import get_global_scheduler
from aio_agent_platform.cron_jobs.service import CronJobService
from aio_agent_platform.db.models import CronJob

from ..models import CommandArg, CommandContext, CommandResult
from ..registry import command


@command(
    "cron",
    group="定时任务",
    desc="管理定时任务",
    usage="/cron <list|create|pause|resume|delete> [...]",
    args=[
        CommandArg(
            name="action",
            required=True,
            choices=["list", "create", "pause", "resume", "delete"],
            hint="list / create / pause / resume / delete",
        ),
        CommandArg(name="param", required=False, hint="cron 表达式或任务 ID"),
        CommandArg(name="message", required=False, variadic=True, hint="任务内容"),
    ],
)
async def cmd_cron(ctx: CommandContext) -> CommandResult:
    action = ctx.args["action"]
    if action == "list":
        return await _cron_list(ctx)
    if action == "create":
        return await _cron_create(ctx)
    if action == "pause":
        return await _cron_set_active(ctx, False)
    if action == "resume":
        return await _cron_set_active(ctx, True)
    return await _cron_delete(ctx)


async def _cron_list(ctx: CommandContext) -> CommandResult:
    result = await ctx.db.execute(
        select(CronJob)
        .where(CronJob.tenant_id == ctx.user.tenant_id)
        .order_by(CronJob.created_at.desc())
    )
    jobs = list(result.scalars().all())
    if not jobs:
        return CommandResult(content="暂无定时任务。\n创建：`/cron create '<表达式>' <任务内容>`")
    lines = ["**定时任务：**", ""]
    for j in jobs:
        schedule = j.cron_expr or (j.run_at.isoformat() if j.run_at else "无")
        status = "🟢 运行中" if j.is_active else "⏸ 已暂停"
        last = j.last_run_at.isoformat(timespec="minutes") if j.last_run_at else "从未"
        lines.append(
            f"- `{j.id}` · {j.name}  [{status}]\n"
            f"  `{schedule}` · 上次 {last}"
        )
    return CommandResult(content="\n".join(lines))


async def _cron_create(ctx: CommandContext) -> CommandResult:
    cron_expr = ctx.args.get("param")
    message = ctx.args.get("message")
    if not cron_expr:
        return CommandResult(
            content="缺少 cron 表达式。\n示例：`/cron create '0 9 * * *' 每天早上发日报`"
        )
    if not message:
        return CommandResult(content="缺少任务内容。\n示例：`/cron create '0 9 * * *' 每天早上发日报`")

    agent_id = ctx.session.agent_id if ctx.session is not None else None
    name = message[:30]
    job = await CronJobService.create_job(
        db=ctx.db,
        tenant_id=ctx.user.tenant_id,
        user_id=UUID(ctx.user_id),
        name=name,
        task_config={},
        agent_id=agent_id,
        message=message,
        cron_expr=cron_expr,
    )
    scheduler = get_global_scheduler()
    if scheduler is not None:
        scheduler.add_job(job)
    return CommandResult(
        content=(
            f"✅ 已创建定时任务\n\n"
            f"- **ID**：`{job.id}`\n"
            f"- **名称**：{name}\n"
            f"- **表达式**：`{cron_expr}`\n"
            f"- **内容**：{message[:80]}"
        )
    )


async def _cron_set_active(ctx: CommandContext, active: bool) -> CommandResult:
    job_id = ctx.args.get("param")
    if not job_id:
        return CommandResult(content="缺少任务 ID。\n示例：`/cron pause <id>`")
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return CommandResult(content=f"任务 ID 格式不正确：{job_id}")

    job = await ctx.db.scalar(
        select(CronJob).where(
            CronJob.id == job_uuid, CronJob.tenant_id == ctx.user.tenant_id
        )
    )
    if job is None:
        return CommandResult(content="任务不存在。")
    job.is_active = active
    await ctx.db.flush()
    scheduler = get_global_scheduler()
    if scheduler is not None:
        scheduler.update_job(job)
    verb = "恢复" if active else "暂停"
    return CommandResult(content=f"✅ 已{verb}任务「{job.name}」。")


async def _cron_delete(ctx: CommandContext) -> CommandResult:
    job_id = ctx.args.get("param")
    if not job_id:
        return CommandResult(content="缺少任务 ID。\n示例：`/cron delete <id>`")
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        return CommandResult(content=f"任务 ID 格式不正确：{job_id}")

    deleted = await CronJobService.delete_job(ctx.db, job_uuid, ctx.user.tenant_id)
    if not deleted:
        return CommandResult(content="任务不存在。")
    scheduler = get_global_scheduler()
    if scheduler is not None:
        scheduler.remove_job(job_uuid)
    return CommandResult(content=f"✅ 已删除定时任务 `{job_id}`。")
