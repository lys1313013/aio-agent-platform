"""Execute scheduled agents in the tenant captured by the job."""

from aio_agent_platform.core.execution_scope import bind_execution_scope


async def execute_cron_job(job, db, run_id, *, tool_executor, push_to_channel):
    """Execute a cron job by running its agent with the configured message."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from aio_agent_platform.core.chat import (
        build_agent_loop,
        build_system_prompt_with_memories,
        filter_tools_by_agent,
        load_agent,
        refresh_mcp_tools_for_agent,
    )
    from aio_agent_platform.core.chat_history import ChatTurnRecorder
    from aio_agent_platform.db.models import CronJobRun, Message, Tenant, TenantMembership, User
    from aio_agent_platform.db.models import Session as ChatSession

    started_at = datetime.now(UTC)

    async def _finalize(
        status: str,
        *,
        output: str | None = None,
        error: str | None = None,
        session_id=None,
    ) -> None:
        """Write the final status/result into the run record (always called)."""
        run = await db.get(CronJobRun, run_id)
        if run:
            run.finished_at = datetime.now(UTC)
            run.duration_ms = int(
                (run.finished_at - started_at).total_seconds() * 1000
            )
            if session_id:
                run.session_id = session_id
            run.status = status
            if output:
                run.output = output
            if error:
                run.error = error
        await db.commit()

    if not job.agent_id or not job.message:
        await _finalize("failed", error="任务未配置 agent 或 message，无法执行")
        return

    user_result = await db.execute(select(User).where(User.id == job.user_id))
    job_user = user_result.scalar_one_or_none()
    if not job_user:
        import structlog
        structlog.get_logger().warning(
            "cron_job_user_not_found",
            job_id=str(job.id),
            user_id=str(job.user_id),
        )
        await _finalize("failed", error="任务所属用户不存在")
        return

    if not job_user.is_active:
        await _finalize("failed", error="任务所属用户已停用")
        return

    member_tenant = await db.scalar(
        select(Tenant.id)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(
            Tenant.id == job.tenant_id,
            Tenant.is_active,
            TenantMembership.user_id == job.user_id,
        )
    )
    if member_tenant is None:
        await _finalize("failed", error="任务所属租户已停用或用户已不属于该租户")
        return

    agent = await load_agent(db, job.agent_id, user=job_user, tenant_id=job.tenant_id)
    if not agent:
        import structlog
        structlog.get_logger().warning(
            "cron_job_agent_not_found",
            job_id=str(job.id),
            agent_id=str(job.agent_id),
        )
        await _finalize("failed", error="任务关联的智能体不存在、已停用或无权访问")
        return

    with bind_execution_scope(job.user_id, job.tenant_id, agent_id=job.agent_id):
        # Cron jobs have no delegation context; keep delegate_task out so a
        # tool that cannot run is never offered. Also blacklist tools that
        # require a browser frontend: AskUserQuestion would block forever
        # (wait_for_response has no backend timeout), and ui_* page actions
        # have no browser to execute in.
        from aio_agent_platform.tools.builtin import FRONTEND_TOOL_NAMES
        await refresh_mcp_tools_for_agent(tool_executor, agent)
        tools_list, tools_schema = filter_tools_by_agent(
            tool_executor, agent,
            extra_blacklist={"delegate_task", "AskUserQuestion", *FRONTEND_TOOL_NAMES},
        )

        # 配置了渠道时，注入 notify_channel 工具让 agent 执行后推送结果（默认通知）
        if job.channel_id:
            from aio_agent_platform.channels.cron_notify import (
                NOTIFY_CHANNEL_TOOL_NAME,
                NOTIFY_CHANNEL_TOOL_SCHEMA,
            )
            tools_list = [*tools_list, NOTIFY_CHANNEL_TOOL_NAME]
            tools_schema = [*tools_schema, NOTIFY_CHANNEL_TOOL_SCHEMA]

        system_prompt = await build_system_prompt_with_memories(
            db, job.user_id, job.message, tools_list, agent=agent, tenant_id=job.tenant_id,
        )
        if job.channel_id:
            system_prompt += (
                "\n\n[通知规则] 执行结束后必须调用 notify_channel 工具把结果推送到用户的 IM 渠道。"
                "默认总是通知，不要在『一切正常』时选择不推。"
                "唯一例外：任务 message 中用户明确写明了不通知的条件（如『仅异常时通知』、"
                "『结果正常时不用推送』），才按用户说明的条件判断是否通知；没有写明就一律推送。"
            )
        loop = await build_agent_loop(
            tool_executor, system_prompt, db,
            agent_model_id=agent.model_id,
            agent_temperature=agent.temperature,
            agent_max_iterations=agent.max_iterations,
            agent_enable_retry=agent.enable_retry if agent.enable_retry is not None else True,
            tenant_id=job.tenant_id,
        )

        loop.execution_tenant_id = job.tenant_id
        loop.execution_agent_id = job.agent_id

        # Create a session for this execution
        new_session = ChatSession(
            user_id=job.user_id,
            agent_id=job.agent_id,
            title=job.name,
            source="cron",
        )
        db.add(new_session)
        await db.flush()
        run = await db.get(CronJobRun, run_id)
        if run:
            run.session_id = new_session.id
        db.add(Message(
            session_id=new_session.id, user_id=job.user_id,
            role="user", content=job.message,
        ))
        await db.commit()

        # Prepare context and run
        from aio_agent_platform.core.context import prepare_context

        prepared_messages, _ = await prepare_context(
            system_prompt=system_prompt,
            history=[],
            user_input=job.message,
            provider=loop.provider,
        )
        conversation_history = [
            m for m in prepared_messages
            if m.role != "system" and not (m.role == "user" and m.content == job.message)
        ]

        import structlog
        log = structlog.get_logger()

        error_msg = ""
        final_output = ""
        notify_token = None
        if job.channel_id:
            from aio_agent_platform.channels.cron_notify import (
                CronNotifyContext,
                current_cron_notify_ctx,
            )
            notify_ctx = CronNotifyContext(
                push_fn=lambda text: push_to_channel(db, job, text),
                job_id=str(job.id),
                channel_id=str(job.channel_id),
            )
            notify_token = current_cron_notify_ctx.set(notify_ctx)

        try:
            turn = ChatTurnRecorder(new_session.id, job.user_id)
            final_output = await turn.consume(
                loop.run(
                    user_input=job.message,
                    user_id=job.user_id,
                    session_id=new_session.id,
                    conversation_history=conversation_history,
                    tools=tools_schema,
                ),
                db,
            )
        except Exception as exc:
            error_msg = str(exc) or "agent loop failed"
            log.exception(
                "cron_job_agent_loop_failed",
                job_id=str(job.id),
            )
        finally:
            if notify_token is not None:
                current_cron_notify_ctx.reset(notify_token)

        await _finalize(
            "success" if final_output else "failed",
            output=final_output or None,
            error=None if final_output else (error_msg or "agent loop failed"),
            session_id=new_session.id,
        )

        if final_output:
            log.info(
                "cron_job_executed",
                job_id=str(job.id),
                session_id=str(new_session.id),
            )
