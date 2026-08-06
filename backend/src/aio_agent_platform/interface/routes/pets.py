"""宠物系统路由 — 包上传/市场/领养/激活，兼容 Codex 宠物包格式。"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from aio_agent_platform.auth.dependencies import AdminUser, CurrentUser, DbSession
from aio_agent_platform.core import task_registry
from aio_agent_platform.core.task_events import broker
from aio_agent_platform.db.models import PetPackage, Session, UserPet
from aio_agent_platform.pets.package import PetPackageError, parse_pet_package
from aio_agent_platform.pets.service import (
    PetNotFoundError,
    PetService,
)
from aio_agent_platform.pets.smart import resolve_bubble_provider, stream_bubble

logger = logging.getLogger("aio_agent_platform.routes.pets")

router = APIRouter(prefix="/api/pets", tags=["pets"])
admin_router = APIRouter(prefix="/api/admin/pet-packages", tags=["admin-pets"])


# ---- Schemas ----


class PetPackageOut(BaseModel):
    id: UUID
    name: str
    display_name: str
    description: str | None = None
    kind: str | None = None
    owner_id: UUID
    tenant_id: UUID
    visibility: str
    status: str
    manifest: dict
    row_mapping: dict
    default_agent_id: UUID | None = None
    actions: dict | None = None
    frame_width: int
    frame_height: int
    col_count: int
    row_count: int
    created_at: datetime
    spritesheet_url: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, p: PetPackage) -> PetPackageOut:
        return cls(
            id=p.id,
            name=p.name,
            display_name=p.display_name,
            description=p.description,
            kind=p.kind,
            owner_id=p.owner_id,
            tenant_id=p.tenant_id,
            visibility=p.visibility,
            status=p.status,
            manifest=p.manifest,
            row_mapping=p.row_mapping,
            default_agent_id=p.default_agent_id,
            actions=p.actions,
            frame_width=p.frame_width,
            frame_height=p.frame_height,
            col_count=p.col_count,
            row_count=p.row_count,
            created_at=p.created_at,
            spritesheet_url=f"/api/pets/packages/{p.id}/spritesheet",
        )


class AgentBriefOut(BaseModel):
    id: UUID
    name: str
    icon: str = "robot"
    level: str = "instance"  # 生效层级: instance(实例绑定) / package(包级默认)


class PetActionOut(BaseModel):
    row: int
    name: str
    state: str | None = None


class UserPetOut(BaseModel):
    id: UUID
    package_id: UUID
    is_active: bool
    adopted_at: datetime
    package: PetPackageOut
    agent: AgentBriefOut | None = None
    actions: list[PetActionOut] = Field(default_factory=list)
    state_mapping: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def from_pair(
        cls,
        pet: UserPet,
        pkg: PetPackage,
        *,
        agent: AgentBriefOut | None = None,
        actions: list[dict] | None = None,
        state_mapping: dict[str, int] | None = None,
    ) -> UserPetOut:
        return cls(
            id=pet.id,
            package_id=pet.package_id,
            is_active=pet.is_active,
            adopted_at=pet.adopted_at,
            package=PetPackageOut.from_model(pkg),
            agent=agent,
            actions=[PetActionOut(**a) for a in (actions or [])],
            state_mapping=state_mapping or {},
        )


class VisibilityUpdate(BaseModel):
    visibility: str = Field(..., pattern="^(private|tenant|public)$")


class RowMappingUpdate(BaseModel):
    row_mapping: dict[str, int]


class AgentBindUpdate(BaseModel):
    agent_id: UUID | None = None


class PackageActionsUpdate(BaseModel):
    actions: dict[str, str]  # {row: name}


class PetActionsUpdate(BaseModel):
    aliases: dict[str, str] = Field(default_factory=dict)  # {row: name}，空串 value 删除覆盖
    state_mapping: dict[str, int] | None = None  # {state: row}，None 表示不改映射


class PetChatOut(BaseModel):
    conversation_id: UUID
    agent_id: UUID | None = None


class AdminVisibilityUpdate(BaseModel):
    visibility: str = Field(..., pattern="^(private|tenant|public|official)$")


class TakedownUpdate(BaseModel):
    taken_down: bool


class ActiveTaskOut(BaseModel):
    """渠道（飞书等）触发的在跑任务，供宠物 widget 轮询展示。"""

    session_id: str
    label: str
    tool: str | None
    source: str
    chat_key: str
    agent_id: str
    started_at: float


# ---- Helpers ----


def _not_found(e: PetNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail="宠物包不存在")


def _bad_request(e: PetPackageError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _sse_event(data: dict) -> str:
    """Format a dict as an SSE data line (matches chat.py convention)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _user_pet_out(db, user, pet: UserPet, pkg: PetPackage) -> UserPetOut:
    """解析绑定 Agent + 动作目录 + 状态映射后组装 UserPetOut（供 active/mine/绑定/改名接口共用）。"""
    svc = PetService(db)
    agent = await svc.resolve_agent(user, pet, pkg)
    agent_brief = None
    if agent is not None:
        agent_brief = AgentBriefOut(
            id=agent.id,
            name=agent.name,
            icon=agent.icon or "robot",
            level="instance" if pet.agent_id == agent.id else "package",
        )
    actions, _ = svc.resolve_actions(pet, pkg)
    state_mapping = svc.resolve_state_mapping(pet, pkg)
    return UserPetOut.from_pair(pet, pkg, agent=agent_brief, actions=actions, state_mapping=state_mapping)


# ---- 市场与包管理 ----


@router.get("/market", response_model=list[PetPackageOut])
async def list_market(user: CurrentUser, db: DbSession) -> list[PetPackageOut]:
    svc = PetService(db)
    packages = await svc.list_market(user)
    return [PetPackageOut.from_model(p) for p in packages]


@router.post("/packages", response_model=PetPackageOut, status_code=201)
async def upload_package(
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File()],
    row_mapping: Annotated[str, Form()],
    visibility: Annotated[str, Form()] = "private",
) -> PetPackageOut:
    """上传宠物包 zip（Codex 格式）。row_mapping 为 JSON 字符串，如 {"idle":0,"work":1}。"""
    zip_bytes = await file.read()
    try:
        parsed = parse_pet_package(zip_bytes)
        mapping = json.loads(row_mapping)
        svc = PetService(db)
        pkg = await svc.create_package(user, parsed, zip_bytes, mapping, visibility)
    except PetPackageError as e:
        raise _bad_request(e) from e
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="row_mapping 不是合法 JSON") from e
    return PetPackageOut.from_model(pkg)


@router.get("/packages/mine", response_model=list[PetPackageOut])
async def list_my_packages(user: CurrentUser, db: DbSession) -> list[PetPackageOut]:
    svc = PetService(db)
    packages = await svc.list_my_packages(user)
    return [PetPackageOut.from_model(p) for p in packages]


@router.put("/packages/{package_id}/visibility", response_model=PetPackageOut)
async def set_visibility(
    package_id: UUID, req: VisibilityUpdate, user: CurrentUser, db: DbSession
) -> PetPackageOut:
    svc = PetService(db)
    try:
        pkg = await svc.set_visibility(user, package_id, req.visibility)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    return PetPackageOut.from_model(pkg)


@router.put("/packages/{package_id}/row-mapping", response_model=PetPackageOut)
async def set_row_mapping(
    package_id: UUID, req: RowMappingUpdate, user: CurrentUser, db: DbSession
) -> PetPackageOut:
    svc = PetService(db)
    try:
        pkg = await svc.set_row_mapping(user, package_id, req.row_mapping)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    return PetPackageOut.from_model(pkg)


@router.put("/packages/{package_id}/default-agent", response_model=PetPackageOut)
async def set_default_agent(
    package_id: UUID, req: AgentBindUpdate, user: CurrentUser, db: DbSession
) -> PetPackageOut:
    """包级默认人设 Agent（仅创建人/管理员）。agent_id 为 null 表示清除。"""
    svc = PetService(db)
    try:
        pkg = await svc.set_default_agent(user, package_id, req.agent_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    return PetPackageOut.from_model(pkg)


@router.put("/packages/{package_id}/actions", response_model=PetPackageOut)
async def set_package_actions(
    package_id: UUID, req: PackageActionsUpdate, user: CurrentUser, db: DbSession
) -> PetPackageOut:
    """上传者改包级动作名（仅创建人/管理员）。"""
    svc = PetService(db)
    try:
        pkg = await svc.set_package_actions(user, package_id, req.actions)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    return PetPackageOut.from_model(pkg)


@router.delete("/packages/{package_id}", status_code=204)
async def delete_package(package_id: UUID, user: CurrentUser, db: DbSession) -> Response:
    svc = PetService(db)
    try:
        await svc.delete_package(user, package_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    return Response(status_code=204)


@router.get("/packages/{package_id}/spritesheet")
async def get_spritesheet(package_id: UUID, user: CurrentUser, db: DbSession) -> StreamingResponse:
    """精灵图（经后端转发以执行可见性判定，不可见返回 404）。"""
    svc = PetService(db)
    try:
        pkg = await svc.get_visible_package(user, package_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    try:
        data = svc.storage.get(pkg.spritesheet_key)
    except Exception as e:
        raise HTTPException(status_code=404, detail="精灵图资源不存在") from e
    media_type = "image/webp" if pkg.spritesheet_key.endswith(".webp") else "image/png"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/packages/{package_id}/download")
async def download_package(package_id: UUID, user: CurrentUser, db: DbSession) -> StreamingResponse:
    """导出原始 zip（与上传时逐字节一致，可放回 ~/.codex/pets 使用）。"""
    svc = PetService(db)
    try:
        pkg = await svc.get_visible_package(user, package_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    try:
        data = svc.storage.get(pkg.package_key)
    except Exception as e:
        raise HTTPException(status_code=404, detail="原始包资源不存在") from e
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{pkg.name}.zip"'},
    )


# ---- 领养 / 激活 ----


@router.post("/{package_id}/adopt", response_model=UserPetOut)
async def adopt_pet(package_id: UUID, user: CurrentUser, db: DbSession) -> UserPetOut:
    svc = PetService(db)
    try:
        pet = await svc.adopt(user, package_id)
        pkg = await svc.get_visible_package(user, package_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    return UserPetOut.from_pair(pet, pkg)


@router.post("/{user_pet_id}/activate", response_model=UserPetOut)
async def activate_pet(user_pet_id: UUID, user: CurrentUser, db: DbSession) -> UserPetOut:
    svc = PetService(db)
    try:
        pet = await svc.activate(user, user_pet_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    pkg = await db.get(PetPackage, pet.package_id)
    assert pkg is not None
    return UserPetOut.from_pair(pet, pkg)


@router.post("/{user_pet_id}/interact", response_model=UserPetOut)
async def interact_pet(user_pet_id: UUID, user: CurrentUser, db: DbSession) -> UserPetOut:
    svc = PetService(db)
    try:
        pet = await svc.interact(user, user_pet_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    pkg = await db.get(PetPackage, pet.package_id)
    assert pkg is not None
    return await _user_pet_out(db, user, pet, pkg)


@router.put("/{user_pet_id}/agent", response_model=UserPetOut)
async def bind_agent(
    user_pet_id: UUID, req: AgentBindUpdate, user: CurrentUser, db: DbSession
) -> UserPetOut:
    """实例级绑定/解绑智能体。agent_id 为 null 表示解绑（回退包级默认）。"""
    svc = PetService(db)
    try:
        pet = await svc.set_agent(user, user_pet_id, req.agent_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    pkg = await db.get(PetPackage, pet.package_id)
    assert pkg is not None
    return await _user_pet_out(db, user, pet, pkg)


@router.put("/{user_pet_id}/actions", response_model=UserPetOut)
async def set_pet_actions(
    user_pet_id: UUID, req: PetActionsUpdate, user: CurrentUser, db: DbSession
) -> UserPetOut:
    """领养者改实例级动作名覆盖 + 状态映射覆盖。aliases 空串 value 删除该条覆盖；传 {} 恢复包级。"""
    svc = PetService(db)
    try:
        pet = await svc.set_pet_actions(user, user_pet_id, req.aliases, req.state_mapping)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    pkg = await db.get(PetPackage, pet.package_id)
    assert pkg is not None
    return await _user_pet_out(db, user, pet, pkg)


@router.post("/{user_pet_id}/bubble")
async def pet_bubble(user_pet_id: UUID, user: CurrentUser, db: DbSession):
    """智能气泡：绑定 Agent 时流式返回 pet_action + text_delta 事件；未绑定/失败/超限回退 JSON。"""
    svc = PetService(db)
    pet = await db.get(UserPet, user_pet_id)
    if pet is None or pet.user_id != user.id:
        raise HTTPException(status_code=404, detail="宠物不存在")
    pkg = await db.get(PetPackage, pet.package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="宠物包不存在")

    fallback = {"fallback": True, "text": None, "action": None, "quota_exceeded": False}
    if not await svc.smart_enabled():
        logger.warning("pet_bubble_fallback reason=smart_disabled user_id=%s pet_id=%s", user.id, user_pet_id)
        return fallback
    agent = await svc.resolve_agent(user, pet, pkg)
    if agent is None:
        logger.warning("pet_bubble_fallback reason=no_agent user_id=%s pet_id=%s", user.id, user_pet_id)
        return fallback
    if await svc.bubble_quota_remaining(user.id, user_pet_id) <= 0:
        fallback["quota_exceeded"] = True
        logger.warning("pet_bubble_fallback reason=quota_exceeded user_id=%s pet_id=%s", user.id, user_pet_id)
        return fallback

    provider, model_name = await resolve_bubble_provider(db, agent)
    if provider is None:
        logger.warning("pet_bubble_fallback reason=provider_unavailable user_id=%s pet_id=%s", user.id, user_pet_id)
        return fallback

    async def event_stream():
        async for ev in stream_bubble(db, user.id, agent, pet, pkg, provider, model_name, mood="happy"):
            yield _sse_event(ev)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{user_pet_id}/chat", response_model=PetChatOut)
async def pet_chat(user_pet_id: UUID, user: CurrentUser, db: DbSession) -> PetChatOut:
    """开启/复用宠物闲聊会话（source='pet'）。之后走现有会话消息接口 + SSE。"""
    svc = PetService(db)
    pet = await db.get(UserPet, user_pet_id)
    if pet is None or pet.user_id != user.id:
        raise HTTPException(status_code=404, detail="宠物不存在")
    pkg = await db.get(PetPackage, pet.package_id)
    if pkg is None:
        raise HTTPException(status_code=404, detail="宠物包不存在")
    agent = await svc.resolve_agent(user, pet, pkg)
    if agent is None:
        raise HTTPException(status_code=400, detail="请先为该宠物绑定智能体")

    result = await db.execute(
        select(Session)
        .where(
            Session.user_id == user.id,
            Session.pet_id == user_pet_id,
            Session.source == "pet",
        )
        .order_by(Session.updated_at.desc())
        .limit(1)
    )
    session = result.scalars().first()
    if session is None:
        session = Session(
            user_id=user.id,
            agent_id=agent.id,
            source="pet",
            pet_id=user_pet_id,
            title=f"与{pkg.display_name}的闲聊",
        )
        db.add(session)
        await db.flush()
    return PetChatOut(conversation_id=session.id, agent_id=agent.id)


@router.post("/deactivate", status_code=204)
async def deactivate_pet(user: CurrentUser, db: DbSession) -> Response:
    svc = PetService(db)
    await svc.deactivate_all(user)
    return Response(status_code=204)


@router.delete("/{user_pet_id}", status_code=204)
async def remove_pet(user_pet_id: UUID, user: CurrentUser, db: DbSession) -> Response:
    svc = PetService(db)
    try:
        await svc.remove_pet(user, user_pet_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    return Response(status_code=204)


@router.get("/mine", response_model=list[UserPetOut])
async def list_my_pets(user: CurrentUser, db: DbSession) -> list[UserPetOut]:
    svc = PetService(db)
    pairs = await svc.list_my_pets(user)
    out = []
    for pet, pkg in pairs:
        out.append(await _user_pet_out(db, user, pet, pkg))
    return out


@router.get("/active", response_model=UserPetOut | None)
async def get_active_pet(user: CurrentUser, db: DbSession) -> UserPetOut | None:
    svc = PetService(db)
    pair = await svc.get_active(user)
    if pair is None:
        return None
    pet, pkg = pair
    return await _user_pet_out(db, user, pet, pkg)


@router.get("/active-tasks", response_model=list[ActiveTaskOut])
async def list_active_tasks(user: CurrentUser) -> list[ActiveTaskOut]:
    """当前用户的在跑任务（渠道触发，如飞书）。调试/回退用，前端已改走 SSE。"""
    return [
        ActiveTaskOut(
            session_id=t.session_id,
            label=t.label,
            tool=t.tool,
            source=t.source,
            chat_key=t.chat_key,
            agent_id=t.agent_id,
            started_at=t.started_at,
        )
        for t in await task_registry.list_tasks(user.id)
    ]


@router.get("/tasks/events")
async def task_events_stream(user: CurrentUser) -> StreamingResponse:
    """用户级 SSE：渠道任务生命周期实时推送（连接即快照，再收增量 + 心跳）。"""

    async def event_generator():
        async for item in broker.stream(user.id):
            if item is None:
                yield ": ping\n\n"
            else:
                yield _sse_event(item)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- 管理端 ----


@admin_router.put("/{package_id}/visibility", response_model=PetPackageOut)
async def admin_set_visibility(
    package_id: UUID, req: AdminVisibilityUpdate, admin: AdminUser, db: DbSession
) -> PetPackageOut:
    svc = PetService(db)
    try:
        pkg = await svc.admin_set_visibility(package_id, req.visibility)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    except PetPackageError as e:
        raise _bad_request(e) from e
    return PetPackageOut.from_model(pkg)


@admin_router.put("/{package_id}/takedown", response_model=PetPackageOut)
async def admin_takedown(
    package_id: UUID, req: TakedownUpdate, admin: AdminUser, db: DbSession
) -> PetPackageOut:
    svc = PetService(db)
    try:
        pkg = await svc.admin_takedown(package_id, req.taken_down)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    return PetPackageOut.from_model(pkg)
