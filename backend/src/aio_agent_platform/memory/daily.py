"""DailyMemoryService — per-day consolidated memories (one row per user per local day).

Day boundary is pinned to Asia/Shanghai, consistent with cron_jobs.scheduler.CRON_TIMEZONE.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import DailyMemory, Memory, Session
from aio_agent_platform.memory.service import MemoryService, create_default_provider_for_user

logger = structlog.get_logger()

DAY_TIMEZONE = ZoneInfo("Asia/Shanghai")

# Matches: 2026年8月9日 / 2026-08-09 / 2026/8/9 / 8月9日 / 8-9 / 8/9
_EXPLICIT_DATE_RE = re.compile(
    r"(?:(\d{4})\s*[年\-/])?\s*(\d{1,2})\s*(?:月|[\-/])\s*(\d{1,2})\s*日?"
)
_RELATIVE_DATES = {
    "今天": 0,
    "昨天": 1,
    "前天": 2,
    "大前天": 3,
}


def local_today() -> date:
    """Current date in the platform day timezone."""
    return datetime.now(DAY_TIMEZONE).date()


def day_range_utc(day: date) -> tuple[datetime, datetime]:
    """[start, end) aware datetimes bounding the local day."""
    start = datetime.combine(day, time.min, tzinfo=DAY_TIMEZONE)
    return start, start + timedelta(days=1)


def extract_dates(text: str, today: date | None = None) -> list[date]:
    """Extract mentioned dates from a user message (explicit dates + 今天/昨天/前天)."""
    today = today or local_today()
    found: list[date] = []

    for m in _EXPLICIT_DATE_RE.finditer(text):
        year_s, month_s, day_s = m.groups()
        year = int(year_s) if year_s else today.year
        month, day_num = int(month_s), int(day_s)
        try:
            d = date(year, month, day_num)
        except ValueError:
            continue
        # No year mentioned and the date is in the future — likely last year
        if not year_s and d > today:
            try:
                d = date(year - 1, month, day_num)
            except ValueError:
                continue
        if d not in found:
            found.append(d)

    for keyword, offset in _RELATIVE_DATES.items():
        if keyword in text:
            d = today - timedelta(days=offset)
            if d not in found:
                found.append(d)

    return found


class DailyMemoryService:
    """CRUD + LLM consolidation for daily memories. Stateless; explicit db session."""

    # ---- CRUD ----

    @staticmethod
    async def get_by_date(
        db: AsyncSession,
        user_id: UUID,
        day: date,
    ) -> DailyMemory | None:
        result = await db.execute(
            select(DailyMemory).where(
                DailyMemory.user_id == user_id, DailyMemory.date == day
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_range(
        db: AsyncSession,
        user_id: UUID,
        start: date | None = None,
        end: date | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[DailyMemory]:
        stmt = (
            select(DailyMemory)
            .where(DailyMemory.user_id == user_id)
            .order_by(DailyMemory.date.desc())
            .limit(limit)
            .offset(offset)
        )
        if start:
            stmt = stmt.where(DailyMemory.date >= start)
        if end:
            stmt = stmt.where(DailyMemory.date <= end)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def upsert(
        db: AsyncSession,
        user_id: UUID,
        day: date,
        content: str,
        highlights: list | None = None,
        source_session_ids: list | None = None,
    ) -> DailyMemory:
        """Insert or update the daily memory for (user_id, day)."""
        memory = await DailyMemoryService.get_by_date(db, user_id, day)
        search_vec = MemoryService._tokenize(content)
        if memory is None:
            memory = DailyMemory(
                user_id=user_id,
                date=day,
                content=content,
                search_vec=search_vec,
                highlights=highlights or [],
                source_session_ids=source_session_ids or [],
            )
            db.add(memory)
        else:
            memory.content = content
            memory.search_vec = search_vec
            if highlights is not None:
                memory.highlights = highlights
            if source_session_ids is not None:
                memory.source_session_ids = source_session_ids
        await db.flush()
        await db.refresh(memory)
        return memory

    @staticmethod
    async def delete_by_date(db: AsyncSession, user_id: UUID, day: date) -> bool:
        result = await db.execute(
            delete(DailyMemory).where(
                DailyMemory.user_id == user_id, DailyMemory.date == day
            )
        )
        await db.flush()
        return (result.rowcount or 0) > 0

    # ---- Prompt integration ----

    @staticmethod
    async def get_for_prompt(
        db: AsyncSession,
        user_id: UUID,
        user_message: str,
        recent_days: int = 2,
        max_items: int = 4,
    ) -> list[DailyMemory]:
        """
        Daily memories to inject into the system prompt.

        - Dates explicitly mentioned in the message (e.g. "8月9日", "昨天") → exact lookup.
        - Otherwise the most recent `recent_days` days (today + yesterday by default).
        """
        today = local_today()
        mentioned = extract_dates(user_message, today)

        target_dates: list[date] = []
        if mentioned:
            target_dates = mentioned
        else:
            target_dates = [today - timedelta(days=i) for i in range(recent_days)]

        result = await db.execute(
            select(DailyMemory).where(
                DailyMemory.user_id == user_id,
                DailyMemory.date.in_(target_dates),
            )
        )
        memories = list(result.scalars().all())

        # Fallback: message mentioned dates but nothing found — still give recency context
        if not memories and mentioned:
            result = await db.execute(
                select(DailyMemory)
                .where(DailyMemory.user_id == user_id)
                .order_by(DailyMemory.date.desc())
                .limit(recent_days)
            )
            memories = list(result.scalars().all())

        memories.sort(key=lambda m: m.date, reverse=True)
        return memories[:max_items]

    # ---- LLM consolidation ----

    @staticmethod
    async def _run_writer(user_id: UUID, prompt_text: str) -> tuple[str, list] | None:
        """Call the writer LLM and parse its JSON output. None = nothing usable."""
        from aio_agent_platform.llm import LLMMessage

        provider = await create_default_provider_for_user(user_id, temperature=0.3)
        if provider is None:
            logger.warning("没有可用的默认模型,跳过每日记忆生成")
            return None

        response = await provider.complete(
            messages=[LLMMessage(role="user", content=prompt_text)],
            max_tokens=2000,
        )

        raw = (response.content or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)

        content = (parsed.get("content") or "").strip()
        if not content:
            return None
        highlights = [
            h
            for h in (parsed.get("highlights") or [])
            if isinstance(h, dict) and h.get("text")
        ][:20]
        return content, highlights

    @staticmethod
    async def append_session_summary(
        user_id: UUID,
        session_id: UUID,
        summary: str,
    ) -> DailyMemory | None:
        """
        Incrementally merge a finished session's L3 summary into today's record.

        Creates its own DB session. Errors are logged, never propagated.
        """
        try:
            from aio_agent_platform.core.prompt import _env
            from aio_agent_platform.db.connection import current_user_id, get_session_factory

            summary = summary.strip()
            if not summary:
                return None

            day = local_today()
            factory = get_session_factory()
            async with factory() as db:
                current_user_id.set(str(user_id))
                await db.execute(
                    select(func.set_config("app.current_user_id", str(user_id), True))
                )
                existing = await DailyMemoryService.get_by_date(db, user_id, day)
                existing_content = existing.content if existing else None
                existing_session_ids = list(existing.source_session_ids or []) if existing else []

            if existing_content is not None:
                template = _env.get_template("daily_memory_merge.j2")
                prompt_text = template.render(
                    date=day.isoformat(),
                    existing_content=existing_content,
                    new_summary=summary,
                )
            else:
                template = _env.get_template("daily_memory_writer.j2")
                prompt_text = template.render(
                    date=day.isoformat(),
                    summaries=[summary],
                    session_count=1,
                )

            result = await DailyMemoryService._run_writer(user_id, prompt_text)
            if result is None:
                return None
            content, highlights = result

            if str(session_id) not in existing_session_ids:
                existing_session_ids.append(str(session_id))

            async with factory() as db:
                current_user_id.set(str(user_id))
                await db.execute(
                    select(func.set_config("app.current_user_id", str(user_id), True))
                )
                memory = await DailyMemoryService.upsert(
                    db,
                    user_id,
                    day,
                    content=content,
                    highlights=highlights,
                    source_session_ids=existing_session_ids,
                )
                await db.commit()

            logger.info(
                "daily_memory_appended",
                user_id=str(user_id),
                day=str(day),
                session_id=str(session_id),
            )
            return memory

        except Exception as e:
            logger.error(
                "daily_memory_append_failed",
                user_id=str(user_id),
                session_id=str(session_id),
                error=str(e),
                exc_info=True,
            )
            return None

    @staticmethod
    async def consolidate_day(user_id: UUID, day: date) -> DailyMemory | None:
        """
        Consolidate one day's sessions + L3 summaries into a daily memory via LLM.

        Creates its own DB session. Returns None when the day had no activity or
        the LLM produced nothing substantial. Errors are logged, never propagated.
        """
        try:
            from aio_agent_platform.core.prompt import _env
            from aio_agent_platform.db.connection import current_user_id, get_session_factory

            start, end = day_range_utc(day)
            factory = get_session_factory()

            async with factory() as db:
                current_user_id.set(str(user_id))
                await db.execute(
                    select(func.set_config("app.current_user_id", str(user_id), True))
                )

                sessions_result = await db.execute(
                    select(Session.id, Session.title)
                    .where(
                        Session.user_id == user_id,
                        Session.updated_at >= start,
                        Session.updated_at < end,
                    )
                    .order_by(Session.updated_at)
                )
                sessions = sessions_result.all()

                l3_result = await db.execute(
                    select(Memory)
                    .where(
                        Memory.user_id == user_id,
                        Memory.layer == "L3",
                        Memory.created_at >= start,
                        Memory.created_at < end,
                    )
                    .order_by(Memory.created_at)
                )
                l3_summaries = [m.content for m in l3_result.scalars().all()]

            if not sessions:
                logger.info(
                    "daily_memory_skip_no_sessions",
                    user_id=str(user_id),
                    day=str(day),
                )
                return None

            template = _env.get_template("daily_memory_writer.j2")
            prompt_text = template.render(
                date=day.isoformat(),
                summaries=l3_summaries,
                session_count=len(sessions),
            )

            result = await DailyMemoryService._run_writer(user_id, prompt_text)
            if result is None:
                return None
            content, highlights = result

            session_ids = [str(row.id) for row in sessions]

            async with factory() as db:
                current_user_id.set(str(user_id))
                await db.execute(
                    select(func.set_config("app.current_user_id", str(user_id), True))
                )
                memory = await DailyMemoryService.upsert(
                    db,
                    user_id,
                    day,
                    content=content,
                    highlights=highlights,
                    source_session_ids=session_ids,
                )
                await db.commit()

            logger.info(
                "daily_memory_consolidated",
                user_id=str(user_id),
                day=str(day),
                sessions=len(session_ids),
            )
            return memory

        except Exception as e:
            logger.error(
                "daily_memory_consolidation_failed",
                user_id=str(user_id),
                day=str(day),
                error=str(e),
                exc_info=True,
            )
            return None


async def run_daily_consolidation(day: date | None = None) -> dict:
    """
    System cron entry: consolidate `day` (default: yesterday) for every user
    who had session activity that day. Returns a small stats dict.
    """
    from aio_agent_platform.db.connection import get_session_factory

    day = day or (local_today() - timedelta(days=1))
    start, end = day_range_utc(day)

    factory = get_session_factory()
    async with factory() as db:
        result = await db.execute(
            select(Session.user_id)
            .where(Session.updated_at >= start, Session.updated_at < end)
            .group_by(Session.user_id)
        )
        user_ids = [row[0] for row in result.all()]

    consolidated = 0
    for uid in user_ids:
        memory = await DailyMemoryService.consolidate_day(uid, day)
        if memory is not None:
            consolidated += 1

    stats = {"day": str(day), "users": len(user_ids), "consolidated": consolidated}
    logger.info("daily_memory_consolidation_run", **stats)
    return stats
