"""Explicit notification decisions and executor-owned delivery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from aio_agent_platform.channels.cron_notify import (
    CronNotifyContext,
    current_cron_notify_ctx,
    handle_cron_respond,
    handle_notify_channel,
)
from aio_agent_platform.core import chat
from aio_agent_platform.core.agent import AgentStep
from aio_agent_platform.core.chat import filter_tools_by_agent as real_filter_tools
from aio_agent_platform.core.execution_scope import execution_tenant_id
from aio_agent_platform.cron_jobs.executor import execute_cron_job
from aio_agent_platform.interface.routes.cron_jobs import CronJobRunOut


@pytest.fixture
def execution(monkeypatch):
    job = SimpleNamespace(id=uuid4(), user_id=uuid4(), tenant_id=uuid4(), agent_id=uuid4(),
                          model_id=None, channel_id=uuid4(), name="Monitor", message="仅触发信号时通知")
    run = SimpleNamespace(id=uuid4(), job_id=job.id, user_id=job.user_id, status="running",
                          delivery_status="pending", delivery_reason=None, delivery_error=None,
                          session_id=None, started_at=None, finished_at=None, duration_ms=None,
                          output=None, error=None)
    user = SimpleNamespace(id=job.user_id, is_active=True)
    agent = SimpleNamespace(model_id=None, temperature=None, max_iterations=10, enable_retry=True)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user)),
        scalar=AsyncMock(return_value=job.tenant_id), get=AsyncMock(return_value=run),
        flush=AsyncMock(), commit=AsyncMock(),
    )

    def add(obj):
        if obj.id is None:
            obj.id = uuid4()

    db.add = add
    d = SimpleNamespace(job=job, run=run, db=db, output="完成检查", action=None,
                        error=None, pushed=AsyncMock(return_value=True), contexts=[], options=None)

    class Loop:
        provider = object()

        async def run(self, **kwargs):
            ctx = current_cron_notify_ctx.get()
            d.contexts.append(ctx)
            assert ctx.user_id == str(job.user_id)
            assert ctx.session_id == str(kwargs["session_id"])
            names = [schema["function"]["name"] for schema in kwargs["tools"]]
            assert "cron_respond" in names
            assert "notify_channel" not in names
            if d.action:
                await d.action(ctx)
            d.pushed.assert_not_awaited()
            if d.output:
                yield f"text_delta:{d.output}"
            if d.error:
                raise d.error
            yield AgentStep(step=1, done=True, final_output=d.output)

    async def build_loop(*args, **kwargs):
        d.options = kwargs
        d.system_prompt = args[1]
        return Loop()

    monkeypatch.setattr(chat, "load_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(chat, "refresh_mcp_tools_for_agent", AsyncMock())
    monkeypatch.setattr(chat, "filter_tools_by_agent", lambda *a, **kw: ([], []))
    monkeypatch.setattr(chat, "build_system_prompt_with_memories", AsyncMock(return_value="task"))
    monkeypatch.setattr(chat, "build_agent_loop", build_loop)
    monkeypatch.setattr("aio_agent_platform.core.context.prepare_context",
                        AsyncMock(return_value=([], None)))
    return d


async def execute(d):
    await execute_cron_job(d.job, d.db, d.run.id, tool_executor=object(), push_to_channel=d.pushed)
    assert current_cron_notify_ctx.get() is None
    assert execution_tenant_id(d.job.user_id) is None
    assert all(not ctx.active for ctx in d.contexts)


async def respond(ctx, **kwargs):
    result = await handle_cron_respond(kwargs, ctx.user_id, ctx.session_id)
    assert not result.startswith("Error:"), result


async def test_final_reply_delivered_without_tool_call(execution):
    d = execution
    await execute(d)
    d.pushed.assert_awaited_once_with(d.db, d.job, "完成检查")
    assert d.run.status == "success"
    assert d.run.delivery_status == "sent"
    assert d.options["allowed_tools"] == {"cron_respond", "notify_channel"}


async def test_structured_silence_preserves_summary_and_reason(execution):
    d = execution
    d.output = "未触发，继续静默观察"

    async def action(ctx):
        await respond(ctx, notify=False, summary="MA60 未突破，量比不足", reason="两个条件均未满足")

    d.action = action
    await execute(d)
    d.pushed.assert_not_awaited()
    assert d.run.output == "MA60 未突破，量比不足"
    assert d.run.status == "success"
    assert d.run.delivery_status == "suppressed"
    data = CronJobRunOut.model_validate(d.run).model_dump()
    assert data["delivery_status"] == "suppressed"
    assert data["delivery_reason"] == "两个条件均未满足"


async def test_separate_notification_text_needs_no_final_text(execution):
    d = execution
    d.output = ""

    async def action(ctx):
        await respond(ctx, notify=True, summary="后台完整检查报告", notification_text="已触发，请关注")

    d.action = action
    await execute(d)
    d.pushed.assert_awaited_once_with(d.db, d.job, "已触发，请关注")
    assert d.run.output == "后台完整检查报告"
    assert d.run.status == "success"


@pytest.mark.parametrize("text,silent", [
    ("NO_REPLY", True), (" \nNO_REPLY\n ", True), ("no_reply", False),
    ("NO_REPLY.", False), ("报告\nNO_REPLY", False), ("NO_REPLY\n报告", False),
    ('"NO_REPLY"', False), ("未发送邮件，保持静默", False),
])
async def test_only_exact_final_control_token_suppresses(execution, text, silent):
    d = execution
    d.output = text
    await execute(d)
    assert d.run.status == "success"
    assert d.run.delivery_status == ("suppressed" if silent else "sent")
    assert d.pushed.await_count == (0 if silent else 1)


async def test_unbound_job_keeps_summary_without_delivery(execution):
    d = execution
    d.job.channel_id = None
    await execute(d)
    d.pushed.assert_not_awaited()
    assert d.run.output == "完成检查"
    assert d.run.delivery_status == "not_requested"
    assert d.run.delivery_reason == "no_channel"


@pytest.mark.parametrize("outcome,expected", [(False, "failed"), (RuntimeError("timeout"), "unknown")])
async def test_delivery_failure_does_not_change_execution_success(execution, outcome, expected):
    d = execution
    if isinstance(outcome, Exception):
        d.pushed.side_effect = outcome
    else:
        d.pushed.return_value = outcome
    await execute(d)
    assert d.run.status == "success"
    assert d.run.delivery_status == expected
    assert d.run.delivery_error
    assert d.run.error is None
    d.pushed.assert_awaited_once()


@pytest.mark.parametrize("error", [RuntimeError("generation failed"), asyncio.CancelledError()])
async def test_failed_or_cancelled_run_never_delivers_queued_result(execution, error):
    d = execution

    async def action(ctx):
        await respond(ctx, notify=True, summary="尚未完成的结果")

    d.action = action
    d.error = error
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await execute(d)
        assert current_cron_notify_ctx.get() is None
        assert execution_tenant_id(d.job.user_id) is None
        assert not d.contexts[0].active
    else:
        await execute(d)
    d.pushed.assert_not_awaited()
    assert d.run.status == "failed"
    assert d.run.delivery_status == "not_requested"
    assert d.run.delivery_reason == "execution_failed"


async def test_cancelled_send_records_unknown_without_retry(execution):
    d = execution
    d.pushed.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await execute(d)
    assert d.run.status == "success"
    assert d.run.delivery_status == "unknown"
    assert d.run.delivery_error
    assert current_cron_notify_ctx.get() is None
    d.pushed.assert_awaited_once()


async def test_empty_output_is_failure_not_silence(execution):
    d = execution
    d.output = "  "
    await execute(d)
    assert d.run.status == "failed"
    assert d.run.delivery_status == "not_requested"
    d.pushed.assert_not_awaited()


async def test_final_silence_overrides_deferred_legacy_request(execution):
    d = execution
    d.output = "NO_REPLY"

    async def action(ctx):
        result = await handle_notify_channel({"text": "旧提示词要求推送"}, ctx.user_id, ctx.session_id)
        assert "尚未发送" in result

    d.action = action
    await execute(d)
    assert d.run.delivery_status == "suppressed"
    d.pushed.assert_not_awaited()


async def test_structured_silence_overrides_legacy_request(execution):
    d = execution

    async def action(ctx):
        await respond(ctx, notify=False, summary="条件未满足", reason="未触发")
        await handle_notify_channel({"text": "旧通知请求"}, ctx.user_id, ctx.session_id)

    d.action = action
    await execute(d)
    assert d.run.delivery_status == "suppressed"
    assert d.run.output == "条件未满足"
    d.pushed.assert_not_awaited()


@pytest.mark.parametrize("args", [
    {"notify": "false", "summary": "report"},
    {"notify": False, "summary": "report"},
    {"notify": False, "summary": "", "reason": "none"},
    {"notify": True, "summary": "report", "notification_text": "NO_REPLY"},
])
async def test_invalid_decisions_do_not_silence_delivery(execution, args):
    d = execution

    async def action(ctx):
        assert (await handle_cron_respond(args, ctx.user_id, ctx.session_id)).startswith("Error:")
        assert ctx.response is None

    d.action = action
    await execute(d)
    assert d.run.delivery_status == "sent"


async def test_reject_foreign_and_expired_contexts(execution):
    d = execution
    pending = asyncio.Event()
    deferred = None

    async def action(ctx):
        nonlocal deferred
        args = {"notify": False, "summary": "report", "reason": "no change"}
        assert (await handle_cron_respond(args, "other-user", ctx.session_id)).startswith("Error:")
        assert (await handle_cron_respond(args, ctx.user_id, "other-session")).startswith("Error:")

        async def later():
            await pending.wait()
            return await handle_cron_respond(args, ctx.user_id, ctx.session_id)

        deferred = asyncio.create_task(later())

    d.action = action
    try:
        await execute(d)
    finally:
        pending.set()
    assert (await deferred).startswith("Error:")
    assert d.contexts[0].response is None


async def test_concurrent_notification_contexts_are_isolated():
    ready = [asyncio.Event(), asyncio.Event()]

    async def run(index):
        ctx = CronNotifyContext(job_id=str(index), user_id="same-owner", session_id=str(index))
        token = current_cron_notify_ctx.set(ctx)
        try:
            ready[index].set()
            await ready[1 - index].wait()
            await respond(ctx, notify=bool(index), summary=f"report-{index}", reason="no change")
            assert ctx.response.notify is bool(index)
            assert ctx.response.summary == f"report-{index}"
        finally:
            ctx.active = False
            current_cron_notify_ctx.reset(token)

    await asyncio.gather(run(0), run(1))
    assert current_cron_notify_ctx.get() is None


async def test_missing_configuration_finalizes_pending_delivery(execution):
    d = execution
    d.job.agent_id = None
    await execute(d)
    assert d.run.status == "failed"
    assert d.run.delivery_status == "not_requested"
    d.pushed.assert_not_awaited()


async def test_legacy_request_sends_once_at_end(execution):
    d = execution

    async def action(ctx):
        for text in ("初稿", "最终通知"):
            await handle_notify_channel({"text": text}, ctx.user_id, ctx.session_id)

    d.action = action
    await execute(d)
    d.pushed.assert_awaited_once_with(d.db, d.job, "最终通知")


async def test_structured_notification_takes_precedence_over_final_token(execution):
    d = execution
    d.output = "NO_REPLY"

    async def action(ctx):
        await respond(ctx, notify=True, summary="必须通知的结果")

    d.action = action
    await execute(d)
    d.pushed.assert_awaited_once_with(d.db, d.job, "必须通知的结果")


async def test_wait_for_timeout_cleans_execution_and_notification_context(execution):
    d = execution
    waiting = asyncio.Event()

    async def action(ctx):
        waiting.set()
        await asyncio.Event().wait()

    d.action = action
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(execute(d), timeout=0.05)
    assert waiting.is_set()
    assert current_cron_notify_ctx.get() is None
    assert execution_tenant_id(d.job.user_id) is None
    assert not d.contexts[0].active
    assert d.run.status == "failed"
    d.pushed.assert_not_awaited()


@pytest.mark.postgres
async def test_delivery_migration_preserves_unknown_history(db_session, engine):
    import runpy
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration = runpy.run_path(str(Path(__file__).resolve().parents[1]
                                  / "alembic/versions/a3c4d5e6f7b8_cron_delivery_outcomes.py"))
    ids = {name: uuid4() for name in ("id", "job_id", "tenant_id", "user_id")}

    def verify(conn):
        with Operations.context(MigrationContext.configure(conn)):
            migration["downgrade"]()
            conn.execute(text(
                "INSERT INTO cron_job_runs (id, job_id, tenant_id, user_id, status, output, started_at) "
                "VALUES (:id, :job_id, :tenant_id, :user_id, 'success', 'historical report', now())"
            ), ids)
            migration["upgrade"]()
            migration["upgrade"]()  # startup/bootstrap and Alembic may both add columns
        row = conn.execute(text("SELECT * FROM cron_job_runs WHERE id=:id"), ids).mappings().one()
        assert row["status"] == "success"
        assert row["output"] == "historical report"
        assert row["delivery_status"] == "unknown"
        assert row["delivery_reason"] is None
        assert row["tenant_id"] == ids["tenant_id"]

    async with engine.begin() as conn:
        await conn.run_sync(verify)


@pytest.mark.parametrize("notify", [False, True])
async def test_real_agent_loop_and_tool_executor_report_decision(execution, monkeypatch, notify):
    import json
    from unittest.mock import Mock

    from aio_agent_platform.core import agent as agent_module
    from aio_agent_platform.llm import LLMChunk, ToolCall
    from aio_agent_platform.tools import executor as executor_module
    from aio_agent_platform.tools.registry import ToolRegistry

    d = execution
    calls = 0
    tool_executor = executor_module.ToolExecutor(ToolRegistry(), Mock())
    tool_executor.register_direct_handler("cron_respond", handle_cron_respond)
    tool_executor.register_direct_handler("notify_channel", handle_notify_channel)
    from aio_agent_platform.tools.builtin import register_builtin_tools
    register_builtin_tools(tool_executor.registry)

    # Keep real Tool objects and schemas: an empty mocked filter cannot catch
    # mixing Tool instances with tool names when constructing a runtime allow-list.
    monkeypatch.setattr(chat, "filter_tools_by_agent", lambda *a, **kw: real_filter_tools(
        tool_executor, None, extra_blacklist=kw.get("extra_blacklist"),
    ))
    for module in (agent_module, executor_module):
        monkeypatch.setattr(module, "get_recorder", Mock(return_value=Mock()))
        monkeypatch.setattr(module, "get_hook_manager", Mock(return_value=Mock()))

    async def stream(messages, tools=None):
        nonlocal calls
        calls += 1
        assert execution_tenant_id(d.job.user_id) == d.job.tenant_id
        d.contexts.append(current_cron_notify_ctx.get())
        if calls == 1:
            args = {"notify": notify, "summary": "真实循环的检查报告", "reason": "未触发"}
            yield LLMChunk(type="tool_call_start",
                           tool_call=ToolCall(id="report", name="cron_respond", arguments={}),
                           argument_delta=json.dumps(args, ensure_ascii=False))
        else:
            yield LLMChunk(type="text_delta", content="已完成")
        yield LLMChunk(type="done")

    async def build_loop(*args, **kwargs):
        return agent_module.AgentLoop(
            provider=SimpleNamespace(model="test-model", stream=stream),
            tool_executor=tool_executor, system_prompt=args[1],
            execution_tenant_id=kwargs["tenant_id"], allowed_tools=kwargs["allowed_tools"],
        )

    monkeypatch.setattr(chat, "build_agent_loop", build_loop)
    await execute(d)
    assert calls == 2
    assert d.run.output == "真实循环的检查报告"
    assert d.run.delivery_status == ("sent" if notify else "suppressed")
    assert d.pushed.await_count == int(notify)


@pytest.mark.parametrize("custom_prompt", [None, "你是监控智能体。旧约定：执行后调用 notify_channel。"])
@pytest.mark.parametrize("bound_channel", [False, True])
async def test_cron_contract_follows_default_or_custom_prompt(execution, monkeypatch, custom_prompt, bound_channel):
    from aio_agent_platform.core.prompt import build_cron_execution_prompt, build_system_prompt

    d = execution
    if not bound_channel:
        d.job.channel_id = None
    base = build_system_prompt(agent_prompt=custom_prompt)
    # Normal chat keeps its own prompt. Only the cron entry appends the runtime contract.
    assert "## 定时任务运行协议" not in base
    monkeypatch.setattr(chat, "build_system_prompt_with_memories", AsyncMock(return_value=base))
    await execute(d)
    contract = build_cron_execution_prompt(has_notify_channel=bound_channel)
    assert d.system_prompt == base + "\n\n" + contract
    assert d.system_prompt.count("## 定时任务运行协议") == 1
    if bound_channel:
        assert "当前任务已绑定 IM 渠道" in contract
        assert "当前任务未绑定 IM 渠道" not in contract
    else:
        assert "当前任务未绑定 IM 渠道" in contract
        assert "当前任务已绑定 IM 渠道" not in contract


@pytest.mark.parametrize("notify", [False, True])
async def test_prompt_examples_execute_the_documented_delivery_decision(execution, notify):
    import json

    from aio_agent_platform.core.prompt import build_cron_execution_prompt

    d = execution
    examples = [json.loads(line) for line in build_cron_execution_prompt(
        has_notify_channel=True,
    ).splitlines() if line.startswith('{"notify":')]
    example = next(item for item in examples if item["notify"] is notify)

    async def action(ctx):
        await respond(ctx, **example)

    d.action = action
    await execute(d)
    assert d.run.output == example["summary"]
    assert d.run.delivery_status == ("sent" if notify else "suppressed")
    if notify:
        d.pushed.assert_awaited_once_with(d.db, d.job, example["notification_text"])
    else:
        d.pushed.assert_not_awaited()
        assert d.run.delivery_reason == example["reason"]


@pytest.mark.parametrize("override", [False, True])
async def test_cron_passes_task_model_or_agent_model(execution, monkeypatch, override):
    d = execution
    agent = await chat.load_agent()
    agent.model_id = uuid4()
    d.job.model_id = uuid4() if override else None
    validate = AsyncMock()
    monkeypatch.setattr("aio_agent_platform.cron_jobs.executor.validate_job_model", validate)
    await execute(d)
    assert d.options["agent_model_id"] == (d.job.model_id or agent.model_id)
    assert d.options["tenant_id"] == d.job.tenant_id
    if override:
        validate.assert_awaited_once_with(d.db, d.job.tenant_id, d.job.model_id)
    else:
        validate.assert_not_awaited()
    assert d.run.status == "success"


async def test_cron_unavailable_override_fails_without_fallback(execution, monkeypatch):
    d = execution
    d.job.model_id = uuid4()
    monkeypatch.setattr("aio_agent_platform.cron_jobs.executor.validate_job_model",
                        AsyncMock(side_effect=ValueError("指定的执行模型已停用")))
    await execute(d)
    assert d.run.status == "failed"
    assert d.run.error == "指定的执行模型已停用"
    assert d.run.session_id is None
    assert d.options is None
    d.pushed.assert_not_awaited()
