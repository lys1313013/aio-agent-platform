"""CronJobService — CRUD for scheduled tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import CronJob

logger = structlog.get_logger()


class CronJobService:
    """Stateless service for managing scheduled tasks."""

    @staticmethod
    async def list_jobs(
        db: AsyncSession,
        tenant_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CronJob]:
        stmt = (
            select(CronJob)
            .where(CronJob.tenant_id == tenant_id)
            .order_by(CronJob.last_run_at.desc().nulls_last())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_job(
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID,
    ) -> CronJob | None:
        result = await db.execute(
            select(CronJob).where(CronJob.id == job_id, CronJob.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_job(
        db: AsyncSession,
        tenant_id: UUID,
        user_id: UUID,
        name: str,
        task_config: dict,
        agent_id: UUID | None = None,
        message: str | None = None,
        cron_expr: str | None = None,
        run_at: datetime | None = None,
        channel_id: UUID | None = None,
        is_active: bool = True,
    ) -> CronJob:
        job = CronJob(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            name=name,
            message=message,
            cron_expr=cron_expr,
            run_at=run_at,
            task_config=task_config,
            channel_id=channel_id,
            is_active=is_active,
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)
        logger.info("cron_job_created", job_id=str(job.id), name=name)
        return job

    @staticmethod
    async def update_job(
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID,
        name: str | None = None,
        agent_id: UUID | None = None,
        message: str | None = None,
        cron_expr: str | None = None,
        run_at: datetime | None = None,
        task_config: dict | None = None,
        channel_id: UUID | None = None,
        is_active: bool | None = None,
        clear_channel: bool = False,
    ) -> CronJob | None:
        job = await CronJobService.get_job(db, job_id, tenant_id)
        if not job:
            return None

        if name is not None:
            job.name = name
        if agent_id is not None:
            job.agent_id = agent_id
        if message is not None:
            job.message = message
        if cron_expr is not None:
            job.cron_expr = cron_expr
        if run_at is not None:
            job.run_at = run_at
        if task_config is not None:
            job.task_config = task_config
        if clear_channel:
            job.channel_id = None
        elif channel_id is not None:
            job.channel_id = channel_id
        if is_active is not None:
            job.is_active = is_active

        await db.flush()
        await db.refresh(job)
        logger.info("cron_job_updated", job_id=str(job.id))
        return job

    @staticmethod
    async def delete_job(
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        job = await CronJobService.get_job(db, job_id, tenant_id)
        if not job:
            return False
        await db.delete(job)
        await db.flush()
        logger.info("cron_job_deleted", job_id=str(job.id))
        return True

    @staticmethod
    async def mark_run(
        db: AsyncSession,
        job_id: UUID,
    ) -> None:
        """Update last_run_at to now for a given job."""
        result = await db.execute(select(CronJob).where(CronJob.id == job_id))
        job = result.scalar_one_or_none()
        if job:
            job.last_run_at = datetime.now(UTC)
            await db.flush()

    @staticmethod
    async def get_active_jobs(
        db: AsyncSession,
    ) -> list[CronJob]:
        """Get all active jobs across all users (for scheduler)."""
        result = await db.execute(
            select(CronJob).where(CronJob.is_active)
        )
        return list(result.scalars().all())
