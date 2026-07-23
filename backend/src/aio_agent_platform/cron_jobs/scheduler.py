"""Scheduler — APScheduler wrapper for cron job execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aio_agent_platform.cron_jobs.service import CronJobService
from aio_agent_platform.db.models import CronJob

logger = structlog.get_logger()

JobExecutor = Callable[[CronJob, AsyncSession], Awaitable[None]]


class Scheduler:
    """Manages scheduled task execution via APScheduler."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        executor: JobExecutor | None = None,
    ):
        self._scheduler = AsyncIOScheduler()
        self._session_factory = session_factory
        self._executor = executor

    def set_executor(self, executor: JobExecutor) -> None:
        self._executor = executor

    async def start(self) -> None:
        """Load active jobs from DB and schedule them."""
        async with self._session_factory() as db:
            jobs = await CronJobService.get_active_jobs(db)

        for job in jobs:
            self._schedule_job(job)

        self._scheduler.start()
        logger.info("scheduler_started", job_count=len(jobs))

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("scheduler_shutdown")

    def add_job(self, job: CronJob) -> None:
        """Add a job to the scheduler (called after DB creation)."""
        self._schedule_job(job)

    def remove_job(self, job_id: UUID) -> None:
        """Remove a job from the scheduler (called after DB deletion)."""
        job_id_str = str(job_id)
        try:
            self._scheduler.remove_job(job_id_str)
        except Exception:
            pass

    def update_job(self, job: CronJob) -> None:
        """Update a job in the scheduler (remove old, add new)."""
        self.remove_job(job.id)
        if job.is_active:
            self._schedule_job(job)

    def _schedule_job(self, job: CronJob) -> None:
        """Add a single CronJob to APScheduler."""
        trigger = self._build_trigger(job)
        if trigger is None:
            logger.warning(
                "cron_job_no_trigger",
                job_id=str(job.id),
                name=job.name,
            )
            return

        self._scheduler.add_job(
            self._execute,
            trigger=trigger,
            args=[job.id],
            id=str(job.id),
            name=job.name,
            replace_existing=True,
        )
        logger.info(
            "cron_job_scheduled",
            job_id=str(job.id),
            name=job.name,
        )

    def _build_trigger(self, job: CronJob) -> CronTrigger | DateTrigger | None:
        if job.cron_expr:
            parts = job.cron_expr.strip().split()
            if len(parts) == 5:
                return CronTrigger(
                    minute=parts[0],
                    hour=parts[1],
                    day=parts[2],
                    month=parts[3],
                    day_of_week=parts[4],
                )
        if job.run_at:
            return DateTrigger(run_date=job.run_at)
        return None

    async def _execute(self, job_id: UUID) -> None:
        """Execute a cron job — called by APScheduler when triggered."""
        async with self._session_factory() as db:
            from sqlalchemy import select

            result = await db.execute(
                select(CronJob).where(CronJob.id == job_id)
            )
            job = result.scalar_one_or_none()
            if job is None:
                logger.warning("cron_job_not_found", job_id=str(job_id))
                return

            if not job.is_active:
                return

            # Mark last_run_at
            await CronJobService.mark_run(db, job_id)
            await db.commit()

            # For one-shot jobs, deactivate after execution
            if job.run_at and not job.cron_expr:
                job.is_active = False
                await db.commit()
                self.remove_job(job_id)

            # Execute via registered executor
            if self._executor:
                try:
                    await self._executor(job, db)
                except Exception:
                    logger.exception(
                        "cron_job_execution_failed",
                        job_id=str(job_id),
                        name=job.name,
                    )
            else:
                logger.info(
                    "cron_job_fired_no_executor",
                    job_id=str(job_id),
                    name=job.name,
                )
