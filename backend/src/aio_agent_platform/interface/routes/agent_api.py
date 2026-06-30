"""Agent external API routes — version management + public API endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID, uuid4

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import AdminUser, CurrentUser
from aio_agent_platform.core.agent import AgentLoop, AgentStep
from aio_agent_platform.core.config import settings
from aio_agent_platform.core.context import current_agent_id, prepare_context, is_context_overflow_error, emergency_compress
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.db import Message, Session
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent, AgentVersion, LLMModel, User
from aio_agent_platform.llm import LLMMessage, create_provider
from aio_agent_platform.memory.service import MemoryService
from aio_agent_platform.tools.executor import ToolExecutor

logger = structlog.get_logger()

router = APIRouter(tags=["agent-api"])


# ---- Schemas ----


class ExecuteRequest(BaseModel):
    user_input: str = Field(..., min_length=1, max_length=10000)
    variables: dict | None = None
    session_rid: str | None = None


class ExecuteResponse(BaseModel):
    code: int = 0
    data: dict


class CreateSessionRequest(BaseModel):
    session_type: str = "production"
    user_id: str | None = None


class CreateSessionResponse(BaseModel):
    code: int = 0
    data: dict


class SseChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=50000)


class VersionOut(BaseModel):
    id: UUID
    agent_id: UUID
    version: str
    changelog: str | None = None
    published_at: str
    is_active: bool

    model_config = {"from_attributes": True}


class PublishVersionRequest(BaseModel):
    version: str | None = None
    changelog: str | None = None


class AgentApiDoc(BaseModel):
    agent_id: str
    agent_name: str
    agent_description: str | None = None
    agent_icon: str = "robot"
    model_name: str | None = None
    streaming_enabled: bool = True
    thinking_enabled: bool = False
    tool_count: int = 0
    skill_count: int = 0
    has_published_version: bool = False
    latest_version: VersionOut | None = None
    base_url: str = ""


# ---- Helpers ----


async def _load_agent_with_relations(db: AsyncSession, agent_id: UUID) -> Agent | None:
    """Load agent with skills, model, children, knowledge_bases."""
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.skills),
            selectinload(Agent.model),
            selectinload(Agent.children),
            selectinload(Agent.knowledge_bases),
        )
        .where(Agent.id == agent_id, Agent.is_active == True)
    )
    return result.scalar_one_or_none()


async def _get_active_version(db: AsyncSession, agent_id: UUID) -> AgentVersion | None:
    """Get the currently active published version for an agent."""
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id, AgentVersion.is_active == True)
        .order_by(AgentVersion.published_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _build_agent_loop_for_version(
    tool_executor: ToolExecutor,
    system_prompt: str,
    db: AsyncSession,
    config_snapshot: dict,
    workspace_id: UUID | None = None,
) -> AgentLoop:
    """Create an AgentLoop using the version's config snapshot."""
    model_to_use = None
    agent_model_id = config_snapshot.get("model_id")

    if agent_model_id:
        try:
            model_uuid = UUID(agent_model_id) if isinstance(agent_model_id, str) else agent_model_id
            result = await db.execute(
                select(LLMModel)
                .options(selectinload(LLMModel.provider))
                .where(LLMModel.id == model_uuid, LLMModel.is_active == True)
            )
            model_to_use = result.scalar_one_or_none()
        except (ValueError, AttributeError):
            pass

    if not model_to_use:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_default == True, LLMModel.is_active == True)
            .limit(1)
        )
        model_to_use = result.scalar_one_or_none()

    if not model_to_use or not model_to_use.provider:
        raise HTTPException(
            status_code=400,
            detail="没有可用的模型，请在管理后台配置模型",
        )

    temperature = config_snapshot.get("temperature") or settings.llm.temperature

    provider = create_provider(
        provider=model_to_use.provider.provider_type,
        model=model_to_use.model_name,
        base_url=model_to_use.provider.base_url,
        api_key=model_to_use.provider.api_key_encrypted,
        temperature=temperature,
    )

    return AgentLoop(
        provider=provider,
        tool_executor=tool_executor,
        system_prompt=system_prompt,
        max_iterations=config_snapshot.get("max_iterations") if config_snapshot.get("max_iterations") is not None else settings.agent.max_iterations,
        trust_level=settings.agent.trust_level,
        workspace_id=workspace_id,
    )


def _filter_tools_for_agent(tool_executor: ToolExecutor, config_snapshot: dict) -> tuple[list, list]:
    """Filter tools based on the version config snapshot."""
    all_tools = tool_executor.registry.list_tools()
    enabled_tools = config_snapshot.get("enabled_tools", [])

    if enabled_tools:
        enabled_set = set(enabled_tools)
        filtered = [t for t in all_tools if t.name in enabled_set]
    else:
        filtered = all_tools

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

    # Add MCP tools
    mcp_manager = tool_executor.mcp_manager
    if mcp_manager:
        mcp_server_ids = config_snapshot.get("mcp_server_ids", [])
        allowed_server_ids = set(str(sid) for sid in mcp_server_ids) if mcp_server_ids else None
        enabled_set = set(enabled_tools) if enabled_tools else None

        for full_name, tool_info in mcp_manager.list_all_tools():
            server_id = mcp_manager._tool_to_server.get(full_name)
            if allowed_server_ids is not None and server_id is not None:
                if str(server_id) not in allowed_server_ids:
                    continue
            if enabled_set is not None and full_name not in enabled_set:
                continue
            tools_schema.append(tool_info.to_openai_tool(
                prefix=full_name[:len(full_name) - len(tool_info.name)]
            ))

    return filtered, tools_schema


def _build_config_snapshot(agent: Agent) -> dict:
    """Build a config snapshot from the current agent state."""
    return {
        "model_id": str(agent.model_id) if agent.model_id else None,
        "system_prompt": agent.system_prompt,
        "enabled_tools": agent.enabled_tools or [],
        "mcp_server_ids": [str(sid) for sid in (agent.mcp_server_ids or [])],
        "skill_ids": [str(s.id) for s in agent.skills] if agent.skills else [],
        "knowledge_base_ids": [str(kb.id) for kb in agent.knowledge_bases] if agent.knowledge_bases else [],
        "child_ids": [str(c.id) for c in agent.children] if agent.children else [],
        "temperature": agent.temperature,
        "welcome_message": agent.welcome_message,
        "enable_memory_extraction": agent.enable_memory_extraction,
        "max_iterations": agent.max_iterations,
    }


# ---- Version Management ----


@router.get("/api/agents/{agent_id}/versions", response_model=list[VersionOut])
async def list_versions(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[VersionOut]:
    """List all published versions of an agent."""
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.published_at.desc())
    )
    versions = result.scalars().all()
    return [
        VersionOut(
            id=v.id,
            agent_id=v.agent_id,
            version=v.version,
            changelog=v.changelog,
            published_at=v.published_at.isoformat(),
            is_active=v.is_active,
        )
        for v in versions
    ]


@router.post("/api/agents/{agent_id}/versions", response_model=VersionOut, status_code=201)
async def publish_version(
    agent_id: UUID,
    req: PublishVersionRequest,
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> VersionOut:
    """Publish a new version of an agent (admin/owner only).

    Creates a snapshot of the current agent configuration.
    """
    # Load agent with relations for snapshot
    agent = await _load_agent_with_relations(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check ownership or admin
    if agent.created_by != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="No permission to publish this agent")

    # Generate version string
    version = req.version or str(uuid4())[:8]

    # Build config snapshot
    config_snapshot = _build_config_snapshot(agent)

    # Deactivate previous active versions
    await db.execute(
        update(AgentVersion)
        .where(AgentVersion.agent_id == agent_id, AgentVersion.is_active == True)
        .values(is_active=False)
    )

    # Create new version
    new_version = AgentVersion(
        agent_id=agent_id,
        version=version,
        changelog=req.changelog,
        config_snapshot=config_snapshot,
        published_by=user.id,
        is_active=True,
    )
    db.add(new_version)
    await db.flush()

    logger.info(
        "agent_version_published",
        agent_id=str(agent_id),
        version=version,
        published_by=str(user.id),
    )

    return VersionOut(
        id=new_version.id,
        agent_id=new_version.agent_id,
        version=new_version.version,
        changelog=new_version.changelog,
        published_at=new_version.published_at.isoformat(),
        is_active=new_version.is_active,
    )


# ---- API Doc Metadata ----


@router.get("/api/agents/{agent_id}/api-doc", response_model=AgentApiDoc)
async def get_api_doc(
    agent_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentApiDoc:
    """Get API documentation metadata for an agent."""
    agent = await _load_agent_with_relations(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get active version
    active_version = await _get_active_version(db, agent_id)
    version_out = None
    if active_version:
        version_out = VersionOut(
            id=active_version.id,
            agent_id=active_version.agent_id,
            version=active_version.version,
            changelog=active_version.changelog,
            published_at=active_version.published_at.isoformat(),
            is_active=active_version.is_active,
        )

    tool_count = len(agent.enabled_tools or [])
    skill_count = len(agent.skills) if agent.skills else 0

    return AgentApiDoc(
        agent_id=str(agent_id),
        agent_name=agent.name,
        agent_description=agent.description,
        agent_icon=agent.icon,
        model_name=agent.model.name if agent.model else None,
        streaming_enabled=True,
        thinking_enabled=False,
        tool_count=tool_count,
        skill_count=skill_count,
        has_published_version=active_version is not None,
        latest_version=version_out,
    )


# ---- Execute (Sync) ----


@router.post("/api/agents/{agent_id}/execute", response_model=ExecuteResponse)
async def execute_agent(
    agent_id: UUID,
    req: ExecuteRequest,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExecuteResponse:
    """Synchronous one-shot execution of an agent.

    Sends a message and waits for the complete response.
    No session management needed unless session_rid is provided.
    """
    try:
        return await _execute_agent_inner(agent_id, req, request, user, db)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("execute_agent_unhandled_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


async def _execute_agent_inner(
    agent_id: UUID,
    req: ExecuteRequest,
    request: Request,
    user: User,
    db: AsyncSession,
) -> ExecuteResponse:
    """Inner implementation of execute_agent."""
    tool_executor: ToolExecutor = request.app.state.tool_executor

    # Check for published version
    active_version = await _get_active_version(db, agent_id)
    if not active_version:
        raise HTTPException(
            status_code=400,
            detail={"code": 40001, "message": "Agent 未发布版本，请先在界面发布一个版本"},
        )

    config_snapshot = active_version.config_snapshot

    # Load agent with relations
    agent = await _load_agent_with_relations(db, agent_id)
    if not agent:
        raise HTTPException(
            status_code=404,
            detail={"code": 40401, "message": "Agent 不存在"},
        )

    current_agent_id.set(str(agent.id))

    # Get or create session
    session = None
    if req.session_rid:
        try:
            sid = UUID(req.session_rid)
            result = await db.execute(
                select(Session).where(Session.id == sid, Session.user_id == user.id)
            )
            session = result.scalar_one_or_none()
        except ValueError:
            pass

    if not session:
        session = Session(
            user_id=user.id,
            agent_id=agent_id,
            title=req.user_input[:100],
        )
        db.add(session)
        await db.flush()

    # Load conversation history
    history_result = await db.execute(
        select(Message)
        .where(Message.session_id == session.id)
        .order_by(Message.created_at)
    )
    history_messages = history_result.scalars().all()
    history: list[LLMMessage] = []
    for msg in history_messages:
        history.append(LLMMessage(role=msg.role, content=msg.content or ""))

    # Build system prompt
    tools_list, tools_schema = _filter_tools_for_agent(tool_executor, config_snapshot)

    memory_top_k = 5
    memory_data = await MemoryService.get_memories_for_prompt(
        db, user.id, req.user_input, top_k=memory_top_k
    )
    from aio_agent_platform.db import UserProfile
    profile_result = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    user_profile = profile_result.scalar_one_or_none()
    user_portrait = user_profile.personal_portrait if user_profile else None

    system_prompt = build_system_prompt(
        tools=tools_list,
        persistent_memories=memory_data.get("l1_memories", []),
        relevant_memories=memory_data.get("l2_memories", []) + memory_data.get("l3_memories", []),
        relevant_skills=agent.skills if agent.skills else None,
        agent_prompt=config_snapshot.get("system_prompt") or agent.system_prompt,
        child_agents=agent.children if agent.children else None,
        user_portrait=user_portrait,
    )

    # Build agent loop
    agent_loop = await _build_agent_loop_for_version(
        tool_executor, system_prompt, db, config_snapshot
    )

    # Prepare context
    prepared_messages, _ = await prepare_context(
        system_prompt=system_prompt,
        history=history,
        user_input=req.user_input,
        provider=agent_loop.provider,
    )

    processed_history = [
        m for m in prepared_messages
        if m.role != "system" and not (m.role == "user" and m.content == req.user_input)
    ]

    # Save user message
    user_msg = Message(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=req.user_input,
    )
    db.add(user_msg)
    await db.flush()

    # Run agent loop
    final_output = ""
    try:
        async for event in agent_loop.run(
            user_input=req.user_input,
            user_id=user.id,
            session_id=session.id,
            conversation_history=processed_history,
            tools=tools_schema,
        ):
            if isinstance(event, AgentStep) and event.done:
                final_output = event.final_output
    except Exception as e:
        if is_context_overflow_error(e):
            logger.warning(f"Context overflow in execute, emergency compress: {e}")
            emergency_history = emergency_compress(prepared_messages, level=1)
            emergency_hist = [
                m for m in emergency_history
                if m.role != "system" and not (m.role == "user" and m.content == req.user_input)
            ]
            async for event in agent_loop.run(
                user_input=req.user_input,
                user_id=user.id,
                session_id=session.id,
                conversation_history=emergency_hist,
                tools=tools_schema,
            ):
                if isinstance(event, AgentStep) and event.done:
                    final_output = event.final_output
        else:
            logger.error("execute_agent_error", error=str(e), exc_info=True)
            raise HTTPException(status_code=500, detail=f"Agent execution failed: {type(e).__name__}: {e}") from e

    # Save assistant message
    assistant_msg = Message(
        session_id=session.id,
        user_id=user.id,
        role="assistant",
        content=final_output,
    )
    db.add(assistant_msg)
    await db.flush()

    return ExecuteResponse(
        code=0,
        data={
            "markdown_response": final_output,
            "session_rid": str(session.id),
        },
    )


# ---- External Session Management ----


@router.post("/api/agents/{agent_id}/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_external_session(
    agent_id: UUID,
    req: CreateSessionRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CreateSessionResponse:
    """Create a new session for external API calls (SSE streaming)."""
    # Verify agent exists and has a published version
    agent = await _load_agent_with_relations(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    active_version = await _get_active_version(db, agent_id)
    if not active_version:
        raise HTTPException(
            status_code=400,
            detail="Agent 未发布版本，请先在界面发布一个版本",
        )

    session = Session(
        user_id=user.id,
        agent_id=agent_id,
        title=f"API Session ({req.session_type})",
    )
    db.add(session)
    await db.flush()

    return CreateSessionResponse(
        code=0,
        data={
            "id": str(session.id),
            "agent_id": str(agent_id),
            "created_at": session.created_at.isoformat(),
        },
    )


# ---- SSE Streaming Chat (external sessions) ----


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/api/sessions/{session_id}/chat")
async def sse_chat(
    session_id: UUID,
    req: SseChatRequest,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE streaming chat for external API sessions.

    Accept: text/event-stream
    """
    tool_executor: ToolExecutor = request.app.state.tool_executor

    # Load session
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    agent_id = session.agent_id
    if not agent_id:
        raise HTTPException(status_code=400, detail="Session has no associated agent")

    # Check for published version
    active_version = await _get_active_version(db, agent_id)
    if not active_version:
        raise HTTPException(status_code=400, detail="Agent 未发布版本")

    config_snapshot = active_version.config_snapshot

    # Load agent
    agent = await _load_agent_with_relations(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    current_agent_id.set(str(agent.id))

    async def event_generator():
        """Generate SSE events for streaming chat."""
        from aio_agent_platform.db.connection import get_session_factory

        async with get_session_factory() as db_session:
            # Send session event
            yield f"event: session\ndata: {json.dumps({'session_id': str(session_id)}, ensure_ascii=False)}\n\n"

            # Load conversation history
            history_result = await db_session.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
            history_messages = history_result.scalars().all()
            history: list[LLMMessage] = []
            for msg in history_messages:
                history.append(LLMMessage(role=msg.role, content=msg.content or ""))

            # Build tools and system prompt
            tools_list, tools_schema = _filter_tools_for_agent(tool_executor, config_snapshot)

            memory_data = await MemoryService.get_memories_for_prompt(
                db_session, user.id, req.message, top_k=5
            )
            from aio_agent_platform.db import UserProfile
            profile_result = await db_session.execute(
                select(UserProfile).where(UserProfile.user_id == user.id)
            )
            user_profile = profile_result.scalar_one_or_none()
            user_portrait = user_profile.personal_portrait if user_profile else None

            system_prompt = build_system_prompt(
                tools=tools_list,
                persistent_memories=memory_data.get("l1_memories", []),
                relevant_memories=memory_data.get("l2_memories", []) + memory_data.get("l3_memories", []),
                relevant_skills=agent.skills if agent.skills else None,
                agent_prompt=config_snapshot.get("system_prompt") or agent.system_prompt,
                child_agents=agent.children if agent.children else None,
                user_portrait=user_portrait,
            )

            # Build agent loop
            agent_loop = await _build_agent_loop_for_version(
                tool_executor, system_prompt, db_session, config_snapshot
            )

            # Prepare context
            prepared_messages, _ = await prepare_context(
                system_prompt=system_prompt,
                history=history,
                user_input=req.message,
                provider=agent_loop.provider,
            )
            processed_history = [
                m for m in prepared_messages
                if m.role != "system" and not (m.role == "user" and m.content == req.message)
            ]

            # Save user message
            user_msg = Message(
                session_id=session_id,
                user_id=user.id,
                role="user",
                content=req.message,
            )
            db_session.add(user_msg)
            await db_session.flush()

            # Run agent loop with streaming
            final_output = ""
            tool_calls_data = []

            try:
                async for event in agent_loop.run(
                    user_input=req.message,
                    user_id=user.id,
                    session_id=session_id,
                    conversation_history=processed_history,
                    tools=tools_schema,
                ):
                    if isinstance(event, AgentStep):
                        if event.thinking:
                            yield f"event: thinking\ndata: {json.dumps({'content': event.thinking}, ensure_ascii=False)}\n\n"
                        if event.tool_call:
                            tc = event.tool_call
                            tc_data = {
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                            }
                            tool_calls_data.append(tc_data)
                            yield f"event: tool_call\ndata: {json.dumps(tc_data, ensure_ascii=False)}\n\n"
                        if event.tool_result:
                            tr = event.tool_result
                            yield f"event: tool_result\ndata: {json.dumps({'tool_call_id': tr.tool_call_id, 'status': tr.status, 'preview': tr.preview}, ensure_ascii=False)}\n\n"
                        if event.text_delta:
                            yield f"event: text_delta\ndata: {json.dumps({'text': event.text_delta}, ensure_ascii=False)}\n\n"
                        if event.done:
                            final_output = event.final_output
                    elif isinstance(event, str) and event.startswith("tool_result:"):
                        pass  # Already handled via AgentStep

            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'code': 50002, 'message': str(e)}, ensure_ascii=False)}\n\n"
                return

            # Save assistant message
            assistant_msg = Message(
                session_id=session_id,
                user_id=user.id,
                role="assistant",
                content=final_output,
            )
            db_session.add(assistant_msg)
            await db_session.flush()

            # Send done event
            done_data = {
                "message_id": str(assistant_msg.id),
                "trace": {
                    "tool_calls": tool_calls_data,
                },
            }
            yield f"event: done\ndata: {json.dumps(done_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- Session Management Endpoints ----


@router.get("/api/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get conversation history for an external API session."""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msg_result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    return {
        "code": 0,
        "data": [
            {
                "id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
    }


@router.post("/api/sessions/{session_id}/clear")
async def clear_session(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Clear all messages in a session (keep the session)."""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    from sqlalchemy import delete
    await db.execute(delete(Message).where(Message.session_id == session_id))
    session.context_summary = None

    return {"code": 0, "data": {"message": "Session cleared"}}


@router.delete("/api/sessions/{session_id}")
async def delete_external_session(
    session_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete an external API session and its messages."""
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user.id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    from sqlalchemy import delete
    await db.execute(delete(Message).where(Message.session_id == session_id))
    await db.execute(delete(Session).where(Session.id == session_id))

    return {"code": 0, "data": {"message": "Session deleted"}}


@router.post("/api/sessions/{session_id}/messages/{message_id}/feedback")
async def submit_message_feedback(
    session_id: UUID,
    message_id: UUID,
    body: dict,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Submit feedback (up/down) for a message."""
    result = await db.execute(
        select(Message)
        .where(Message.id == message_id, Message.session_id == session_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    feedback = body.get("feedback", "")
    if feedback not in ("up", "down"):
        raise HTTPException(status_code=400, detail="feedback must be 'up' or 'down'")

    # Store feedback in token_usage JSONB (reuse field for simplicity)
    token_usage = msg.token_usage or {}
    token_usage["feedback"] = feedback
    msg.token_usage = token_usage

    return {"code": 0, "data": {"message": "Feedback recorded"}}
