"""Agent Loop -- ReAct reasoning + acting."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select

from aio_agent_platform.core.confirmation import confirmation_manager
from aio_agent_platform.core.context import (
    ContextBudget,
    compress_early_tool_results,
    estimate_messages_tokens,
)
from aio_agent_platform.core.usage import record_llm_usage
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import Session as ChatSession
from aio_agent_platform.db.models import User
from aio_agent_platform.llm import (
    LLMMessage,
    LLMProvider,
    ToolCall,
)
from aio_agent_platform.observation import get_current_observation, get_langfuse_client
from aio_agent_platform.observation.recorder import (
    ObsContext,
    get_obs_context,
    get_recorder,
    set_obs_context,
)
from aio_agent_platform.tools.executor import ToolExecutor, ToolResult

logger = structlog.get_logger(__name__)

# user_id -> tenant_id 的进程内缓存，避免每次 Agent.run 重复查库
_tenant_cache: dict[str, UUID | None] = {}


async def _resolve_tenant_id(user_id: UUID) -> UUID | None:
    """Resolve a user's tenant_id (cached per process)."""
    key = str(user_id)
    if key in _tenant_cache:
        return _tenant_cache[key]
    try:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.execute(select(User.tenant_id).where(User.id == user_id))
            tid = row.scalar_one_or_none()
    except Exception:
        tid = None
    _tenant_cache[key] = tid
    return tid


async def _resolve_agent_id(session_id: UUID) -> UUID | None:
    """Resolve the agent bound to a session (session can switch agents)."""
    try:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.execute(
                select(ChatSession.agent_id).where(ChatSession.id == session_id)
            )
            return row.scalar_one_or_none()
    except Exception:
        return None


@dataclass
class DelegationContext:
    """Context for multi-agent delegation."""

    parent_agent_id: UUID
    delegation_depth: int
    max_depth: int
    event_queue: asyncio.Queue | None = None
    workspace_id: UUID | None = None
    workspace_slug: str | None = None


@dataclass
class ToolContext:
    """Context passed to every tool call."""

    user_id: UUID
    session_id: UUID
    trust_level: str = "ask_dangerous"
    delegation: DelegationContext | None = None
    workspace_id: UUID | None = None
    workspace_slug: str | None = None


@dataclass
class AgentStep:
    """Single step in agent execution."""

    step: int
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    final_output: str = ""
    done: bool = False


def _format_ask_user_output(
    response: dict,
    mode: str,
    options: list[dict],
    table_schema: dict | None = None,
) -> str:
    """将用户确认响应格式化为 Agent 可读的文本结果。"""
    status = response.get("status", "timeout")

    if status == "timeout":
        return (
            "用户暂未响应，请等待用户操作后再继续。"
        )

    if status == "rejected":
        user_note = response.get("user_input", "")
        if user_note:
            return f"User rejected this operation and noted: {user_note}"
        return "User rejected this operation. Please choose an alternative approach."

    if status == "modified":
        feedback = response.get("user_input", "")
        return f"User requested modifications: {feedback}"

    # status == "approved"
    user_input = response.get("user_input", "")

    if mode == "free_input":
        return f"User responded: {user_input}"

    if mode == "table_input":
        return _format_table_output(response, table_schema)

    # single_select / multi_select / approve
    selected = response.get("selected_options", [])
    selected_labels: list[str] = []
    for opt in options:
        if isinstance(opt, str):
            # LLM may pass plain strings instead of {id, label} dicts
            if opt in selected:
                selected_labels.append(opt)
        elif isinstance(opt, dict):
            if opt.get("id") in selected:
                selected_labels.append(opt.get("label", opt.get("id", "")))

    if not selected_labels:
        if user_input:
            return f"User did not select any options but noted: {user_input}"
        return "User did not select any options."

    result = f"User selected: {', '.join(selected_labels)}"
    if user_input:
        result += f" (with note: {user_input})"
    return result


def _format_table_output(response: dict, table_schema: dict | None) -> str:
    """将用户提交的表格数据格式化为 markdown 表格文本喂给 LLM。"""
    rows = response.get("table_data") or []
    note = (response.get("user_input") or "").strip()

    if not rows:
        if note:
            return f"User submitted an empty table but noted: {note}"
        return "User submitted an empty table (no rows)."

    # Normalize: LLM may pass columns directly as a list, or wrapped in {columns: [...]}
    if isinstance(table_schema, list):
        columns = table_schema
    elif isinstance(table_schema, dict):
        columns = table_schema.get("columns") or []
    else:
        columns = []
    if columns:
        col_keys = [c.get("key", "") for c in columns]
        headers = [c.get("title", c.get("key", "")) for c in columns]
    else:
        # 无 schema 时按首行的 key 推断列
        col_keys = list(rows[0].keys())
        headers = col_keys

    def _cell(value: object) -> str:
        if value is None:
            return ""
        return str(value).replace("\n", " ").replace("|", "\\|")

    lines = [
        f"User submitted the following table ({len(rows)} row(s)):",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(k)) for k in col_keys) + " |")

    if note:
        lines.append("")
        lines.append(f"Additional note from user: {note}")
    return "\n".join(lines)


class AgentLoop:
    """
    ReAct (Reasoning + Acting) loop.

    1. Build messages with system prompt + history + user input
    2. Call LLM (streaming)
    3. If text only -> return as final output
    4. If tool_calls -> execute tools -> inject results -> goto 2
    """

    def __init__(
        self,
        provider: LLMProvider,
        tool_executor: ToolExecutor,
        system_prompt: str = "",
        max_iterations: int = 100,
        trust_level: str = "ask_dangerous",
        delegation: DelegationContext | None = None,
        event_queue: asyncio.Queue | None = None,
        workspace_id: UUID | None = None,
        workspace_slug: str | None = None,
        allowed_tools: set[str] | None = None,
    ):
        self.provider = provider
        self.tool_executor = tool_executor
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.trust_level = trust_level
        self.delegation = delegation
        self.event_queue = event_queue
        self.workspace_id = workspace_id
        self.workspace_slug = workspace_slug
        self._last_ask_user_output: str = ""
        # Tool permission whitelist: None means all tools allowed (parent agent),
        # set of tool names means only those tools can be executed (child agents).
        self.allowed_tools = allowed_tools

    async def run(
        self,
        user_input: str | list[dict] | None,
        user_id: UUID,
        session_id: UUID,
        conversation_history: list[LLMMessage],
        tools: list[dict],
    ) -> AsyncIterator[AgentStep | str]:
        """
        Run the agent loop.

        Yields:
            str: Streaming events like "thinking:..." or "tool_call:..." or "tool_result:..."
            AgentStep: Final step when done=True
        """
        ctx = ToolContext(
            user_id=user_id,
            session_id=session_id,
            trust_level=self.trust_level,
            delegation=self.delegation,
            workspace_id=self.workspace_id,
            workspace_slug=self.workspace_slug,
        )

        # ---- Observation: open a trace for this execution ----
        trace_id = uuid4()
        tenant_id = await _resolve_tenant_id(user_id)
        agent_id = await _resolve_agent_id(session_id)
        set_obs_context(
            ObsContext(
                trace_id=trace_id,
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
        )
        t_start = time.monotonic()
        total_tokens = 0
        tool_call_count = 0

        # Build message list
        messages: list[LLMMessage] = []
        if self.system_prompt:
            messages.append(LLMMessage(role="system", content=self.system_prompt))
        messages.extend(conversation_history)
        messages.append(LLMMessage(role="user", content=user_input))

        for step_num in range(1, self.max_iterations + 1):
            step = AgentStep(step=step_num)
            logger.debug(
                "agent_loop_iteration_start",
                session_id=str(session_id),
                iteration=step_num,
                message_count=len(messages),
            )

            # --- Context budget check: compress early tool results ---
            if step_num > 3:
                messages = compress_early_tool_results(messages, step_num)
                current_tokens = estimate_messages_tokens(messages)
                budget = ContextBudget.from_settings()
                if current_tokens > budget.usable * 0.9:
                    logger.warning(
                        "ReAct iteration %d: context at ~%d tokens (90%% of %d usable)",
                        step_num,
                        current_tokens,
                        budget.usable,
                    )

            # Pending tool calls being accumulated from stream deltas
            pending_tool_calls: dict[int, dict] = {}  # index -> {id, name, args_str}

            # Buffer text chunks to decide thinking vs final text after stream ends
            text_chunks: list[str] = []

            # Usage reported by the provider's final stream event
            step_usage: dict | None = None

            # Call LLM (streaming)
            logger.debug(
                "agent_loop_llm_call",
                session_id=str(session_id),
                iteration=step_num,
            )
            async for chunk in self.provider.stream(messages, tools=tools):
                if chunk.type == "text_delta" and chunk.content:
                    step.thinking += chunk.content
                    text_chunks.append(chunk.content)

                elif chunk.type == "tool_call_start" and chunk.tool_call:
                    # New tool call starting
                    tc = chunk.tool_call
                    idx = len(pending_tool_calls)
                    pending_tool_calls[idx] = {
                        "id": tc.id,
                        "name": tc.name,
                        "args_str": chunk.argument_delta or "",
                    }

                elif chunk.type == "tool_call_delta" and chunk.argument_delta:
                    # Accumulate argument string delta
                    if pending_tool_calls:
                        last_idx = max(pending_tool_calls.keys())
                        pending_tool_calls[last_idx]["args_str"] += chunk.argument_delta

                elif chunk.type == "done" and chunk.usage:
                    step_usage = chunk.usage

            if step_usage:
                record_llm_usage(user_id, self.provider.model, step_usage)
                total_tokens += step_usage.get("total_tokens", 0) or 0

            # Finalize tool calls from accumulated deltas
            for _idx, tc_data in pending_tool_calls.items():
                try:
                    args = json.loads(tc_data["args_str"]) if tc_data["args_str"] else {}
                except json.JSONDecodeError:
                    args = {}
                step.tool_calls.append(
                    ToolCall(id=tc_data["id"], name=tc_data["name"], arguments=args)
                )

            tool_call_count += len(step.tool_calls)

            # No tool calls -> final answer text, we're done
            if not step.tool_calls:
                logger.info(
                    "agent_loop_final_answer",
                    session_id=str(session_id),
                    iteration=step_num,
                    text_length=len(step.thinking),
                )
                # Yield all buffered text as text_delta events (final text, not reasoning)
                for t in text_chunks:
                    yield f"text_delta:{t}"
                step.final_output = step.thinking
                self._finalize_trace(
                    trace_id, t_start, session_id, user_id, tenant_id,
                    status="completed",
                    iteration_count=step_num,
                    tool_call_count=tool_call_count,
                    total_tokens=total_tokens,
                )
                step.done = True
                yield step
                return

            # Has tool calls -> yield buffered thinking as reasoning, then tool calls
            logger.info(
                "agent_loop_tool_calls",
                session_id=str(session_id),
                iteration=step_num,
                tool_count=len(step.tool_calls),
                tools=[tc.name for tc in step.tool_calls],
                thinking_length=len(step.thinking),
            )
            if step.thinking:
                yield f"reasoning:{step.thinking}"

            for tc in step.tool_calls:
                yield f"tool_call:{tc.id}:{tc.name}:{json.dumps(tc.arguments, ensure_ascii=False)}"

            # Append assistant message with tool calls (required by both OpenAI and Anthropic)
            messages.append(
                LLMMessage(
                    role="assistant",
                    content=step.thinking if step.thinking else None,
                    tool_calls=step.tool_calls,
                )
            )

            # Execute tools: delegation calls run concurrently with regular
            # calls, but results are yielded in the ORIGINAL LLM order so
            # the frontend renders cards in the correct sequence.
            delegation_calls = [tc for tc in step.tool_calls if tc.name == "delegate_task"]
            regular_calls = [tc for tc in step.tool_calls if tc.name != "delegate_task"]

            # --- Start delegation tasks concurrently (background) ---
            # These run while regular tools execute, so by the time we need
            # delegation results they're usually already done.
            delegation_futures: dict[str, asyncio.Task] = {}  # tc.id -> Task
            for tc in delegation_calls:
                delegation_futures[tc.id] = asyncio.create_task(
                    self.tool_executor.execute(
                        tool_name=tc.name,
                        arguments=tc.arguments,
                        tool_call_id=tc.id,
                        user_id=str(ctx.user_id),
                        session_id=str(ctx.session_id),
                        delegation=ctx.delegation,
                        event_queue=self.event_queue,
                        workspace_id=str(ctx.workspace_id) if ctx.workspace_id else None,
                        workspace_slug=ctx.workspace_slug,
                        allowed_tools=self.allowed_tools,
                    )
                )

            if delegation_calls:
                logger.info(
                    "agent_loop_delegation_start",
                    session_id=str(session_id),
                    iteration=step_num,
                    delegation_count=len(delegation_calls),
                )

            # --- Execute regular tools sequentially ---
            for tc in regular_calls:
                # ============================================================
                # Tool permission check (enforces allowed_tools whitelist)
                # ============================================================
                if self.allowed_tools is not None and tc.name not in self.allowed_tools:
                    logger.warning(
                        "tool_permission_denied",
                        tool_name=tc.name,
                        allowed_tools=sorted(self.allowed_tools),
                    )
                    result = ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                        output="",
                        success=False,
                        error=f"Permission denied: tool '{tc.name}' is not available to this agent.",
                    )
                    step.tool_results.append(result)
                    yield f"tool_result:{tc.id}:{tc.name}:err:{json.dumps(result.error, ensure_ascii=False)}"
                    continue

                # ============================================================
                # AskUserQuestion: 特殊处理，避免死锁
                # ============================================================
                if tc.name == "AskUserQuestion":
                    ask_t_start = time.monotonic()
                    async for evt in self._run_ask_user_flow(tc, ctx):
                        yield evt

                    ask_output = self._last_ask_user_output or ""
                    result = ToolResult(
                        tool_call_id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                        output=ask_output,
                        success=True,
                        duration_ms=(time.monotonic() - ask_t_start) * 1000,
                    )
                    step.tool_results.append(result)
                    get_recorder().record_tool_call(
                        tool_name="AskUserQuestion",
                        exec_type="direct",
                        duration_ms=result.duration_ms,
                        is_error=ask_output.startswith("Error:"),
                        user_id=str(ctx.user_id),
                        session_id=str(ctx.session_id),
                    )

                    output_preview = ask_output[:500]
                    yield f"tool_result:{tc.id}:{tc.name}:ok:{json.dumps(output_preview, ensure_ascii=False)}"
                    continue

                logger.debug(
                    "agent_loop_execute_tool",
                    session_id=str(session_id),
                    iteration=step_num,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                )
                result = await self._execute_tool_traced(tc, ctx)
                step.tool_results.append(result)

                status = "ok" if result.success else "err"
                output_preview = (result.output if result.success else result.error or "")[:10000]
                logger.debug(
                    "agent_loop_tool_result",
                    session_id=str(session_id),
                    iteration=step_num,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    status=status,
                    duration_ms=round(result.duration_ms, 2),
                    output_length=len(result.output) if result.output else 0,
                )
                yield f"tool_result:{tc.id}:{tc.name}:{status}:{json.dumps(output_preview, ensure_ascii=False)}"

                # Yield control to event loop so delegation handler and
                # event_queue drain can run between tool executions.
                await asyncio.sleep(0)

            # --- Collect delegation results ---
            # Delegation tasks were started early and have been running in
            # the background.  Wait for them and yield results.
            if delegation_futures:
                # Wait for all delegation tasks with heartbeat so the SSE
                # generator can drain event_queue (child agent events).
                gather_task = asyncio.ensure_future(
                    asyncio.gather(
                        *delegation_futures.values(),
                        return_exceptions=True,
                    )
                )
                while not gather_task.done():
                    await asyncio.sleep(0.3)
                    if not gather_task.done():
                        yield "delegation_heartbeat:drain"

                results_list = gather_task.result()

                for i, tc in enumerate(delegation_calls):
                    result = results_list[i]
                    if isinstance(result, BaseException):
                        result = ToolResult(
                            tool_call_id=tc.id,
                            name=tc.name,
                            arguments=tc.arguments,
                            output="",
                            success=False,
                            error=str(result),
                        )
                    step.tool_results.append(result)

                    status = "ok" if result.success else "err"
                    output_preview = (result.output if result.success else result.error or "")[:500]
                    logger.info(
                        "agent_loop_delegation_result",
                        session_id=str(session_id),
                        iteration=step_num,
                        tool_call_id=tc.id,
                        status=status,
                        duration_ms=round(result.duration_ms, 2),
                    )
                    yield f"tool_result:{tc.id}:{tc.name}:{status}:{json.dumps(output_preview, ensure_ascii=False)}"

            # Inject tool results as messages
            for tr in step.tool_results:
                role_content = tr.output if tr.success else f"Error: {tr.error}"
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=role_content,
                        tool_call_id=tr.tool_call_id,
                    )
                )

            logger.debug(
                "agent_loop_iteration_complete",
                session_id=str(session_id),
                iteration=step_num,
                tool_results_count=len(step.tool_results),
            )

            # Continue to next iteration (LLM will see tool results and decide next action)

        # Max iterations reached
        logger.warning(
            "agent_loop_max_iterations",
            session_id=str(session_id),
            max_iterations=self.max_iterations,
        )
        self._finalize_trace(
            trace_id, t_start, session_id, user_id, tenant_id,
            status="timeout",
            iteration_count=self.max_iterations,
            tool_call_count=tool_call_count,
            total_tokens=total_tokens,
            hit_max_iterations=True,
        )
        yield AgentStep(
            step=self.max_iterations,
            final_output="智能体已达到最大迭代次数，仍未完成任务。",
            done=True,
        )

    # ------------------------------------------------------------------
    # Observation finalize
    # ------------------------------------------------------------------

    def _finalize_trace(
        self,
        trace_id: UUID,
        t_start: float,
        session_id: UUID,
        user_id: UUID,
        tenant_id: UUID | None,
        *,
        status: str,
        iteration_count: int = 0,
        tool_call_count: int = 0,
        total_tokens: int = 0,
        hit_max_iterations: bool = False,
    ) -> None:
        """Write the trace summary row (fire-and-forget) and clear obs context."""
        try:
            get_recorder().record_trace(
                trace_id=trace_id,
                session_id=session_id,
                user_id=user_id,
                tenant_id=tenant_id,
                status=status,
                iteration_count=iteration_count,
                tool_call_count=tool_call_count,
                total_tokens=total_tokens,
                duration_ms=int((time.monotonic() - t_start) * 1000),
                hit_max_iterations=hit_max_iterations,
            )
        except Exception:
            logger.exception("agent_trace_record_failed", session_id=str(session_id))
        finally:
            set_obs_context(None)

    def finalize_trace_error(self) -> None:
        """Called by callers on abnormal termination to record an errored trace."""
        ctx = get_obs_context()
        if ctx is None:
            return
        get_recorder().record_trace(
            trace_id=ctx.trace_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            status="error",
            duration_ms=0,
        )
        set_obs_context(None)

    # ------------------------------------------------------------------
    # Tool execution with tracing
    # ------------------------------------------------------------------

    async def _execute_tool_traced(
        self,
        tc: ToolCall,
        ctx: ToolContext,
    ):
        """Execute a tool with Langfuse tracing."""
        parent = get_current_observation()
        lf_client = get_langfuse_client()

        tool_obs = None
        if parent and lf_client:
            tool_obs = parent.start_observation(
                name=tc.name,
                as_type="tool",
                input={
                    "tool_call_id": tc.id,
                    "arguments": tc.arguments,
                },
            )

        try:
            result = await self.tool_executor.execute(
                tool_name=tc.name,
                arguments=tc.arguments,
                tool_call_id=tc.id,
                user_id=str(ctx.user_id),
                session_id=str(ctx.session_id),
                delegation=ctx.delegation,
                event_queue=self.event_queue,
                workspace_id=str(ctx.workspace_id) if ctx.workspace_id else None,
                workspace_slug=ctx.workspace_slug,
                allowed_tools=self.allowed_tools,
            )
        except Exception as e:
            if tool_obs:
                tool_obs.update(level="ERROR", status_message=str(e))
                tool_obs.end()
            raise

        if tool_obs:
            status = "success" if result.success else "error"
            output = (result.output if result.success else result.error or "")[:10000]
            tool_obs.update(
                output=output,
                metadata={
                    "status": status,
                    "duration_ms": result.duration_ms,
                    "tool_call_id": tc.id,
                },
                level="ERROR" if not result.success else "DEFAULT",
                status_message=result.error if not result.success else None,
            )
            tool_obs.end()

        return result

    # ------------------------------------------------------------------
    # AskUserQuestion handling
    # ------------------------------------------------------------------

    async def _run_ask_user_flow(
        self,
        tc: ToolCall,
        ctx: ToolContext,
    ) -> AsyncIterator[str]:
        """
        处理 AskUserQuestion 的完整确认流程（async generator）。

        关键设计：通过 yield 让控制权返回 SSE generator，使其有机会
        drain event_queue，将 confirmation_required 事件发送给前端。

        流程：
        1. 创建确认请求 → 推入 event_queue
        2. yield（让 SSE generator drain → 前端收到确认卡片）
        3. 阻塞等待用户响应（前端 REST API → confirmation_manager.resolve）
        4. 推送 confirmation_resolved → event_queue
        5. 保存结果到 self._last_ask_user_output
        """
        from datetime import datetime

        args = tc.arguments or {}
        question = (args.get("question", "") or "").strip()
        mode = args.get("mode", "single_select")
        raw_options = args.get("options", [])
        # Normalize: LLM may pass strings or {id, label} dicts
        options: list[dict] = []
        for i, opt in enumerate(raw_options):
            if isinstance(opt, str):
                options.append({"id": str(i), "label": opt})
            elif isinstance(opt, dict):
                if "id" not in opt:
                    opt["id"] = opt.get("label", str(i))
                options.append(opt)
        context = args.get("context", {})
        table_schema = args.get("table_schema")
        t_start = time.monotonic()

        # Guard: don't show an empty confirmation — return error directly to LLM
        if not question:
            logger.warning(
                "ask_user_empty_question",
                session_id=str(ctx.session_id),
                args_keys=list(args.keys()),
            )
            self._last_ask_user_output = (
                "Error: AskUserQuestion requires a non-empty question. "
                "Please call again with a specific question for the user."
            )
            return

        # ---- Step 1: 创建确认请求 ----
        confirmation = confirmation_manager.create_confirmation(
            session_id=str(ctx.session_id),
            user_id=str(ctx.user_id),
            question=question,
            mode=mode,
            options=options,
            context=context,
            table_schema=table_schema,
        )

        logger.info(
            "ask_user_created",
            session_id=str(ctx.session_id),
            confirmation_id=confirmation.id,
            mode=mode,
            options_count=len(options),
            risk_level=context.get("risk_level", "low"),
        )

        # ---- Step 2: 推入 event_queue + yield 让 SSE drain ----
        if self.event_queue:
            await self.event_queue.put({
                "type": "confirmation_required",
                "confirmation_id": confirmation.id,
                "question": question,
                "mode": mode,
                "options": options,
                "table_schema": table_schema,
                "context": {
                    **context,
                    "timeout_seconds": confirmation.timeout_seconds,
                },
                "created_at": confirmation.created_at.isoformat(),
            })

        # 这个 yield 是关键！
        # 它让控制权从 _run_ask_user_flow → agent_loop.run() → SSE generator
        # SSE generator 收到这个事件后，会先 drain event_queue（发送 confirmation_required）
        # 然后再调用 agent_loop 的 __anext__() 恢复执行
        yield f"confirmation_flow:{tc.id}:waiting"

        # ---- Step 3: 阻塞等待用户响应 ----
        # 此时 SSE generator 已经 drain 了 event_queue，前端已收到确认卡片
        logger.info(
            "ask_user_waiting",
            session_id=str(ctx.session_id),
            confirmation_id=confirmation.id,
            timeout_seconds=confirmation.timeout_seconds,
        )

        confirmation = await confirmation_manager.wait_for_response(confirmation.id)

        if not confirmation:
            self._last_ask_user_output = "Error: confirmation not found"
            logger.error("ask_user_not_found", confirmation_id=tc.id)
            return

        response = confirmation.response or {}
        status = response.get("status", "timeout")
        elapsed_ms = round((time.monotonic() - t_start) * 1000, 2)

        logger.info(
            "ask_user_resolved",
            session_id=str(ctx.session_id),
            confirmation_id=confirmation.id,
            status=status,
            elapsed_ms=elapsed_ms,
        )

        # ---- Step 4: 推送 confirmation_resolved ----
        if self.event_queue:
            await self.event_queue.put({
                "type": "confirmation_resolved",
                "confirmation_id": confirmation.id,
                "status": status,
                "selected_options": response.get("selected_options", []),
                "user_input": response.get("user_input"),
                "table_data": response.get("table_data"),
                "resolved_at": datetime.now(UTC).isoformat(),
            })

        # ---- Step 5: 格式化输出，保存供调用方使用 ----
        self._last_ask_user_output = _format_ask_user_output(
            response, mode, options, table_schema
        )
