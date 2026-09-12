"""Room commands and durable state. All mutations serialize on the room row."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.core.chat import load_agent
from aio_agent_platform.db.models import (
    Agent,
    ChatRoom,
    ChatRoomMember,
    ChatRoomMessage,
    ChatRoomRun,
    ChatRoomTask,
    Session,
    User,
)
from aio_agent_platform.db.sanitize import sanitize_pg_text
from aio_agent_platform.rooms.schemas import RoomCreate, RoomRetry, RoomSend, RoomUpdate
from aio_agent_platform.workspaces.service import WorkspaceService

ACTIVE_RUNS = ("queued", "running", "stopping")
ACTIVE_TASKS = ("queued", "running", "waiting_user")
LEASE_SECONDS = 120
SUMMARY_PROMPT = (
    "请总结本聊天室目前的讨论，按讨论目标、已有共识、分歧及发言来源、待确认事项、"
    "建议下一步组织。没有充分依据时明确尚未达成共识；说明失败或未完成成员的缺失意见。"
    "只生成总结，不执行新的外部操作。"
)


def now() -> datetime:
    return datetime.now(UTC)


def touch(room: ChatRoom) -> None:
    room.revision += 1
    room.updated_at = now()


async def owned_room(db: AsyncSession, room_id: UUID, user: User, *, lock=False) -> ChatRoom:
    stmt = select(ChatRoom).where(
        ChatRoom.id == room_id, ChatRoom.user_id == user.id, ChatRoom.tenant_id == user.tenant_id,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    room = await db.scalar(stmt)
    if room is None:
        raise HTTPException(404, "聊天室不存在")
    return room


async def active_run(db: AsyncSession, room_id: UUID) -> ChatRoomRun | None:
    return await db.scalar(select(ChatRoomRun).where(
        ChatRoomRun.room_id == room_id, ChatRoomRun.status.in_(ACTIVE_RUNS),
    ))


async def expire_stale_run(db: AsyncSession, room: ChatRoom) -> None:
    """Called under the room lock; fences a dead worker without replaying its tools."""
    run = await active_run(db, room.id)
    if run is None or run.heartbeat_at > now() - timedelta(seconds=LEASE_SECONDS):
        return
    run.status = "interrupted"
    run.error = "执行服务中断，已保留已有结果；请检查工具动作后重试失败成员"
    run.completed_at = now()
    tasks = (await db.scalars(select(ChatRoomTask).where(ChatRoomTask.run_id == run.id))).all()
    for task in tasks:
        if task.status in ACTIVE_TASKS:
            task.status = "failed"
            task.error = run.error
            task.completed_at = now()
            task.confirmation = None
            if task.message_id:
                message = await db.get(ChatRoomMessage, task.message_id)
                if message:
                    message.status = "failed"
    touch(room)
    await db.flush()


async def require_idle(db: AsyncSession, room: ChatRoom) -> None:
    await expire_stale_run(db, room)
    if await active_run(db, room.id):
        raise HTTPException(409, "聊天室正在执行，请停止或等待结束后再操作")


def add_message(db: AsyncSession, room: ChatRoom, *, role: str, content: str,
                name="系统", **kwargs) -> ChatRoomMessage:
    room.message_sequence += 1
    message = ChatRoomMessage(
        id=uuid4(), room_id=room.id, sequence=room.message_sequence,
        role=role, name=name, content=sanitize_pg_text(content), **kwargs,
    )
    db.add(message)
    touch(room)
    return message


async def members(db: AsyncSession, room_id: UUID) -> list[ChatRoomMember]:
    return list((await db.scalars(select(ChatRoomMember).where(
        ChatRoomMember.room_id == room_id,
    ).order_by(ChatRoomMember.position))).all())


async def create_room(db: AsyncSession, user: User, req: RoomCreate) -> ChatRoom:
    agents = []
    for agent_id in req.agent_ids:
        agent = await load_agent(db, agent_id, user)
        if not agent:
            raise HTTPException(404, "所选智能体不存在或无权使用")
        agents.append(agent)
    workspace = await WorkspaceService.get_or_create_default(db=db, user_id=user.id)
    room_id = uuid4()
    roster = [ChatRoomMember(
        id=uuid4(), room_id=room_id, agent_id=a.id, name=a.name, icon=a.icon,
        description=a.description, position=i, is_active=True,
    ) for i, a in enumerate(agents)]
    default = next((m for m in roster if m.agent_id == req.default_agent_id), roster[0])
    room = ChatRoom(
        id=room_id, user_id=user.id, tenant_id=user.tenant_id, workspace_id=workspace.id,
        title=sanitize_pg_text(req.title), goal=sanitize_pg_text(req.goal), default_member_id=default.id,
        revision=0, message_sequence=0, summary_sequence=0, is_pinned=False, is_archived=False,
    )
    db.add_all([room, *roster, Session(
        id=room_id, user_id=user.id, source="room", workspace_id=workspace.id, title=req.title,
    )])
    add_message(db, room, role="system", content="创建聊天室，加入成员：" + "、".join(a.name for a in agents))
    await db.flush()
    return room


async def update_room(db: AsyncSession, room: ChatRoom, user: User, req: RoomUpdate) -> None:
    await require_idle(db, room)
    roster = await members(db, room.id)
    if req.agent_ids is not None:
        by_agent = {m.agent_id: m for m in roster}
        changes = []
        for position, agent_id in enumerate(req.agent_ids):
            member = by_agent.get(agent_id)
            if member is None or not member.is_active:
                agent = await load_agent(db, agent_id, user)
                if not agent:
                    raise HTTPException(404, "新增成员不可用或无权使用")
                if member is None:
                    member = ChatRoomMember(
                        id=uuid4(), room_id=room.id, agent_id=agent.id,
                        name=agent.name, icon=agent.icon, description=agent.description,
                    )
                    roster.append(member)
                    db.add(member)
                member.is_active = True
                changes.append(f"加入：{member.name}")
            member.position = position
        for member in roster:
            if member.is_active and member.agent_id not in req.agent_ids:
                member.is_active = False
                changes.append(f"移除：{member.name}")
        if changes:
            add_message(db, room, role="system", content="；".join(changes))
    default_id = req.default_member_id or room.default_member_id
    if req.default_agent_id:
        default_member = next((m for m in roster if m.agent_id == req.default_agent_id and m.is_active), None)
        if default_member is None:
            raise HTTPException(422, "默认回答成员必须在房间内")
        default_id = default_member.id
    default = next((m for m in roster if m.id == default_id and m.is_active), None)
    if default is None:
        raise HTTPException(422, "移除默认成员时，请指定另一位房间成员为默认回答成员")
    if default_id != room.default_member_id:
        if not await load_agent(db, default.agent_id, user):
            raise HTTPException(422, "默认回答成员不可用")
        room.default_member_id = default_id
        add_message(db, room, role="system", content=f"默认回答成员改为：{default.name}")
    for field in ("title", "goal", "is_pinned", "is_archived"):
        value = getattr(req, field)
        if value is not None:
            setattr(room, field, sanitize_pg_text(value) if isinstance(value, str) else value)
    session = await db.get(Session, room.id)
    session.title, session.is_pinned, session.is_archived = room.title, room.is_pinned, room.is_archived
    touch(room)
    await db.flush()


def select_targets(req: RoomSend, roster: list[ChatRoomMember], default_id: UUID) -> list[UUID]:
    active_ids = [m.id for m in roster if m.is_active]
    selected = active_ids if req.mode == "all" else list(dict.fromkeys(req.member_ids or [default_id]))
    if not selected or any(mid not in active_ids for mid in selected):
        raise HTTPException(422, "请选择有效的房间成员")
    return selected


def validate_attachments(req: RoomSend, room: ChatRoom, user: User) -> dict:
    data = req.model_dump(mode="json", exclude={"request_id"})
    for image in data["attachments"]:
        parts = image["key"].split("/")
        if (len(parts) != 4 or parts[0] != "chat-attachments" or parts[1] != str(user.id)
                or parts[2] not in {str(room.id), "_pending"} or ".." in parts):
            raise HTTPException(403, "图片不属于当前用户或聊天室")
        # Never trust a client URL when constructing model input.
        image["url"] = "/api/public/images/" + image["key"]
    for file in data["file_attachments"]:
        path = PurePosixPath(file["workspace_path"])
        if (path.is_absolute() or ".." in path.parts or "\\" in str(path)
                or len(path.parts) != 2 or path.parts[0] != "uploads"
                or not path.name.startswith(file["file_id"] + "_")):
            raise HTTPException(422, "文件必须来自当前工作区的上传目录")
    return sanitize_pg_text(data)


async def submit_run(db: AsyncSession, room: ChatRoom, user: User, req: RoomSend,
                     retry: ChatRoomTask | None = None, retry_hash: str | None = None) -> tuple[ChatRoomRun, bool]:
    digest = retry_hash or hashlib.sha256(
        req.model_dump_json(exclude={"request_id"}).encode()
    ).hexdigest()
    existing = await db.scalar(select(ChatRoomRun).where(
        ChatRoomRun.room_id == room.id, ChatRoomRun.request_id == req.request_id,
    ))
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(409, "该请求标识已用于不同内容")
        return existing, False
    await require_idle(db, room)
    if room.is_archived:
        raise HTTPException(409, "请先恢复已归档的聊天室")
    roster = await members(db, room.id)
    targets = select_targets(req, roster, room.default_member_id)
    valid = []
    for mid in targets:
        member = next(m for m in roster if m.id == mid)
        if await load_agent(db, member.agent_id, user):
            valid.append(mid)
        elif req.mode != "all":
            raise HTTPException(422, f"成员 {member.name} 当前不可用，请调整接收对象")
    if not valid:
        raise HTTPException(422, "没有可用的接收成员")
    data = validate_attachments(req, room, user)
    data["room_goal"] = room.goal
    data["roster"] = "；".join(f"{m.name}: {m.description or ''}" for m in roster if m.is_active)
    data["member_ids"] = [str(mid) for mid in valid]
    if req.reply_to_id:
        quote = await db.scalar(select(ChatRoomMessage).where(
            ChatRoomMessage.id == req.reply_to_id, ChatRoomMessage.room_id == room.id,
        ))
        if not quote:
            raise HTTPException(404, "引用消息不存在")
        data["quote"] = {"name": quote.name, "content": quote.content[:12000],
                         "truncated": len(quote.content) > 12000, "status": quote.status}
    if req.mode == "summary":
        data["message"] = SUMMARY_PROMPT
    run = ChatRoomRun(id=uuid4(), room_id=room.id, request_id=req.request_id,
                      request_hash=digest, input=data, status="queued", heartbeat_at=now())
    db.add(run)
    for i, mid in enumerate(valid):
        db.add(ChatRoomTask(
            id=uuid4(), room_id=room.id, run_id=run.id, member_id=mid, position=i,
            context_snapshot=retry.context_snapshot if retry else None,
            retry_of=retry.id if retry else None, status="queued",
        ))
    add_message(db, room, role="user", name="我", content=data["message"], run_id=run.id,
                reply_to_id=req.reply_to_id, payload={
                    "attachments": data["attachments"], "file_attachments": data["file_attachments"],
                    "member_ids": data["member_ids"], "mode": req.mode,
                    "retry_of": str(retry.id) if retry else None,
                })
    await db.flush()
    return run, True


async def retry_task(db: AsyncSession, room: ChatRoom, user: User, task_id: UUID,
                     req: RoomRetry) -> tuple[ChatRoomRun, bool]:
    task = await db.scalar(select(ChatRoomTask).where(
        ChatRoomTask.id == task_id, ChatRoomTask.room_id == room.id,
    ))
    if not task or task.status != "failed":
        raise HTTPException(422, "只能重试失败成员")
    message = await db.get(ChatRoomMessage, task.message_id) if task.message_id else None
    # Conservatively require acknowledgement for any attempted tool, including unknown results.
    if message and message.payload.get("tool_calls") and not req.acknowledge_side_effects:
        raise HTTPException(409, "原任务调用过工具，请检查已有动作并确认是否重新执行")
    original = await db.get(ChatRoomRun, task.run_id)
    if task.context_snapshot is None:
        # A member can fail permission/model validation before it starts. Rebuild
        # its original boundary without including later members or later runs.
        prior_ids = list((await db.scalars(select(ChatRoomTask.message_id).where(
            ChatRoomTask.run_id == original.id, ChatRoomTask.position < task.position,
        ))).all())
        boundary_messages = (await db.scalars(select(ChatRoomMessage).where(
            ChatRoomMessage.room_id == room.id,
            or_((ChatRoomMessage.run_id == original.id) & (ChatRoomMessage.role == "user"),
                ChatRoomMessage.id.in_([mid for mid in prior_ids if mid])),
        ))).all()
        boundary = max((m.sequence for m in boundary_messages), default=0)
        history = (await db.scalars(select(ChatRoomMessage).where(
            ChatRoomMessage.room_id == room.id, ChatRoomMessage.sequence <= boundary,
        ).order_by(ChatRoomMessage.sequence))).all()
        from aio_agent_platform.rooms.runtime import public_record
        task.context_snapshot = {
            "goal": original.input.get("room_goal", room.goal),
            "roster": original.input.get("roster", ""), "input": original.input,
            "summary": None,
            "history": [public_record(m) for m in history if not (m.run_id == original.id and m.role == "user")],
            "sequences": [m.sequence for m in history if not (m.run_id == original.id and m.role == "user")],
        }
    data = {k: v for k, v in original.input.items() if k in RoomSend.model_fields}
    data.update(request_id=req.request_id, mode="mentions", member_ids=[task.member_id])
    digest = hashlib.sha256(json.dumps({"task_id": str(task_id), **req.model_dump(mode="json")}, sort_keys=True).encode()).hexdigest()
    return await submit_run(db, room, user, RoomSend(**data), retry=task, retry_hash=digest)


def room_dict(room: ChatRoom) -> dict:
    return {key: getattr(room, key) for key in (
        "id", "title", "goal", "workspace_id", "default_member_id", "is_pinned", "is_archived",
        "revision", "created_at", "updated_at",
    )}


def message_dict(message: ChatRoomMessage) -> dict:
    return {**message.payload, **{key: getattr(message, key) for key in (
        "id", "run_id", "member_id", "sequence", "role", "name", "icon", "content", "status",
        "reply_to_id", "created_at",
    )}}


async def room_detail(db: AsyncSession, room: ChatRoom, user: User, before: int | None = None) -> dict:
    roster = await members(db, room.id)
    allowed = set((await db.scalars(select(Agent.id).where(
        Agent.tenant_id == user.tenant_id, Agent.is_active,
        or_(Agent.visibility == "tenant", Agent.created_by == user.id),
    ))).all())
    stmt = select(ChatRoomMessage).where(ChatRoomMessage.room_id == room.id)
    if before is not None:
        stmt = stmt.where(ChatRoomMessage.sequence < before)
    messages = list((await db.scalars(stmt.order_by(ChatRoomMessage.sequence.desc()).limit(101))).all())
    has_more = len(messages) > 100
    messages = list(reversed(messages[:100]))
    runs = list((await db.scalars(select(ChatRoomRun).where(
        ChatRoomRun.room_id == room.id,
    ).order_by(ChatRoomRun.created_at.desc()).limit(20))).all())
    tasks = list((await db.scalars(select(ChatRoomTask).where(
        ChatRoomTask.room_id == room.id,
        or_(ChatRoomTask.run_id.in_([r.id for r in runs]),
            ChatRoomTask.message_id.in_([m.id for m in messages])),
    ).order_by(ChatRoomTask.position))).all())
    return {
        **room_dict(room), "has_more": has_more,
        "members": [{**{key: getattr(m, key) for key in (
            "id", "agent_id", "name", "icon", "description", "position", "is_active",
        )}, "available": m.is_active and m.agent_id in allowed} for m in roster],
        "messages": [message_dict(m) for m in messages],
        "runs": [{key: getattr(r, key) for key in (
            "id", "status", "error", "stop_requested", "created_at", "completed_at",
        )} for r in runs],
        "tasks": [{"response_submitted": bool(t.confirmation_response), **{key: getattr(t, key) for key in (
            "id", "run_id", "member_id", "message_id", "position", "status", "error",
            "confirmation", "token_usage", "duration_ms", "retry_of",
        )}} for t in tasks],
    }


async def delete_room(db: AsyncSession, room: ChatRoom) -> None:
    await require_idle(db, room)
    for model in (ChatRoomMessage, ChatRoomTask, ChatRoomRun, ChatRoomMember):
        await db.execute(delete(model).where(model.room_id == room.id))
    await db.execute(delete(Session).where(Session.id == room.id))
    await db.delete(room)
