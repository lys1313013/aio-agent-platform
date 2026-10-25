"""CronJobService — CRUD for scheduled tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.core.execution_scope import (
    ExecutionAccessError,
    require_agent_tenant,
    require_execution_user,
)
from aio_agent_platform.cron_jobs.expressions import normalize_cron
from aio_agent_platform.db.models import ChannelConfig, CronJob, LLMModel
from aio_agent_platform.llm.catalog import available_models_query

logger = structlog.get_logger()


class CronJobConflict(ValueError):
    pass


async def validate_job_scope(db, tenant_id, user_id, agent_id, channel_id):
    await require_execution_user(db, user_id, tenant_id)
    if agent_id is not None and await require_agent_tenant(db, user_id, agent_id) != tenant_id:
        raise ExecutionAccessError("智能体与任务所属租户不一致")
    if channel_id is not None:
        channel = await db.scalar(select(ChannelConfig.id).where(
            ChannelConfig.id == channel_id, ChannelConfig.tenant_id == tenant_id,
            ChannelConfig.status == "enabled",
        ))
        if channel is None:
            raise ExecutionAccessError("通知渠道不可用或不属于任务租户")


async def validate_job_model(db: AsyncSession, tenant_id: UUID, model_id: UUID) -> None:
    """An explicit override must remain usable in the job's tenant; never fall back."""
    model = await db.scalar(
        available_models_query(tenant_id).with_only_columns(LLMModel.id).where(LLMModel.id == model_id)
    )
    if model is None:
        raise ValueError("指定的执行模型不存在、已停用或不属于任务租户")


class CronJobService:
    """Stateless service for managing scheduled tasks."""

    @staticmethod
    async def list_jobs(
        db: AsyncSession,
        tenant_id: UUID,
        limit: int = 50,
        offset: int = 0,
        *,
        actor_user_id: UUID,
    ) -> list[CronJob]:
        await require_execution_user(db, actor_user_id, tenant_id)
        stmt = (
            select(CronJob)
            .where(CronJob.tenant_id == tenant_id, CronJob.user_id == actor_user_id)
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
        tenant_id: UUID | None,
        *,
        actor_user_id: UUID,
    ) -> CronJob | None:
        query = select(CronJob).where(CronJob.id == job_id, CronJob.user_id == actor_user_id)
        if tenant_id is not None:
            query = query.where(CronJob.tenant_id == tenant_id)
        result = await db.execute(query)
        job = result.scalar_one_or_none()
        if job is not None:
            await require_execution_user(db, actor_user_id, job.tenant_id)
        return job

    @staticmethod
    async def create_job(
        db: AsyncSession,
        tenant_id: UUID | None,
        user_id: UUID,
        name: str,
        task_config: dict,
        agent_id: UUID | None = None,
        message: str | None = None,
        cron_expr: str | None = None,
        run_at: datetime | None = None,
        channel_id: UUID | None = None,
        is_active: bool = True,
        request_id: UUID | None = None,
        allow_duplicate: bool = False,
        model_id: UUID | None = None,
    ) -> CronJob:
        # 租户必须显式解析，禁止静默回退到默认租户（否则任务归错租户、管理页不可见）。
        tenant_id = await CronJobService._resolve_tenant(
            db, tenant_id, user_id, agent_id
        )
        await validate_job_scope(db, tenant_id, user_id, agent_id, channel_id)
        if model_id is not None:
            await validate_job_model(db, tenant_id, model_id)
        if cron_expr:
            cron_expr = normalize_cron(cron_expr)
        elif run_at is None:
            raise ValueError("必须配置 cron_expr 或 run_at")
        if run_at is not None and run_at.tzinfo is None:
            from aio_agent_platform.cron_jobs.expressions import TIMEZONE
            run_at = run_at.replace(tzinfo=TIMEZONE)
        values = {"tenant_id": tenant_id, "user_id": user_id, "agent_id": agent_id, "name": name,
                  "message": message, "cron_expr": cron_expr, "run_at": run_at,
                  "task_config": task_config, "channel_id": channel_id, "is_active": is_active,
                  "model_id": model_id}
        # Serialize submissions by owner, including concurrent retries with different names.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                         {"key": f"cron-create:{tenant_id}:{user_id}"})
        job_id = uuid5(NAMESPACE_URL, f"aio-cron:{tenant_id}:{user_id}:{request_id}") if request_id else None
        if job_id is not None:
            existing = await db.get(CronJob, job_id)
            if existing is not None:
                if any(getattr(existing, k) != v for k, v in values.items()):
                    raise CronJobConflict("请求标识已用于不同配置，请重新打开创建窗口")
                return existing
        matches = (await db.scalars(select(CronJob).where(
            CronJob.tenant_id == tenant_id, CronJob.user_id == user_id,
            CronJob.name == name, CronJob.is_active == is_active,
        ))).all()
        for existing in matches:
            old_values = {k: getattr(existing, k) for k in values}
            if old_values["cron_expr"]:
                old_values["cron_expr"] = normalize_cron(old_values["cron_expr"], legacy=True)
            if old_values == values:
                if request_id is not None:
                    raise CronJobConflict(f"已存在相同任务 {existing.id}，请使用已有任务")
                return existing
        if matches and not allow_duplicate:
            raise CronJobConflict("已存在同名但配置不同的任务；请编辑已有任务，或确认需要并存")
        job = CronJob(**values, **({"id": job_id} if job_id is not None else {}))
        db.add(job)
        await db.flush()
        await db.refresh(job)
        logger.info("cron_job_created", job_id=str(job.id), name=name)
        return job

    @staticmethod
    async def _resolve_tenant(
        db: AsyncSession,
        tenant_id: UUID | None,
        user_id: UUID,
        agent_id: UUID | None,
    ) -> UUID:
        """解析定时任务所属租户。

        优先级：显式传入 → 已授权智能体的租户。
        均解析不到时抛错（响亮失败），绝不回退到默认租户。
        """
        if tenant_id is not None:
            return tenant_id
        if agent_id is not None:
            return await require_agent_tenant(db, user_id, agent_id)
        raise ExecutionAccessError("创建任务必须明确执行租户或智能体，不能回退用户当前租户")

    @staticmethod
    async def update_job(
        db: AsyncSession,
        job_id: UUID,
        tenant_id: UUID | None,
        name: str | None = None,
        agent_id: UUID | None = None,
        message: str | None = None,
        cron_expr: str | None = None,
        run_at: datetime | None = None,
        task_config: dict | None = None,
        channel_id: UUID | None = None,
        is_active: bool | None = None,
        clear_channel: bool = False,
        model_id: UUID | None = None,
        clear_model: bool = False,
        *,
        actor_user_id: UUID,
    ) -> CronJob | None:
        job = await CronJobService.get_job(db, job_id, tenant_id, actor_user_id=actor_user_id)
        if not job:
            return None

        await validate_job_scope(db, job.tenant_id, job.user_id,
                                 agent_id if agent_id is not None else job.agent_id,
                                 None if clear_channel else (channel_id if channel_id is not None else job.channel_id))
        if model_id is not None and not clear_model:
            await validate_job_model(db, job.tenant_id, model_id)
        if cron_expr is not None:
            # A legacy unchanged string is not reinterpreted on unrelated edits.
            cron_expr = normalize_cron(cron_expr, legacy=cron_expr == job.cron_expr) if cron_expr else cron_expr
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
        if clear_model:
            job.model_id = None
        elif model_id is not None:
            job.model_id = model_id
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
        tenant_id: UUID | None,
        *,
        actor_user_id: UUID,
    ) -> bool:
        job = await CronJobService.get_job(db, job_id, tenant_id, actor_user_id=actor_user_id)
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
