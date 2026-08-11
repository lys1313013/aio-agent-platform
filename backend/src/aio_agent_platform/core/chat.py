"""Chat orchestration helpers — reusable by routes, cron jobs, and channel adapters.

These functions assemble an AgentLoop: load the Agent row, filter tools by the
agent's allow-list, build the system prompt with memories, resolve the LLM model,
and construct the AgentLoop. Kept out of ``interface/routes/chat.py`` so cron
jobs and channel adapters can drive the same pipeline without importing from
a router module.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, with_loader_criteria

from aio_agent_platform.core.agent import AgentLoop, DelegationContext
from aio_agent_platform.core.config import settings
from aio_agent_platform.core.context import (
    ContextBudget,
    generate_summary,
)
from aio_agent_platform.core.context import (
    estimate_messages_tokens as _est_tokens,
)
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.db import Message, Session
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import (
    Agent,
    GraphKnowledgeBase,
    KnowledgeBase,
    LLMModel,
    User,
    UserConfig,
    UserProfile,
)
from aio_agent_platform.llm import LLMMessage, ToolCall, create_provider
from aio_agent_platform.memory.service import MemoryService
from aio_agent_platform.observation import get_langfuse_client
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.tools.executor import ToolExecutor

logger = structlog.get_logger()

# Background tasks set — keeps references alive until completion.
background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Agent loading
# ---------------------------------------------------------------------------


async def load_agent(
    db: AsyncSession,
    agent_id: UUID | None,
    user: User | None = None,
    tenant_id: UUID | None = None,
) -> Agent | None:
    """Load an Agent with eager-loaded relationships.

    Visibility is scoped by either ``user`` (tenant + creator) or ``tenant_id``
    alone (for system callers like cron jobs and channel adapters).
    """
    if not agent_id:
        return None

    effective_tenant = user.tenant_id if user else tenant_id
    if effective_tenant is None:
        return None

    stmt = (
        select(Agent)
        .options(
            selectinload(Agent.skills),
            selectinload(Agent.model),
            selectinload(Agent.children),
            selectinload(Agent.knowledge_bases),
            selectinload(Agent.graph_knowledge_bases),
        )
        .where(
            Agent.id == agent_id,
            Agent.is_active,
            Agent.tenant_id == effective_tenant,
        )
    )

    if user is not None:
        # User-scoped: apply knowledge-base visibility and agent ownership filter.
        stmt = stmt.options(
            with_loader_criteria(
                KnowledgeBase,
                (KnowledgeBase.tenant_id == user.tenant_id)
                & or_(
                    KnowledgeBase.visibility == "tenant",
                    KnowledgeBase.created_by == user.id,
                ),
                include_aliases=True,
            ),
            with_loader_criteria(
                GraphKnowledgeBase,
                (GraphKnowledgeBase.tenant_id == user.tenant_id)
                & or_(
                    GraphKnowledgeBase.visibility == "tenant",
                    GraphKnowledgeBase.created_by == user.id,
                ),
                include_aliases=True,
            ),
        ).where(
            or_(Agent.visibility == "tenant", Agent.created_by == user.id),
        )

    result = await db.execute(stmt)
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------


def filter_tools_by_agent(
    tool_executor: ToolExecutor,
    agent: Agent | None,
    delegation_depth: int = 0,
    extra_blacklist: set[str] | None = None,
) -> tuple[list, list]:
    """Filter tools based on agent's enabled_tools, delegation depth, and blacklist.

    Returns (tools_list, tools_schema).
    ``extra_blacklist`` lets callers (e.g. channel adapters) drop specific tools
    on top of the agent's own allow-list.
    """
    all_tools = tool_executor.registry.list_tools()
    blacklist = set(extra_blacklist or ())

    if agent and agent.enabled_tools:
        enabled_set = set(agent.enabled_tools)
        filtered = [
            t for t in all_tools
            if t.name in enabled_set
            and t.name not in {"knowledge_retrieval", "graph_retrieval", "delegate_task"}
            and t.name not in blacklist
        ]
    else:
        filtered = [
            t for t in all_tools
            if t.name not in {"knowledge_retrieval", "graph_retrieval", "delegate_task"}
            and t.name not in blacklist
        ]

    # Auto-inject knowledge_retrieval when agent has bound knowledge bases.
    has_knowledge = bool(agent and agent.knowledge_bases)
    if has_knowledge and "knowledge_retrieval" not in blacklist:
        kr_tool = next((t for t in all_tools if t.name == "knowledge_retrieval"), None)
        if kr_tool and kr_tool not in filtered:
            filtered.append(kr_tool)
        kb_names = [kb.name for kb in agent.knowledge_bases] if agent else []
        logger.info(
            "knowledge_retrieval_tool_injected",
            agent_id=str(agent.id) if agent else None,
            knowledge_bases=kb_names,
            kb_count=len(kb_names),
        )

    # Auto-inject graph_retrieval when agent has bound graph knowledge bases.
    has_graph_kb = bool(agent and agent.graph_knowledge_bases)
    if has_graph_kb and "graph_retrieval" not in blacklist:
        gr_tool = next((t for t in all_tools if t.name == "graph_retrieval"), None)
        if gr_tool and gr_tool not in filtered:
            filtered.append(gr_tool)
        gkb_names = [kb.name for kb in agent.graph_knowledge_bases] if agent else []
        logger.info(
            "graph_retrieval_tool_injected",
            agent_id=str(agent.id) if agent else None,
            graph_knowledge_bases=gkb_names,
            kb_count=len(gkb_names),
        )

    # Auto-inject delegate_task so the agent can delegate to existing children
    # OR dynamically spawn temp sub-agents, within the depth limit. Available to
    # every agent (not just ones with pre-associated children).
    max_depth = settings.agent.max_delegation_depth
    if (
        agent is not None
        and delegation_depth < max_depth
        and "delegate_task" not in blacklist
    ):
        dt_tool = next((t for t in all_tools if t.name == "delegate_task"), None)
        if dt_tool and dt_tool not in filtered:
            filtered.append(dt_tool)
        child_names = [c.name for c in agent.children] if agent and agent.children else []
        logger.info(
            "delegate_task_tool_injected",
            agent_id=str(agent.id) if agent else None,
            children=child_names,
            child_count=len(child_names),
            dynamic_spawn=True,
        )

    # Build OpenAI schema from filtered built-in tools.
    tools_schema = []
    for t in filtered:
        tools_schema.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        })

    # Add MCP tools, filtered by agent's mcp_server_ids and enabled_tools.
    mcp_manager = tool_executor.mcp_manager
    if mcp_manager:
        allowed_server_ids = None
        if agent and agent.mcp_server_ids is not None:
            try:
                allowed_server_ids = {str(sid) for sid in agent.mcp_server_ids}
            except Exception:
                allowed_server_ids = set()

        enabled_set = None
        if agent and agent.enabled_tools:
            enabled_set = set(agent.enabled_tools)

        for full_name, tool_info in mcp_manager.list_all_tools():
            server_id = mcp_manager._tool_to_server.get(full_name)
            if allowed_server_ids is not None and server_id is not None:
                if str(server_id) not in allowed_server_ids:
                    continue
            if enabled_set is not None and full_name not in enabled_set:
                continue
            if full_name in blacklist:
                continue
            tools_schema.append(tool_info.to_openai_tool(
                prefix=full_name[:len(full_name) - len(tool_info.name)]
            ))

    return filtered, tools_schema


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


async def get_memory_top_k(db: AsyncSession, user_id: UUID) -> int:
    """Get user's memory_top_k setting, falling back to global default."""
    result = await db.execute(
        select(UserConfig).where(UserConfig.user_id == user_id)
    )
    config = result.scalar_one_or_none()
    return config.memory_top_k if config else settings.agent.memory_top_k


async def build_system_prompt_with_memories(
    db: AsyncSession,
    user_id: UUID,
    user_message: str,
    tools_list: list,
    agent: Agent | None = None,
    workspace_files: list | None = None,
    tenant_id: UUID | None = None,
) -> str:
    """Build system prompt with L1/L2/L3 memories and relevant skills injected."""
    memory_top_k = await get_memory_top_k(db, user_id)
    memory_data = await MemoryService.get_memories_for_prompt(
        db, user_id, user_message, top_k=memory_top_k
    )

    if agent and agent.skills:
        matched_skills = agent.skills
    else:
        matched_skills = await SkillService.get_skills_for_prompt(
            db, user_id, user_message, top_k=3
        )

    # Resolve tenant_id if not provided
    if tenant_id is None:
        from aio_agent_platform.db.models import User
        result = await db.execute(select(User.tenant_id).where(User.id == user_id))
        tenant_id = result.scalar_one_or_none()

    result = await db.execute(
        select(UserProfile).where(
            UserProfile.user_id == user_id,
            UserProfile.tenant_id == tenant_id,
        )
    )
    user_profile = result.scalar_one_or_none()
    user_portrait = user_profile.personal_portrait if user_profile else None

    return build_system_prompt(
        tools=tools_list,
        persistent_memories=memory_data["l1_memories"],
        relevant_memories=memory_data["l2_memories"] + memory_data["l3_memories"],
        daily_memories=memory_data["daily_memories"],
        relevant_skills=matched_skills if matched_skills else None,
        agent_prompt=agent.system_prompt if agent else None,
        child_agents=agent.children if agent and agent.children else None,
        workspace_files=workspace_files,
        user_portrait=user_portrait,
    )


# ---------------------------------------------------------------------------
# AgentLoop construction
# ---------------------------------------------------------------------------


def resolve_provider_type(provider_type: str | None) -> str:
    name = (provider_type or "").lower()
    if "anthropic" in name or "claude" in name:
        return "anthropic"
    return "openai"


async def resolve_model(
    db: AsyncSession,
    tenant_id: UUID,
    model_id: UUID | None = None,
) -> LLMModel:
    """Resolve an LLMModel from a specific id, else the tenant default.

    Returns a model with its ``provider`` relationship loaded. Raises
    ``RuntimeError`` when no usable model exists (callers outside HTTP context
    shouldn't raise HTTPException).
    """
    model_to_use = None
    if model_id:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.id == model_id, LLMModel.is_active, LLMModel.tenant_id == tenant_id)
        )
        model_to_use = result.scalar_one_or_none()

    if not model_to_use:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_default, LLMModel.is_active, LLMModel.tenant_id == tenant_id)
            .limit(1)
        )
        model_to_use = result.scalar_one_or_none()

    if not model_to_use or not model_to_use.provider:
        raise RuntimeError("没有可用的模型，请在管理后台配置模型并为智能体绑定模型")
    return model_to_use


async def build_agent_loop(
    tool_executor: ToolExecutor,
    system_prompt: str,
    db: AsyncSession,
    tenant_id: UUID,
    agent_model_id: UUID | None = None,
    agent_temperature: float | None = None,
    agent_max_iterations: int | None = None,
    agent_enable_retry: bool = True,
    delegation: DelegationContext | None = None,
    event_queue: asyncio.Queue | None = None,
    workspace_id: UUID | None = None,
    workspace_slug: str | None = None,
    allowed_tools: set[str] | None = None,
) -> AgentLoop:
    """Create an AgentLoop using the specified or default model from DB.

    Raises HTTPException (or RuntimeError for non-HTTP callers) if no model is available.
    """
    model_to_use = await resolve_model(db, tenant_id, agent_model_id)

    provider = create_provider(
        provider=model_to_use.provider.provider_type,
        model=model_to_use.model_name,
        base_url=model_to_use.provider.base_url,
        api_key=model_to_use.provider.api_key_encrypted,
        temperature=agent_temperature if agent_temperature is not None else settings.llm.temperature,
        enable_retry=agent_enable_retry,
        langfuse_client=get_langfuse_client(),
    )
    logger.info(
        "使用模型",
        model=model_to_use.model_name,
        provider=model_to_use.provider.name,
    )

    return AgentLoop(
        provider=provider,
        tool_executor=tool_executor,
        system_prompt=system_prompt,
        max_iterations=agent_max_iterations if agent_max_iterations is not None else settings.agent.max_iterations,
        trust_level=settings.agent.trust_level,
        delegation=delegation,
        event_queue=event_queue,
        workspace_id=workspace_id,
        workspace_slug=workspace_slug,
        allowed_tools=allowed_tools,
    )


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------


async def resolve_workspace(
    db: AsyncSession,
    session: Session,
    user_id: UUID,
):
    """Resolve the workspace for a chat session.

    Returns (workspace_id, workspace_slug). Sandbox is user-bound: all sessions
    use the user's default workspace. Always uses the default workspace,
    overriding any previously assigned value.
    """
    from aio_agent_platform.workspaces.service import WorkspaceService

    workspace = await WorkspaceService.get_or_create_default(db=db, user_id=user_id)

    if session.workspace_id != workspace.id:
        session.workspace_id = workspace.id
        await db.flush()

    return workspace.id, workspace.slug


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------


async def load_conversation_history(
    db: AsyncSession,
    session_id: UUID,
    limit: int | None = None,
    provider_type: str = "openai",
    allow_images: bool = True,
) -> tuple[list[LLMMessage], str | None]:
    """Load recent messages from DB as LLMMessage list, plus session context_summary.

    Adaptive loading: if no explicit limit is given, uses the configured soft
    limit and further reduces it if the loaded messages exceed the history
    token budget. When ``allow_images`` is False, image attachments are skipped
    so non-vision models never receive image blocks.
    """
    from aio_agent_platform.llm import build_image_url_refs, build_user_content
    from aio_agent_platform.storage.chat_attachments import ChatAttachmentStorage

    soft_limit = limit or settings.agent.context_history_soft_limit

    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(soft_limit)
    )
    messages = list(reversed(result.scalars().all()))

    session_result = await db.execute(
        select(Session.context_summary).where(Session.id == session_id)
    )
    context_summary = session_result.scalar_one_or_none()

    llm_messages: list[LLMMessage] = []
    attachment_storage = ChatAttachmentStorage()
    for msg in messages:
        if msg.role in ("user", "assistant"):
            if msg.attachments and msg.role == "user":
                if allow_images:
                    content = build_user_content(
                        text=msg.content or "",
                        attachments=msg.attachments,
                        provider_type=provider_type,
                        to_data_uri=attachment_storage.to_data_uri,
                    )
                else:
                    refs = build_image_url_refs(msg.attachments)
                    text = msg.content or ""
                    content = f"{text}\n\n{refs}" if text and refs else (text or refs)
            else:
                content = msg.content or ""

            stored_tool_calls = msg.tool_calls
            if msg.role == "assistant" and stored_tool_calls and isinstance(stored_tool_calls, list):
                tc_objects: list[ToolCall] = []
                for tc in stored_tool_calls:
                    if isinstance(tc, dict) and "id" in tc and "name" in tc:
                        tc_objects.append(ToolCall(
                            id=tc["id"],
                            name=tc["name"],
                            arguments=tc.get("arguments", {}),
                        ))
                llm_messages.append(LLMMessage(
                    role="assistant",
                    content=content,
                    tool_calls=tc_objects if tc_objects else None,
                ))
                emitted_ids = {tc.id for tc in tc_objects}
                for tc in stored_tool_calls:
                    if (
                        isinstance(tc, dict)
                        and tc.get("id") in emitted_ids
                        and "result" in tc
                        and tc["result"]
                    ):
                        r = tc["result"]
                        r_str = json.dumps(r, ensure_ascii=False) if not isinstance(r, str) else r
                        llm_messages.append(LLMMessage(
                            role="tool",
                            content=r_str,
                            tool_call_id=tc.get("id", ""),
                        ))
            else:
                llm_messages.append(LLMMessage(role=msg.role, content=content))

    # Adaptive: reduce loaded history if it exceeds the token budget.
    budget = ContextBudget.from_settings()
    total = _est_tokens(llm_messages)
    if total > budget.history_budget and len(llm_messages) > 4:
        kept: list[LLMMessage] = []
        used = 0
        for m in reversed(llm_messages):
            mt = _est_tokens([m])
            if used + mt > budget.history_budget:
                break
            kept.append(m)
            used += mt
        kept.reverse()
        logger.info(
            "Adaptive history load: %d -> %d messages (~%d -> ~%d tokens, budget %d)",
            len(llm_messages), len(kept), total, used, budget.history_budget,
        )
        # The truncation keeps a contiguous tail window; the budget boundary may cut
        # at an assistant's tool_calls while its (newer) tool results survive, leaving
        # orphan role='tool' messages at the window head. LLM APIs reject those.
        llm_messages = drop_orphan_tool_messages(kept)

    return llm_messages, context_summary


def drop_orphan_tool_messages(messages: list[LLMMessage]) -> list[LLMMessage]:
    """Drop role='tool' messages whose tool_call_id has no preceding assistant tool_calls.

    Can arise after token-budget truncation (assistant cut at the boundary) or from
    stored tool_calls lacking id/name. LLM providers reject orphan tool messages.
    """
    cleaned: list[LLMMessage] = []
    pending_tool_ids: set[str] = set()
    for m in messages:
        if m.role == "assistant":
            pending_tool_ids = {tc.id for tc in m.tool_calls} if m.tool_calls else set()
            cleaned.append(m)
        elif m.role == "tool":
            if m.tool_call_id in pending_tool_ids:
                pending_tool_ids.discard(m.tool_call_id)
                cleaned.append(m)
        else:
            cleaned.append(m)
    dropped = len(messages) - len(cleaned)
    if dropped:
        logger.info("History cleanup: dropped %d orphan tool messages", dropped)
    return cleaned


# ---------------------------------------------------------------------------
# Fire-and-forget helpers
# ---------------------------------------------------------------------------


def fire_memory_extraction(
    user_id: UUID,
    session_id: UUID,
    history: list[LLMMessage],
    user_message: str,
    assistant_output: str,
    enable: bool = True,
) -> None:
    """Fire-and-forget memory extraction as a background task."""
    if not enable:
        return
    if len(history) < 2:
        return
    if len(user_message.strip()) < 10 or len(assistant_output.strip()) < 20:
        return

    messages = []
    for msg in history[-6:]:
        messages.append({"role": msg.role, "content": msg.content or ""})
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": assistant_output})

    async def _extract_and_merge_daily() -> None:
        created = await MemoryService.extract_memories_from_conversation(
            user_id=user_id,
            session_id=session_id,
            messages=messages,
        )
        # 实时追加:把本次会话的 L3 摘要合并进今天的每日记忆(凌晨 cron 会再精修)
        l3_memory = next((m for m in created if m.layer == "L3"), None)
        if l3_memory is not None:
            from aio_agent_platform.memory.daily import DailyMemoryService

            await DailyMemoryService.append_session_summary(
                user_id, session_id, l3_memory.content
            )

    task = asyncio.create_task(_extract_and_merge_daily())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


async def persist_assistant_message(
    session_id: UUID,
    user_id: UUID,
    content: str,
    tool_calls: list[dict] | None,
) -> None:
    """Save an assistant message on its own DB session (rescue path)."""
    if not content and not tool_calls:
        return
    try:
        current_user_id.set(str(user_id))
        factory = get_session_factory()
        async with factory() as db:
            db.add(
                Message(
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls if tool_calls else None,
                )
            )
            await db.commit()
        logger.info(
            "stream_partial_message_saved",
            session_id=str(session_id),
            tool_calls_count=len(tool_calls) if tool_calls else 0,
        )
    except Exception:
        logger.exception(
            "stream_partial_message_save_failed", session_id=str(session_id)
        )


async def update_context_summary(
    session_id: UUID,
    history: list[LLMMessage],
    user_message: str,
    assistant_output: str,
    provider: Any,
) -> None:
    """Update session context_summary if conversation is long enough."""
    all_messages = [
        *list(history),
        LLMMessage(role="user", content=user_message),
        LLMMessage(role="assistant", content=assistant_output),
    ]
    if len(all_messages) < 10:
        return

    try:
        summary = await generate_summary(all_messages[-20:], provider)
        if summary:
            factory = get_session_factory()
            async with factory() as db:
                await db.execute(
                    sql_update(Session)
                    .where(Session.id == session_id)
                    .values(context_summary=summary)
                )
                await db.commit()
                logger.info(f"Context summary updated for session {session_id}: {len(summary)} chars")
    except Exception:
        logger.exception(f"Failed to update context summary for session {session_id}")


# ---------------------------------------------------------------------------
# File-attachment helpers (used by chat routes)
# ---------------------------------------------------------------------------


def file_refs_to_dicts(refs: list | None) -> list | None:
    """Convert FileAttachmentRef list to plain dicts for the prompt builder."""
    if not refs:
        return None
    return [r.model_dump() if hasattr(r, "model_dump") else r for r in refs]


def inject_file_refs_into_message(message: str, file_refs: list | None) -> str:
    """Prepend file references to the user message so the agent knows about them."""
    if not file_refs:
        return message
    refs = file_refs_to_dicts(file_refs)
    if not refs:
        return message
    lines = ["[用户上传了以下文件到工作区：]"]
    for f in refs:
        name = f.get("filename", "unknown")
        size = f.get("size", 0)
        path = f.get("workspace_path", "")
        size_str = f"{size / (1024 * 1024):.1f} MB" if size > 1024 * 1024 else f"{size:,} 字节"
        lines.append(f"- {name} ({size_str}) → {path} (相对路径，使用时直接传此路径)")
    lines.append(f"\n用户消息：{message}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Back-compat aliases — keep routes/chat.py's internal imports working while
# we migrate call sites.
# ---------------------------------------------------------------------------

_load_agent = load_agent
_filter_tools_by_agent = filter_tools_by_agent
_build_system_prompt_with_memories = build_system_prompt_with_memories
_build_agent_loop = build_agent_loop
_resolve_workspace = resolve_workspace
_get_memory_top_k = get_memory_top_k
_resolve_provider_type = resolve_provider_type
_fire_memory_extraction = fire_memory_extraction
_persist_assistant_message = persist_assistant_message
_update_context_summary = update_context_summary
_load_conversation_history = load_conversation_history
_file_refs_to_dicts = file_refs_to_dicts
_inject_file_refs_into_message = inject_file_refs_into_message
_background_tasks = background_tasks
