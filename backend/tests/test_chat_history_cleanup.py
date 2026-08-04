"""load_conversation_history 孤立 tool 消息清洗回归测试。

触发场景：token 预算截断在 assistant(tool_calls) 处切断，其 tool 结果因更晚
而被保留，导致历史开头出现无前置 assistant 的 role='tool' 消息，LLM 返回 400。
"""

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.core.chat import drop_orphan_tool_messages, load_conversation_history
from aio_agent_platform.db.connection import current_user_id
from aio_agent_platform.db.models import Message, Session, User
from aio_agent_platform.llm.client import LLMMessage, ToolCall


def _tool(role: str, call_id: str | None = None, tool_calls=None) -> LLMMessage:
    return LLMMessage(role=role, content="x", tool_call_id=call_id, tool_calls=tool_calls)


def test_drops_orphan_tool_messages_at_head():
    """截断留下的孤立 tool 消息被丢弃，且不会误删配对的 tool 结果。"""
    messages = [
        _tool("tool", "call_old_1"),
        _tool("tool", "call_old_2"),
        _tool("assistant", tool_calls=[ToolCall(id="call_a", name="f", arguments={})]),
        _tool("tool", "call_a"),
        _tool("user"),
    ]
    cleaned = drop_orphan_tool_messages(messages)
    assert cleaned == [
        _tool("assistant", tool_calls=[ToolCall(id="call_a", name="f", arguments={})]),
        _tool("tool", "call_a"),
        _tool("user"),
    ]


def test_keeps_intact_sequences():
    """正常配对的 assistant(tool_calls) → tool 结果原样保留。"""
    messages = [
        _tool("user"),
        _tool(
            "assistant",
            tool_calls=[
                ToolCall(id="c1", name="f", arguments={}),
                ToolCall(id="c2", name="g", arguments={}),
            ],
        ),
        _tool("tool", "c1"),
        _tool("tool", "c2"),
        _tool("user"),
    ]
    assert drop_orphan_tool_messages(messages) == messages


def test_mid_sequence_orphan_dropped():
    """窗口中部残留的无主 tool 消息（assistant 无 tool_calls 却被接上 tool）也被丢弃。"""
    messages = [
        _tool("assistant", tool_calls=[ToolCall(id="c1", name="f", arguments={})]),
        _tool("tool", "c1"),
        _tool("assistant"),  # 无 tool_calls
        _tool("tool", "c_orphan"),
        _tool("user"),
    ]
    cleaned = drop_orphan_tool_messages(messages)
    assert [m.role for m in cleaned] == ["assistant", "tool", "assistant", "user"]


# ---- 集成回归：截断边界切断 assistant(tool_calls) 时不得残留孤立 tool ----

_TENANT_ID = uuid.UUID("11111111-0000-0000-0000-000000000001")
_USER_ID = uuid.UUID("22222222-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("33333333-0000-0000-0000-000000000001")


def _make_hist_user() -> User:
    return User(
        id=_USER_ID,
        username="orphan-t",
        email="orphan-t@test.com",
        password_hash="fake",
        role="user",
        is_active=True,
        tenant_id=_TENANT_ID,
    )


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_history_records(db_session: AsyncSession):
    yield
    await db_session.execute(Message.__table__.delete().where(Message.user_id == _USER_ID))
    await db_session.execute(Session.__table__.delete().where(Session.user_id == _USER_ID))
    await db_session.execute(User.__table__.delete().where(User.id == _USER_ID))
    await db_session.commit()


@pytest.mark.asyncio
async def test_truncation_cut_at_tool_calls_drops_orphan_tools(db_session: AsyncSession):
    """截断边界切在 assistant(tool_calls) 与其 tool 结果之间时，输出开头不得是孤立 tool。

    消息权重：带 tool_calls 的 assistant 50 tokens，其余 1。预算 60 时保留尾部
    窗口，恰好砍掉最老的 assistant(c_old)，留下其 tool(c_old) → 清洗后应被丢弃。
    """
    db_session.add(_make_hist_user())
    db_session.add(Session(id=_SESSION_ID, user_id=_USER_ID, title="t"))
    db_session.add_all(
        [
            Message(
                session_id=_SESSION_ID, user_id=_USER_ID, role="assistant", content="",
                tool_calls=[{"id": "c_old", "name": "fetch", "arguments": {}, "result": "old"}],
                created_at=datetime(2026, 1, 1, 0, 0, 1),
            ),
            Message(
                session_id=_SESSION_ID, user_id=_USER_ID, role="user", content="q1",
                created_at=datetime(2026, 1, 1, 0, 0, 2),
            ),
            Message(
                session_id=_SESSION_ID, user_id=_USER_ID, role="assistant", content="",
                tool_calls=[{"id": "c_new", "name": "fetch", "arguments": {}, "result": "new"}],
                created_at=datetime(2026, 1, 1, 0, 0, 3),
            ),
            Message(
                session_id=_SESSION_ID, user_id=_USER_ID, role="user", content="q3",
                created_at=datetime(2026, 1, 1, 0, 0, 4),
            ),
        ]
    )
    await db_session.commit()

    def fake_est(msgs):
        def weight(m):
            return 50 if (m.role == "assistant" and m.tool_calls) else 1

        return sum(weight(m) for m in msgs)

    token = current_user_id.set(str(_USER_ID))
    try:
        await db_session.execute(text(f"SET LOCAL app.current_user_id = '{_USER_ID}'"))
        with patch("aio_agent_platform.core.chat._est_tokens", side_effect=fake_est), patch(
            "aio_agent_platform.core.chat.ContextBudget.from_settings",
            return_value=SimpleNamespace(history_budget=60),
        ):
            history, _ = await load_conversation_history(db_session, _SESSION_ID)
    finally:
        current_user_id.reset(token)

    roles = [m.role for m in history]
    # 最老的 assistant(c_old) 被截断后，其 tool(c_old) 不得作为孤岛残留
    assert roles == ["user", "assistant", "tool", "user"]

    pending: set[str] = set()
    for m in history:
        if m.role == "assistant":
            pending = {tc.id for tc in m.tool_calls} if m.tool_calls else set()
        elif m.role == "tool":
            assert m.tool_call_id in pending, f"孤儿 tool 消息: {m.tool_call_id}"
