"""Inbound pipeline — dedup, identity resolution, session mapping, Agent execution.

The pipeline is shared by all transports for a given channel. It:

1. Deduplicates events by ``event_id`` (TTL 1h in-memory cache).
2. Resolves the external user to a platform ``User`` via their binding — if
   unbound, replies with a bind-code guide instead of creating an account.
3. Maps ``(channel_id, chat_id, external_id)`` to a platform session.
4. Intercepts built-in commands (``/bind``, ``/new``, ``/help``, ``/stop``)
   before they reach the Agent.
5. Drives ``AgentLoop`` for normal messages, streaming deltas back through the
   adapter's send/update methods.

同一 chat 的消息经 chat 级队列串行驱动；``/stop`` 与消息撤回
（``im.message.recalled_v1``）即时取消该 chat 在跑的 AgentLoop。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.channels.adapter import ChannelAdapter, ChatKind, InboundEvent
from aio_agent_platform.channels.binding import (
    BindCodeError,
    BindCodeRateLimited,
    issue_bind_code,
    resolve_external_user,
)
from aio_agent_platform.channels.file_send import (
    SEND_FILE_TOOL_SCHEMA,
    ChannelSendContext,
    current_channel_send_ctx,
)
from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.auto_title import generate_session_title
from aio_agent_platform.core.chat import (
    background_tasks,
    build_agent_loop,
    build_system_prompt_with_memories,
    filter_tools_by_agent,
    fire_memory_extraction,
    inject_file_refs_into_message,
    load_agent,
    load_conversation_history,
    resolve_provider_type,
    resolve_workspace,
    update_context_summary,
)
from aio_agent_platform.core.context import prepare_context
from aio_agent_platform.core.task_event_log import log_event
from aio_agent_platform.core.task_registry import task_finished, task_started, task_tool
from aio_agent_platform.db import Message
from aio_agent_platform.db import Session as ChatSession
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import (
    ChannelConfig,
    ChannelSessionMapping,
    LLMModel,
)
from aio_agent_platform.interface.routes.chat import _compress_image
from aio_agent_platform.llm.client import build_user_content
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.tools.registry import Tool

logger = structlog.get_logger()

# --- In-memory event dedup (TTL ~1h) ---
_event_seen: dict[str, float] = {}
_DEDUP_TTL_SECONDS = 3600


def _dedup(event_id: str) -> bool:
    """Return True if this event_id has been seen recently."""
    now = time.monotonic()
    # Lazy prune (cheap — dict stays small).
    if len(_event_seen) > 10_000:
        cutoff = now - _DEDUP_TTL_SECONDS
        for k in [k for k, v in _event_seen.items() if v < cutoff]:
            _event_seen.pop(k, None)
    if event_id in _event_seen:
        return True
    _event_seen[event_id] = now
    return False


# 出站文本单条上限默认值（字节）。各适配器可用 max_message_bytes 覆写：
# 飞书 30KB 上限保守取 3500 字节；企微 text 上限 2048 字节（中文 1 字 3 字节）。
_MAX_MESSAGE_BYTES = 3500

# CardKit 单卡最多更新 10 次/秒；仅按官方上限合并，首个 delta 立即推送。
_STREAM_UPDATE_INTERVAL_SECONDS = 0.1
_STREAM_INITIAL_TEXT = ""


@dataclass
class _StreamingReply:
    """Drive a channel's native streaming-message API with throttling."""

    adapter: ChannelAdapter
    event: InboundEvent
    max_bytes: int = _MAX_MESSAGE_BYTES
    stream_id: str | None = None
    sequence: int = 0
    last_update_at: float = 0.0
    rendered_text: str = ""
    has_content: bool = False
    stream_failed: bool = False
    pending_text: str | None = None
    flush_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        try:
            self.stream_id = await self.adapter.start_stream(
                self.event, _STREAM_INITIAL_TEXT
            )
            self.rendered_text = _STREAM_INITIAL_TEXT
        except Exception:
            logger.warning("channel_stream_placeholder_failed", exc_info=True)
            self.stream_id = None

    def _next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    async def push(self, text: str) -> None:
        """Queue accumulated output without blocking consumption of LLM tokens."""
        if not text or self.stream_id is None or self.stream_failed:
            return
        self.pending_text = _byte_truncate(text, self.max_bytes)
        if self.flush_task is None or self.flush_task.done():
            self.flush_task = asyncio.create_task(self._flush_pending())

    async def _flush_pending(self) -> None:
        """Coalesce deltas and push them independently of the model stream."""
        stream_id = self.stream_id
        if stream_id is None:
            return
        while self.pending_text is not None and not self.stream_failed:
            visible = self.pending_text
            self.pending_text = None
            if visible == self.rendered_text:
                continue

            if self.has_content:
                remaining = (
                    _STREAM_UPDATE_INTERVAL_SECONDS
                    - (time.monotonic() - self.last_update_at)
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)

            # Record request start time. Network latency must not make the next
            # request appear overdue and defeat the CardKit rate limiter.
            self.last_update_at = time.monotonic()
            try:
                updated = await self.adapter.update_stream(
                    stream_id, visible, self._next_sequence()
                )
            except Exception:
                logger.warning("channel_stream_update_failed", exc_info=True)
                updated = False
            if not updated:
                self.stream_failed = True
                return
            self.rendered_text = visible
            self.has_content = True

    async def finish(self, text: str) -> None:
        """Force the final content to the streamed message, with safe fallback."""
        final_text = text or "（无输出）"
        chunks = _split_text(final_text, self.max_bytes)

        if self.stream_id is None:
            for chunk in chunks:
                await self.adapter.send_markdown(self.event, chunk)
            return

        # Wait only at completion; CardKit network latency never blocks the
        # AgentLoop while it is producing text deltas.
        if self.flush_task is not None:
            await self.flush_task

        updated = not self.stream_failed
        if updated and chunks[0] != self.rendered_text:
            # Respect the same CardKit update window for the forced final flush.
            if self.has_content:
                remaining = (
                    _STREAM_UPDATE_INTERVAL_SECONDS
                    - (time.monotonic() - self.last_update_at)
                )
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self.last_update_at = time.monotonic()
            try:
                updated = await self.adapter.update_stream(
                    self.stream_id, chunks[0], self._next_sequence()
                )
            except Exception:
                logger.warning("channel_stream_final_update_failed", exc_info=True)
                updated = False

        # Always try to close streaming mode. This removes the "[生成中...]"
        # preview and restores forwarding/card interaction even after a failed push.
        if self.last_update_at:
            remaining = (
                _STREAM_UPDATE_INTERVAL_SECONDS
                - (time.monotonic() - self.last_update_at)
            )
            if remaining > 0:
                await asyncio.sleep(remaining)
        try:
            closed = await self.adapter.finish_stream(
                self.stream_id, final_text, self._next_sequence()
            )
        except Exception:
            logger.warning("channel_stream_close_failed", exc_info=True)
            closed = False
        if not closed:
            logger.warning("channel_stream_not_closed", stream_id=self.stream_id)

        if not updated:
            for chunk in chunks:
                await self.adapter.send_markdown(self.event, chunk)
            return

        for chunk in chunks[1:]:
            await self.adapter.send_markdown(self.event, chunk)


@dataclass
class _BufferedEventLogger:
    """Write replay events in order without blocking the AgentLoop."""

    user_id: UUID
    session_id: UUID
    pending: list[dict] = field(default_factory=list)
    flush_task: asyncio.Task[None] | None = None

    def submit(self, event: dict) -> None:
        event_type = event.get("type")
        # Redis replay only needs the same concatenated content. Coalescing
        # consecutive deltas avoids one Redis round trip per model token.
        if (
            event_type in {"text_delta", "thinking"}
            and self.pending
            and self.pending[-1].get("type") == event_type
        ):
            self.pending[-1]["content"] = (
                self.pending[-1].get("content", "") + event.get("content", "")
            )
        else:
            self.pending.append(event)
        if self.flush_task is None or self.flush_task.done():
            self.flush_task = asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        while self.pending:
            event = self.pending.pop(0)
            await log_event(self.user_id, self.session_id, event)

    async def drain(self) -> None:
        if self.flush_task is not None:
            await self.flush_task

# --- Pending file/image attachments (Feishu file msg → next text msg) ---
# 飞书文件消息与文字消息分离：文件到达先下载存储，缓存为「待处理附件」，
# 等用户下一条文字消息到达时注入文件引用一并驱动 Agent。
_PENDING_TTL_SECONDS = 1800  # 30 分钟


@dataclass
class _PendingAttachment:
    ref: dict  # FileAttachmentRef 的 dict 形式（file_id/filename/mime/size/workspace_path）
    ts: float


_pending_attachments: dict[str, list[_PendingAttachment]] = {}


def _pending_key(channel_id: UUID, chat_id: str, external_id: str) -> str:
    return f"{channel_id}:{chat_id}:{external_id}"


def _pop_pending(key: str) -> list[dict]:
    """Return non-expired pending attachment refs for a chat, clearing the slot."""
    now = time.monotonic()
    # Lazy prune expired chat slots.
    if len(_pending_attachments) > 1000:
        for k in [k for k, v in _pending_attachments.items() if now - v[-1].ts > _PENDING_TTL_SECONDS]:
            _pending_attachments.pop(k, None)
    items = _pending_attachments.pop(key, [])
    return [p.ref for p in items if now - p.ts <= _PENDING_TTL_SECONDS]


def _sniff_mime(data: bytes) -> str | None:
    """Detect MIME from magic bytes (images only, used to name image uploads)."""
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


_IMAGE_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _build_image_data_uri(data: bytes, mime: str) -> str:
    """Base64 data URI for direct multimodal input to vision models."""
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _collect_image_attachments(refs: list[dict]) -> list[dict]:
    """Extract vision-ready image refs (with cached data URI) for build_user_content."""
    out: list[dict] = []
    for r in refs:
        uri = r.get("_image_data_uri")
        if uri:
            out.append({
                "key": uri,  # 已是 data URI；build_user_content 的 to_data_uri 原样返回
                "url": r.get("workspace_path", ""),
                "mime": r.get("mime", "image/jpeg"),
                "size": r.get("size", 0),
                "filename": r.get("filename", "image"),
            })
    return out


def _strip_internal_keys(refs: list[dict]) -> list[dict]:
    """Remove internal cache keys (``_image_data_uri``) before persisting."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in refs]


@dataclass
class _ResolvedContext:
    user_id: UUID | None  # None when the external user is not bound yet
    session_id: UUID | None


# --- Task interruption (/stop + recall-to-stop) ---

# 「已撤回 message_id」集合：撤回事件可能先于任务登记（甚至先于消息出队）
# 到达，先记录再在登记/出队时自查，保证「先撤回后登记」也能拦下。
_recalled_messages: dict[str, float] = {}
_RECALLED_TTL_SECONDS = 600
_CHAT_QUEUE_IDLE_SECONDS = 3600  # 队列空闲多久后回收消费者


def _mark_recalled(message_id: str) -> None:
    now = time.monotonic()
    if len(_recalled_messages) > 10_000:
        cutoff = now - _RECALLED_TTL_SECONDS
        for k in [k for k, v in _recalled_messages.items() if v < cutoff]:
            _recalled_messages.pop(k, None)
    _recalled_messages[message_id] = now


def _is_recalled(message_id: str | None) -> bool:
    if not message_id:
        return False
    ts = _recalled_messages.get(message_id)
    if ts is None:
        return False
    if time.monotonic() - ts > _RECALLED_TTL_SECONDS:
        _recalled_messages.pop(message_id, None)
        return False
    return True


def _format_recall_time(raw: object) -> str | None:
    """飞书撤回时间为毫秒时间戳字符串，转成可读 ISO 时间；缺省/非法返回 None。"""
    try:
        ms = int(str(raw))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000).isoformat(sep=" ", timespec="seconds")


@dataclass
class _RunningTask:
    """An in-flight event-processing task for one chat."""

    task: asyncio.Task
    trigger_message_id: str | None


class ChannelInboundPipeline:
    """Process inbound events for a single channel."""

    def __init__(self, channel: ChannelConfig, adapter: ChannelAdapter, tool_executor):
        self.channel = channel
        self.adapter = adapter
        self.tool_executor = tool_executor
        self._processing_tasks: set[asyncio.Task] = set()
        # chat 级串行队列：同一 chat_key 的消息逐条驱动，不并发执行。
        self._chat_queues: dict[str, asyncio.Queue[InboundEvent]] = {}
        self._chat_consumers: dict[str, asyncio.Task] = {}
        # 在跑任务登记：chat_key → 当前处理任务；trigger_message_id → chat_key 反查。
        self._running: dict[str, _RunningTask] = {}
        self._trigger_index: dict[str, str] = {}

    def _chat_key(self, event: InboundEvent) -> str:
        return f"{self.channel.id}:{event.chat_id}:{event.external_id}"

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        self._processing_tasks.add(task)
        task.add_done_callback(self._processing_tasks.discard)

    def submit(self, event: InboundEvent) -> None:
        """Schedule an event for processing. Returns immediately.

        Callers (transports) should ACK the inbound request before or after
        calling this — the pipeline does all heavy work asynchronously.

        路由：去重后，撤回事件与 /stop 直接处理（需即时取消，不能排在队列里）；
        普通消息进入 chat 级队列串行消费。
        """
        if _dedup(event.event_id):
            logger.info("pipeline_duplicate_event", event_id=event.event_id)
            return

        if event.kind == "recall":
            self._spawn(self._safe_handle_recall(event))
            return

        # 群聊中 /stop 同样要求 @ 机器人（与消息处理规则一致）。
        if event.text.strip() == "/stop" and (
            event.chat_kind != ChatKind.GROUP or event.mentions_bot
        ):
            self._spawn(self._safe_handle_stop(event))
            return

        chat_key = self._chat_key(event)
        queue = self._chat_queues.get(chat_key)
        if queue is None:
            queue = asyncio.Queue()
            self._chat_queues[chat_key] = queue
            self._chat_consumers[chat_key] = asyncio.create_task(
                self._chat_consumer(chat_key, queue)
            )
        queue.put_nowait(event)

    async def _chat_consumer(
        self, chat_key: str, queue: asyncio.Queue[InboundEvent]
    ) -> None:
        """Drain one chat's queue serially; each event runs in a child task so
        /stop can cancel the in-flight turn without killing the consumer."""
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_CHAT_QUEUE_IDLE_SECONDS
                )
            except TimeoutError:
                # 空闲回收：仅当队列仍是自己且为空时注销，避免与 submit 竞态。
                if queue.empty() and self._chat_queues.get(chat_key) is queue:
                    self._chat_queues.pop(chat_key, None)
                    self._chat_consumers.pop(chat_key, None)
                    if queue.empty():
                        return
                    # 注销瞬间又有消息入队：重新注册继续消费。
                    self._chat_queues[chat_key] = queue
                    self._chat_consumers[chat_key] = asyncio.current_task()  # type: ignore[assignment]
                continue

            # 出队前自查：消息在排队期间被撤回 → 跳过。
            if _is_recalled(event.message_id):
                logger.info(
                    "pipeline_recalled_message_skipped",
                    channel_id=str(self.channel.id),
                    message_id=event.message_id,
                )
                queue.task_done()
                continue

            inner = asyncio.create_task(self._safe_handle(event))
            running = _RunningTask(task=inner, trigger_message_id=event.message_id)
            self._running[chat_key] = running
            if event.message_id:
                self._trigger_index[event.message_id] = chat_key
            try:
                await inner
            except asyncio.CancelledError:
                # 两种来源都表现为 CancelledError，必须区分：
                # - inner 被 /stop / 撤回取消 → 冒泡到此处，但消费者自身
                #   cancelling() 为 0，吞掉继续消费下一条；
                # - 消费者自身被取消（关闭流程）→ cancelling() > 0，传播。
                # 不能用 inner.cancelled() 区分：取消消费者时其 await 的
                # inner 会被连带取消，两种场景下它都为 True。
                current = asyncio.current_task()
                if current is not None and current.cancelling() > 0:
                    raise
            finally:
                if self._running.get(chat_key) is running:
                    self._running.pop(chat_key, None)
                    if running.trigger_message_id:
                        self._trigger_index.pop(running.trigger_message_id, None)
                queue.task_done()

    def _clear_queue(self, chat_key: str) -> int:
        """Drop all queued (not yet started) events for a chat. Returns count."""
        queue = self._chat_queues.get(chat_key)
        if queue is None:
            return 0
        dropped = 0
        while True:
            try:
                queue.get_nowait()
                queue.task_done()
                dropped += 1
            except asyncio.QueueEmpty:
                return dropped

    async def _safe_handle_stop(self, event: InboundEvent) -> None:
        try:
            chat_key = self._chat_key(event)
            dropped = self._clear_queue(chat_key)
            running = self._running.get(chat_key)
            if running is None:
                await self.adapter.send(event, "当前没有进行中的回复。")
                return
            running.task.cancel()
            reply = "⏹ 已中断当前回复。"
            if dropped:
                reply += f"（同时丢弃 {dropped} 条排队消息）"
            await self.adapter.send(event, reply)
        except Exception:
            logger.exception(
                "pipeline_stop_failed",
                channel_id=str(self.channel.id),
                event_id=event.event_id,
            )

    async def _safe_handle_recall(self, event: InboundEvent) -> None:
        """Recall-to-stop: cancel the in-flight run triggered by the recalled
        message. Unknown/completed recalls are silently ignored."""
        try:
            body = event.raw.get("event") or {}
            recall_time = _format_recall_time(body.get("recall_time"))
            # 撤回方类别：message_owner / group_owner / group_manager /
            # enterprise_manager（事件不含操作人身份，仅记录类别）
            recall_type = body.get("recall_type")
            logger.info(
                "pipeline_recall_received",
                channel_id=str(self.channel.id),
                chat_id=event.chat_id,
                message_id=event.message_id,
                recall_time=recall_time,
                recall_type=recall_type,
            )
            if event.message_id:
                _mark_recalled(event.message_id)
            chat_key = self._trigger_index.get(event.message_id or "")
            if chat_key is None:
                return  # 任务已结束或撤回的不是触发消息
            running = self._running.get(chat_key)
            if running is None or running.trigger_message_id != event.message_id:
                return
            logger.info(
                "pipeline_recall_cancel",
                channel_id=str(self.channel.id),
                message_id=event.message_id,
                recall_time=recall_time,
                recall_type=recall_type,
            )
            running.task.cancel()
        except Exception:
            logger.exception(
                "pipeline_recall_failed",
                channel_id=str(self.channel.id),
                event_id=event.event_id,
            )

    async def _safe_handle(self, event: InboundEvent) -> None:
        try:
            await self._handle(event)
        except Exception:
            logger.exception(
                "pipeline_handle_failed",
                channel_id=str(self.channel.id),
                event_id=event.event_id,
            )
            try:
                await self.adapter.send(event, "处理消息时发生错误，请稍后重试。")
            except Exception:
                pass

    async def _handle(self, event: InboundEvent) -> None:
        # 1. Group chat: only respond when the bot is @-mentioned.
        #    （去重已上移到 submit，/stop 与撤回事件也需去重。）
        if event.chat_kind.value == "group" and not event.mentions_bot:
            return

        logger.info(
            "channel_message_received",
            channel_id=str(self.channel.id),
            channel_type=self.channel.channel_type,
            chat_id=event.chat_id,
            chat_kind=event.chat_kind.value,
            external_id=event.external_id,
            message_id=event.message_id,
            text=event.text[:200],
            text_len=len(event.text),
            has_attachment=event.attachment is not None,
        )

        # 2. Resolve user + session.
        factory = get_session_factory()
        async with factory() as db:
            # 渠道配置实时从 DB 读取，不缓存进程内存：管理后台更换 agent 等
            # 变更会立即生效，避免使用启动时缓存的过期配置。
            fresh_channel = await db.get(ChannelConfig, self.channel.id)
            if fresh_channel is not None:
                self.channel = fresh_channel
            ctx = await self._resolve_context(db, event)
            text = event.text.strip()

            # 4. Unbound users: only /bind is accepted; everything else gets a
            #    bind guide. No session or Agent is created until they bind.
            if ctx.user_id is None:
                if text == "/bind":
                    await self._handle_bind(db, event, ctx)
                else:
                    await self._handle_unbound_guide(event)
                return

            # Set RLS context for the rest of the request.
            current_user_id.set(str(ctx.user_id))

            # 5. Command interception.
            if text == "/bind":
                await self._handle_bind(db, event, ctx)
                return
            if text in ("/new", "/reset", "/clear"):
                await self._handle_new(db, event, ctx)
                return
            if text == "/help":
                await self._handle_help(db, event, ctx)
                return
            if text.startswith("/") and await self._handle_unified_command(db, event, ctx, text):
                return

            # 6. File/image message: download + store, wait for next text message.
            if event.attachment is not None:
                await self._handle_attachment(db, event, ctx)
                return

            # 7. Drive AgentLoop.
            await self._drive_agent(db, event, ctx)

    # --- Context resolution ---

    async def _resolve_context(self, db: AsyncSession, event: InboundEvent) -> _ResolvedContext:
        user_id = await resolve_external_user(
            db, self.channel.tenant_id, event.external_id
        )
        if user_id is None:
            return _ResolvedContext(user_id=None, session_id=None)

        # Resolve session mapping.
        result = await db.execute(
            select(ChannelSessionMapping).where(
                ChannelSessionMapping.channel_id == self.channel.id,
                ChannelSessionMapping.chat_id == event.chat_id,
                ChannelSessionMapping.external_id == event.external_id,
                ChannelSessionMapping.is_active.is_(True),
            )
        )
        mapping = result.scalar_one_or_none()
        if mapping is None:
            # Create a new session + mapping.
            session = ChatSession(
                user_id=user_id,
                agent_id=self.channel.agent_id,
                title=f"{event.chat_kind.value} · {event.external_id[:8]}",
                source=self.channel.channel_type,
            )
            db.add(session)
            await db.flush()
            mapping = ChannelSessionMapping(
                channel_id=self.channel.id,
                chat_id=event.chat_id,
                external_id=event.external_id,
                session_id=session.id,
            )
            db.add(mapping)
            await db.flush()
        session_id = mapping.session_id

        return _ResolvedContext(user_id=user_id, session_id=session_id)

    # --- Commands ---

    async def _handle_unbound_guide(self, event: InboundEvent) -> None:
        reply = (
            "🔒 你还没有绑定平台账号，暂时无法与我对话。\n\n"
            "完成绑定后即可使用：\n"
            "1. 在此会话发送 /bind 获取 6 位绑定码\n"
            "2. 登录 Web 端「账号设置 → 渠道绑定」，输入绑定码完成关联\n\n"
            f"绑定码有效期为 {BIND_CODE_TTL_MINUTES} 分钟。"
        )
        await self.adapter.send(event, reply)

    async def _handle_bind(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext
    ) -> None:
        if ctx.user_id is not None:
            await self.adapter.send(event, "✅ 该渠道已绑定到你的账号，无需重复绑定。")
            return
        try:
            code, _expires_at = await issue_bind_code(
                db, self.channel.id, event.external_id, self.channel.tenant_id
            )
            await db.commit()
        except BindCodeRateLimited as e:
            await self.adapter.send(event, f"⚠️ {e}")
            return
        except BindCodeError as e:
            await self.adapter.send(event, f"❌ {e}")
            return
        minutes = BIND_CODE_TTL_MINUTES
        reply = (
            f"📋 你的绑定码是：\n\n"
            f"    {code}\n\n"
            f"请在 {minutes} 分钟内登录 Web 端「账号设置 → 渠道绑定」输入该码完成关联。"
        )
        await self.adapter.send(event, reply)

    async def _handle_new(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext
    ) -> None:
        assert ctx.user_id is not None  # only reached for bound users
        # Mark the current mapping inactive.
        await db.execute(
            update(ChannelSessionMapping)
            .where(
                ChannelSessionMapping.channel_id == self.channel.id,
                ChannelSessionMapping.chat_id == event.chat_id,
                ChannelSessionMapping.external_id == event.external_id,
                ChannelSessionMapping.is_active.is_(True),
            )
            .values(is_active=False)
        )
        # Create a new session + mapping.
        session = ChatSession(
            user_id=ctx.user_id,
            agent_id=self.channel.agent_id,
            title=f"新对话 · {event.external_id[:8]}",
            source=self.channel.channel_type,
        )
        db.add(session)
        await db.flush()
        db.add(ChannelSessionMapping(
            channel_id=self.channel.id,
            chat_id=event.chat_id,
            external_id=event.external_id,
            session_id=session.id,
        ))
        await db.commit()
        await self.adapter.send(event, "✅ 已开始新对话。")

    async def _handle_help(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext
    ) -> None:
        """Help listing generated from the unified command registry."""
        assert ctx.user_id is not None

        from aio_agent_platform.db.models import User
        from aio_agent_platform.interface.commands.dispatcher import dynamic_commands
        from aio_agent_platform.interface.commands.registry import registry

        user = await db.get(User, UUID(ctx.user_id))
        cmds = registry.list_for(user) if user else registry.all()
        dyn = await dynamic_commands(db, ctx.user_id)
        known = {c.name for c in cmds}
        cmds = [*cmds, *[d for d in dyn if d.name not in known]]

        groups: dict[str, list] = {}
        for c in cmds:
            groups.setdefault(c.group, []).append(c)

        order = [
            "帮助", "会话", "上下文", "工具", "技能", "记忆", "知识", "定时任务",
            "智能体", "确认", "工作区", "模型", "运行", "系统", "通用",
        ]
        lines = ["📖 可用命令：", ""]
        for g in order:
            if g not in groups:
                continue
            lines.append(f"【{g}】")
            for c in sorted(groups[g], key=lambda x: x.name):
                lines.append(f"`{c.usage_text}` {c.desc}")
            lines.append("")
        lines.append("未知命令将作为普通消息发送。")
        await self.adapter.send(event, "\n".join(lines))

    async def _handle_unified_command(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext, text: str
    ) -> bool:
        """Run a slash command through the unified command registry.

        Returns True if the command is known and a reply was sent; False when
        the command is unknown, in which case the caller degrades it to a
        normal message for the agent.
        """
        assert ctx.user_id is not None and ctx.session_id is not None

        from aio_agent_platform.db.models import User
        from aio_agent_platform.interface.commands import CommandContext, dispatch
        from aio_agent_platform.interface.commands import dynamic as _dynamic
        from aio_agent_platform.interface.commands.registry import registry

        name = text.split()[0].lstrip("/") if text.strip() else ""
        if (
            registry.get(name) is None
            and await _dynamic.find_skill_command(db, ctx.user_id, name) is None
        ):
            return False  # unknown command → let the agent handle it

        user = await db.get(User, UUID(ctx.user_id))
        if user is None:
            return False
        session = await db.get(ChatSession, ctx.session_id)

        cmd_ctx = CommandContext(
            user=user,
            user_id=ctx.user_id,
            db=db,
            raw=text,
            session_id=str(ctx.session_id),
            session=session,
            tool_executor=self.tool_executor,
        )
        result = await dispatch(cmd_ctx)
        # /switch and /resume carry the target session id in data; rebind this
        # chat's active ChannelSessionMapping so the next message continues that
        # session (the /switch handler already validated ownership).
        switch_session_id = (result.data or {}).get("switch_session_id")
        if switch_session_id:
            await db.execute(
                update(ChannelSessionMapping)
                .where(
                    ChannelSessionMapping.channel_id == self.channel.id,
                    ChannelSessionMapping.chat_id == event.chat_id,
                    ChannelSessionMapping.external_id == event.external_id,
                    ChannelSessionMapping.is_active.is_(True),
                )
                .values(session_id=UUID(switch_session_id))
            )
        await db.commit()  # persist writes made by the command handler
        await self.adapter.send(event, result.content)
        return True

    # --- Attachment (file/image) handling ---

    async def _handle_attachment(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext
    ) -> None:
        """Download a file/image message into the user's workspace and hold it
        as a pending attachment for their next text message."""
        assert ctx.user_id is not None and ctx.session_id is not None
        assert event.attachment is not None

        session = await db.get(ChatSession, ctx.session_id)
        if session is None:
            await self.adapter.send(event, "⚠️ 会话不可用，请发送 /new 后重试。")
            return

        data = await self.adapter.download_attachment(event)
        if not data:
            await self.adapter.send(event, "⚠️ 文件下载失败，请稍后重试。")
            return

        workspace_id, _slug = await resolve_workspace(db, session, ctx.user_id)

        file_id = uuid4().hex[:8]
        filename = event.attachment.filename.replace("/", "_").replace("\\", "_")
        mime = _sniff_mime(data) or "application/octet-stream"
        if event.attachment.resource_type == "image":
            ext = _IMAGE_EXT_BY_MIME.get(mime, "")
            if ext and not filename.lower().endswith(ext):
                filename = f"{filename}{ext}"
            # 压缩并缓存 base64 data URI，供视觉模型直喂（与页面 _compress_image 一致）。
            data, mime = _compress_image(data, mime)

        workspace_path = f"uploads/{file_id}_{filename}"
        WorkspaceStorage(ObjectStorage()).put_file(str(workspace_id), workspace_path, data)

        ref = {
            "file_id": file_id,
            "filename": filename,
            "mime": mime,
            "size": len(data),
            "workspace_path": workspace_path,
        }
        if event.attachment.resource_type == "image":
            ref["_image_data_uri"] = _build_image_data_uri(data, mime)
        key = _pending_key(self.channel.id, event.chat_id, event.external_id)
        _pending_attachments.setdefault(key, []).append(
            _PendingAttachment(ref=ref, ts=time.monotonic())
        )

        logger.info(
            "feishu_attachment_stored",
            workspace_path=workspace_path,
            size=len(data),
            resource_type=event.attachment.resource_type,
        )
        await self.adapter.send(event, f"📎 已收到 {filename}，请补充你的需求。")

    async def _resolve_vision_capability(
        self, db: AsyncSession, agent_model_id: UUID | None
    ) -> tuple[bool, str]:
        """Resolve (is_multimodal, provider_type_for_content) for the agent's model."""
        tenant_id = self.channel.tenant_id
        provider_type_for_content = "openai"
        model = None
        if agent_model_id:
            result = await db.execute(
                select(LLMModel)
                .options(selectinload(LLMModel.provider))
                .where(LLMModel.id == agent_model_id, LLMModel.tenant_id == tenant_id)
            )
            model = result.scalar_one_or_none()
        if model is None:
            result = await db.execute(
                select(LLMModel)
                .options(selectinload(LLMModel.provider))
                .where(LLMModel.is_default, LLMModel.is_active, LLMModel.tenant_id == tenant_id)
                .limit(1)
            )
            model = result.scalar_one_or_none()
        if model and model.provider:
            provider_type_for_content = resolve_provider_type(model.provider.provider_type)
        return bool(model and model.is_multimodal), provider_type_for_content

    # --- Agent execution ---

    async def _drive_agent(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext
    ) -> None:
        assert ctx.user_id is not None  # only reached for bound users
        assert ctx.session_id is not None

        # Pending file/image attachments carried by this text message.
        pending_refs = _pop_pending(
            _pending_key(self.channel.id, event.chat_id, event.external_id)
        )
        user_message_for_llm = (
            inject_file_refs_into_message(event.text, pending_refs)
            if pending_refs else event.text
        )

        agent = await load_agent(db, self.channel.agent_id, tenant_id=self.channel.tenant_id)
        if agent is None:
            await self.adapter.send(event, "⚠️ 渠道绑定的智能体不可用，请联系管理员。")
            return

        # 视觉模型：图片以 base64 data URI 直喂；否则图片仅作为工作区路径引用。
        is_multimodal, provider_type_for_content = await self._resolve_vision_capability(
            db, agent.model_id
        )
        image_attachments = _collect_image_attachments(pending_refs)
        user_content: str | list[dict] = user_message_for_llm
        if is_multimodal and image_attachments:
            user_content = build_user_content(
                text=user_message_for_llm,
                attachments=image_attachments,
                provider_type=provider_type_for_content,
                to_data_uri=lambda _key, _mime: _key,  # key 已是 data URI
            )

        # Filter tools + apply channel blacklist.
        blacklist = set(self.channel.tool_blacklist or [])
        # Filter out AskUserQuestion for channel conversations — it would block
        # waiting for a Web UI confirmation that will never come.
        blacklist.add("AskUserQuestion")
        # Channel conversations have no delegation context (no SSE event loop);
        # keep delegate_task out so a tool that cannot run is never offered.
        blacklist.add("delegate_task")
        # ui_* page actions execute in the user's browser — channel sessions
        # have no browser attached, so never offer them.
        from aio_agent_platform.tools.builtin import FRONTEND_TOOL_NAMES

        blacklist.update(FRONTEND_TOOL_NAMES)
        tools_list, tools_schema = filter_tools_by_agent(
            self.tool_executor, agent, extra_blacklist=blacklist
        )

        # 支持文件发送的渠道注入文件发送工具：schema 供 LLM 调用，Tool 供 system prompt 提及。
        if self.adapter.supports_file_send:
            fn = SEND_FILE_TOOL_SCHEMA["function"]
            tools_list = [
                *tools_list,
                Tool(
                    name=fn["name"],
                    description=fn["description"],
                    parameters=fn["parameters"],
                    requires_sandbox=False,
                ),
            ]
            tools_schema = [*tools_schema, SEND_FILE_TOOL_SCHEMA]

        system_prompt = await build_system_prompt_with_memories(
            db, ctx.user_id, user_message_for_llm, tools_list, agent=agent,
            workspace_files=pending_refs or None,
        )

        history, context_summary = await load_conversation_history(db, ctx.session_id)

        # Resolve workspace so the agent can access uploaded files.
        session = await db.get(ChatSession, ctx.session_id)
        workspace_id = workspace_slug = None
        if session is not None:
            workspace_id, workspace_slug = await resolve_workspace(db, session, ctx.user_id)

        agent_loop = await build_agent_loop(
            self.tool_executor, system_prompt, db,
            agent_model_id=(session.model_id if session and session.model_id else None) or agent.model_id,
            agent_temperature=agent.temperature,
            agent_max_iterations=agent.max_iterations,
            agent_enable_retry=agent.enable_retry if agent.enable_retry is not None else True,
            tenant_id=agent.tenant_id,
            workspace_id=workspace_id,
            workspace_slug=workspace_slug,
        )

        prepared_messages, _ = await prepare_context(
            system_prompt=system_prompt,
            history=history,
            user_input=user_content if isinstance(user_content, str) else user_message_for_llm,
            provider=agent_loop.provider,
            existing_summary=context_summary,
        )
        processed_history = [
            m for m in prepared_messages
            if m.role != "system" and not (
                m.role == "user"
                and (m.content == user_content or m.content == user_message_for_llm)
            )
        ]

        # Show a typing indicator on the user's message while the agent runs.
        reaction_id = None
        try:
            reaction_id = await self.adapter.add_reaction(event, "Typing")
        except Exception:
            pass

        # 首条消息并发生成标题（渠道会话与 Web 端一致，覆盖「新对话 · xx」占位标题）
        title_task: asyncio.Task[str] | None = None
        prior_msg_count = await db.scalar(
            select(func.count(Message.id)).where(Message.session_id == ctx.session_id)
        )
        if not prior_msg_count and (agent.enable_auto_title if agent else True):
            title_task = asyncio.create_task(generate_session_title(event.text, self.channel.tenant_id))

        # Persist the user message up front so it survives agent failures.
        db.add(Message(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            role="user",
            content=event.text,
            file_attachments=_strip_internal_keys(pending_refs) or None,
        ))
        await db.commit()

        # 登记在跑任务，供宠物 widget 展示（渠道任务没有浏览器 SSE）
        chat_key = f"{self.channel.id}:{event.chat_id}:{event.external_id}"
        await task_started(
            ctx.user_id, ctx.session_id, user_message_for_llm, self.channel.channel_type, chat_key,
            agent_id=str(self.channel.agent_id),
        )

        # 渠道原生流式卡片输出，不调用普通消息编辑接口。
        # 卡片在进入 AgentLoop 前发出，让思考/调用工具期间也有即时反馈。
        stream_reply = _StreamingReply(
            self.adapter,
            event,
            max_bytes=self.adapter.max_message_bytes or _MAX_MESSAGE_BYTES,
        )
        if self.channel.enable_streaming:
            await stream_reply.start()

        final_output = ""
        tool_calls_list: list[dict] = []
        tool_results_map: dict[str, dict] = {}
        event_logger = _BufferedEventLogger(ctx.user_id, ctx.session_id)

        # 渠道上下文注入：让 send_file_to_user 工具知道发给哪个会话。
        channel_ctx_token = current_channel_send_ctx.set(
            ChannelSendContext(adapter=self.adapter, event=event)
        )
        try:
            async for step in agent_loop.run(
                user_input=user_content,
                user_id=ctx.user_id,
                session_id=ctx.session_id,
                conversation_history=processed_history,
                tools=tools_schema,
            ):
                if isinstance(step, AgentStep) and step.done:
                    if not final_output:
                        final_output = step.final_output or ""
                elif isinstance(step, str):
                    if step.startswith("reasoning_delta:"):
                        # 推理过程实时广播（Web 端「重新连接」回放用）
                        event_logger.submit({
                            "type": "thinking",
                            "content": step[len("reasoning_delta:"):],
                        })
                    elif step.startswith("text_delta:"):
                        delta = step[len("text_delta:"):]
                        final_output += delta
                        await stream_reply.push(final_output)
                        event_logger.submit({
                            "type": "text_delta",
                            "content": delta,
                        })
                    elif step.startswith("tool_call:"):
                        parts = step.split(":", 3)
                        try:
                            tc_args = json.loads(parts[3]) if len(parts) > 3 else {}
                        except json.JSONDecodeError:
                            tc_args = {}
                        tc_id = parts[1] if len(parts) > 1 else ""
                        tool_name = parts[2] if len(parts) > 2 else ""
                        tool_calls_list.append({
                            "id": tc_id,
                            "name": tool_name,
                            "arguments": tc_args,
                        })
                        event_logger.submit({
                            "type": "tool_call",
                            "id": tc_id,
                            "name": tool_name,
                            "arguments": tc_args,
                        })
                        if tool_name:
                            await task_tool(ctx.user_id, ctx.session_id, tool_name)
                    elif step.startswith("tool_result:"):
                        parts = step.split(":", 4)
                        tc_id = parts[1] if len(parts) > 1 else ""
                        tc_name = parts[2] if len(parts) > 2 else ""
                        try:
                            preview = json.loads(parts[4]) if len(parts) > 4 else ""
                        except json.JSONDecodeError:
                            preview = parts[4] if len(parts) > 4 else ""
                        tool_results_map[tc_id] = {
                            "status": parts[3] if len(parts) > 3 else "",
                            "preview": preview,
                        }
                        event_logger.submit({
                            "type": "tool_result",
                            "tool_call_id": tc_id,
                            "name": tc_name,
                            "status": parts[3] if len(parts) > 3 else "",
                            "preview": preview,
                        })
                        for tc in tool_calls_list:
                            if tc["id"] in tool_results_map:
                                tc["result"] = tool_results_map[tc["id"]]

            await stream_reply.finish(final_output)
        except asyncio.CancelledError:
            # /stop 或撤回即停触发的取消。CancelledError 是 BaseException，
            # 不会被下面的 except Exception 吞掉；在这里做中断收尾后继续传播。
            logger.info(
                "agent_loop_cancelled",
                channel_id=str(self.channel.id),
                chat_id=event.chat_id,
            )
            if title_task is not None:
                title_task.cancel()
            await asyncio.shield(
                self._finalize_interrupted(
                    stream_reply, event_logger, ctx, final_output, tool_calls_list
                )
            )
            raise
        except Exception as e:
            logger.exception("agent_loop_failed", channel_id=str(self.channel.id))
            try:
                await stream_reply.finish(f"❌ 执行出错：{e}")
            except Exception:
                pass
            event_logger.submit({"type": "error", "message": str(e)})
            await event_logger.drain()
            return
        finally:
            current_channel_send_ctx.reset(channel_ctx_token)
            await task_finished(ctx.user_id, ctx.session_id)
            if reaction_id:
                try:
                    await self.adapter.delete_reaction(event, reaction_id)
                except Exception:
                    pass

        # Persist the assistant reply (with tool call details for rendering).
        assistant_msg = Message(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            role="assistant",
            content=final_output,
            tool_calls=tool_calls_list if tool_calls_list else None,
        )
        db.add(assistant_msg)
        await db.flush()
        await db.commit()

        # 应用自动生成的标题（与 Agent 循环并发启动的任务）
        if title_task is not None and session is not None:
            new_title = await title_task
            if new_title:
                session.title = new_title
                await db.commit()

        # 记录最终结果事件，Web 端「重新连接」据此收尾（message_id 供前端定位消息）。
        event_logger.submit({"type": "text", "content": final_output})
        event_logger.submit({
            "type": "done",
            "message_id": str(assistant_msg.id),
            "content": final_output,
            "tool_calls": tool_calls_list,
        })
        await event_logger.drain()

        # Fire-and-forget: memory extraction and context summary update.
        fire_memory_extraction(
            ctx.user_id,
            ctx.session_id,
            history,
            event.text,
            final_output,
            enable=agent.enable_memory_extraction if agent else True,
        )
        summary_task = asyncio.create_task(
            update_context_summary(
                ctx.session_id, history, event.text, final_output, agent_loop.provider
            )
        )
        background_tasks.add(summary_task)
        summary_task.add_done_callback(background_tasks.discard)

    async def _finalize_interrupted(
        self,
        stream_reply: _StreamingReply,
        event_logger: _BufferedEventLogger,
        ctx: _ResolvedContext,
        final_output: str,
        tool_calls_list: list[dict],
    ) -> None:
        """中断收尾：部分结果写入消息历史、回放流收尾、卡片标记「已中断」。

        在 CancelledError 分支中被 asyncio.shield 包裹调用；原 DB session 随
        被取消的请求上下文关闭，持久化使用新 session。
        """
        assert ctx.user_id is not None and ctx.session_id is not None
        interrupted_text = f"{final_output}\n\n⏹ 已中断" if final_output else "⏹ 已中断"

        # 1. 部分 assistant 消息落库（新 session，原 session 已随取消关闭）。
        message_id: str | None = None
        try:
            factory = get_session_factory()
            async with factory() as db:
                assistant_msg = Message(
                    session_id=ctx.session_id,
                    user_id=ctx.user_id,
                    role="assistant",
                    content=interrupted_text,
                    tool_calls=tool_calls_list if tool_calls_list else None,
                )
                db.add(assistant_msg)
                await db.commit()
                message_id = str(assistant_msg.id)
        except Exception:
            logger.exception(
                "agent_loop_cancelled_persist_failed", session_id=str(ctx.session_id)
            )

        # 2. 回放事件流收尾（Web 端「重新连接」据此渲染中断终态）。
        try:
            event_logger.submit({"type": "text", "content": interrupted_text})
            event_logger.submit({
                "type": "done",
                "message_id": message_id,
                "content": interrupted_text,
                "tool_calls": tool_calls_list,
                "interrupted": True,
            })
            await event_logger.drain()
        except Exception:
            logger.exception(
                "agent_loop_cancelled_drain_failed", session_id=str(ctx.session_id)
            )

        # 3. 流式卡片标记中断并关闭 streaming_mode；非流式降级为普通消息。
        try:
            await stream_reply.finish(interrupted_text)
        except Exception:
            logger.exception(
                "agent_loop_cancelled_card_failed", session_id=str(ctx.session_id)
            )

# --- Utilities ---


def _find_prefix_bytes(text: str, max_bytes: int) -> int:
    """Largest char index n such that ``text[:n]`` fits within ``max_bytes`` UTF-8 bytes.

    Walks code points so a multi-byte character is never split in half.
    """
    size = 0
    for i, ch in enumerate(text):
        size += len(ch.encode("utf-8"))
        if size > max_bytes:
            return i
    return len(text)


def _byte_truncate(text: str, max_bytes: int) -> str:
    """Truncate ``text`` to a byte-safe prefix of at most ``max_bytes``."""
    return text[:_find_prefix_bytes(text, max_bytes)]


def _split_text(text: str, max_bytes: int) -> list[str]:
    """Split long text into UTF-8 byte-safe chunks no larger than ``max_bytes``.

    Prefers paragraph/newline boundaries, then spaces; falls back to a hard
    byte cut that never splits a multi-byte code point.
    """
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text.encode("utf-8")) <= max_bytes:
            chunks.append(text)
            break
        # Largest byte-safe prefix index; look for a natural boundary inside it.
        hard_cut = _find_prefix_bytes(text, max_bytes)
        cut = text.rfind("\n", 0, hard_cut)
        if cut <= 0:
            cut = text.rfind(" ", 0, hard_cut)
        if cut <= 0:
            cut = hard_cut
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# Imported at module level for /bind command.
from aio_agent_platform.channels.binding import BIND_CODE_TTL_MINUTES  # noqa: E402
