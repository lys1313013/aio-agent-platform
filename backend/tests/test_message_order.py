"""History ordering regression tests; use only an isolated SQLite database."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import selectinload

from aio_agent_platform.core.chat import load_conversation_history
from aio_agent_platform.db.models import Message, Session


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def history_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    async with engine.begin() as conn:
        for model in (Session, Message):
            await conn.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id, user_id = uuid4(), uuid4()
    async with factory() as db:
        db.add(Session(id=session_id, user_id=user_id, title="ordering"))
        start = datetime(2026, 10, 1, tzinfo=UTC)
        # Insert replies first and give them smaller UUIDs: neither physical
        # row order nor ordering only by timestamp + UUID may define the turn.
        for turn in range(2):
            timestamp = start + timedelta(seconds=turn)
            db.add_all([
                Message(id=UUID(int=(0xa << 124) + turn * 2 + 1), session_id=session_id,
                        user_id=user_id, role="assistant", content=f"reply-{turn}", created_at=timestamp),
                Message(id=UUID(int=(0xa << 124) + turn * 2 + 2), session_id=session_id,
                        user_id=user_id, role="user", content=f"prompt-{turn}", created_at=timestamp),
            ])
        await db.commit()
    try:
        async with factory() as db:
            yield db, session_id
    finally:
        await engine.dispose()


async def test_session_reload_keeps_prompt_before_reply_with_identical_timestamps(history_db):
    db, session_id = history_db
    session = (await db.execute(
        select(Session).where(Session.id == session_id).options(selectinload(Session.messages))
    )).scalar_one()
    assert [message.content for message in session.messages] == [
        "prompt-0", "reply-0", "prompt-1", "reply-1",
    ]


async def test_history_export_order_preserves_turns(history_db):
    db, session_id = history_db
    messages = (await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(*Message.chronological_order())
    )).scalars().all()
    assert [message.content for message in messages] == ["prompt-0", "reply-0", "prompt-1", "reply-1"]


@pytest.mark.parametrize("limit, expected", [
    (4, ["prompt-0", "reply-0", "prompt-1", "reply-1"]),
    (3, ["reply-0", "prompt-1", "reply-1"]),
    (1, ["reply-1"]),
])
async def test_llm_recent_history_reverses_all_sort_keys(history_db, limit, expected):
    db, session_id = history_db
    messages, _ = await load_conversation_history(db, session_id, limit=limit)
    assert [message.content for message in messages] == expected
