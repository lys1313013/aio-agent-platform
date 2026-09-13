"""Multi-instance cron regression tests with an isolated real Redis server."""

import asyncio
import shutil
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from apscheduler.triggers.date import DateTrigger
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from aio_agent_platform.cron_jobs.claims import (
    CLAIM_TTL_SECONDS,
    OccurrenceClaims,
    scheduled_run_time,
)
from aio_agent_platform.cron_jobs.scheduler import Scheduler


@pytest_asyncio.fixture
async def claims_pair(tmp_path):
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server is required for multi-instance integration tests")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [binary, "--bind", "127.0.0.1", "--port", str(port), "--save", "",
         "--appendonly", "no", "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    clients = [Redis(host="127.0.0.1", port=port) for _ in range(2)]
    try:
        for _ in range(100):
            try:
                await clients[0].ping()
                break
            except ConnectionError:
                await asyncio.sleep(0.02)
        else:
            pytest.fail("isolated Redis did not start")
        pair = [OccurrenceClaims(), OccurrenceClaims()]
        for claim, client in zip(pair, clients, strict=True):
            await claim.close()
            claim._client = client
        yield pair
    finally:
        for client in clients:
            await client.aclose()
        process.terminate()
        process.wait(timeout=5)


async def test_competing_instances_late_arrival_and_next_occurrence(claims_pair):
    first, second = claims_pair
    now = datetime.now(UTC)
    job_id = str(uuid4())
    # Equivalent timezone representations must map to the same Redis key.
    from zoneinfo import ZoneInfo
    results = await asyncio.gather(
        first.claim(job_id, now),
        second.claim(job_id, now.astimezone(ZoneInfo("Asia/Shanghai"))),
    )
    assert sum(results) == 1
    assert not await second.claim(job_id, now)
    assert await second.claim(job_id, now + timedelta(microseconds=1))
    assert await second.claim(str(uuid4()), now)
    keys = await first._client.keys("aio:cron:claim:*")
    assert len(keys) == 3
    ttls = [await first._client.ttl(key) for key in keys]
    assert all(0 < ttl <= CLAIM_TTL_SECONDS for ttl in ttls)


async def test_invalid_or_expired_occurrence_never_claims():
    claims = OccurrenceClaims()
    claims._client = AsyncMock()
    for when in [None, datetime.now(), datetime.now(UTC) - timedelta(days=2),
                 datetime.now(UTC) + timedelta(minutes=1)]:
        assert not await claims.claim("job", when)
    claims._client.set.assert_not_awaited()


async def test_redis_outage_fails_closed():
    claims = OccurrenceClaims()
    claims._client = AsyncMock()
    claims._client.set.side_effect = ConnectionError("offline")
    assert not await claims.claim("job", datetime.now(UTC))


def scheduler_with_job(job, claims):
    db = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    scheduler = Scheduler(factory)
    scheduler._claims = claims
    scheduler._load_job = AsyncMock(return_value=job)
    scheduler._execute_job = AsyncMock()
    return scheduler


@pytest.mark.parametrize("one_shot", [False, True])
async def test_two_schedulers_only_execute_once(claims_pair, one_shot):
    job_id = uuid4()
    jobs = [SimpleNamespace(id=job_id, is_active=True,
                            run_at=datetime.now(UTC) if one_shot else None,
                            cron_expr=None if one_shot else "* * * * *") for _ in range(2)]
    schedulers = [scheduler_with_job(job, claim)
                  for job, claim in zip(jobs, claims_pair, strict=True)]
    token = scheduled_run_time.set(datetime.now(UTC))
    try:
        await asyncio.gather(*(s._execute(job_id) for s in schedulers))
        await schedulers[1]._execute(job_id)  # arrival after winner has finished
    finally:
        scheduled_run_time.reset(token)
    assert sum(s._execute_job.await_count for s in schedulers) == 1
    if one_shot:
        assert sum(not job.is_active for job in jobs) == 1


async def test_failed_attempt_keeps_claim(claims_pair):
    job = SimpleNamespace(id=uuid4(), is_active=True, run_at=None, cron_expr="* * * * *")
    first, second = [scheduler_with_job(job, claim) for claim in claims_pair]
    first._execute_job.side_effect = RuntimeError("execution failed")
    token = scheduled_run_time.set(datetime.now(UTC))
    try:
        with pytest.raises(RuntimeError):
            await first._execute(job.id)
        await second._execute(job.id)
    finally:
        scheduled_run_time.reset(token)
    second._execute_job.assert_not_awaited()


async def test_manual_execution_bypasses_automatic_claim():
    claims = AsyncMock()
    claims.claim.side_effect = AssertionError("manual runs must not claim")
    job = SimpleNamespace(id=uuid4(), is_active=False)
    scheduler = scheduler_with_job(job, claims)
    await scheduler.run_now(job.id)
    scheduler._execute_job.assert_awaited_once()


async def test_scheduler_preserves_actual_fire_time_across_delayed_dispatch():
    scheduler = Scheduler(MagicMock())
    fired = asyncio.Event()
    captured = []
    planned = datetime.now(UTC) - timedelta(seconds=30)

    async def capture():
        captured.append(scheduled_run_time.get())
        fired.set()

    scheduler._scheduler.add_job(capture, DateTrigger(run_date=planned))
    scheduler._scheduler.start()
    try:
        await asyncio.wait_for(fired.wait(), timeout=2)
        assert captured == [planned]
        assert scheduled_run_time.get() is None
    finally:
        await scheduler.shutdown()
        await asyncio.sleep(0)


async def test_system_jobs_share_occurrence_claim(claims_pair):
    handler = AsyncMock()
    schedulers = [Scheduler(MagicMock()) for _ in range(2)]
    token = scheduled_run_time.set(datetime.now(UTC))
    try:
        for scheduler, claims in zip(schedulers, claims_pair, strict=True):
            scheduler._claims = claims
            scheduler.add_system_job("daily-memory", "40 0 * * *", handler)
        await asyncio.gather(*(s._scheduler.get_jobs()[0].func() for s in schedulers))
    finally:
        scheduled_run_time.reset(token)
    handler.assert_awaited_once()
