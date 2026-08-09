"""Cron job tool handlers for ToolExecutor._execute_direct() dispatch."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from aio_agent_platform.cron_jobs.scheduler import CRON_TIMEZONE, get_global_scheduler
from aio_agent_platform.cron_jobs.service import CronJobService
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import Session, User


async def _set_rls_context(db, user_id: str) -> None:
    """Set PostgreSQL RLS context using set_config."""
    await db.execute(select(func.set_config("app.current_user_id", user_id, True)))


async def _get_tenant_id(db, user_id: UUID) -> UUID | None:
    """Resolve the tenant a job operation belongs to (handlers receive no tenant)."""
    result = await db.execute(select(User.tenant_id).where(User.id == user_id))
    return result.scalar_one_or_none()


async def handle_create_cron_job(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Create a new scheduled task."""
    name = arguments.get("name", "")
    cron_expr = arguments.get("cron_expr")
    run_at = arguments.get("run_at")
    task_config = arguments.get("task_config", {})
    agent_id_str = arguments.get("agent_id")
    channel_id_str = arguments.get("channel_id")
    message = arguments.get("message")

    if not name:
        return "Error: name is required"
    if not cron_expr and not run_at:
        return "Error: either cron_expr or run_at must be provided"

    # run_at arrives from the LLM as a string; naive values mean Beijing time.
    parsed_run_at = None
    if run_at:
        if isinstance(run_at, datetime):
            parsed_run_at = run_at
        else:
            try:
                parsed_run_at = datetime.fromisoformat(
                    str(run_at).replace("Z", "+00:00")
                )
            except ValueError:
                return f"Error: invalid run_at format: {run_at}"
        if parsed_run_at.tzinfo is None:
            parsed_run_at = parsed_run_at.replace(tzinfo=CRON_TIMEZONE)

    uid = UUID(user_id)
    agent_id = UUID(agent_id_str) if agent_id_str else None
    channel_id = UUID(channel_id_str) if channel_id_str else None

    # Default to current session's agent if not explicitly provided
    if not agent_id and session_id:
        try:
            sid = UUID(session_id)
            factory = get_session_factory()
            async with factory() as lookup_db:
                result = await lookup_db.execute(
                    select(Session.agent_id).where(Session.id == sid)
                )
                row = result.scalar_one_or_none()
                if row:
                    agent_id = row
        except (ValueError, Exception):
            pass

    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        tenant_id = await _get_tenant_id(db, uid)
        if tenant_id is None:
            return f"Error: user {uid} not found"
        job = await CronJobService.create_job(
            db=db,
            tenant_id=tenant_id,
            user_id=uid,
            name=name,
            agent_id=agent_id,
            message=message,
            cron_expr=cron_expr,
            run_at=parsed_run_at,
            task_config=task_config,
            channel_id=channel_id,
        )
        await db.commit()

    scheduler = get_global_scheduler()
    if scheduler is not None:
        scheduler.add_job(job)

    return (
        f"Cron job created successfully.\n"
        f"  ID: {job.id}\n"
        f"  Name: {job.name}\n"
        f"  Schedule: {job.cron_expr or job.run_at}\n"
        f"  Active: {job.is_active}"
    )


async def handle_list_cron_jobs(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """List all scheduled tasks for the current user."""
    uid = UUID(user_id)

    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        tenant_id = await _get_tenant_id(db, uid)
        if tenant_id is None:
            return f"Error: user {uid} not found"
        jobs = await CronJobService.list_jobs(db, tenant_id)

    if not jobs:
        return "No cron jobs found."

    parts = [f"You have {len(jobs)} cron job(s):\n"]
    for j in jobs:
        schedule = j.cron_expr or (j.run_at.isoformat() if j.run_at else "no schedule")
        status = "active" if j.is_active else "paused"
        last = j.last_run_at.isoformat() if j.last_run_at else "never"
        parts.append(
            f"  [{j.id}] {j.name}\n"
            f"    Schedule: {schedule}\n"
            f"    Status: {status}\n"
            f"    Config: {json.dumps(j.task_config or {}, ensure_ascii=False)}\n"
            f"    Last run: {last}"
        )
    return "\n".join(parts)


async def handle_delete_cron_job(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Delete a scheduled task by ID."""
    job_id_str = arguments.get("job_id", "")

    if not job_id_str:
        return "Error: job_id is required"

    try:
        job_id = UUID(job_id_str)
    except ValueError:
        return f"Error: invalid job_id format: {job_id_str}"

    uid = UUID(user_id)

    factory = get_session_factory()
    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        tenant_id = await _get_tenant_id(db, uid)
        if tenant_id is None:
            return f"Error: user {uid} not found"
        deleted = await CronJobService.delete_job(db, job_id, tenant_id)
        await db.commit()

    if deleted:
        scheduler = get_global_scheduler()
        if scheduler is not None:
            scheduler.remove_job(job_id)
        return f"Cron job {job_id_str} deleted successfully."
    return f"Error: cron job {job_id_str} not found."


CRON_JOB_HANDLERS: dict[str, Callable] = {
    "create_cron_job": handle_create_cron_job,
    "list_cron_jobs": handle_list_cron_jobs,
    "delete_cron_job": handle_delete_cron_job,
}
