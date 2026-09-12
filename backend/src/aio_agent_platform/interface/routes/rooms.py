"""Collaborative room endpoints, including a reconnectable snapshot SSE stream."""

import asyncio
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db, get_session_factory
from aio_agent_platform.db.models import (
    ChatRoom,
    ChatRoomRun,
    ChatRoomTask,
    Tenant,
    TenantMembership,
    User,
)
from aio_agent_platform.rooms import service
from aio_agent_platform.rooms.runtime import start_run
from aio_agent_platform.rooms.schemas import (
    RoomConfirmation,
    RoomCreate,
    RoomRetry,
    RoomSend,
    RoomUpdate,
)

router = APIRouter(prefix="/api/rooms", tags=["rooms"])
Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_rooms(user: CurrentUser, db: Db, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    rooms = (await db.scalars(select(ChatRoom).where(
        ChatRoom.user_id == user.id, ChatRoom.tenant_id == user.tenant_id,
    ).order_by(ChatRoom.is_pinned.desc(), ChatRoom.updated_at.desc()).offset(offset).limit(limit))).all()
    return [service.room_dict(room) for room in rooms]


@router.post("", status_code=201)
async def create_room(req: RoomCreate, user: CurrentUser, db: Db):
    room = await service.create_room(db, user, req)
    return await service.room_detail(db, room, user)


@router.get("/{room_id}")
async def get_room(room_id: UUID, user: CurrentUser, db: Db, before: int | None = Query(None, ge=1)):
    room = await service.owned_room(db, room_id, user, lock=True)
    await service.expire_stale_run(db, room)
    return await service.room_detail(db, room, user, before)


@router.patch("/{room_id}")
async def update_room(room_id: UUID, req: RoomUpdate, user: CurrentUser, db: Db):
    room = await service.owned_room(db, room_id, user, lock=True)
    await service.update_room(db, room, user, req)
    return await service.room_detail(db, room, user)


@router.get("/{room_id}/messages/{message_id}")
async def get_message(room_id: UUID, message_id: UUID, user: CurrentUser, db: Db):
    from aio_agent_platform.db.models import ChatRoomMessage
    await service.owned_room(db, room_id, user)
    message = await db.scalar(select(ChatRoomMessage).where(
        ChatRoomMessage.id == message_id, ChatRoomMessage.room_id == room_id,
    ))
    if not message:
        raise HTTPException(404, "消息不存在")
    return service.message_dict(message)


@router.delete("/{room_id}", status_code=204)
async def delete_room(room_id: UUID, user: CurrentUser, db: Db):
    room = await service.owned_room(db, room_id, user, lock=True)
    await service.delete_room(db, room)


@router.post("/{room_id}/runs", status_code=202)
async def send_message(room_id: UUID, req: RoomSend, request: Request, user: CurrentUser, db: Db):
    room = await service.owned_room(db, room_id, user, lock=True)
    run, created = await service.submit_run(db, room, user, req)
    await db.commit()  # Worker never sees an uncommitted queue.
    if created:
        start_run(request.app, room_id, run.id)
    return {"run_id": run.id, "created": created}


@router.post("/{room_id}/tasks/{task_id}/retry", status_code=202)
async def retry_member(room_id: UUID, task_id: UUID, req: RoomRetry, request: Request, user: CurrentUser, db: Db):
    room = await service.owned_room(db, room_id, user, lock=True)
    run, created = await service.retry_task(db, room, user, task_id, req)
    await db.commit()
    if created:
        start_run(request.app, room_id, run.id)
    return {"run_id": run.id, "created": created}


@router.post("/{room_id}/runs/{run_id}/stop")
async def stop_run(room_id: UUID, run_id: UUID, user: CurrentUser, db: Db):
    room = await service.owned_room(db, room_id, user, lock=True)
    run = await db.scalar(select(ChatRoomRun).where(ChatRoomRun.id == run_id, ChatRoomRun.room_id == room.id))
    if not run:
        raise HTTPException(404, "运行不存在")
    if run.status in service.ACTIVE_RUNS:
        run.stop_requested = True
        run.status = "stopping"
        service.touch(room)
    return {"status": run.status}


async def store_confirmation(db, room, task, req):
    run = await db.get(ChatRoomRun, task.run_id)
    if (not run or run.status not in service.ACTIVE_RUNS or run.stop_requested
            or task.status != "waiting_user" or not task.confirmation
            or task.confirmation.get("confirmation_id") != req.confirmation_id):
        raise HTTPException(409, "该确认已失效")
    if task.confirmation_response:
        raise HTTPException(409, "该确认已经回答")
    task.confirmation_response = req.model_dump(exclude={"confirmation_id"})
    service.touch(room)
    return {"success": True}


@router.post("/{room_id}/tasks/{task_id}/respond")
async def respond(room_id: UUID, task_id: UUID, req: RoomConfirmation, user: CurrentUser, db: Db):
    room = await service.owned_room(db, room_id, user, lock=True)
    task = await db.scalar(select(ChatRoomTask).where(ChatRoomTask.id == task_id, ChatRoomTask.room_id == room.id))
    if not task:
        raise HTTPException(404, "成员任务不存在")
    return await store_confirmation(db, room, task, req)


@router.get("/{room_id}/events")
async def events(room_id: UUID, request: Request, user: CurrentUser, db: Db,
                 after_revision: int = Query(-1, ge=-1)):
    await service.owned_room(db, room_id, user)
    user_id, tenant_id = user.id, user.tenant_id
    await db.commit()

    async def stream():
        revision = after_revision
        # Periodically end the connection so JWT is revalidated on reconnect.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not await request.is_disconnected():
            async with get_session_factory()() as stream_db:
                current = await stream_db.get(User, user_id)
                tenant = await stream_db.get(Tenant, tenant_id)
                membership = await stream_db.scalar(select(TenantMembership.user_id).where(
                    TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id,
                ))
                if (not current or not current.is_active or current.tenant_id != tenant_id
                        or not tenant or not tenant.is_active or not membership):
                    yield 'data: {"type":"access_revoked"}\n\n'
                    return
                try:
                    room = await service.owned_room(stream_db, room_id, current, lock=True)
                except HTTPException:
                    yield 'data: {"type":"access_revoked"}\n\n'
                    return
                await service.expire_stale_run(stream_db, room)
                if room.revision != revision:
                    snapshot = await service.room_detail(stream_db, room, current)
                    revision = room.revision
                    data = json.dumps(jsonable_encoder({"type": "snapshot", "room": snapshot}), ensure_ascii=False)
                    await stream_db.commit()
                    yield f"id: {revision}\ndata: {data}\n\n"
                else:
                    await stream_db.commit()
                    yield ": keepalive\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
