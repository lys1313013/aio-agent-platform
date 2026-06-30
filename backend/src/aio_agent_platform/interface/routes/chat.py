"""Chat routes — REST and SSE endpoints for agent interaction."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.auth.jwt_handler import TokenExpiredError, decode_token
from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.core.agent import AgentLoop, AgentStep, DelegationContext
from aio_agent_platform.core.config import settings
from aio_agent_platform.core.context import (
    ContextBudget,
    emergency_compress,
    estimate_messages_tokens,
    generate_summary,
    is_context_overflow_error,
    prepare_context,
)
from aio_agent_platform.core.prompt import build_system_prompt
from aio_agent_platform.db import Message, Session
from aio_agent_platform.db.connection import current_user_id, get_db, get_session_factory
from aio_agent_platform.db.models import Agent, LLMModel, UserConfig
from aio_agent_platform.llm import LLMMessage, ToolCall, build_image_url_refs, build_user_content, create_provider
from aio_agent_platform.memory.service import MemoryService
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.storage.chat_attachments import (
    ALLOWED_MIME,
    MAX_BYTES,
    ChatAttachmentStorage,
)
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.tools.executor import ToolExecutor

logger = structlog.get_logger()

# Track background tasks to prevent garbage collection
_background_tasks: set[asyncio.Task] = set()

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---- Schemas ----


class ChatRequest(BaseModel):
    session_id: UUID | None = None
    agent_id: UUID | None = None
    message: str = Field("", max_length=50000)
    attachments: list["AttachmentOut"] | None = None
    file_attachments: list["FileAttachmentRef"] | None = None

    @model_validator(mode="after")
    def _require_content(self) -> "ChatRequest":
        if not self.message.strip() and not self.attachments and not self.file_attachments:
            raise ValueError("message、attachments 或 file_attachments 至少需要一个")
        return self


class ChatResponse(BaseModel):
    session_id: UUID
    message_id: UUID
    content: str
    tool_calls_count: int = 0
    done: bool = True


class AttachmentOut(BaseModel):
    """Metadata for an uploaded chat image attachment."""

    key: str
    url: str
    mime: str
    size: int
    filename: str


class FileAttachmentOut(BaseModel):
    """Metadata for an uploaded workspace file attachment."""

    file_id: str
    key: str
    url: str
    filename: str
    mime: str
    size: int
    workspace_path: str  # path relative to /workspace


class FileAttachmentRef(BaseModel):
    """Lightweight reference to a file attachment in a chat message."""

    file_id: str
    filename: str
    mime: str
    size: int
    workspace_path: str


# ---- Helpers ----


def _file_refs_to_dicts(refs: list | None) -> list | None:
    """Convert FileAttachmentRef list to plain dicts for the prompt builder."""
    if not refs:
        return None
    return [r.model_dump() if hasattr(r, 'model_dump') else r for r in refs]


def _inject_file_refs_into_message(message: str, file_refs: list | None) -> str:
    """Prepend file references to the user message so the agent knows about them."""
    if not file_refs:
        return message
    refs = _file_refs_to_dicts(file_refs)
    if not refs:
        return message
    lines = ["[用户上传了以下文件到工作区：]"]
    for f in refs:
        name = f.get("filename", "unknown")
        size = f.get("size", 0)
        path = f.get("workspace_path", "")
        size_str = f"{size / (1024*1024):.1f} MB" if size > 1024 * 1024 else f"{size:,} 字节"
        lines.append(f"- {name} ({size_str}) → {path} (相对路径，使用时直接传此路径)")
    lines.append(f"\n用户消息：{message}")
    return "\n".join(lines)


def _detect_image_mime(data: bytes) -> str | None:
    """Detect the real image MIME type from magic bytes.

    Returns the detected MIME string, or ``None`` if the data is not
    a recognised image format.  Does NOT trust the browser-declared
    content-type — files with a ``.png`` extension may actually be JPEG,
    which is common with scanned invoices / screenshots.
    """
    if not data:
        return None
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# Image compression settings for LLM vision input
_COMPRESS_MAX_LONG_SIDE = 2048  # pixels
_COMPRESS_JPEG_QUALITY = 85
_COMPRESS_THRESHOLD_BYTES = 500 * 1024  # 500 KB — skip compression for small images


def _compress_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Compress an image for LLM vision input.

    - Resizes so the longest side ≤ 2048px (preserves aspect ratio).
    - Converts to JPEG for better compression (PNG/WebP originals included).
    - Skips compression if the original is already small (< 500 KB).
    - GIF images are returned as-is (may be animated).

    Returns (compressed_bytes, new_mime).
    """
    if len(data) < _COMPRESS_THRESHOLD_BYTES or mime == "image/gif":
        return data, mime

    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(data))

        # Convert RGBA/P palette to RGB for JPEG output
        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize if longer side exceeds threshold
        w, h = img.size
        long_side = max(w, h)
        if long_side > _COMPRESS_MAX_LONG_SIDE:
            ratio = _COMPRESS_MAX_LONG_SIDE / long_side
            new_w, new_h = int(w * ratio), int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_COMPRESS_JPEG_QUALITY, optimize=True)
        compressed = buf.getvalue()

        # Only use compressed version if it's actually smaller
        if len(compressed) < len(data):
            return compressed, "image/jpeg"
    except Exception:
        pass  # Fall back to original on any error

    return data, mime


def _resolve_provider_type(provider_type: str | None) -> str:
    name = (provider_type or "").lower()
    if "anthropic" in name or "claude" in name:
        return "anthropic"
    return "openai"


async def _get_memory_top_k(db: AsyncSession, user_id: UUID) -> int:
    """Get user's memory_top_k setting, falling back to global default."""
    result = await db.execute(
        select(UserConfig).where(UserConfig.user_id == user_id)
    )
    config = result.scalar_one_or_none()
    return config.memory_top_k if config else settings.agent.memory_top_k


async def _resolve_workspace_id(
    db: AsyncSession,
    session: Session,
    user_id: UUID,
) -> UUID:
    """
    Resolve the workspace_id for a chat session.

    Isolation model: per-session by default.

    Priority:
    1. Session.workspace_id (if explicitly set — shared workspace)
    2. Auto-create a dedicated workspace for this session (per-session isolation)

    This means each session gets its own isolated file space by default.
    Users can opt into sharing by explicitly setting workspace_id on session creation.
    """
    from aio_agent_platform.workspaces.service import WorkspaceService

    if session.workspace_id:
        return session.workspace_id

    # Per-session isolation: create a dedicated workspace for this session
    name = session.title or "Untitled Session"
    workspace = await WorkspaceService.create_workspace(
        db=db,
        user_id=user_id,
        name=name,
    )

    # Persist the association so future requests skip this creation
    session.workspace_id = workspace.id
    await db.flush()

    return workspace.id


async def _load_agent(db: AsyncSession, agent_id: UUID | None) -> Agent | None:
    """Load agent with its skills, model, children, and knowledge_bases relationships."""
    if not agent_id:
        return None
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


def _filter_tools_by_agent(
    tool_executor: ToolExecutor,
    agent: Agent | None,
    delegation_depth: int = 0,
) -> tuple[list, list]:
    """Filter tools based on agent's enabled_tools list and delegation depth.

    Returns (tools_list, tools_schema).
    The tools_list contains built-in Tool objects.
    The tools_schema contains both built-in and MCP tool schemas in OpenAI format.
    """
    all_tools = tool_executor.registry.list_tools()
    if agent and agent.enabled_tools:
        enabled_set = set(agent.enabled_tools)
        # knowledge_retrieval and delegate_task are auto-injected based on
        # bindings, not manual tool selection — exclude them from the filter.
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

    # Auto-inject knowledge_retrieval if agent has knowledge bases bound
    has_knowledge = bool(agent and agent.knowledge_bases)
    if has_knowledge:
        kr_tool = next((t for t in all_tools if t.name == "knowledge_retrieval"), None)
        if kr_tool:
            filtered.append(kr_tool)
        kb_names = [kb.name for kb in agent.knowledge_bases] if agent else []
        logger.info(
            "knowledge_retrieval_tool_injected",
            agent_id=str(agent.id) if agent else None,
            knowledge_bases=kb_names,
            kb_count=len(kb_names),
        )
    else:
        logger.info(
            "knowledge_retrieval_tool_skipped",
            agent_id=str(agent.id) if agent else None,
            reason="no knowledge bases bound",
        )

    # Auto-inject delegate_task if agent has children and within depth limit
    max_depth = settings.agent.max_delegation_depth
    has_children = bool(agent and agent.children)
    if has_children and delegation_depth < max_depth:
        dt_tool = next((t for t in all_tools if t.name == "delegate_task"), None)
        if dt_tool:
            filtered.append(dt_tool)
        child_names = [c.name for c in agent.children] if agent else []
        logger.info(
            "delegate_task_tool_injected",
            agent_id=str(agent.id) if agent else None,
            children=child_names,
            child_count=len(child_names),
        )
    else:
        logger.info(
            "delegate_task_tool_skipped",
            agent_id=str(agent.id) if agent else None,
            reason="no children" if not has_children else "max depth reached",
        )

    # Build OpenAI schema from filtered built-in tools
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

    # Add MCP tools to schema, filtered by agent's mcp_server_ids and enabled_tools
    mcp_manager = tool_executor.mcp_manager
    if mcp_manager:
        allowed_server_ids = None
        if agent and agent.mcp_server_ids is not None:
            try:
                allowed_server_ids = set(str(sid) for sid in agent.mcp_server_ids)
            except Exception:
                allowed_server_ids = set()

        # Build enabled_tools set for MCP tool-level filtering
        enabled_set = None
        if agent and agent.enabled_tools:
            enabled_set = set(agent.enabled_tools)

        for full_name, tool_info in mcp_manager.list_all_tools():
            # Resolve server_id for this tool via the manager's internal mapping
            server_id = mcp_manager._tool_to_server.get(full_name)
            if allowed_server_ids is not None and server_id is not None:
                if str(server_id) not in allowed_server_ids:
                    continue
            # Filter by enabled_tools if set (tool-level granularity)
            if enabled_set is not None and full_name not in enabled_set:
                continue
            tools_schema.append(tool_info.to_openai_tool(
                prefix=full_name[:len(full_name) - len(tool_info.name)]
            ))

    return filtered, tools_schema


async def _build_system_prompt_with_memories(
    db: AsyncSession,
    user_id: UUID,
    user_message: str,
    tools_list: list,
    agent: Agent | None = None,
    workspace_files: list | None = None,
) -> str:
    """Build system prompt with L1/L2/L3 memories and relevant skills injected."""
    memory_top_k = await _get_memory_top_k(db, user_id)
    memory_data = await MemoryService.get_memories_for_prompt(
        db, user_id, user_message, top_k=memory_top_k
    )

    # Load relevant skills — use agent-bound skills if agent has bindings
    if agent and agent.skills:
        matched_skills = agent.skills
    else:
        matched_skills = await SkillService.get_skills_for_prompt(
            db, user_id, user_message, top_k=3
        )

    # Load user portrait from profile
    from aio_agent_platform.db import UserProfile
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    user_profile = result.scalar_one_or_none()
    user_portrait = user_profile.personal_portrait if user_profile else None

    return build_system_prompt(
        tools=tools_list,
        persistent_memories=memory_data["l1_memories"],
        relevant_memories=memory_data["l2_memories"] + memory_data["l3_memories"],
        relevant_skills=matched_skills if matched_skills else None,
        agent_prompt=agent.system_prompt if agent else None,
        child_agents=agent.children if agent and agent.children else None,
        workspace_files=workspace_files,
        user_portrait=user_portrait,
    )


def _fire_memory_extraction(
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

    # Only extract if conversation has some substance
    if len(history) < 2:
        return

    # Skip trivial exchanges: very short user message or very short assistant reply
    # This filters out test messages like "111", "hello", "test", etc.
    if len(user_message.strip()) < 10 or len(assistant_output.strip()) < 20:
        return

    # Build message list for extraction (recent context + current exchange)
    messages = []
    for msg in history[-6:]:
        messages.append({"role": msg.role, "content": msg.content or ""})
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": assistant_output})

    task = asyncio.create_task(
        MemoryService.extract_memories_from_conversation(
            user_id=user_id,
            session_id=session_id,
            messages=messages,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _persist_assistant_message(
    session_id: UUID,
    user_id: UUID,
    content: str,
    tool_calls: list[dict] | None,
) -> None:
    """Save an assistant message on its own DB session.

    Used to rescue a turn that was interrupted (client disconnect, error)
    before the normal end-of-stream save: the in-memory tool_calls would
    otherwise be lost. Opens a fresh session because the generator's session
    is already torn down by the time the interruption handler runs.
    """
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


async def _update_context_summary(
    session_id: UUID,
    history: list[LLMMessage],
    user_message: str,
    assistant_output: str,
    provider,
) -> None:
    """Update session context_summary if conversation is long enough."""
    # Add current exchange to history for counting
    all_messages = list(history) + [
        LLMMessage(role="user", content=user_message),
        LLMMessage(role="assistant", content=assistant_output),
    ]

    # Only generate summary if conversation has enough substance
    if len(all_messages) < 10:
        return

    try:
        summary = await generate_summary(all_messages[-20:], provider)
        if summary:
            factory = get_session_factory()
            async with factory() as db:
                from sqlalchemy import update
                await db.execute(
                    update(Session)
                    .where(Session.id == session_id)
                    .values(context_summary=summary)
                )
                await db.commit()
                logger.info(f"Context summary updated for session {session_id}: {len(summary)} chars")
    except Exception:
        logger.exception(f"Failed to update context summary for session {session_id}")


async def _load_conversation_history(
    db: AsyncSession,
    session_id: UUID,
    limit: int | None = None,
    provider_type: str = "openai",
    allow_images: bool = True,
) -> tuple[list[LLMMessage], str | None]:
    """Load recent messages from DB as LLMMessage list, plus session context_summary.

    Adaptive loading: if no explicit limit is given, uses the configured soft limit
    and further reduces it if the loaded messages exceed the history token budget.

    If the message has attachments, multimodal content is re-hydrated using
    base64 data URIs downloaded from object storage, so the LLM always
    receives image bytes regardless of URL accessibility.

    When ``allow_images`` is False (the model is not multimodal), image
    attachments are skipped entirely so non-vision models never receive image
    blocks.

    Returns:
        (messages, context_summary): The loaded messages and any existing summary.
    """
    soft_limit = limit or settings.agent.context_history_soft_limit

    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(soft_limit)
    )
    messages = list(reversed(result.scalars().all()))

    # Load context_summary from session
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
                    # Re-hydrate multimodal content from stored attachments.
                    # Always uses base64 data URIs downloaded from object storage.
                    content = build_user_content(
                        text=msg.content or "",
                        attachments=msg.attachments,
                        provider_type=provider_type,
                        to_data_uri=attachment_storage.to_data_uri,
                    )
                else:
                    # Non-multimodal: include image URLs so model can invoke OCR tools
                    refs = build_image_url_refs(msg.attachments)
                    text = msg.content or ""
                    content = f"{text}\n\n{refs}" if text and refs else (text or refs)
            else:
                content = msg.content or ""

            # Reconstruct tool_call history so the LLM can resume with full context
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
                # Append tool result messages so the LLM sees execution outcomes
                for tc in stored_tool_calls:
                    if isinstance(tc, dict) and "result" in tc and tc["result"]:
                        result = tc["result"]
                        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
                        llm_messages.append(LLMMessage(
                            role="tool",
                            content=result_str,
                            tool_call_id=tc.get("id", ""),
                        ))
            else:
                llm_messages.append(LLMMessage(role=msg.role, content=content))

    # Adaptive: if loaded messages exceed history budget, reduce further
    budget = ContextBudget.from_settings()
    from aio_agent_platform.core.context import estimate_messages_tokens as _est

    total = _est(llm_messages)
    if total > budget.history_budget and len(llm_messages) > 4:
        # Walk from newest, keep only what fits
        kept: list[LLMMessage] = []
        used = 0
        for m in reversed(llm_messages):
            mt = _est([m])
            if used + mt > budget.history_budget:
                break
            kept.append(m)
            used += mt
        kept.reverse()
        logger.info(
            "Adaptive history load: %d -> %d messages (~%d -> ~%d tokens, budget %d)",
            len(llm_messages),
            len(kept),
            total,
            used,
            budget.history_budget,
        )
        llm_messages = kept

    return llm_messages, context_summary


async def _build_agent_loop(
    tool_executor: ToolExecutor,
    system_prompt: str,
    db: AsyncSession,
    agent_model_id: UUID | None = None,
    agent_temperature: float | None = None,
    agent_max_iterations: int | None = None,
    agent_enable_retry: bool = True,
    delegation: DelegationContext | None = None,
    event_queue: asyncio.Queue | None = None,
    workspace_id: UUID | None = None,
) -> AgentLoop:
    """Create an AgentLoop instance using the specified or default model from DB.

    Raises HTTPException if no model is available.
    """
    # Try agent's model first, then global default
    model_to_use = None
    if agent_model_id:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.id == agent_model_id, LLMModel.is_active == True)
        )
        model_to_use = result.scalar_one_or_none()

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
            detail="没有可用的模型，请在管理后台配置模型并为智能体绑定模型",
        )

    provider = create_provider(
        provider=model_to_use.provider.provider_type,
        model=model_to_use.model_name,
        base_url=model_to_use.provider.base_url,
        api_key=model_to_use.provider.api_key_encrypted,
        temperature=agent_temperature if agent_temperature is not None else settings.llm.temperature,
        enable_retry=agent_enable_retry,
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
    )


# ---- REST Chat ----


@router.post("/attachments", response_model=AttachmentOut, status_code=201)
async def upload_chat_attachment(
    user: CurrentUser,
    file: UploadFile = File(...),
    session_id: UUID | None = Form(None),
) -> AttachmentOut:
    """Upload an image attachment for a future chat message.

    - Mime: image/jpeg, image/png, image/gif, image/webp (detected from magic bytes)
    - Size: ≤ 10 MB (post-magic-byte validation)
    - Returns a stable public URL for frontend <img> preview and
      for LLM providers that accept URL sources.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large (max {MAX_BYTES // (1024 * 1024)} MB)",
        )

    # Detect real MIME from magic bytes — do not trust the browser-declared
    # content-type, which is often wrong (e.g. a JPEG saved with .png extension).
    detected_mime = _detect_image_mime(data)
    if detected_mime is None or detected_mime not in ALLOWED_MIME:
        declared = (file.content_type or "unknown").lower()
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported image format (declared: {declared}, detected: {detected_mime or 'none'})",
        )
    mime = detected_mime

    # Compress large images to reduce base64 payload size for LLM API calls
    original_size = len(data)
    data, mime = _compress_image(data, mime)

    storage = ChatAttachmentStorage()
    filename = (file.filename or "image")[:256]
    key = storage.make_key(
        user_id=str(user.id),
        session_id=str(session_id) if session_id else None,
        filename=filename,
    )
    storage.put(key, data, mime)
    url = storage.get_public_url(key)
    logger.info(
        "chat_attachment_uploaded",
        user_id=str(user.id),
        session_id=str(session_id) if session_id else None,
        key=key,
        mime=mime,
        size=len(data),
        original_size=original_size,
        compressed=original_size != len(data),
    )
    return AttachmentOut(
        key=key, url=url, mime=mime, size=len(data), filename=filename,
    )


# Large file upload settings
_MAX_FILE_BYTES = 500 * 1024 * 1024  # 500 MB
_MAX_WORKSPACE_BYTES = 500 * 1024 * 1024  # 500 MB per workspace


def _detect_mime_type(data: bytes, filename: str) -> str:
    """Detect MIME type from magic bytes, falling back to extension."""
    # Check for common magic bytes
    if not data:
        return "application/octet-stream"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and len(data) > 12 and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    if data[:2] == b"PK":
        return "application/zip"
    if data[:4] == b"\x50\x4b\x03\x04":
        return "application/zip"
    if data[:2] == b"\x1f\x8b":
        return "application/gzip"
    # Fallback: guess from extension
    import mimetypes
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


@router.post("/files", response_model=FileAttachmentOut, status_code=201)
async def upload_workspace_file(
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    session_id: UUID = Form(...),
) -> FileAttachmentOut:
    """Upload a file to the workspace for agent processing.

    The file is stored in MinIO and immediately injected into any active
    sandbox for the session, so the agent can access it right away.
    """
    from uuid import uuid4

    # Resolve workspace_id from session
    session_result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    workspace_id = await _resolve_workspace_id(db, session, user.id)

    # Read file data
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大 (最大 {_MAX_FILE_BYTES // (1024 * 1024)} MB)",
        )

    # Detect MIME type
    filename = (file.filename or "file")[:256]
    mime = _detect_mime_type(data, filename)

    # Check workspace quota
    obj_storage = ObjectStorage()
    ws_storage = WorkspaceStorage(obj_storage)
    _, total_size = ws_storage.get_workspace_stats(str(workspace_id))
    if total_size + len(data) > _MAX_WORKSPACE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"工作区空间不足 (已用 {total_size / (1024*1024):.1f} MB, "
                    f"上限 {_MAX_WORKSPACE_BYTES // (1024 * 1024)} MB)",
        )

    # Store file in MinIO under workspace uploads prefix
    # Use short uuid prefix to avoid collisions while preserving original name
    file_id = uuid4().hex[:8]
    safe_filename = filename.replace("/", "_").replace("\\", "_")
    workspace_path = f"uploads/{file_id}_{safe_filename}"
    ws_storage.put_file(str(workspace_id), workspace_path, data)

    # Inject file into active sandbox if one exists for this session
    try:
        tool_executor = request.app.state.tool_executor
        sandbox_mgr = tool_executor.sandbox_mgr
        key = sandbox_mgr._key(str(workspace_id), str(session_id))
        if key in sandbox_mgr._active:
            sandbox = sandbox_mgr._active[key]
            import base64
            b64_data = base64.b64encode(data).decode()
            cmd = (
                f"mkdir -p /workspace/uploads && "
                f"echo '{b64_data}' | base64 -d > /workspace/{workspace_path}"
            )
            await sandbox_mgr.execute(sandbox, cmd)
            logger.info(
                "workspace_file_injected_to_sandbox",
                workspace_path=workspace_path,
                size=len(data),
            )
    except Exception as e:
        logger.warning("workspace_file_inject_failed", error=str(e))

    # Generate presigned URL for download
    url = ws_storage.presign_download(str(workspace_id), workspace_path)

    logger.info(
        "workspace_file_uploaded",
        user_id=str(user.id),
        session_id=str(session_id),
        workspace_id=str(workspace_id),
        filename=filename,
        mime=mime,
        size=len(data),
        workspace_path=workspace_path,
    )

    return FileAttachmentOut(
        file_id=file_id,
        key=ws_storage._object_key(str(workspace_id), workspace_path),
        url=url,
        filename=filename,
        mime=mime,
        size=len(data),
        workspace_path=workspace_path,
    )


@router.post("", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChatResponse:
    """Non-streaming chat endpoint."""
    tool_executor: ToolExecutor = request.app.state.tool_executor

    # Load agent
    agent = await _load_agent(db, req.agent_id)
    if agent:
        current_agent_id.set(str(agent.id))

    # Get or create session
    session = None
    if req.session_id:
        result = await db.execute(
            select(Session).where(Session.id == req.session_id, Session.user_id == user.id)
        )
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    if not session:
        # Auto-create session
        session = Session(user_id=user.id, agent_id=req.agent_id, title=req.message[:100])
        db.add(session)
        await db.flush()

    # Resolve model info for provider-specific content formatting
    agent_model_id = agent.model_id if agent else None
    agent_temperature = agent.temperature if agent else None
    agent_max_iterations = agent.max_iterations if agent else None
    agent_enable_retry = agent.enable_retry if agent else True
    provider_type_for_content = "openai"
    _resolved_model = None
    if agent_model_id:
        model_result = await db.execute(
            select(LLMModel).options(selectinload(LLMModel.provider)).where(LLMModel.id == agent_model_id)
        )
        _resolved_model = model_result.scalar_one_or_none()
    if not _resolved_model:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_default == True, LLMModel.is_active == True)
            .limit(1)
        )
        _resolved_model = result.scalar_one_or_none()
    if _resolved_model and _resolved_model.provider:
        provider_type_for_content = _resolve_provider_type(_resolved_model.provider.provider_type)
    allow_images = bool(_resolved_model and _resolved_model.is_multimodal)

    # Load conversation history (adaptive + context_summary + multimodal re-hydration)
    history, context_summary = await _load_conversation_history(
        db, session.id,
        provider_type=provider_type_for_content,
        allow_images=allow_images,
    )

    # Build system prompt with memories (using agent config)
    tools_list, tools_schema = _filter_tools_by_agent(tool_executor, agent)
    system_prompt = await _build_system_prompt_with_memories(
        db, user.id, req.message, tools_list, agent=agent,
        workspace_files=_file_refs_to_dicts(req.file_attachments),
    )

    # Build agent loop (using agent's model)
    workspace_id = await _resolve_workspace_id(db, session, user.id)
    agent_loop = await _build_agent_loop(
        tool_executor, system_prompt, db,
        agent_model_id=agent_model_id,
        agent_temperature=agent_temperature,
        agent_max_iterations=agent_max_iterations,
        agent_enable_retry=agent_enable_retry,
        workspace_id=workspace_id,
    )

    # Convert Pydantic AttachmentOut objects to dicts for JSONB storage and build_user_content
    _attachments_data = [a.model_dump() for a in req.attachments] if req.attachments else None
    _file_attachments_data = _file_refs_to_dicts(req.file_attachments)

    # Inject file references into user message for LLM context
    user_message_for_llm = _inject_file_refs_into_message(req.message, req.file_attachments)

    # Build multimodal user content for this turn (always uses base64 for images).
    # Non-multimodal models receive image URL references instead of image blocks.
    attachment_storage = ChatAttachmentStorage()
    if allow_images:
        user_content = build_user_content(
            text=user_message_for_llm,
            attachments=_attachments_data,
            provider_type=provider_type_for_content,
            to_data_uri=attachment_storage.to_data_uri,
        )
    else:
        refs = build_image_url_refs(_attachments_data)
        user_content = f"{user_message_for_llm}\n\n{refs}" if refs else user_message_for_llm

    # Prepare context with compression
    prepared_messages, _summary_generated = await prepare_context(
        system_prompt=system_prompt,
        history=history,
        user_input=user_content if isinstance(user_content, str) else user_message_for_llm,
        provider=agent_loop.provider,
        existing_summary=context_summary,
    )

    # Extract the processed history from prepared_messages (minus system + user input)
    # We pass prepared history to agent loop which will rebuild its own messages
    processed_history = [m for m in prepared_messages if m.role != "system" and not (m.role == "user" and (m.content == user_message_for_llm or m.content == user_content))]

    # Save user message
    user_msg = Message(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=req.message,
        attachments=_attachments_data,
        file_attachments=_file_attachments_data,
    )
    db.add(user_msg)
    await db.flush()

    # Run agent loop with overflow retry
    final_output = ""
    tool_calls_list: list[dict] = []
    tool_results_map: dict[str, dict] = {}
    incremental_msg_id: UUID | None = None

    async def _save_tool_calls_incremental() -> None:
        """Incrementally persist tool calls after each result."""
        nonlocal incremental_msg_id
        if incremental_msg_id is None:
            inc_msg = Message(
                session_id=session.id,
                user_id=user.id,
                role="assistant",
                content="",
                tool_calls=list(tool_calls_list),
            )
            db.add(inc_msg)
            await db.flush()
            incremental_msg_id = inc_msg.id
        else:
            await db.execute(
                sql_update(Message)
                .where(Message.id == incremental_msg_id)
                .values(tool_calls=list(tool_calls_list))
            )
        await db.commit()

    async def _handle_event(event) -> None:
        """Parse agent events and track tool calls/results."""
        nonlocal final_output
        if isinstance(event, AgentStep) and event.done:
            final_output = event.final_output
        elif isinstance(event, str):
            if event.startswith("tool_call:"):
                parts = event.split(":", 3)
                tc_id = parts[1] if len(parts) > 1 else ""
                tc_name = parts[2] if len(parts) > 2 else ""
                try:
                    tc_args = json.loads(parts[3]) if len(parts) > 3 else {}
                except json.JSONDecodeError:
                    tc_args = {}
                tool_calls_list.append({
                    "id": tc_id,
                    "name": tc_name,
                    "arguments": tc_args,
                })
            elif event.startswith("tool_result:"):
                parts = event.split(":", 4)
                tc_id = parts[1] if len(parts) > 1 else ""
                status = parts[3] if len(parts) > 3 else ""
                try:
                    preview = json.loads(parts[4]) if len(parts) > 4 else ""
                except json.JSONDecodeError:
                    preview = parts[4] if len(parts) > 4 else ""
                tool_results_map[tc_id] = {"status": status, "preview": preview}
                # Merge results into tool_calls and persist incrementally
                for tc in tool_calls_list:
                    if tc["id"] in tool_results_map:
                        tc["result"] = tool_results_map[tc["id"]]
                await _save_tool_calls_incremental()

    try:
        async for event in agent_loop.run(
            user_input=user_content,
            user_id=user.id,
            session_id=session.id,
            conversation_history=processed_history,
            tools=tools_schema,
        ):
            await _handle_event(event)
    except Exception as e:
        if is_context_overflow_error(e):
            logger.warning(f"Context overflow in REST chat, emergency compress: {e}")
            emergency_history = emergency_compress(prepared_messages, level=1)
            emergency_hist = [m for m in emergency_history if m.role != "system" and not (m.role == "user" and (m.content == user_message_for_llm or m.content == user_content))]
            async for event in agent_loop.run(
                user_input=user_content,
                user_id=user.id,
                session_id=session.id,
                conversation_history=emergency_hist,
                tools=tools_schema,
            ):
                await _handle_event(event)
        else:
            raise

    # Save assistant message — update incremental row or create new
    if incremental_msg_id is not None:
        await db.execute(
            sql_update(Message)
            .where(Message.id == incremental_msg_id)
            .values(
                content=final_output,
                tool_calls=tool_calls_list if tool_calls_list else None,
            )
        )
        await db.flush()
        msg_id = incremental_msg_id
    else:
        assistant_msg = Message(
            session_id=session.id,
            user_id=user.id,
            role="assistant",
            content=final_output,
            tool_calls=tool_calls_list if tool_calls_list else None,
        )
        db.add(assistant_msg)
        await db.flush()
        msg_id = assistant_msg.id

    # Fire-and-forget memory extraction
    _fire_memory_extraction(
        user.id, session.id, history, req.message, final_output,
        enable=agent.enable_memory_extraction if agent else True,
    )

    # Fire-and-forget context summary update
    summary_task = asyncio.create_task(
        _update_context_summary(
            session.id, history, req.message, final_output, agent_loop.provider
        )
    )
    _background_tasks.add(summary_task)
    summary_task.add_done_callback(_background_tasks.discard)

    return ChatResponse(
        session_id=session.id,
        message_id=msg_id,
        content=final_output,
        tool_calls_count=len(tool_calls_list),
    )


# ---- SSE Streaming Chat ----


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Streaming chat via Server-Sent Events."""
    import time
    t_start = time.monotonic()
    tool_executor: ToolExecutor = request.app.state.tool_executor

    logger.info(
        "stream_request",
        user_id=str(user.id),
        session_id=str(req.session_id) if req.session_id else None,
        agent_id=str(req.agent_id) if req.agent_id else None,
        message_length=len(req.message),
        message_preview=req.message[:100],
    )

    # Load agent
    agent = await _load_agent(db, req.agent_id)
    if agent:
        current_agent_id.set(str(agent.id))
    logger.debug(
        "stream_agent_loaded",
        agent_id=str(agent.id) if agent else None,
        agent_name=agent.name if agent else None,
        has_children=len(agent.children) if agent and agent.children else 0,
    )

    # Get or create session
    session = None
    if req.session_id:
        result = await db.execute(
            select(Session).where(Session.id == req.session_id, Session.user_id == user.id)
        )
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

    if not session:
        session = Session(user_id=user.id, agent_id=req.agent_id, title=req.message[:100])
        db.add(session)
        await db.flush()
        await db.commit()
        logger.info(
            "stream_session_created",
            session_id=str(session.id),
            user_id=str(user.id),
        )
    else:
        logger.debug(
            "stream_session_reused",
            session_id=str(session.id),
        )

    session_id = session.id

    # Resolve model/provider info for provider-specific content formatting
    agent_model_id = agent.model_id if agent else None
    agent_temperature = agent.temperature if agent else None
    agent_max_iterations = agent.max_iterations if agent else None
    agent_enable_retry = agent.enable_retry if agent else True
    provider_type_for_content = "openai"
    _resolved_model = None
    if agent_model_id:
        model_result = await db.execute(
            select(LLMModel).options(selectinload(LLMModel.provider)).where(LLMModel.id == agent_model_id)
        )
        _resolved_model = model_result.scalar_one_or_none()
    if not _resolved_model:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_default == True, LLMModel.is_active == True)
            .limit(1)
        )
        _resolved_model = result.scalar_one_or_none()
    if _resolved_model and _resolved_model.provider:
        provider_type_for_content = _resolve_provider_type(_resolved_model.provider.provider_type)
    allow_images = bool(_resolved_model and _resolved_model.is_multimodal)

    # Convert Pydantic AttachmentOut objects to dicts for JSONB storage
    _attachments_data = [a.model_dump() for a in req.attachments] if req.attachments else None
    _file_attachments_data = _file_refs_to_dicts(req.file_attachments)
    user_msg = Message(
        session_id=session_id,
        user_id=user.id,
        role="user",
        content=req.message,
        attachments=_attachments_data,
        file_attachments=_file_attachments_data,
    )
    db.add(user_msg)
    await db.commit()

    async def event_generator():
        try:
            # Get a fresh DB session for this generator
            factory = get_session_factory()
            async with factory() as gen_db:
                # Load conversation history (adaptive + context_summary)
                logger.debug(
                    "stream_loading_history",
                    session_id=str(session_id),
                )
                history, context_summary = await _load_conversation_history(
                    gen_db, session_id,
                    provider_type=provider_type_for_content,
                    allow_images=allow_images,
                )
                logger.info(
                    "stream_history_loaded",
                    session_id=str(session_id),
                    history_count=len(history),
                    has_summary=context_summary is not None,
                )

                # Build tools schema + system prompt with memories (using agent config)
                logger.debug("stream_building_prompt", session_id=str(session_id))
                tools_list, tools_schema = _filter_tools_by_agent(tool_executor, agent)
                system_prompt = await _build_system_prompt_with_memories(
                    gen_db, user.id, req.message, tools_list, agent=agent,
                    workspace_files=_file_refs_to_dicts(req.file_attachments),
                )
                logger.info(
                    "stream_prompt_built",
                    session_id=str(session_id),
                    tools_count=len(tools_list),
                    prompt_length=len(system_prompt),
                )

                # Resolve workspace first (needed by delegation context)
                agent_model_id = agent.model_id if agent else None
                agent_temperature = agent.temperature if agent else None
                agent_max_iterations = agent.max_iterations if agent else None
                workspace_id = await _resolve_workspace_id(gen_db, session, user.id)
                # Commit workspace_id so it's persisted
                await gen_db.commit()

                # Build delegation context if agent has children
                delegation = None
                event_queue = asyncio.Queue()  # Always create for confirmations + delegation
                if agent and agent.children:
                    delegation = DelegationContext(
                        parent_agent_id=agent.id,
                        delegation_depth=0,
                        max_depth=settings.agent.max_delegation_depth,
                        event_queue=event_queue,
                        workspace_id=workspace_id,
                    )

                # Build agent loop (using agent's model)
                agent_loop = await _build_agent_loop(
                    tool_executor, system_prompt, gen_db,
                    agent_model_id=agent_model_id,
                    agent_temperature=agent_temperature,
                    agent_max_iterations=agent_max_iterations,
                    agent_enable_retry=agent_enable_retry,
                    delegation=delegation,
                    event_queue=event_queue,
                    workspace_id=workspace_id,
                )
                logger.info(
                    "stream_agent_loop_ready",
                    session_id=str(session_id),
                    model=agent_loop.provider.model if hasattr(agent_loop.provider, 'model') else 'unknown',
                    max_iterations=agent_loop.max_iterations,
                )

                # Inject file references into user message for LLM context
                _user_message_for_llm = _inject_file_refs_into_message(req.message, req.file_attachments)

                # Build multimodal user content for this turn (always uses base64 for images).
                # Non-multimodal models receive image URL references instead of image blocks.
                attachment_storage = ChatAttachmentStorage()
                if allow_images:
                    user_content = build_user_content(
                        text=_user_message_for_llm,
                        attachments=_attachments_data,
                        provider_type=provider_type_for_content,
                        to_data_uri=attachment_storage.to_data_uri,
                    )
                else:
                    refs = build_image_url_refs(_attachments_data)
                    user_content = f"{_user_message_for_llm}\n\n{refs}" if refs else _user_message_for_llm

                # Prepare context with compression
                logger.debug("stream_preparing_context", session_id=str(session_id))
                prepared_messages, _summary_generated = await prepare_context(
                    system_prompt=system_prompt,
                    history=history,
                    user_input=user_content if isinstance(user_content, str) else _user_message_for_llm,
                    provider=agent_loop.provider,
                    existing_summary=context_summary,
                )
                processed_history = [
                    m for m in prepared_messages
                    if m.role != "system" and not (m.role == "user" and (m.content == _user_message_for_llm or m.content == user_content))
                ]
                logger.debug(
                    "stream_context_prepared",
                    session_id=str(session_id),
                    message_count=len(prepared_messages),
                    history_count=len(processed_history),
                )

                final_output = ""
                tool_calls_list: list[dict] = []
                saved_flag = {"done": False}  # guards against double-save on rescue
                incremental_msg_id: UUID | None = None  # track message for incremental updates
                tool_results_map: dict[str, dict] = {}  # tool_call_id -> result info
                # Track delegation details for persistence
                delegations_map: dict[str, dict] = {}  # tool_call_id -> delegation data
                # Track confirmation cards (AskUserQuestion) in arrival order for persistence
                confirmations_order: list[str] = []  # confirmation_id, in arrival order
                confirmations_map: dict[str, dict] = {}  # confirmation_id -> card data

                # Send session_id first so frontend knows which session
                yield _sse_event({"type": "session", "session_id": str(session_id)})
                logger.info("stream_started", session_id=str(session_id))

                iteration_count = 0
                # delegation_id → tool_call_id mapping for embedding delegation details
                delegation_to_toolcall: dict[str, str] = {}

                def _track_delegation_event(evt: dict) -> None:
                    """Track delegation events for persistence in message tool_calls."""
                    evt_type = evt.get("type", "")
                    del_id = evt.get("delegation_id", "")

                    if evt_type == "delegation_start":
                        tc_id = evt.get("tool_call_id", "")
                        if tc_id:
                            delegation_to_toolcall[del_id] = tc_id
                        delegations_map[tc_id] = {
                            "delegation_id": del_id,
                            "child_agent_id": evt.get("child_agent_id", ""),
                            "child_agent_name": evt.get("child_agent_name", ""),
                            "child_agent_icon": evt.get("child_agent_icon", ""),
                            "task": evt.get("task", ""),
                            "status": "running",
                            "thinking": "",
                            "toolCalls": [],
                            "result": "",
                        }
                    elif evt_type == "delegation_thinking":
                        tc_id = delegation_to_toolcall.get(del_id, "")
                        if tc_id and tc_id in delegations_map:
                            delegations_map[tc_id]["thinking"] += evt.get("content", "")
                    elif evt_type == "delegation_tool_call":
                        tc_id = delegation_to_toolcall.get(del_id, "")
                        if tc_id and tc_id in delegations_map:
                            delegations_map[tc_id]["toolCalls"].append({
                                "id": evt.get("id", ""),
                                "name": evt.get("name", ""),
                                "arguments": evt.get("arguments", {}),
                            })
                    elif evt_type == "delegation_tool_result":
                        tc_id = delegation_to_toolcall.get(del_id, "")
                        if tc_id and tc_id in delegations_map:
                            for d_tc in delegations_map[tc_id]["toolCalls"]:
                                if d_tc["id"] == evt.get("tool_call_id", ""):
                                    d_tc["result"] = {
                                        "status": evt.get("status", ""),
                                        "preview": evt.get("preview", ""),
                                    }
                                    break
                    elif evt_type == "delegation_text_delta":
                        tc_id = delegation_to_toolcall.get(del_id, "")
                        if tc_id and tc_id in delegations_map:
                            delegations_map[tc_id]["result"] += evt.get("content", "")
                    elif evt_type == "delegation_end":
                        tc_id = delegation_to_toolcall.get(del_id, "")
                        if tc_id and tc_id in delegations_map:
                            delegations_map[tc_id]["status"] = evt.get("status", "completed")
                            delegations_map[tc_id]["duration_ms"] = evt.get("duration_ms", 0)
                            delegations_map[tc_id]["error"] = evt.get("error")
                            # Use result from delegation_end if result is empty
                            if not delegations_map[tc_id]["result"]:
                                delegations_map[tc_id]["result"] = evt.get("result_preview", "")

                    elif evt_type == "confirmation_required":
                        c_id = evt.get("confirmation_id", "")
                        if c_id:
                            confirmations_order.append(c_id)
                            confirmations_map[c_id] = {
                                "confirmation_id": c_id,
                                "question": evt.get("question", ""),
                                "mode": evt.get("mode", ""),
                                "options": evt.get("options") or [],
                                "table_schema": evt.get("table_schema"),
                                "context": evt.get("context") or {},
                            }
                    elif evt_type == "confirmation_resolved":
                        c_id = evt.get("confirmation_id", "")
                        if c_id and c_id in confirmations_map:
                            confirmations_map[c_id]["resolved"] = {
                                "status": evt.get("status", ""),
                                "selected_options": evt.get("selected_options") or [],
                                "user_input": evt.get("user_input"),
                                "table_data": evt.get("table_data"),
                                "resolved_at": evt.get("resolved_at"),
                            }

                def _merge_tool_metadata() -> None:
                    """Fold results/delegation/confirmation data into tool_calls_list.

                    Idempotent — safe to call from the normal completion path and
                    again from an interruption handler.
                    """
                    for tc in tool_calls_list:
                        if tc["id"] in tool_results_map:
                            tc["result"] = tool_results_map[tc["id"]]
                        if tc["name"] == "delegate_task" and tc["id"] in delegations_map:
                            tc["delegation"] = delegations_map[tc["id"]]
                    ask_tcs = [tc for tc in tool_calls_list if tc["name"] == "AskUserQuestion"]
                    for tc, c_id in zip(ask_tcs, confirmations_order):
                        tc["confirmation"] = confirmations_map.get(c_id)

                async def _rescue_partial() -> None:
                    """Persist whatever tool calls accumulated before an interruption."""
                    if saved_flag["done"]:
                        return
                    if incremental_msg_id is not None:
                        return  # already incrementally persisted in the loop
                    _merge_tool_metadata()
                    await _persist_assistant_message(
                        session_id, user.id, final_output, tool_calls_list,
                    )

                async for event in agent_loop.run(
                    user_input=user_content,
                    user_id=user.id,
                    session_id=session_id,
                    conversation_history=processed_history,
                    tools=tools_schema,
                ):
                    # Drain events from event_queue (delegation + confirmation events)
                    while not event_queue.empty():
                        del_event = event_queue.get_nowait()
                        logger.debug(
                            "stream_queue_event",
                            session_id=str(session_id),
                            event_type=del_event.get("type", "unknown"),
                        )
                        _track_delegation_event(del_event)
                        yield _sse_event(del_event)

                    if isinstance(event, str):
                        if event.startswith("confirmation_flow:"):
                            # AskUserQuestion 内部信号：确认请求已推入 event_queue，
                            # 上面的 drain 已经将其发送给前端。
                            # 不转发此事件，agent_loop 会 resume 等待用户响应。
                            logger.info(
                                "stream_confirmation_flow",
                                session_id=str(session_id),
                                detail=event,
                            )
                            continue
                        elif event.startswith("delegation_heartbeat:"):
                            # Heartbeat signal during delegation: drain
                            # event_queue (handled at top of loop). Forward
                            # a keepalive so frontend knows delegation is alive.
                            yield _sse_event({
                                "type": "delegation_heartbeat",
                            })
                        elif event.startswith("reasoning:"):
                            # Bulk reasoning (thinking before tool calls)
                            content = event[len("reasoning:"):]
                            logger.debug(
                                "stream_reasoning",
                                session_id=str(session_id),
                                length=len(content),
                            )
                            yield _sse_event({
                                "type": "thinking",
                                "content": content,
                            })
                        elif event.startswith("text_delta:"):
                            # Final answer text streaming
                            delta = event[len("text_delta:"):]
                            final_output += delta
                            yield _sse_event({
                                "type": "text_delta",
                                "content": delta,
                            })
                        elif event.startswith("tool_call:"):
                            # Format: tool_call:id:name:args_json
                            parts = event.split(":", 3)
                            tc_id = parts[1] if len(parts) > 1 else ""
                            tc_name = parts[2] if len(parts) > 2 else ""
                            try:
                                tc_args = json.loads(parts[3]) if len(parts) > 3 else {}
                            except json.JSONDecodeError:
                                tc_args = {}
                            logger.info(
                                "stream_tool_call",
                                session_id=str(session_id),
                                tool_call_id=tc_id,
                                tool_name=tc_name,
                                args_preview=str(tc_args)[:200],
                            )
                            tool_calls_list.append({
                                "id": tc_id,
                                "name": tc_name,
                                "arguments": tc_args,
                            })
                            yield _sse_event({
                                "type": "tool_call",
                                "id": tc_id,
                                "name": tc_name,
                                "arguments": tc_args,
                            })
                        elif event.startswith("tool_result:"):
                            # Format: tool_result:id:name:status:output_json
                            parts = event.split(":", 4)
                            tc_id = parts[1] if len(parts) > 1 else ""
                            tc_name = parts[2] if len(parts) > 2 else ""
                            status = parts[3] if len(parts) > 3 else ""
                            try:
                                preview = json.loads(parts[4]) if len(parts) > 4 else ""
                            except json.JSONDecodeError:
                                preview = parts[4] if len(parts) > 4 else ""
                            logger.info(
                                "stream_tool_result",
                                session_id=str(session_id),
                                tool_call_id=tc_id,
                                tool_name=tc_name,
                                status=status,
                                preview_length=len(str(preview)),
                            )
                            tool_results_map[tc_id] = {
                                "status": status,
                                "preview": preview,
                            }
                            yield _sse_event({
                                "type": "tool_result",
                                "tool_call_id": tc_id,
                                "name": tc_name,
                                "status": status,
                                "preview": preview,
                            })
                            # Incrementally persist tool calls so they survive process crashes
                            _merge_tool_metadata()
                            if incremental_msg_id is None:
                                inc_msg = Message(
                                    session_id=session_id,
                                    user_id=user.id,
                                    role="assistant",
                                    content="",
                                    tool_calls=list(tool_calls_list),
                                )
                                gen_db.add(inc_msg)
                                await gen_db.flush()
                                incremental_msg_id = inc_msg.id
                            else:
                                await gen_db.execute(
                                    sql_update(Message)
                                    .where(Message.id == incremental_msg_id)
                                    .values(tool_calls=list(tool_calls_list))
                                )
                            await gen_db.commit()
                    elif isinstance(event, AgentStep):
                        iteration_count += 1
                        if event.done:
                            # If no text_delta events were sent, use final_output
                            if not final_output:
                                final_output = event.final_output
                            elapsed = (time.monotonic() - t_start) * 1000
                            logger.info(
                                "stream_agent_done",
                                session_id=str(session_id),
                                output_length=len(final_output),
                                iterations=iteration_count,
                                tool_calls_count=len(tool_calls_list),
                                elapsed_ms=round(elapsed, 2),
                            )

                # Drain any remaining events (delegation + confirmation)
                while not event_queue.empty():
                    del_event = event_queue.get_nowait()
                    logger.debug(
                        "stream_queue_event_tail",
                        session_id=str(session_id),
                        event_type=del_event.get("type", "unknown"),
                    )
                    _track_delegation_event(del_event)
                    yield _sse_event(del_event)

                # Merge tool results / delegation / confirmation metadata into
                # tool_calls_list (also used by the interruption handlers below).
                _merge_tool_metadata()

                # Send final text
                yield _sse_event({"type": "text", "content": final_output})

                # Save assistant message — update incremental row or create new
                logger.debug(
                    "stream_saving_message",
                    session_id=str(session_id),
                    content_length=len(final_output),
                    tool_calls_count=len(tool_calls_list),
                )
                if incremental_msg_id is not None:
                    await gen_db.execute(
                        sql_update(Message)
                        .where(Message.id == incremental_msg_id)
                        .values(
                            content=final_output,
                            tool_calls=tool_calls_list if tool_calls_list else None,
                        )
                    )
                    await gen_db.commit()
                    msg_id = str(incremental_msg_id)
                else:
                    assistant_msg = Message(
                        session_id=session_id,
                        user_id=user.id,
                        role="assistant",
                        content=final_output,
                        tool_calls=tool_calls_list if tool_calls_list else None,
                    )
                    gen_db.add(assistant_msg)
                    await gen_db.commit()
                    msg_id = str(assistant_msg.id)
                saved_flag["done"] = True

                # Fire-and-forget memory extraction
                logger.debug("stream_triggering_memory_extraction", session_id=str(session_id))
                _fire_memory_extraction(
                    user.id, session_id, history, req.message, final_output,
                    enable=agent.enable_memory_extraction if agent else True,
                )

                # Fire-and-forget context summary update
                summary_task = asyncio.create_task(
                    _update_context_summary(
                        session_id, history, req.message, final_output, agent_loop.provider
                    )
                )
                _background_tasks.add(summary_task)
                summary_task.add_done_callback(_background_tasks.discard)
                total_elapsed = (time.monotonic() - t_start) * 1000
                logger.info(
                    "stream_completed",
                    session_id=str(session_id),
                    message_id=msg_id,
                    user_id=str(user.id),
                    total_elapsed_ms=round(total_elapsed, 2),
                    output_length=len(final_output),
                    tool_calls_count=len(tool_calls_list),
                )

                # Done — include tool_calls so frontend can persist them
                yield _sse_event({
                    "type": "done",
                    "message_id": msg_id,
                    "content": final_output,
                    "tool_calls": tool_calls_list,
                })

        except asyncio.CancelledError:
            # Client disconnected — this is expected, not an error
            elapsed = (time.monotonic() - t_start) * 1000
            logger.info(
                "stream_cancelled",
                session_id=str(session_id),
                user_id=str(user.id),
                elapsed_ms=round(elapsed, 2),
                reason="client_disconnected",
            )
            # Rescue tool calls accumulated so far. Shield the save so it isn't
            # itself cancelled, then let the cancellation propagate.
            try:
                if "_rescue_partial" in locals():
                    await asyncio.shield(_rescue_partial())
            except Exception:
                logger.exception("stream_cancelled_rescue_failed", session_id=str(session_id))
            return
        except Exception as e:
            elapsed = (time.monotonic() - t_start) * 1000
            if is_context_overflow_error(e):
                logger.warning(
                    "stream_context_overflow",
                    session_id=str(session_id),
                    user_id=str(user.id),
                    elapsed_ms=round(elapsed, 2),
                    error=str(e),
                )
                try:
                    yield _sse_event({
                        "type": "warning",
                        "message": "对话上下文过长，正在压缩后重试...",
                    })
                except Exception:
                    pass
            else:
                logger.error(
                    "stream_error",
                    session_id=str(session_id),
                    user_id=str(user.id),
                    elapsed_ms=round(elapsed, 2),
                    error_type=type(e).__name__,
                    error=str(e),
                    exc_info=True,
                )
            try:
                yield _sse_event({"type": "error", "message": str(e)})
            except Exception:
                # Connection already closed, cannot send error event
                pass
            # Rescue tool calls accumulated before the error so history survives.
            try:
                if "_rescue_partial" in locals():
                    await _rescue_partial()
            except Exception:
                logger.exception("stream_error_rescue_failed", session_id=str(session_id))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- WebSocket Chat (legacy) ----


@router.websocket("/ws/{session_id}")
async def chat_websocket(
    websocket: WebSocket,
    session_id: UUID,
    token: str = Query(default=""),
) -> None:
    """Streaming chat via WebSocket."""
    # Authenticate via query parameter
    try:
        payload = decode_token(token)
        if payload.type != "access":
            await websocket.close(code=4001, reason="Invalid token type")
            return
        user_id = UUID(payload.sub)
    except (TokenExpiredError, Exception):
        await websocket.close(code=4001, reason="Authentication failed")
        return

    await websocket.accept()

    # Set RLS context
    current_user_id.set(str(user_id))

    # Get tool executor from app state
    tool_executor: ToolExecutor = websocket.app.state.tool_executor

    # Build tools schema
    tools_list = tool_executor.registry.list_tools()
    tools_schema = tool_executor.registry.to_openai_tools()

    # Get DB session
    factory = get_session_factory()
    async with factory() as db:
        # Verify session belongs to user
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        session = result.scalar_one_or_none()
        if not session:
            await websocket.send_json({"type": "error", "message": "Session not found"})
            await websocket.close(code=4004)
            return

        try:
            while True:
                # Receive message from client
                data = await websocket.receive_json()
                if data.get("type") != "message":
                    continue

                user_message = data.get("content", "")
                if not user_message.strip():
                    continue

                logger.info(f"收到消息: {user_message[:100]}")

                # Load conversation history (adaptive + context_summary)
                history, context_summary = await _load_conversation_history(db, session_id)
                logger.info(f"加载了 {len(history)} 条历史消息")

                # Build system prompt with memories (per-message for freshness)
                system_prompt = await _build_system_prompt_with_memories(
                    db, user_id, user_message, tools_list, agent=agent,
                    workspace_files=None,  # WebSocket doesn't support file attachments
                )

                # Save user message
                user_msg = Message(
                    session_id=session_id,
                    user_id=user_id,
                    role="user",
                    content=user_message,
                )
                db.add(user_msg)
                await db.flush()
                logger.info("用户消息已保存")

                # Build agent loop
                agent_model_id = agent.model_id if agent else None
                agent_temperature = agent.temperature if agent else None
                agent_max_iterations = agent.max_iterations if agent else None
                workspace_id = await _resolve_workspace_id(db, session, user_id)
                agent_loop = await _build_agent_loop(
                    tool_executor, system_prompt, db,
                    agent_model_id=agent_model_id,
                    agent_temperature=agent_temperature,
                    agent_max_iterations=agent_max_iterations,
                    agent_enable_retry=agent_enable_retry,
                    workspace_id=workspace_id,
                )
                logger.info("Agent loop 已创建，开始处理...")

                # Prepare context with compression
                prepared_messages, _summary_generated = await prepare_context(
                    system_prompt=system_prompt,
                    history=history,
                    user_input=user_message,
                    provider=agent_loop.provider,
                    existing_summary=context_summary,
                )
                processed_history = [
                    m for m in prepared_messages
                    if m.role != "system" and not (m.role == "user" and m.content == user_message)
                ]

                # Run agent loop, streaming events via WebSocket
                final_output = ""
                tool_calls_list: list[dict] = []
                event_count = 0

                try:
                    async for event in agent_loop.run(
                        user_input=user_message,
                        user_id=user_id,
                        session_id=session_id,
                        conversation_history=processed_history,
                        tools=tools_schema,
                    ):
                        event_count += 1
                        if isinstance(event, str):
                            if event.startswith("thinking:"):
                                await websocket.send_json(
                                    {
                                        "type": "thinking",
                                        "content": event[len("thinking:") :],
                                    }
                                )
                            elif event.startswith("tool_result:"):
                                parts = event.split(":", 3)
                                await websocket.send_json(
                                    {
                                        "type": "tool_result",
                                        "tool_call_id": parts[1] if len(parts) > 1 else "",
                                        "status": parts[2] if len(parts) > 2 else "",
                                        "preview": parts[3] if len(parts) > 3 else "",
                                    }
                                )
                        elif isinstance(event, AgentStep):
                            if event.tool_calls:
                                for tc in event.tool_calls:
                                    tool_calls_list.append(
                                        {
                                            "id": tc.id,
                                            "name": tc.name,
                                            "arguments": tc.arguments,
                                        }
                                    )
                                    await websocket.send_json(
                                        {
                                            "type": "tool_call",
                                            "id": tc.id,
                                            "name": tc.name,
                                            "arguments": tc.arguments,
                                        }
                                    )
                            if event.done:
                                final_output = event.final_output
                                logger.info(f"Agent loop 完成，输出长度: {len(final_output)}")

                    logger.info(f"Agent loop 结束，共处理 {event_count} 个事件")
                except Exception as e:
                    logger.error(f"Agent loop 执行出错: {e}", exc_info=True)
                    await websocket.send_json({"type": "error", "message": f"处理消息时出错: {e!s}"})
                    continue

                # Send final text output
                await websocket.send_json(
                    {
                        "type": "text",
                        "content": final_output,
                    }
                )
                logger.info("已发送最终文本")

                # Save assistant message
                assistant_msg = Message(
                    session_id=session_id,
                    user_id=user_id,
                    role="assistant",
                    content=final_output,
                    tool_calls=tool_calls_list if tool_calls_list else None,
                )
                db.add(assistant_msg)
                await db.commit()
                logger.info("助手消息已保存")

                # Fire-and-forget memory extraction
                _fire_memory_extraction(
                    user_id, session_id, history, user_message, final_output
                )

                # Done signal
                await websocket.send_json({"type": "done"})
                logger.info("已发送完成信号")

        except WebSocketDisconnect:
            logger.info("WebSocket 连接断开")
            pass
        except Exception as e:
            logger.error(f"WebSocket 处理出错: {e}", exc_info=True)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
