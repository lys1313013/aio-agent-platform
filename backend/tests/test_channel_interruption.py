"""渠道任务中断：串行排队、/stop、撤回即停。

覆盖需求文档 docs/12-飞书渠道接入/01-需求文档.md R17/R18 与验收 A18–A23：

- 同一 chat 连发消息串行执行（不并发驱动 AgentLoop）
- /stop 取消在跑任务并清空排队消息；无在跑任务时不误报
- 撤回触发消息即取消对应任务；撤回已结束/非触发消息静默忽略
- 「先撤回后出队」的排队消息被跳过（秒撤回竞态）
- 中断收尾：部分结果落库、回放流 done(interrupted)、卡片标记「已中断」
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from aio_agent_platform.channels.adapter import InboundEvent
from aio_agent_platform.channels.feishu.events import normalize_recall_event
from aio_agent_platform.channels.pipeline import (
    ChannelInboundPipeline,
    _BufferedEventLogger,
    _recalled_messages,
    _ResolvedContext,
    _StreamingReply,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _StubAdapter:
    """ChannelAdapter 替身：只记录发出的文本。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.sent_markdown: list[str] = []

    async def send(self, event: InboundEvent, text: str) -> str:
        self.sent.append(text)
        return "om_out"

    async def send_markdown(self, event: InboundEvent, text: str) -> str:
        self.sent_markdown.append(text)
        return "om_out"


def _make_pipeline() -> ChannelInboundPipeline:
    channel = SimpleNamespace(id=uuid4(), channel_type="feishu")
    return ChannelInboundPipeline(channel, _StubAdapter(), tool_executor=None)


def _msg_event(
    pipeline: ChannelInboundPipeline,
    text: str,
    *,
    event_id: str | None = None,
    message_id: str | None = None,
    chat_id: str = "oc_1",
    external_id: str = "ou_1",
) -> InboundEvent:
    return InboundEvent(
        channel_id=pipeline.channel.id,
        event_id=event_id or f"evt_{uuid4().hex[:8]}",
        chat_id=chat_id,
        external_id=external_id,
        text=text,
        message_id=message_id,
    )


async def _wait_for(cond, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


@pytest.fixture(autouse=True)
def _clear_recalled():
    _recalled_messages.clear()
    yield
    _recalled_messages.clear()


@pytest.fixture
async def pipeline():
    pipe = _make_pipeline()
    yield pipe
    for task in pipe._chat_consumers.values():
        task.cancel()
    for task in list(pipe._processing_tasks):
        task.cancel()
    await asyncio.gather(
        *pipe._chat_consumers.values(), *pipe._processing_tasks, return_exceptions=True
    )


# ---------------------------------------------------------------------------
# normalize_recall_event
# ---------------------------------------------------------------------------


def test_normalize_recall_event_happy_path() -> None:
    channel_id = uuid4()
    payload = {
        "event": {
            "message_id": "om_recalled",
            "chat_id": "oc_1",
            "recall_time": "1700000000",
            "operator_id": {"open_id": "ou_operator"},
        }
    }
    event = normalize_recall_event(channel_id, "evt_recall", payload)
    assert event is not None
    assert event.kind == "recall"
    assert event.message_id == "om_recalled"
    assert event.chat_id == "oc_1"
    assert event.external_id == "ou_operator"
    assert event.text == ""


def test_normalize_recall_event_without_operator() -> None:
    payload = {"event": {"message_id": "om_recalled", "chat_id": "oc_1"}}
    event = normalize_recall_event(uuid4(), "evt_recall", payload)
    assert event is not None
    assert event.kind == "recall"
    assert event.external_id == ""


def test_normalize_recall_event_missing_fields() -> None:
    assert normalize_recall_event(uuid4(), "e1", {"event": {"chat_id": "oc_1"}}) is None
    assert normalize_recall_event(uuid4(), "e2", {"event": {"message_id": "om_x"}}) is None


# ---------------------------------------------------------------------------
# 串行排队
# ---------------------------------------------------------------------------


async def test_chat_messages_run_serially(pipeline: ChannelInboundPipeline) -> None:
    started: list[str] = []
    release_first = asyncio.Event()

    async def fake_handle(event: InboundEvent) -> None:
        started.append(event.text)
        if event.text == "first":
            await release_first.wait()

    pipeline._safe_handle = fake_handle  # type: ignore[method-assign]

    pipeline.submit(_msg_event(pipeline, "first", message_id="om_1"))
    pipeline.submit(_msg_event(pipeline, "second", message_id="om_2"))

    await _wait_for(lambda: started == ["first"])
    # 第一条未结束，第二条不得开始
    await asyncio.sleep(0.05)
    assert started == ["first"]

    release_first.set()
    await _wait_for(lambda: started == ["first", "second"])


async def test_submit_dedups_by_event_id(pipeline: ChannelInboundPipeline) -> None:
    handled: list[str] = []

    async def fake_handle(event: InboundEvent) -> None:
        handled.append(event.text)

    pipeline._safe_handle = fake_handle  # type: ignore[method-assign]

    event = _msg_event(pipeline, "hello", event_id="evt_dup", message_id="om_1")
    pipeline.submit(event)
    pipeline.submit(event)  # 重投同 event_id
    await _wait_for(lambda: handled == ["hello"])
    await asyncio.sleep(0.05)
    assert handled == ["hello"]


# ---------------------------------------------------------------------------
# /stop
# ---------------------------------------------------------------------------


def _blocking_handle(cancelled: list[str]):
    async def fake_handle(event: InboundEvent) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(event.text)
            raise

    return fake_handle


async def test_stop_cancels_running_task(pipeline: ChannelInboundPipeline) -> None:
    cancelled: list[str] = []
    pipeline._safe_handle = _blocking_handle(cancelled)  # type: ignore[method-assign]
    adapter: _StubAdapter = pipeline.adapter  # type: ignore[assignment]

    pipeline.submit(_msg_event(pipeline, "run", message_id="om_1"))
    chat_key = pipeline._chat_key(_msg_event(pipeline, "x"))
    await _wait_for(lambda: chat_key in pipeline._running)

    pipeline.submit(_msg_event(pipeline, "/stop"))
    await _wait_for(lambda: cancelled == ["run"])
    await _wait_for(lambda: any("已中断" in t for t in adapter.sent))
    await _wait_for(lambda: chat_key not in pipeline._running)


async def test_stop_without_running_task_replies_no_task(
    pipeline: ChannelInboundPipeline,
) -> None:
    adapter: _StubAdapter = pipeline.adapter  # type: ignore[assignment]
    pipeline.submit(_msg_event(pipeline, "/stop"))
    await _wait_for(lambda: "当前没有进行中的回复。" in adapter.sent)


async def test_stop_clears_queued_messages(pipeline: ChannelInboundPipeline) -> None:
    cancelled: list[str] = []
    started: list[str] = []

    async def fake_handle(event: InboundEvent) -> None:
        started.append(event.text)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(event.text)
            raise

    pipeline._safe_handle = fake_handle  # type: ignore[method-assign]
    adapter: _StubAdapter = pipeline.adapter  # type: ignore[assignment]

    pipeline.submit(_msg_event(pipeline, "run", message_id="om_1"))
    pipeline.submit(_msg_event(pipeline, "queued", message_id="om_2"))
    await _wait_for(lambda: started == ["run"])

    pipeline.submit(_msg_event(pipeline, "/stop"))
    await _wait_for(lambda: cancelled == ["run"])
    await _wait_for(lambda: any("丢弃 1 条排队消息" in t for t in adapter.sent))
    # 排队消息不会被执行
    await asyncio.sleep(0.05)
    assert "queued" not in started


# ---------------------------------------------------------------------------
# 撤回即停
# ---------------------------------------------------------------------------


def _recall_event(
    pipeline: ChannelInboundPipeline, message_id: str, *, chat_id: str = "oc_1"
) -> InboundEvent:
    return InboundEvent(
        channel_id=pipeline.channel.id,
        event_id=f"evt_recall_{uuid4().hex[:8]}",
        chat_id=chat_id,
        external_id="",
        text="",
        kind="recall",
        message_id=message_id,
    )


async def test_recall_cancels_triggered_task(pipeline: ChannelInboundPipeline) -> None:
    cancelled: list[str] = []
    pipeline._safe_handle = _blocking_handle(cancelled)  # type: ignore[method-assign]

    pipeline.submit(_msg_event(pipeline, "run", message_id="om_1"))
    chat_key = pipeline._chat_key(_msg_event(pipeline, "x"))
    await _wait_for(lambda: chat_key in pipeline._running)

    pipeline.submit(_recall_event(pipeline, "om_1"))
    await _wait_for(lambda: cancelled == ["run"])
    await _wait_for(lambda: chat_key not in pipeline._running)


async def test_recall_unknown_message_ignored(pipeline: ChannelInboundPipeline) -> None:
    cancelled: list[str] = []
    pipeline._safe_handle = _blocking_handle(cancelled)  # type: ignore[method-assign]

    pipeline.submit(_msg_event(pipeline, "run", message_id="om_1"))
    chat_key = pipeline._chat_key(_msg_event(pipeline, "x"))
    await _wait_for(lambda: chat_key in pipeline._running)

    # 撤回的不是触发消息 → 在跑任务不受影响
    pipeline.submit(_recall_event(pipeline, "om_other"))
    await asyncio.sleep(0.1)
    assert cancelled == []
    assert chat_key in pipeline._running


async def test_recalled_queued_message_skipped(pipeline: ChannelInboundPipeline) -> None:
    """秒撤回竞态：消息还在排队时就被撤回，出队时直接跳过不执行。"""
    started: list[str] = []
    release_first = asyncio.Event()

    async def fake_handle(event: InboundEvent) -> None:
        started.append(event.text)
        if event.text == "first":
            await release_first.wait()

    pipeline._safe_handle = fake_handle  # type: ignore[method-assign]

    pipeline.submit(_msg_event(pipeline, "first", message_id="om_1"))
    pipeline.submit(_msg_event(pipeline, "second", message_id="om_2"))
    await _wait_for(lambda: started == ["first"])

    # 第二条还在排队，用户撤回了它
    pipeline.submit(_recall_event(pipeline, "om_2"))
    release_first.set()

    chat_key = pipeline._chat_key(_msg_event(pipeline, "x"))
    await _wait_for(lambda: chat_key not in pipeline._running)
    await asyncio.sleep(0.05)
    assert started == ["first"]


# ---------------------------------------------------------------------------
# 中断收尾
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass


class _FakeDBCtx:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDB:
        return self._db

    async def __aexit__(self, *args: object) -> None:
        pass


async def test_finalize_interrupted_persists_partial_and_marks_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_db = _FakeDB()
    monkeypatch.setattr(
        "aio_agent_platform.channels.pipeline.get_session_factory",
        lambda: (lambda: _FakeDBCtx(fake_db)),
    )
    logged: list[dict] = []

    async def fake_log_event(user_id: UUID, session_id: UUID, event: dict) -> None:
        logged.append(event)

    monkeypatch.setattr(
        "aio_agent_platform.channels.pipeline.log_event", fake_log_event
    )

    pipe = _make_pipeline()
    adapter: _StubAdapter = pipe.adapter  # type: ignore[assignment]
    event = _msg_event(pipe, "run", message_id="om_1")
    stream_reply = _StreamingReply(adapter=adapter, event=event)  # type: ignore[arg-type]
    user_id, session_id = uuid4(), uuid4()
    event_logger = _BufferedEventLogger(user_id=user_id, session_id=session_id)
    ctx = _ResolvedContext(user_id=user_id, session_id=session_id)
    tool_calls = [{"id": "t1", "name": "web_search", "arguments": {}}]

    await pipe._finalize_interrupted(
        stream_reply, event_logger, ctx, "半截输出", tool_calls
    )

    # 1. 部分 assistant 消息落库，尾部带中断标记
    assert len(fake_db.added) == 1
    msg = fake_db.added[0]
    assert msg.role == "assistant"  # type: ignore[attr-defined]
    assert msg.content == "半截输出\n\n⏹ 已中断"  # type: ignore[attr-defined]
    assert msg.tool_calls == tool_calls  # type: ignore[attr-defined]

    # 2. 回放流以 done(interrupted=True) 收尾
    done_events = [e for e in logged if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["interrupted"] is True
    assert done_events[0]["message_id"] is not None

    # 3. 非流式（stream_id=None）下降级为普通消息发出中断标记
    assert any("⏹ 已中断" in t for t in adapter.sent_markdown)
