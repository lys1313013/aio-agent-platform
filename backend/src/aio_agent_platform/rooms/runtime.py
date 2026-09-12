"""Sequential, durable room execution; SSE readers never own a model task."""

import asyncio
import copy
import json
import time
from contextlib import suppress
from uuid import UUID

import structlog
from sqlalchemy import select

from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.chat import build_agent_loop, filter_tools_by_agent, load_agent
from aio_agent_platform.core.confirmation import confirmation_manager
from aio_agent_platform.core.context import (
    ContextBudget,
    current_agent_id,
    estimate_messages_tokens,
    generate_summary,
)
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.core.usage import record_llm_usage
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import (
    ChatRoom,
    ChatRoomMember,
    ChatRoomMessage,
    ChatRoomRun,
    ChatRoomTask,
    User,
    Workspace,
)
from aio_agent_platform.db.sanitize import sanitize_pg_text
from aio_agent_platform.llm import AnthropicProvider, LLMMessage, build_user_content
from aio_agent_platform.rooms.service import (
    ACTIVE_RUNS,
    ACTIVE_TASKS,
    add_message,
    members,
    now,
    touch,
)
from aio_agent_platform.storage.chat_attachments import ChatAttachmentStorage
from aio_agent_platform.tools.builtin import FRONTEND_TOOL_NAMES

logger = structlog.get_logger()
_workers: dict[UUID, asyncio.Task] = {}


class RoomExecutionError(Exception):
    """A safe, user-visible room error (provider exceptions stay in server logs)."""


class MeteredProvider:
    """Counts both context summaries and ReAct calls; missing usage remains unknown."""

    def __init__(self, provider, user_id):
        self.provider = provider
        self.user_id = user_id
        self.known = True
        self.calls = 0
        self.totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def __getattr__(self, key):
        return getattr(self.provider, key)

    def record(self, usage):
        self.calls += 1
        if not usage or usage.get("total_tokens") is None:
            self.known = False
            return
        for key in self.totals:
            self.totals[key] += usage.get(key, 0) or 0

    @property
    def usage(self):
        return dict(self.totals) if self.known and self.calls else None

    async def complete(self, *args, **kwargs):
        result = await self.provider.complete(*args, **kwargs)
        usage = getattr(result, "usage", None)
        self.record(usage)
        if usage:
            record_llm_usage(self.user_id, self.provider.model, usage)
        return result

    async def stream(self, *args, **kwargs):
        usage = None
        try:
            async for chunk in self.provider.stream(*args, **kwargs):
                if chunk.type == "done":
                    usage = chunk.usage
                yield chunk
        finally:
            self.record(usage)


def public_record(message: ChatRoomMessage) -> dict:
    content = message.content
    if len(content) > 12000:
        content = content[:12000] + "\n[长消息已截取，可通过引用原消息追问]"
    if message.status != "completed":
        content = f"[该成员未完成，状态：{message.status}，不能视为最终结论]\n{content}"
    files = message.payload.get("file_attachments") or []
    file_notes = "\n".join(f"附件：{f['filename']}，工作区路径：{f['workspace_path']}" for f in files)
    for change in message.payload.get("file_changes") or []:
        file_notes += f"\n文件变更：{change['action']}，工作区路径：{change['path']}"
    return {"content": f"[{message.name}，消息 #{message.sequence}]\n{content}\n{file_notes}",
            "attachments": message.payload.get("attachments") or []}


def input_text(snapshot: dict) -> str:
    data = snapshot["input"]
    parts = ["房间目标：" + snapshot["goal"], "成员职责：" + snapshot["roster"]]
    quote = data.get("quote")
    if quote:
        marker = "（引用过长，已截取）" if quote.get("truncated") else ""
        parts.append(f"引用 {quote['name']} 的消息{marker}，状态 {quote['status']}：\n{quote['content']}")
    parts.append("用户本次要求：\n" + data["message"])
    for file in data.get("file_attachments", []):
        parts.append(f"附件：{file['filename']}，工作区路径：{file['workspace_path']}")
    return "\n\n".join(parts)


class Output:
    def __init__(self):
        self.content = ""
        self.payload = {"tool_calls": [], "reasoning": [], "file_changes": []}
        self.confirmation = None
        self.status = "running"
        self.done = False

    def queue_event(self, event: dict):
        if event.get("type") == "confirmation_required":
            self.confirmation = event
            self.status = "waiting_user"
        elif event.get("type") == "confirmation_resolved":
            if self.confirmation and self.confirmation["confirmation_id"] == event.get("confirmation_id"):
                self.payload.setdefault("room_confirmations", []).append({
                    **self.confirmation, "resolved": event,
                })
                self.confirmation = None
                self.status = "running"

    def consume(self, event) -> bool:
        """Return True for state that must be flushed before execution continues."""
        if isinstance(event, AgentStep):
            if event.done:
                self.done = True
                self.content = self.content or event.final_output or ""
            return True
        if not isinstance(event, str):
            return False
        if event.startswith("text_delta:"):
            self.content += event[len("text_delta:"):]
            return False
        if event.startswith("reasoning:"):
            self.payload["reasoning"].append({"id": str(len(self.payload["reasoning"])),
                                               "content": event[len("reasoning:"):]})
        elif event.startswith("tool_call:"):
            _, tc_id, name, arguments = event.split(":", 3)
            self.payload["tool_calls"].append({"id": tc_id, "name": name, "arguments": json.loads(arguments)})
        elif event.startswith("tool_result:"):
            _, tc_id, _name, status, preview = event.split(":", 4)
            for tc in self.payload["tool_calls"]:
                if tc["id"] == tc_id:
                    parsed = json.loads(preview)
                    tc["result"] = {"status": status, "preview": parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False)}
        elif event.startswith("file_changes:"):
            from aio_agent_platform.interface.routes.chat import _merge_file_changes
            self.payload["file_changes"] = _merge_file_changes(
                self.payload["file_changes"], json.loads(event[len("file_changes:"):]),
            )
        return True


def start_run(app, room_id: UUID, run_id: UUID) -> None:
    if run_id in _workers:
        return
    task = asyncio.create_task(_run(app, room_id, run_id), name=f"room:{run_id}")
    _workers[run_id] = task
    task.add_done_callback(lambda _task: _workers.pop(run_id, None))


async def shutdown() -> None:
    tasks = list(_workers.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _monitor(room_id: UUID, run_id: UUID) -> str:
    """Cross-worker stop/confirmation mailbox and lease heartbeat."""
    last_heartbeat = 0.0
    answered = set()
    while True:
        await asyncio.sleep(0.5)
        async with get_session_factory()() as db:
            run = await db.get(ChatRoomRun, run_id)
            if not run or run.status not in ACTIVE_RUNS:
                return "interrupted"
            if run.stop_requested:
                return "stopped"
            tasks = (await db.scalars(select(ChatRoomTask).where(
                ChatRoomTask.run_id == run_id, ChatRoomTask.status == "waiting_user",
            ))).all()
            for task in tasks:
                if task.confirmation and task.confirmation_response:
                    cid = task.confirmation["confirmation_id"]
                    if cid not in answered:
                        confirmation_manager.resolve_confirmation(cid, task.confirmation_response)
                        answered.add(cid)
            if time.monotonic() - last_heartbeat >= 5:
                room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id).with_for_update())
                if room is None:
                    return "interrupted"
                user = await db.get(User, room.user_id)
                if not user or not user.is_active or user.tenant_id != room.tenant_id:
                    return "interrupted"
                # Only update the lease. Do not overwrite a concurrent stop request.
                run.heartbeat_at = now()
                await db.commit()
                last_heartbeat = time.monotonic()


async def _finish_run(room_id: UUID, run_id: UUID, reason: str | None) -> None:
    async with get_session_factory()() as db:
        room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id).with_for_update())
        run = await db.get(ChatRoomRun, run_id)
        if not room or not run or run.status not in ACTIVE_RUNS:
            return
        tasks = (await db.scalars(select(ChatRoomTask).where(ChatRoomTask.run_id == run_id))).all()
        if run.stop_requested:
            reason = "stopped"
        for task in tasks:
            if task.status in ACTIVE_TASKS:
                task.status = "cancelled" if reason == "stopped" and task.status == "queued" else ("stopped" if reason == "stopped" else "failed")
                task.error = "用户已停止" if reason == "stopped" else "执行服务中断，请检查已完成动作后重试"
                task.completed_at = now()
                task.confirmation = None
                if task.message_id:
                    message = await db.get(ChatRoomMessage, task.message_id)
                    if message:
                        message.status = task.status
        statuses = [t.status for t in tasks]
        run.status = reason or ("completed" if all(s == "completed" for s in statuses)
                                else "partial" if "completed" in statuses else "failed")
        run.error = "执行服务中断，未自动重放工具动作" if reason == "interrupted" else None
        run.completed_at = now()
        touch(room)
        await db.commit()


async def _run(app, room_id: UUID, run_id: UUID) -> None:
    body = asyncio.create_task(_run_members(app, room_id, run_id))
    monitor = asyncio.create_task(_monitor(room_id, run_id))
    reason = None
    try:
        done, _ = await asyncio.wait({body, monitor}, return_when=asyncio.FIRST_COMPLETED)
        if monitor in done:
            reason = monitor.result()
            body.cancel()
        else:
            body.result()
    except asyncio.CancelledError:
        reason = "interrupted"
    except Exception:
        logger.exception("room_run_failed", room_id=str(room_id), run_id=str(run_id))
        reason = "interrupted"
    finally:
        body.cancel()
        monitor.cancel()
        await asyncio.gather(body, monitor, return_exceptions=True)
        with suppress(Exception):
            await _finish_run(room_id, run_id, reason)


async def _run_members(app, room_id: UUID, run_id: UUID) -> None:
    async with get_session_factory()() as db:
        room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id).with_for_update())
        run = await db.get(ChatRoomRun, run_id)
        if not room or not run or run.status != "queued" or run.stop_requested:
            return
        run.status = "running"
        touch(room)
        task_ids = list((await db.scalars(select(ChatRoomTask.id).where(
            ChatRoomTask.run_id == run_id,
        ).order_by(ChatRoomTask.position))).all())
        await db.commit()
    for task_id in task_ids:
        await _execute_task(app, room_id, run_id, task_id)


async def _save_output(room_id, run_id, task_id, output, provider, started, *, terminal=None, error=None):
    async with get_session_factory()() as db:
        room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id).with_for_update())
        run = await db.get(ChatRoomRun, run_id)
        if not room or not run or run.status not in ACTIVE_RUNS:
            raise asyncio.CancelledError
        task = await db.get(ChatRoomTask, task_id)
        if run.stop_requested and not terminal:
            raise asyncio.CancelledError
        if terminal == "interrupted":
            terminal = "stopped" if run.stop_requested else "failed"
            error = "用户已停止" if run.stop_requested else "执行服务中断，请检查已完成动作后重试"
        task.status = terminal or output.status
        task.error = error
        task.token_usage = provider.usage if provider else None
        task.duration_ms = int((time.monotonic() - started) * 1000)
        old_id = (task.confirmation or {}).get("confirmation_id")
        new_id = (output.confirmation or {}).get("confirmation_id")
        if old_id != new_id:
            task.confirmation_response = None
        task.confirmation = sanitize_pg_text(copy.deepcopy(output.confirmation)) if not terminal else None
        if terminal:
            task.completed_at = now()
        if task.message_id:
            message = await db.get(ChatRoomMessage, task.message_id)
            message.content = sanitize_pg_text(output.content)
            message.status = task.status
            message.payload = sanitize_pg_text({**copy.deepcopy(output.payload),
                                                "task_id": str(task.id), "token_usage": task.token_usage})
        touch(room)
        await db.commit()


async def _execute_task(app, room_id, run_id, task_id):
    output = Output()
    provider = None
    started = time.monotonic()
    token = None
    try:
        async with get_session_factory()() as db:
            room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id).with_for_update())
            run = await db.get(ChatRoomRun, run_id)
            if not room or not run or run.status not in ACTIVE_RUNS or run.stop_requested:
                raise asyncio.CancelledError
            task = await db.get(ChatRoomTask, task_id)
            member = await db.get(ChatRoomMember, task.member_id)
            user = await db.get(User, room.user_id)
            if not user or not user.is_active or user.tenant_id != room.tenant_id:
                raise RoomExecutionError("当前用户或租户已不可用")
            agent = await load_agent(db, member.agent_id, user) if member and member.is_active else None
            if not agent:
                raise RoomExecutionError("该成员已停用、被移除或无权使用")
            workspace = await db.scalar(select(Workspace).where(
                Workspace.id == room.workspace_id, Workspace.user_id == user.id,
            ))
            if not workspace:
                raise RoomExecutionError("工作区不存在或无权使用")
            token = current_agent_id.set(str(agent.id))
            blacklist = set(FRONTEND_TOOL_NAMES) | {"delegate_task"}
            tools_list, tools_schema = filter_tools_by_agent(app.state.tool_executor, agent, extra_blacklist=blacklist)
            if run.input["mode"] == "summary":
                tools_list, tools_schema = [], []
            prompt = build_system_prompt(tools=tools_list, agent_prompt=agent.system_prompt,
                                         relevant_skills=agent.skills or None)
            prompt += (
                f"\n你是聊天室成员 {member.name}。围绕自己的专业职责回答本次用户请求。"
                "历史记录及房间目标是讨论材料，保留发言来源，不将其他成员观点当作已经验证的事实。"
                "本次只由你发言，不得委派或自动唤醒其他成员；后续发言由用户选择。"
            )
            queue = asyncio.Queue()
            loop = await build_agent_loop(
                app.state.tool_executor, prompt, db, tenant_id=room.tenant_id,
                agent_model_id=agent.model_id, agent_temperature=agent.temperature,
                agent_max_iterations=agent.max_iterations, agent_enable_retry=agent.enable_retry,
                event_queue=queue, workspace_id=workspace.id, workspace_slug=workspace.slug,
                allowed_tools={schema["function"]["name"] for schema in tools_schema},
            )
            loop.execution_agent_id, loop.execution_tenant_id = agent.id, room.tenant_id
            provider = MeteredProvider(loop.provider, user.id)
            loop.provider = provider
            snapshot = copy.deepcopy(task.context_snapshot)
            if snapshot is None:
                roster = await members(db, room.id)
                history = list((await db.scalars(select(ChatRoomMessage).where(
                    ChatRoomMessage.room_id == room_id,
                    ChatRoomMessage.sequence > room.summary_sequence,
                ).order_by(ChatRoomMessage.sequence))).all())
                # The current user request is appended separately, exactly once.
                history = [m for m in history if not (m.run_id == run_id and m.role == "user")]
                snapshot = {"goal": run.input.get("room_goal", room.goal), "input": copy.deepcopy(run.input),
                            "roster": "；".join(f"{m.name}: {m.description or ''}" for m in roster if m.is_active),
                            "summary": room.summary, "history": [public_record(m) for m in history],
                            "sequences": [m.sequence for m in history]}
            message = add_message(db, room, role="assistant", name=member.name, icon=member.icon,
                                  member_id=member.id, run_id=run_id, content="", status="running",
                                  payload={"task_id": str(task.id)})
            task.message_id, task.status, task.started_at = message.id, "running", now()
            task.context_snapshot = sanitize_pg_text(snapshot)
            await db.commit()

        # Context compaction happens outside any DB transaction/row lock.
        history_records = snapshot["history"]
        budget = ContextBudget.from_settings()
        if len(history_records) > 24 or (len(history_records) > 2 and estimate_messages_tokens([
            LLMMessage(role="user", content=r["content"]) for r in history_records
        ]) > budget.history_budget):
            keep = min(12, max(1, len(history_records) // 2))
            older = history_records[:-keep]
            previous = [LLMMessage(role="user", content="已有摘要：" + snapshot["summary"])] if snapshot.get("summary") else []
            summary = await generate_summary(previous + [LLMMessage(role="user", content=r["content"]) for r in older], provider)
            snapshot["summary"] = summary
            snapshot["history"] = history_records[-keep:]
            async with get_session_factory()() as db:
                room = await db.scalar(select(ChatRoom).where(ChatRoom.id == room_id).with_for_update())
                run = await db.get(ChatRoomRun, run_id)
                if not run or run.status not in ACTIVE_RUNS or run.stop_requested:
                    raise asyncio.CancelledError
                if not task.retry_of:
                    room.summary, room.summary_sequence = summary, snapshot["sequences"][-keep - 1]
                stored_task = await db.get(ChatRoomTask, task_id)
                stored_task.context_snapshot = sanitize_pg_text(snapshot)
                await db.commit()

        text_input = input_text(snapshot)
        vision = bool(getattr(provider, "supports_vision", False))
        storage = ChatAttachmentStorage() if vision and (
            snapshot["input"].get("attachments") or any(r.get("attachments") for r in snapshot["history"])
        ) else None

        def content(text, images):
            if images and vision:
                return build_user_content(text=text, attachments=images,
                                          provider_type="anthropic" if isinstance(provider.provider, AnthropicProvider) else "openai",
                                          to_data_uri=storage.to_data_uri)
            return text + ("\n[本成员模型不支持读取图片内容，请其他成员提供文字说明]" if images else "")

        history_messages = [LLMMessage(role="user", content=content(r["content"], r.get("attachments"))) for r in snapshot["history"]]
        if snapshot.get("summary"):
            history_messages.insert(0, LLMMessage(role="user", content="[房间历史摘要，保留发言来源]\n" + snapshot["summary"]))
        budget = ContextBudget.from_settings()
        # Preserve the current request and quote; trim older context first.
        current = content(text_input, snapshot["input"].get("attachments"))
        fixed = [LLMMessage(role="system", content=prompt), LLMMessage(role="user", content=current)]
        if estimate_messages_tokens(fixed) > budget.usable:
            raise RoomExecutionError("本次问题、目标或引用超过模型上下文预算，请缩短后重试")
        trimmed = False
        while history_messages and estimate_messages_tokens(fixed + history_messages) > budget.usable:
            history_messages.pop(1 if snapshot.get("summary") and len(history_messages) > 1 else 0)
            trimmed = True
        if trimmed:
            current = content(text_input + "\n[部分较早历史因上下文预算已省略]", snapshot["input"].get("attachments"))
        last_save = 0.0
        async for event in loop.run(user_input=current, user_id=user.id, session_id=room_id,
                                    conversation_history=history_messages, tools=tools_schema):
            force = False
            while not queue.empty():
                output.queue_event(queue.get_nowait())
                force = True
            force = output.consume(event) or force
            if force or time.monotonic() - last_save >= 0.35:
                await _save_output(room_id, run_id, task_id, output, provider, started)
                last_save = time.monotonic()
        while not queue.empty():
            output.queue_event(queue.get_nowait())
        if not output.done or getattr(loop, "stop_reason", None) == "max_iterations":
            raise RoomExecutionError("智能体达到迭代上限或未完成回答")
        await _save_output(room_id, run_id, task_id, output, provider, started, terminal="completed")
    except asyncio.CancelledError:
        with suppress(Exception, asyncio.CancelledError):
            await _save_output(room_id, run_id, task_id, output, provider, started, terminal="interrupted")
        raise
    except Exception as exc:
        logger.exception("room_member_failed", room_id=str(room_id), run_id=str(run_id), task_id=str(task_id))
        # Runtime errors are controlled messages; provider errors may contain URLs or credentials.
        error = str(exc) if isinstance(exc, RoomExecutionError) else "成员执行失败，请查看服务端日志或稍后重试"
        await _save_output(room_id, run_id, task_id, output, provider, started, terminal="failed", error=error)
    finally:
        if token is not None:
            current_agent_id.reset(token)
