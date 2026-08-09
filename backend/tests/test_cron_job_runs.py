"""Tests for cron job scheduler linkage and execution run logging."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from aio_agent_platform.cron_jobs.scheduler import Scheduler
from aio_agent_platform.db.models import CronJob, CronJobRun, Tenant, User
from aio_agent_platform.interface.api import app


@pytest_asyncio.fixture
async def cron_client(client: AsyncClient, db_session: AsyncSession, engine):
    """HTTP client with auth bypassed and a real Scheduler on app.state."""
    from aio_agent_platform.auth.dependencies import get_current_user

    user = User(
        id=uuid4(),
        username="cron-tester",
        email="cron@test.com",
        password_hash="fake",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    async def override_current_user():
        return user

    scheduler = Scheduler(_factory(engine))
    app.state.scheduler = scheduler
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        yield client, scheduler, user
    finally:
        app.state.scheduler = None



def _factory(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _create_job(engine, **kwargs):
    factory = _factory(engine)
    async with factory() as db:
        fields = {
            "user_id": uuid4(),
            "name": "test job",
            "cron_expr": "*/1 * * * *",
            "message": "hello",
            "task_config": {},
            "is_active": True,
        }
        fields.update(kwargs)
        job = CronJob(**fields)
        db.add(job)
        await db.commit()
        return job.id, job


async def _get_runs(factory, job_id):
    async with factory() as db:
        runs = (
            await db.execute(
                select(CronJobRun).where(CronJobRun.job_id == job_id)
            )
        ).scalars().all()
        return runs


async def test_execute_records_run_on_success(engine):
    factory = _factory(engine)
    job_id, _ = await _create_job(engine)

    captured = {}

    async def exec_ok(job, db, run_id):
        run = await db.get(CronJobRun, run_id)
        captured["initial_status"] = run.status
        captured["started_at"] = run.started_at
        run.status = "success"
        run.output = "done"
        run.duration_ms = 500
        run.finished_at = datetime.now(UTC)
        await db.commit()

    scheduler = Scheduler(factory, executor=exec_ok)
    await scheduler._execute(job_id)

    assert captured["initial_status"] == "running"
    assert captured["started_at"] is not None

    runs = await _get_runs(factory, job_id)
    assert len(runs) == 1
    r = runs[0]
    assert r.status == "success"
    assert r.output == "done"
    assert r.duration_ms == 500
    assert r.finished_at is not None

    # last_run_at should be updated
    async with factory() as db:
        job = await db.get(CronJob, job_id)
        assert job.last_run_at is not None


async def test_execute_marks_run_failed_when_executor_raises(engine):
    factory = _factory(engine)
    job_id, _ = await _create_job(engine)

    async def exec_fail(job, db, run_id):
        raise RuntimeError("boom")

    scheduler = Scheduler(factory, executor=exec_fail)
    await scheduler._execute(job_id)

    runs = await _get_runs(factory, job_id)
    assert len(runs) == 1
    r = runs[0]
    assert r.status == "failed"
    assert r.error == "boom"
    assert r.finished_at is not None
    assert r.duration_ms is not None


async def test_execute_never_leaves_run_stuck_when_executor_noops(engine):
    """Regression: an executor that returns without finalizing must not leave
    the run stuck at 'running' (e.g. job without message/agent, early return)."""
    factory = _factory(engine)
    job_id, _ = await _create_job(engine)

    async def exec_noop(job, db, run_id):
        pass  # forgets to finalize the run record

    scheduler = Scheduler(factory, executor=exec_noop)
    await scheduler._execute(job_id)

    runs = await _get_runs(factory, job_id)
    assert len(runs) == 1
    r = runs[0]
    assert r.status == "failed"
    assert "finalizing" in r.error
    assert r.finished_at is not None
    assert r.duration_ms is not None


async def test_one_shot_job_deactivated_after_execution(engine):
    factory = _factory(engine)
    job_id, _ = await _create_job(
        engine,
        run_at=datetime.now(UTC) + timedelta(minutes=1),
        cron_expr=None,
    )

    async def exec_ok(job, db, run_id):
        run = await db.get(CronJobRun, run_id)
        run.status = "success"
        await db.commit()

    scheduler = Scheduler(factory, executor=exec_ok)
    await scheduler._execute(job_id)

    async with factory() as db:
        job = await db.get(CronJob, job_id)
        assert job.is_active is False


async def test_scheduler_add_update_remove(engine):
    """Routes/handlers call add_job/update_job/remove_job — verify they sync APScheduler."""
    factory = _factory(engine)
    job_id, job = await _create_job(engine)
    scheduler = Scheduler(factory)

    scheduler.add_job(job)
    assert [j.id for j in scheduler._scheduler.get_jobs()] == [str(job_id)]

    job.is_active = False
    scheduler.update_job(job)
    assert scheduler._scheduler.get_jobs() == []

    job.is_active = True
    scheduler.update_job(job)
    assert len(scheduler._scheduler.get_jobs()) == 1

    scheduler.remove_job(job_id)
    assert scheduler._scheduler.get_jobs() == []


async def _create_via_api(client, name="rest job"):
    resp = await client.post(
        "/api/cron-jobs",
        json={
            "name": name,
            "cron_expr": "0 9 * * *",
            "message": "hello",
            "task_config": {},
            "is_active": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_rest_create_syncs_scheduler(cron_client):
    """Creating a job via REST must register it in the running scheduler."""
    client, scheduler, _ = cron_client
    job_id = await _create_via_api(client)
    scheduled = [j.id for j in scheduler._scheduler.get_jobs()]
    assert job_id in scheduled


async def test_rest_update_pause_removes_from_scheduler(cron_client):
    client, scheduler, _ = cron_client
    job_id = await _create_via_api(client)
    resp = await client.put(f"/api/cron-jobs/{job_id}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert scheduler._scheduler.get_jobs() == []


async def test_rest_delete_unsyncs_scheduler(cron_client):
    client, scheduler, _ = cron_client
    job_id = await _create_via_api(client)
    resp = await client.delete(f"/api/cron-jobs/{job_id}")
    assert resp.status_code == 204, resp.text
    assert scheduler._scheduler.get_jobs() == []


async def test_rest_runs_endpoint(cron_client, db_session: AsyncSession):
    from uuid import UUID

    client, _, user = cron_client
    job_id = await _create_via_api(client)
    job_uuid = UUID(job_id)

    resp = await client.get(f"/api/cron-jobs/{job_id}/runs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # a run for a DIFFERENT job is filtered out
    other = CronJobRun(
        job_id=uuid4(),
        user_id=user.id,
        status="failed",
        error="boom",
    )
    db_session.add(other)
    await db_session.flush()

    # a run for THIS job shows up
    run = CronJobRun(
        job_id=job_uuid,
        user_id=user.id,
        status="success",
        output="ok",
    )
    db_session.add(run)
    await db_session.flush()

    resp2 = await client.get(f"/api/cron-jobs/{job_id}/runs")
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1
    assert resp2.json()["items"][0]["status"] == "success"
    assert resp2.json()["items"][0]["output"] == "ok"


# ---- notify_channel tool (silent-by-default cron notification) ----


async def test_notify_channel_handler_pushes_text():
    from aio_agent_platform.channels.cron_notify import (
        CronNotifyContext,
        current_cron_notify_ctx,
        handle_notify_channel,
    )

    pushed = []

    async def fake_push(text: str) -> bool:
        pushed.append(text)
        return True

    ctx = CronNotifyContext(push_fn=fake_push, job_id="j", channel_id="c")
    token = current_cron_notify_ctx.set(ctx)
    try:
        result = await handle_notify_channel({"text": "发现问题"}, "u", "s")
    finally:
        current_cron_notify_ctx.reset(token)

    assert pushed == ["发现问题"]
    assert "已通过渠道通知用户" in result


async def test_notify_channel_handler_push_failed_reports_to_agent():
    from aio_agent_platform.channels.cron_notify import (
        CronNotifyContext,
        current_cron_notify_ctx,
        handle_notify_channel,
    )

    async def fake_push(text: str) -> bool:
        return False

    ctx = CronNotifyContext(push_fn=fake_push, job_id="j", channel_id="c")
    token = current_cron_notify_ctx.set(ctx)
    try:
        result = await handle_notify_channel({"text": "hi"}, "u", "s")
    finally:
        current_cron_notify_ctx.reset(token)

    assert "未成功" in result


async def test_notify_channel_handler_without_ctx_returns_unsupported():
    from aio_agent_platform.channels.cron_notify import handle_notify_channel

    result = await handle_notify_channel({"text": "hi"}, "u", "s")
    assert "不支持渠道推送" in result


async def test_notify_channel_handler_rejects_empty_text():
    from aio_agent_platform.channels.cron_notify import (
        CronNotifyContext,
        current_cron_notify_ctx,
        handle_notify_channel,
    )

    async def fake_push(text: str) -> bool:
        return True

    ctx = CronNotifyContext(push_fn=fake_push, job_id="j", channel_id="c")
    token = current_cron_notify_ctx.set(ctx)
    try:
        result = await handle_notify_channel({"text": "  "}, "u", "s")
    finally:
        current_cron_notify_ctx.reset(token)

    assert "text 参数" in result


# ---- Tenant isolation ----

async def test_cron_job_tenant_isolation(client, db_session: AsyncSession):
    """A user must not see or manage a cron job belonging to another tenant."""
    from aio_agent_platform.auth.dependencies import get_current_user

    tenant_a = Tenant(name="tenant-a", slug="cron-iso-a")
    tenant_b = Tenant(name="tenant-b", slug="cron-iso-b")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    user_a = User(
        id=uuid4(), username="cron-iso-user-a", email="a@test.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant_a.id,
    )
    user_b = User(
        id=uuid4(), username="cron-iso-user-b", email="b@test.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant_b.id,
    )
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    async def act_as(user):
        async def override_current_user():
            return user
        app.dependency_overrides[get_current_user] = override_current_user

    # user A creates a job in tenant A
    await act_as(user_a)
    resp = await client.post(
        "/api/cron-jobs",
        json={"name": "tenant-a job", "cron_expr": "0 9 * * *", "message": "hi", "task_config": {}},
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]

    # user B cannot fetch, update, delete, or see the job
    await act_as(user_b)
    assert (await client.get(f"/api/cron-jobs/{job_id}")).status_code == 404
    resp = await client.put(f"/api/cron-jobs/{job_id}", json={"name": "hijack"})
    assert resp.status_code == 404
    assert (await client.delete(f"/api/cron-jobs/{job_id}")).status_code == 404
    assert (await client.get(f"/api/cron-jobs/{job_id}/runs")).status_code == 404

    resp = await client.get("/api/cron-jobs")
    assert resp.status_code == 200
    assert job_id not in [j["id"] for j in resp.json()["items"]]

    # job still exists for tenant A
    await act_as(user_a)
    resp = await client.get(f"/api/cron-jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


# ---- Tool handler tenant isolation (agent auto-created cron jobs) ----
# The `create_cron_job` / `list_cron_jobs` / `delete_cron_job` tools run through
# cron_jobs.handlers, which resolve tenant_id from the invoking user. Patch the
# module-level session factory so handlers hit the test DB instead of the
# configured remote DB.


def _test_factory(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_handler_create_cron_job_sets_tenant_id(engine, db_session, monkeypatch):
    from aio_agent_platform.cron_jobs.handlers import handle_create_cron_job

    tenant = Tenant(name="handler-tenant", slug="cron-handler-create")
    db_session.add(tenant)
    await db_session.flush()
    user = User(
        id=uuid4(), username="handler-user", email="h@test.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant.id,
    )
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()

    factory = _test_factory(engine)
    monkeypatch.setattr(
        "aio_agent_platform.cron_jobs.handlers.get_session_factory",
        lambda: factory,
    )

    resp = await handle_create_cron_job(
        {"name": "auto job", "cron_expr": "0 8 * * *", "message": "hello"},
        str(user.id),
        "session-x",
    )
    assert "successfully" in resp

    async with factory() as db:
        job = (
            await db.execute(select(CronJob).where(CronJob.name == "auto job"))
        ).scalar_one()
        assert job.user_id == user.id
        assert job.tenant_id == tenant.id


async def test_handler_list_cron_jobs_scoped_to_tenant(engine, db_session, monkeypatch):
    from aio_agent_platform.cron_jobs.handlers import handle_list_cron_jobs

    tenant_a = Tenant(name="ta", slug="cron-handler-list-a")
    tenant_b = Tenant(name="tb", slug="cron-handler-list-b")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()
    user_a = User(
        id=uuid4(), username="cron-h-u-a", email="a@t.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant_a.id,
    )
    user_b = User(
        id=uuid4(), username="cron-h-u-b", email="b@t.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant_b.id,
    )
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    await db_session.commit()

    factory = _test_factory(engine)
    monkeypatch.setattr(
        "aio_agent_platform.cron_jobs.handlers.get_session_factory",
        lambda: factory,
    )
    async with factory() as db:
        db.add_all([
            CronJob(
                tenant_id=tenant_a.id, user_id=user_a.id, name="job-a",
                cron_expr="0 9 * * *", task_config={}, is_active=True,
            ),
            CronJob(
                tenant_id=tenant_b.id, user_id=user_b.id, name="job-b",
                cron_expr="0 9 * * *", task_config={}, is_active=True,
            ),
        ])
        await db.commit()

    resp = await handle_list_cron_jobs({}, str(user_a.id), "s")
    assert "job-a" in resp
    assert "job-b" not in resp


async def test_handler_delete_cron_job_scoped_to_tenant(engine, db_session, monkeypatch):
    from aio_agent_platform.cron_jobs.handlers import handle_delete_cron_job

    tenant_a = Tenant(name="ta", slug="cron-handler-del-a")
    tenant_b = Tenant(name="tb", slug="cron-handler-del-b")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()
    user_a = User(
        id=uuid4(), username="cron-h-d-a", email="a@t.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant_a.id,
    )
    user_b = User(
        id=uuid4(), username="cron-h-d-b", email="b@t.com",
        password_hash="x", role="admin", is_active=True, tenant_id=tenant_b.id,
    )
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    await db_session.commit()

    factory = _test_factory(engine)
    monkeypatch.setattr(
        "aio_agent_platform.cron_jobs.handlers.get_session_factory",
        lambda: factory,
    )
    async with factory() as db:
        job_a = CronJob(
            tenant_id=tenant_a.id, user_id=user_a.id, name="job-a",
            cron_expr="0 9 * * *", task_config={}, is_active=True,
        )
        job_b = CronJob(
            tenant_id=tenant_b.id, user_id=user_b.id, name="job-b",
            cron_expr="0 9 * * *", task_config={}, is_active=True,
        )
        db.add_all([job_a, job_b])
        await db.commit()
        job_a_id, job_b_id = job_a.id, job_b.id

    # cross-tenant delete must fail; own-tenant delete must succeed
    resp = await handle_delete_cron_job({"job_id": str(job_b_id)}, str(user_a.id), "s")
    assert "not found" in resp
    resp = await handle_delete_cron_job({"job_id": str(job_a_id)}, str(user_a.id), "s")
    assert "deleted" in resp

    async with factory() as db:
        remaining = (
            await db.execute(select(CronJob).where(CronJob.name == "job-b"))
        ).scalar_one_or_none()
        assert remaining is not None
