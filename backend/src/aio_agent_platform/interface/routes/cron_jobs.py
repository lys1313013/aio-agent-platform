"""Cron job management routes — CRUD for scheduled tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.cron_jobs.service import CronJobService
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import ChannelConfig, CronJob, CronJobRun

router = APIRouter(prefix="/api/cron-jobs", tags=["cron-jobs"])


async def _validate_channel(db: AsyncSession, channel_id: UUID, tenant_id: UUID) -> None:
    """Ensure the notify channel exists and belongs to the user's tenant."""
    result = await db.execute(
        select(ChannelConfig.id).where(
            ChannelConfig.id == channel_id,
            ChannelConfig.tenant_id == tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=400, detail="推送渠道不存在或不属于当前租户")


# ---- Schemas ----


class CronJobOut(BaseModel):
    id: UUID
    agent_id: UUID | None = None
    name: str
    cron_expr: str | None = None
    run_at: datetime | None = None
    message: str | None = None
    task_config: dict
    channel_id: UUID | None = None
    is_active: bool
    last_run_at: datetime | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, j: CronJob) -> CronJobOut:
        return cls(
            id=j.id,
            agent_id=j.agent_id,
            name=j.name,
            cron_expr=j.cron_expr,
            run_at=j.run_at,
            message=j.message,
            task_config=j.task_config or {},
            channel_id=j.channel_id,
            is_active=j.is_active,
            last_run_at=j.last_run_at,
        )


class CronJobCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    agent_id: UUID | None = None
    cron_expr: str | None = Field(default=None, max_length=128)
    run_at: datetime | None = None
    message: str | None = None
    task_config: dict = Field(default_factory=dict)
    channel_id: UUID | None = None
    is_active: bool = True


class CronJobUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    agent_id: UUID | None = None
    cron_expr: str | None = Field(default=None, max_length=128)
    run_at: datetime | None = None
    message: str | None = None
    task_config: dict | None = None
    channel_id: UUID | None = None
    is_active: bool | None = None


class CronJobListResponse(BaseModel):
    items: list[CronJobOut]
    total: int


class CronJobRunOut(BaseModel):
    id: UUID
    job_id: UUID
    user_id: UUID
    status: str
    session_id: UUID | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    output: str | None = None
    error: str | None = None
    job_name: str | None = None

    model_config = {"from_attributes": True}


class CronJobRunListResponse(BaseModel):
    items: list[CronJobRunOut]
    total: int


# ---- Endpoints ----


@router.get("", response_model=CronJobListResponse)
async def list_cron_jobs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List cron jobs for current tenant."""
    jobs = await CronJobService.list_jobs(db, user.tenant_id, limit=limit, offset=offset)

    count_stmt = (
        select(func.count()).select_from(CronJob).where(CronJob.tenant_id == user.tenant_id)
    )
    total_result = await db.execute(count_stmt)
    total = total_result.scalar()

    return CronJobListResponse(
        items=[CronJobOut.from_model(j) for j in jobs],
        total=total,
    ).model_dump(mode="json")


@router.post("", response_model=CronJobOut, status_code=201)
async def create_cron_job(
    req: CronJobCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> dict:
    """Create a new cron job."""
    if not req.cron_expr and not req.run_at:
        raise HTTPException(
            status_code=400,
            detail="Either cron_expr or run_at must be provided",
        )
    if req.channel_id is not None:
        await _validate_channel(db, req.channel_id, user.tenant_id)

    job = await CronJobService.create_job(
        db=db,
        tenant_id=user.tenant_id,
        user_id=user.id,
        name=req.name,
        agent_id=req.agent_id,
        message=req.message,
        cron_expr=req.cron_expr,
        run_at=req.run_at,
        task_config=req.task_config,
        channel_id=req.channel_id,
        is_active=req.is_active,
    )
    await db.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.add_job(job)

    return CronJobOut.from_model(job).model_dump(mode="json")


@router.get("/runs", response_model=CronJobRunListResponse)
async def list_all_cron_job_runs(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    job_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List run records across all cron jobs of the current tenant.

    Supports filtering by job (tenant-validated) and status, with
    server-side pagination. LEFT JOINs CronJob so the job name survives
    even after the job itself is deleted.
    """
    filters = [CronJobRun.tenant_id == user.tenant_id]

    if job_id is not None:
        job = await CronJobService.get_job(db, job_id, user.tenant_id)
        if not job:
            raise HTTPException(status_code=404, detail="Cron job not found")
        filters.append(CronJobRun.job_id == job_id)

    if status:
        filters.append(CronJobRun.status == status)

    runs_stmt = (
        select(CronJobRun, CronJob.name)
        .outerjoin(CronJob, CronJob.id == CronJobRun.job_id)
        .where(*filters)
        .order_by(CronJobRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(runs_stmt)).all()

    count_stmt = (
        select(func.count()).select_from(CronJobRun).where(*filters)
    )
    total = (await db.execute(count_stmt)).scalar()

    items: list[CronJobRunOut] = []
    for run, job_name in rows:
        item = CronJobRunOut.model_validate(run)
        item.job_name = job_name
        items.append(item)

    return CronJobRunListResponse(
        items=items,
        total=total or 0,
    ).model_dump(mode="json")


@router.get("/{job_id}", response_model=CronJobOut)
async def get_cron_job(
    job_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a single cron job."""
    job = await CronJobService.get_job(db, job_id, user.tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return CronJobOut.from_model(job).model_dump(mode="json")


@router.put("/{job_id}", response_model=CronJobOut)
async def update_cron_job(
    job_id: UUID,
    req: CronJobUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> dict:
    """Update a cron job."""
    if req.channel_id is not None:
        await _validate_channel(db, req.channel_id, user.tenant_id)
    job = await CronJobService.update_job(
        db=db,
        job_id=job_id,
        tenant_id=user.tenant_id,
        name=req.name,
        agent_id=req.agent_id,
        message=req.message,
        cron_expr=req.cron_expr,
        run_at=req.run_at,
        task_config=req.task_config,
        channel_id=req.channel_id,
        clear_channel="channel_id" in req.model_fields_set and req.channel_id is None,
        is_active=req.is_active,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")
    await db.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.update_job(job)

    return CronJobOut.from_model(job).model_dump(mode="json")


@router.delete("/{job_id}", status_code=204)
async def delete_cron_job(
    job_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> None:
    """Delete a cron job."""
    deleted = await CronJobService.delete_job(db, job_id, user.tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cron job not found")
    await db.commit()

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.remove_job(job_id)


@router.post("/{job_id}/run", status_code=200)
async def run_cron_job_now(
    job_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> dict:
    """Manually trigger a single execution of a cron job.

    Reuses the exact same pipeline as the scheduler trigger: creates a run
    record, runs the job's agent, and finalizes the record with output/error.
    """
    job = await CronJobService.get_job(db, job_id, user.tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")

    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="调度器未初始化")

    await scheduler.run_now(job_id)
    return {"ok": True}


@router.get("/{job_id}/runs", response_model=CronJobRunListResponse)
async def list_cron_job_runs(
    job_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List execution logs for a cron job."""
    job = await CronJobService.get_job(db, job_id, user.tenant_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")

    runs_stmt = (
        select(CronJobRun)
        .where(CronJobRun.job_id == job_id)
        .order_by(CronJobRun.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    runs_result = await db.execute(runs_stmt)
    runs = list(runs_result.scalars().all())

    count_stmt = (
        select(func.count())
        .select_from(CronJobRun)
        .where(CronJobRun.job_id == job_id)
    )
    total = (await db.execute(count_stmt)).scalar()

    return CronJobRunListResponse(
        items=[CronJobRunOut.model_validate(r) for r in runs],
        total=total or 0,
    ).model_dump(mode="json")
