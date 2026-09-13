"""Redis occurrence claims: at most one automatic attempt per scheduled time.

Claims are deliberately retained on success, failure and cancellation. This
prevents late replicas from executing the same occurrence after the winner exits.
An interrupted attempt is not automatically retried.
"""

from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

import redis.asyncio as aioredis
import structlog
from apscheduler.executors.asyncio import AsyncIOExecutor
from redis.exceptions import RedisError

from aio_agent_platform.core.config import settings

logger = structlog.get_logger()
MISFIRE_GRACE_SECONDS = 60
CLAIM_TTL_SECONDS = 86400
scheduled_run_time: ContextVar[datetime | None] = ContextVar(
    "cron_scheduled_run_time", default=None
)


class OccurrenceExecutor(AsyncIOExecutor):
    """Pass APScheduler's actual fire time into the coroutine task context.

All jobs on this executor are async and coalesced to one occurrence. Setting
context before create_task preserves the time even if execution crosses a minute.
"""

    def _do_submit_job(self, job, run_times):
        if len(run_times) != 1:
            raise ValueError("OccurrenceExecutor requires coalesce=True")
        token = scheduled_run_time.set(run_times[0])
        try:
            super()._do_submit_job(job, run_times)
        finally:
            scheduled_run_time.reset(token)


class OccurrenceClaims:
    def __init__(self):
        self._client = aioredis.Redis.from_url(
            settings.redis.url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    async def claim(self, job_id: str, scheduled_at: datetime | None) -> bool:
        if scheduled_at is None or scheduled_at.tzinfo is None:
            logger.error("cron_claim_missing_scheduled_time", job_id=job_id)
            return False
        scheduled_at = scheduled_at.astimezone(UTC)
        age = (datetime.now(UTC) - scheduled_at).total_seconds()
        # Never replay an occurrence after its claim may have expired.
        if age < 0 or age > MISFIRE_GRACE_SECONDS:
            logger.warning("cron_claim_outside_grace", job_id=job_id, age=age)
            return False
        key = f"aio:cron:claim:{job_id}:{scheduled_at.isoformat()}"
        try:
            claimed = await self._client.set(
                key, str(uuid4()), nx=True, ex=CLAIM_TTL_SECONDS
            )
        except RedisError as exc:
            # Fail closed: a Redis outage must not fan out into duplicate sends.
            logger.error(
                "cron_claim_unavailable", job_id=job_id, error_type=type(exc).__name__
            )
            return False
        if not claimed:
            logger.info("cron_occurrence_already_claimed", job_id=job_id)
        return bool(claimed)

    async def close(self) -> None:
        await self._client.aclose()
