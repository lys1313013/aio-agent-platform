"""Execute scheduled agents in the tenant captured by the job."""

from aio_agent_platform.core.execution_scope import bind_execution_scope


async def execute_cron_job(job, db, run_id, *, tool_executor, push_to_channel):
    """Execute a cron job by running its agent with the configured message."""
    import asyncio
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
    from aio_agent_platform.core.prompt import build_cron_execution_prompt
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
            if status == "failed" and run.delivery_status == "pending":
                run.delivery_status = "not_requested"
                run.delivery_reason = "execution_failed"
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

        from aio_agent_platform.channels.cron_notify import (
            CRON_RESPOND_TOOL_NAME,
            CRON_RESPOND_TOOL_SCHEMA,
            NOTIFY_CHANNEL_TOOL_NAME,
            SILENT_REPLY_TOKEN,
            CronNotifyContext,
            current_cron_notify_ctx,
        )
        from aio_agent_platform.tools.registry import Tool

        response_function = CRON_RESPOND_TOOL_SCHEMA["function"]
        tools_list = [*tools_list, Tool(
            name=CRON_RESPOND_TOOL_NAME, description=response_function["description"],
            parameters=response_function["parameters"], requires_sandbox=False,
        )]
        tools_schema = [*tools_schema, CRON_RESPOND_TOOL_SCHEMA]

        system_prompt = await build_system_prompt_with_memories(
            db, job.user_id, job.message, tools_list, agent=agent, tenant_id=job.tenant_id,
        )
        system_prompt += "\n\n" + build_cron_execution_prompt(
            has_notify_channel=bool(job.channel_id),
        )
        loop = await build_agent_loop(
            tool_executor, system_prompt, db,
            agent_model_id=agent.model_id,
            agent_temperature=agent.temperature,
            agent_max_iterations=agent.max_iterations,
            agent_enable_retry=agent.enable_retry if agent.enable_retry is not None else True,
            tenant_id=job.tenant_id,
            allowed_tools={schema["function"]["name"] for schema in tools_schema}
            | {NOTIFY_CHANNEL_TOOL_NAME},
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
        notify_ctx = CronNotifyContext(
            job_id=str(job.id), user_id=str(job.user_id), session_id=str(new_session.id),
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
        except asyncio.CancelledError:
            await _finalize("failed", error="任务执行已取消", session_id=new_session.id)
            raise
        except Exception as exc:
            final_output = turn.content
            error_msg = str(exc) or "agent loop failed"
            log.exception(
                "cron_job_agent_loop_failed",
                job_id=str(job.id),
            )
        finally:
            # Invalidate inherited child-task contexts as well as resetting this task.
            notify_ctx.active = False
            current_cron_notify_ctx.reset(notify_token)

        response = notify_ctx.response
        output = response.summary if response else (final_output or notify_ctx.legacy_text or "")
        succeeded = bool(output.strip()) and not error_msg
        # Execution is complete independently of delivery. Save the report before
        # any outbound IO so an interrupted send cannot discard the result.
        await _finalize(
            "success" if succeeded else "failed",
            output=output or None,
            error=None if succeeded else (error_msg or "agent loop failed"),
            session_id=new_session.id,
        )
        run = await db.get(CronJobRun, run_id)
        if run:
            run.delivery_status = "not_requested"
            if not succeeded:
                run.delivery_reason = "execution_failed"
            elif not job.channel_id:
                run.delivery_reason = "no_channel"
            elif response is not None and not response.notify:
                run.delivery_status = "suppressed"
                run.delivery_reason = response.reason
            elif response is None and final_output.strip() == SILENT_REPLY_TOKEN:
                run.delivery_status = "suppressed"
                run.delivery_reason = "silent_token"
            else:
                # Persist before external IO; a crash must not invite a blind replay.
                run.delivery_status = "unknown"
                await db.commit()
                try:
                    sent = await push_to_channel(
                        db, job, (response.notification_text or response.summary)
                        if response else (notify_ctx.legacy_text or final_output),
                    )
                except asyncio.CancelledError:
                    run.delivery_error = "通知发送已取消，投递结果未知"
                    await db.commit()
                    raise
                except Exception as exc:
                    run.delivery_error = str(exc) or "渠道发送结果未知"
                    log.exception("cron_job_delivery_unknown", job_id=str(job.id))
                else:
                    run.delivery_status = "sent" if sent else "failed"
                    if not sent:
                        run.delivery_error = "渠道推送未成功，请检查渠道连接、启用状态和账号绑定"

        await db.commit()

        if succeeded:
            log.info(
                "cron_job_executed",
                job_id=str(job.id),
                session_id=str(new_session.id),
            )
