"""Inbound pipeline — dedup, identity resolution, session mapping, Agent execution.

The pipeline is shared by all transports for a given channel. It:

1. Deduplicates events by ``event_id`` (TTL 1h in-memory cache).
2. Resolves the external user to a platform ``User`` via their binding — if
   unbound, replies with a bind-code guide instead of creating an account.
3. Maps ``(channel_id, chat_id, external_id)`` to a platform session.
4. Intercepts built-in commands (``/bind``, ``/new``, ``/help``) before they
   reach the Agent.
5. Drives ``AgentLoop`` for normal messages, streaming deltas back through the
   adapter's send/update methods.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.channels.adapter import ChannelAdapter, InboundEvent
from aio_agent_platform.channels.binding import (
    BindCodeError,
    BindCodeRateLimited,
    issue_bind_code,
    resolve_external_user,
)
from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.chat import (
    background_tasks,
    build_agent_loop,
    build_system_prompt_with_memories,
    filter_tools_by_agent,
    fire_memory_extraction,
    load_agent,
    load_conversation_history,
    update_context_summary,
)
from aio_agent_platform.core.context import prepare_context
from aio_agent_platform.core.task_registry import task_finished, task_started, task_tool
from aio_agent_platform.db import Message
from aio_agent_platform.db import Session as ChatSession
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import (
    ChannelConfig,
    ChannelSessionMapping,
)

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


# Feishu's per-message text limit is ~30KB; we use a conservative char cap.
_MAX_CHARS_PER_MESSAGE = 3500


@dataclass
class _ResolvedContext:
    user_id: UUID | None  # None when the external user is not bound yet
    session_id: UUID | None
    bind_type: str  # "bound" or "unbound"


class ChannelInboundPipeline:
    """Process inbound events for a single channel."""

    def __init__(self, channel: ChannelConfig, adapter: ChannelAdapter, tool_executor):
        self.channel = channel
        self.adapter = adapter
        self.tool_executor = tool_executor
        self._processing_tasks: set[asyncio.Task] = set()

    def submit(self, event: InboundEvent) -> None:
        """Schedule an event for processing. Returns immediately.

        Callers (transports) should ACK the inbound request before or after
        calling this — the pipeline does all heavy work asynchronously.
        """
        task = asyncio.create_task(self._safe_handle(event))
        self._processing_tasks.add(task)
        task.add_done_callback(self._processing_tasks.discard)

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
        # 1. Dedup.
        if _dedup(event.event_id):
            logger.info("pipeline_duplicate_event", event_id=event.event_id)
            return

        # 2. Group chat: only respond when the bot is @-mentioned.
        if event.chat_kind.value == "group" and not event.mentions_bot:
            return

        # 3. Resolve user + session.
        factory = get_session_factory()
        async with factory() as db:
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
            if text == "/new":
                await self._handle_new(db, event, ctx)
                return
            if text == "/help":
                await self._handle_help(event)
                return

            # 6. Drive AgentLoop.
            await self._drive_agent(db, event, ctx)

    # --- Context resolution ---

    async def _resolve_context(self, db: AsyncSession, event: InboundEvent) -> _ResolvedContext:
        user_id, bind_type = await resolve_external_user(
            db, self.channel.tenant_id, event.external_id
        )
        if user_id is None:
            return _ResolvedContext(user_id=None, session_id=None, bind_type=bind_type)

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

        return _ResolvedContext(user_id=user_id, session_id=session_id, bind_type=bind_type)

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
        if ctx.bind_type == "bound":
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

    async def _handle_help(self, event: InboundEvent) -> None:
        reply = (
            "📖 可用指令：\n"
            "• /bind — 获取绑定码，关联已有平台账号\n"
            "• /new — 开启新对话\n"
            "• /help — 查看此帮助"
        )
        await self.adapter.send(event, reply)

    # --- Agent execution ---

    async def _drive_agent(
        self, db: AsyncSession, event: InboundEvent, ctx: _ResolvedContext
    ) -> None:
        assert ctx.user_id is not None  # only reached for bound users
        assert ctx.session_id is not None
        agent = await load_agent(db, self.channel.agent_id, tenant_id=self.channel.tenant_id)
        if agent is None:
            await self.adapter.send(event, "⚠️ 渠道绑定的智能体不可用，请联系管理员。")
            return

        # Filter tools + apply channel blacklist.
        blacklist = set(self.channel.tool_blacklist or [])
        # Filter out AskUserQuestion for channel conversations — it would block
        # waiting for a Web UI confirmation that will never come.
        blacklist.add("AskUserQuestion")
        tools_list, tools_schema = filter_tools_by_agent(
            self.tool_executor, agent, extra_blacklist=blacklist
        )

        system_prompt = await build_system_prompt_with_memories(
            db, ctx.user_id, event.text, tools_list, agent=agent
        )

        history, context_summary = await load_conversation_history(db, ctx.session_id)

        agent_loop = await build_agent_loop(
            self.tool_executor, system_prompt, db,
            agent_model_id=agent.model_id,
            agent_temperature=agent.temperature,
            agent_max_iterations=agent.max_iterations,
            agent_enable_retry=agent.enable_retry if agent.enable_retry is not None else True,
        )

        prepared_messages, _ = await prepare_context(
            system_prompt=system_prompt,
            history=history,
            user_input=event.text,
            provider=agent_loop.provider,
            existing_summary=context_summary,
        )
        processed_history = [
            m for m in prepared_messages
            if m.role != "system" and not (m.role == "user" and m.content == event.text)
        ]

        # Show a typing indicator on the user's message while the agent runs.
        reaction_id = None
        try:
            reaction_id = await self.adapter.add_reaction(event, "Typing")
        except Exception:
            pass

        # Persist the user message up front so it survives agent failures.
        db.add(Message(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            role="user",
            content=event.text,
        ))
        await db.commit()

        # 登记在跑任务，供宠物 widget 展示（渠道任务没有浏览器 SSE）
        chat_key = f"{self.channel.id}:{event.chat_id}:{event.external_id}"
        await task_started(
            ctx.user_id, ctx.session_id, event.text, self.channel.channel_type, chat_key,
            agent_id=str(self.channel.agent_id),
        )

        final_output = ""
        tool_calls_list: list[dict] = []
        tool_results_map: dict[str, dict] = {}

        try:
            async for step in agent_loop.run(
                user_input=event.text,
                user_id=ctx.user_id,
                session_id=ctx.session_id,
                conversation_history=processed_history,
                tools=tools_schema,
            ):
                if isinstance(step, AgentStep) and step.done:
                    if not final_output:
                        final_output = step.final_output or ""
                elif isinstance(step, str):
                    if step.startswith("text_delta:"):
                        final_output += step[len("text_delta:"):]
                    elif step.startswith("tool_call:"):
                        parts = step.split(":", 3)
                        try:
                            tc_args = json.loads(parts[3]) if len(parts) > 3 else {}
                        except json.JSONDecodeError:
                            tc_args = {}
                        tool_name = parts[2] if len(parts) > 2 else ""
                        tool_calls_list.append({
                            "id": parts[1] if len(parts) > 1 else "",
                            "name": tool_name,
                            "arguments": tc_args,
                        })
                        if tool_name:
                            await task_tool(ctx.user_id, ctx.session_id, tool_name)
                    elif step.startswith("tool_result:"):
                        parts = step.split(":", 4)
                        tc_id = parts[1] if len(parts) > 1 else ""
                        try:
                            preview = json.loads(parts[4]) if len(parts) > 4 else ""
                        except json.JSONDecodeError:
                            preview = parts[4] if len(parts) > 4 else ""
                        tool_results_map[tc_id] = {
                            "status": parts[3] if len(parts) > 3 else "",
                            "preview": preview,
                        }
                        for tc in tool_calls_list:
                            if tc["id"] in tool_results_map:
                                tc["result"] = tool_results_map[tc["id"]]

            if final_output:
                await self._send_final(event, final_output)
            else:
                await self.adapter.send(event, "（无输出）")
        except Exception as e:
            logger.exception("agent_loop_failed", channel_id=str(self.channel.id))
            try:
                await self.adapter.send(event, f"❌ 执行出错：{e}")
            except Exception:
                pass
            return
        finally:
            await task_finished(ctx.user_id, ctx.session_id)
            if reaction_id:
                try:
                    await self.adapter.delete_reaction(event, reaction_id)
                except Exception:
                    pass

        # Persist the assistant reply (with tool call details for rendering).
        db.add(Message(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            role="assistant",
            content=final_output,
            tool_calls=tool_calls_list if tool_calls_list else None,
        ))
        await db.commit()

        # Fire-and-forget: memory extraction (skip for shadow accounts — they
        # don't write L2 long-term memory) and context summary update.
        if ctx.bind_type != "shadow":
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

    async def _send_final(self, event: InboundEvent, text: str) -> None:
        """Send the final content, splitting into multiple messages if needed."""
        if len(text) <= _MAX_CHARS_PER_MESSAGE:
            await self.adapter.send_markdown(event, text)
            return
        for chunk in _split_text(text, _MAX_CHARS_PER_MESSAGE):
            await self.adapter.send_markdown(event, chunk)


# --- Utilities ---


def _split_text(text: str, limit: int) -> list[str]:
    """Split long text at natural boundaries (paragraphs / newlines)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# Imported at module level for /bind command.
from aio_agent_platform.channels.binding import BIND_CODE_TTL_MINUTES  # noqa: E402
