"""Delegate task handler — core logic for multi-agent task dispatching."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.core.agent import AgentLoop, AgentStep, DelegationContext
from aio_agent_platform.core.config import settings
from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import (
    Agent,
    AgentRelationship,
    Delegation,
    LLMModel,
    Session,
    User,
)
from aio_agent_platform.llm import create_provider
from aio_agent_platform.memory.service import MemoryService
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.tools.executor import ToolExecutor

logger = structlog.get_logger()


@dataclass
class DynamicSubAgent:
    """In-memory stand-in for a temporary sub-agent spawned by the parent agent.

    Mirrors the attributes of the ``Agent`` model that the delegation pipeline
    reads, so a dynamically created specialist agent can flow through the same
    tool/prompt/provider build logic without a database row.
    """

    id: UUID
    name: str
    description: str | None = None
    icon: str = "rocket"
    system_prompt: str | None = None
    model_id: None = None
    enabled_tools: None = None
    temperature: None = None
    enable_retry: None = None
    max_iterations: None = None
    is_active: bool = True
    skills: list = field(default_factory=list)
    knowledge_bases: list = field(default_factory=list)
    children: list = field(default_factory=list)


def _build_dynamic_agent(child_id: UUID, name: str, role_description: str) -> DynamicSubAgent:
    """Build an in-memory temporary sub-agent from a role description."""
    return DynamicSubAgent(
        id=child_id,
        name=name,
        description=role_description or None,
    )


async def _set_rls_context(db: AsyncSession, user_id: str) -> None:
    """Set PostgreSQL RLS context."""
    await db.execute(select(func.set_config("app.current_user_id", user_id, True)))


async def handle_delegate_task(
    arguments: dict,
    user_id: str,
    session_id: str,
    *,
    delegation: DelegationContext | None = None,
    tool_executor: ToolExecutor | None = None,
    tool_call_id: str | None = None,
    **kwargs,
) -> str:
    """
    Handle delegate_task tool call.

    Dispatches a task to a child agent, executing it in an isolated AgentLoop
    with shared sandbox and user memory context.
    """
    if not delegation:
        return "Error: delegation context not available"

    if not tool_executor:
        return "Error: tool executor not available"

    child_agent_id = arguments.get("child_agent_id", "")
    role_name = (arguments.get("role_name", "") or "").strip()
    role_description = (arguments.get("role_description", "") or "").strip()
    task = arguments.get("task", "")
    context = arguments.get("context", "")

    if not task:
        return "Error: task is required"

    # Two modes: delegate to an existing child agent (child_agent_id) OR
    # dynamically spawn a temporary specialist sub-agent (role_name/role_description).
    if child_agent_id and (role_name or role_description):
        return "Error: provide either child_agent_id or role_name/role_description, not both"
    if not child_agent_id and not role_name and not role_description:
        return "Error: provide child_agent_id (existing child agent) or role_name/role_description (dynamic spawn)"

    parent_agent_id = delegation.parent_agent_id
    new_depth = delegation.delegation_depth + 1
    max_depth = delegation.max_depth

    # Depth limit check
    if new_depth > max_depth:
        return f"Error: delegation depth limit reached ({max_depth})"

    t_start = time.monotonic()
    factory = get_session_factory()

    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)

        # 1. Resolve child agent: existing associated agent or dynamic temp agent
        is_dynamic = False
        if child_agent_id:
            try:
                child_uuid = UUID(child_agent_id)
            except ValueError:
                return f"Error: invalid child_agent_id format: {child_agent_id}"
            child_agent = await _load_child_agent(db, child_uuid, parent_agent_id, UUID(user_id))
            if not child_agent:
                return f"Error: child agent {child_agent_id} not found or not related to parent"
            if not child_agent.is_active:
                return f"Error: child agent {child_agent.name} is not active"
        else:
            is_dynamic = True
            child_uuid = uuid4()
            child_name = role_name or (role_description[:24] if role_description else "临时子智能体")
            child_agent = _build_dynamic_agent(child_uuid, child_name, role_description)

        # 2. Create delegation record
        delegation_record = Delegation(
            parent_session_id=UUID(session_id),
            parent_agent_id=parent_agent_id,
            child_agent_id=child_uuid,
            user_id=UUID(user_id),
            depth=new_depth,
            task=task,
            context=context or None,
            status="running",
        )
        db.add(delegation_record)
        await db.flush()

        delegation_id = delegation_record.id
        event_queue = delegation.event_queue

        # Notify frontend: delegation started
        if event_queue:
            await event_queue.put({
                "type": "delegation_start",
                "delegation_id": str(delegation_id),
                "child_agent_id": str(child_uuid),
                "child_agent_name": child_agent.name,
                "child_agent_icon": child_agent.icon,
                "is_dynamic": is_dynamic,
                "task": task,
                "depth": new_depth,
                "tool_call_id": tool_call_id or "",
            })
            # Push an immediate thinking placeholder so the DelegationCard
            # shows activity right away instead of just a spinner.
            await event_queue.put({
                "type": "delegation_thinking",
                "delegation_id": str(delegation_id),
                "content": "正在分析任务...\n",
            })

        try:
            # 3. Build child agent configuration (with inheritance)
            child_tools_list, child_tools_schema = _build_child_tools(
                tool_executor, child_agent, new_depth, max_depth
            )

            child_system_prompt = await _build_child_system_prompt(
                db, UUID(user_id), task, child_tools_list, child_agent
            )

            child_provider = await _build_child_provider(db, child_agent)
            if not child_provider:
                raise RuntimeError("No available model for child agent")

            # Resolve workspace: prefer delegation context (fast, no DB query),
            # fall back to parent session, last resort: create new workspace
            from aio_agent_platform.workspaces.service import WorkspaceService

            workspace_id = delegation.workspace_id if delegation else None
            workspace_slug = delegation.workspace_slug if delegation else None
            if not workspace_id:
                parent_session_result = await db.execute(
                    select(Session).where(Session.id == UUID(session_id))
                )
                parent_session = parent_session_result.scalar_one_or_none()
                if parent_session and parent_session.workspace_id:
                    workspace_id = parent_session.workspace_id
                    # Fetch slug for this workspace
                    ws_obj = await WorkspaceService.get_workspace(db, workspace_id, UUID(user_id))
                    if ws_obj:
                        workspace_slug = ws_obj.slug
                else:
                    # Fallback: create a dedicated per-session workspace
                    ws_name = (parent_session.title if parent_session else None) or "Delegation Session"
                    ws = await WorkspaceService.create_workspace(db, UUID(user_id), ws_name)
                    workspace_id = ws.id
                    workspace_slug = ws.slug
                    if parent_session:
                        parent_session.workspace_id = ws.id
                        await db.flush()

            # 4. Create child AgentLoop (propagate workspace_id for nested delegation)
            child_delegation = DelegationContext(
                parent_agent_id=child_uuid,
                delegation_depth=new_depth,
                max_depth=max_depth,
                event_queue=event_queue,
                workspace_id=workspace_id,
                workspace_slug=workspace_slug,
            )

            child_loop = AgentLoop(
                provider=child_provider,
                tool_executor=tool_executor,
                system_prompt=child_system_prompt,
                max_iterations=child_agent.max_iterations if child_agent.max_iterations is not None else settings.agent.child_max_iterations,
                trust_level=settings.agent.trust_level,
                delegation=child_delegation,
                event_queue=event_queue,
                workspace_id=workspace_id,
                workspace_slug=workspace_slug,
                allowed_tools={t.name for t in child_tools_list},
            )

            # 5. Execute child agent
            user_input = task
            if context:
                user_input = f"## Task\n{task}\n\n## Context\n{context}"

            # Switch current_agent_id to the child so tool handlers
            # (e.g. knowledge_retrieval) resolve the child's config, not the parent's.
            parent_agent_id_token = current_agent_id.set(str(child_uuid))

            final_output = ""
            child_event_count = 0
            try:
                async for event in child_loop.run(
                    user_input=user_input,
                    user_id=UUID(user_id),
                    session_id=UUID(session_id),  # Share parent's sandbox
                    conversation_history=[],  # Child has no history
                    tools=child_tools_schema,
                ):
                    child_event_count += 1
                    if isinstance(event, str):
                        # Proxy child events to parent's SSE stream
                        if event_queue:
                            await _proxy_child_event(event_queue, delegation_id, event)
                    elif isinstance(event, AgentStep):
                        if event.done:
                            final_output = event.final_output
            finally:
                # Restore parent's agent ID so subsequent parent tool calls are unaffected.
                current_agent_id.reset(parent_agent_id_token)

            logger.info(
                "delegation_completed",
                delegation_id=str(delegation_id),
                child_event_count=child_event_count,
                output_length=len(final_output),
            )

            # 6. Update delegation record
            duration = int((time.monotonic() - t_start) * 1000)
            delegation_record.status = "completed"
            delegation_record.result = final_output
            delegation_record.duration_ms = duration
            delegation_record.completed_at = datetime.now(UTC)
            await db.commit()

            # Notify frontend: delegation completed
            if event_queue:
                await event_queue.put({
                    "type": "delegation_end",
                    "delegation_id": str(delegation_id),
                    "status": "completed",
                    "result_preview": final_output[:500] if final_output else "",
                    "duration_ms": duration,
                })

            return final_output

        except Exception as e:
            logger.error("Delegation failed", error=str(e), delegation_id=str(delegation_id))

            duration = int((time.monotonic() - t_start) * 1000)
            delegation_record.status = "failed"
            delegation_record.error = str(e)
            delegation_record.duration_ms = duration
            delegation_record.completed_at = datetime.now(UTC)
            await db.commit()

            if event_queue:
                await event_queue.put({
                    "type": "delegation_end",
                    "delegation_id": str(delegation_id),
                    "status": "failed",
                    "error": str(e),
                    "duration_ms": duration,
                })

            return f"Error: delegation failed - {e}"


async def _load_child_agent(
    db: AsyncSession, child_id: UUID, parent_id: UUID, user_id: UUID
) -> Agent | None:
    """Load child agent and validate the parent-child relationship."""
    # Check relationship exists
    rel_result = await db.execute(
        select(AgentRelationship).where(
            AgentRelationship.parent_id == parent_id,
            AgentRelationship.child_id == child_id,
        )
    )
    if not rel_result.scalar_one_or_none():
        return None

    tenant_id = await db.scalar(select(User.tenant_id).where(User.id == user_id))
    if not tenant_id:
        return None

    # Load child agent with relationships
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.skills),
            selectinload(Agent.model),
            selectinload(Agent.children),
            selectinload(Agent.knowledge_bases),
        )
        .where(
            Agent.id == child_id,
            Agent.tenant_id == tenant_id,
            or_(Agent.visibility == "tenant", Agent.created_by == user_id),
        )
    )
    return result.scalar_one_or_none()


def _build_child_tools(
    tool_executor: ToolExecutor,
    child_agent: Agent | DynamicSubAgent,
    current_depth: int,
    max_depth: int,
) -> tuple[list, list]:
    """Build tool list for child agent with depth-aware filtering."""
    all_tools = tool_executor.registry.list_tools()

    # Filter by child's enabled_tools.
    # knowledge_retrieval and delegate_task are auto-injected based on
    # bindings, not manual tool selection — exclude them from the filter.
    if child_agent.enabled_tools:
        enabled_set = set(child_agent.enabled_tools)
        filtered = [
            t for t in all_tools
            if t.name in enabled_set
            and t.name != "knowledge_retrieval"
            and t.name != "delegate_task"
        ]
    else:
        filtered = [
            t for t in all_tools
            if t.name != "knowledge_retrieval"
            and t.name != "delegate_task"
        ]

    # Auto-inject knowledge_retrieval if child has knowledge bases bound
    has_knowledge = bool(child_agent.knowledge_bases)
    if has_knowledge:
        kr_tool = next((t for t in all_tools if t.name == "knowledge_retrieval"), None)
        if kr_tool:
            filtered.append(kr_tool)

    # Auto-inject delegate_task so the child can delegate to its own children
    # OR dynamically spawn temp agents (nested delegation), within the depth limit.
    if current_depth < max_depth:
        dt_tool = next((t for t in all_tools if t.name == "delegate_task"), None)
        if dt_tool:
            filtered.append(dt_tool)

    # Build OpenAI schema
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

    return filtered, tools_schema


async def _build_child_system_prompt(
    db: AsyncSession,
    user_id: UUID,
    task: str,
    tools_list: list,
    child_agent: Agent | DynamicSubAgent,
) -> str:
    """Build system prompt for child agent with inheritance."""
    # Get memory context
    memory_data = await MemoryService.get_memories_for_prompt(
        db, user_id, task, top_k=settings.agent.memory_top_k
    )

    # Get skills — use child's bound skills or search by task relevance
    if child_agent.skills:
        matched_skills = child_agent.skills
    else:
        matched_skills = await SkillService.get_skills_for_prompt(
            db, user_id, task, top_k=3
        )

    # Build child agent info section
    child_info = f"\n## Your Role\nYou are **{child_agent.name}**, a specialized sub-agent."
    if child_agent.description:
        child_info += f"\n{child_agent.description}"

    # Load user portrait
    from aio_agent_platform.db import UserProfile
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    user_profile = result.scalar_one_or_none()
    user_portrait = user_profile.personal_portrait if user_profile else None

    return build_system_prompt(
        tools=tools_list,
        persistent_memories=memory_data["l1_memories"],
        relevant_memories=memory_data["l2_memories"] + memory_data["l3_memories"],
        relevant_skills=matched_skills if matched_skills else None,
        agent_prompt=child_agent.system_prompt,
        child_agents=child_agent.children if child_agent.children else None,
        user_portrait=user_portrait,
    ) + child_info


async def _build_child_provider(db: AsyncSession, child_agent: Agent | DynamicSubAgent):
    """Build LLM provider for child agent with model inheritance."""
    # Try child's model, then default model
    model_to_use = None

    if child_agent.model_id:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.id == child_agent.model_id, LLMModel.is_active)
        )
        model_to_use = result.scalar_one_or_none()

    if not model_to_use:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_default, LLMModel.is_active)
            .limit(1)
        )
        model_to_use = result.scalar_one_or_none()

    if not model_to_use or not model_to_use.provider:
        return None

    return create_provider(
        provider=model_to_use.provider.provider_type,
        model=model_to_use.model_name,
        base_url=model_to_use.provider.base_url,
        api_key=model_to_use.provider.api_key_encrypted,
        temperature=child_agent.temperature if child_agent.temperature is not None else settings.llm.temperature,
        enable_retry=child_agent.enable_retry if child_agent.enable_retry is not None else True,
    )


async def _proxy_child_event(
    event_queue: asyncio.Queue,
    delegation_id: UUID,
    event: str,
) -> None:
    """Proxy child agent events to parent's SSE stream with delegation prefix."""
    if event.startswith("reasoning:"):
        content = event[len("reasoning:"):]
        await event_queue.put({
            "type": "delegation_thinking",
            "delegation_id": str(delegation_id),
            "content": content,
        })
    elif event.startswith("text_delta:"):
        delta = event[len("text_delta:"):]
        await event_queue.put({
            "type": "delegation_text_delta",
            "delegation_id": str(delegation_id),
            "content": delta,
        })
    elif event.startswith("tool_call:"):
        parts = event.split(":", 3)
        tc_id = parts[1] if len(parts) > 1 else ""
        tc_name = parts[2] if len(parts) > 2 else ""
        try:
            tc_args = json.loads(parts[3]) if len(parts) > 3 else {}
        except json.JSONDecodeError:
            tc_args = {}
        await event_queue.put({
            "type": "delegation_tool_call",
            "delegation_id": str(delegation_id),
            "id": tc_id,
            "name": tc_name,
            "arguments": tc_args,
        })
    elif event.startswith("tool_result:"):
        parts = event.split(":", 4)
        tc_id = parts[1] if len(parts) > 1 else ""
        tc_name = parts[2] if len(parts) > 2 else ""
        status = parts[3] if len(parts) > 3 else ""
        try:
            preview = json.loads(parts[4]) if len(parts) > 4 else ""
        except json.JSONDecodeError:
            preview = parts[4] if len(parts) > 4 else ""
        await event_queue.put({
            "type": "delegation_tool_result",
            "delegation_id": str(delegation_id),
            "tool_call_id": tc_id,
            "name": tc_name,
            "status": status,
            "preview": preview,
        })


# Handler registry
DELEGATION_HANDLERS: dict[str, Callable] = {
    "delegate_task": handle_delegate_task,
}
