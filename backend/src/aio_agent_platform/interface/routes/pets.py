"""宠物系统路由 — 包上传/市场/领养/激活，兼容 Codex 宠物包格式。"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from aio_agent_platform.auth.dependencies import AdminUser, CurrentUser, DbSession
from aio_agent_platform.db.models import PetPackage, UserPet
from aio_agent_platform.pets.package import PetPackageError, parse_pet_package
from aio_agent_platform.pets.service import (
    PetNotFoundError,
    PetService,
)

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
            frame_width=p.frame_width,
            frame_height=p.frame_height,
            col_count=p.col_count,
            row_count=p.row_count,
            created_at=p.created_at,
            spritesheet_url=f"/api/pets/packages/{p.id}/spritesheet",
        )


class UserPetOut(BaseModel):
    id: UUID
    package_id: UUID
    level: int
    exp: int
    is_active: bool
    adopted_at: datetime
    package: PetPackageOut

    @classmethod
    def from_pair(cls, pet: UserPet, pkg: PetPackage) -> UserPetOut:
        return cls(
            id=pet.id,
            package_id=pet.package_id,
            level=pet.level,
            exp=pet.exp,
            is_active=pet.is_active,
            adopted_at=pet.adopted_at,
            package=PetPackageOut.from_model(pkg),
        )


class VisibilityUpdate(BaseModel):
    visibility: str = Field(..., pattern="^(private|tenant|public)$")


class RowMappingUpdate(BaseModel):
    row_mapping: dict[str, int]


class AdminVisibilityUpdate(BaseModel):
    visibility: str = Field(..., pattern="^(private|tenant|public|official)$")


class TakedownUpdate(BaseModel):
    taken_down: bool


# ---- Helpers ----


def _not_found(e: PetNotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail="宠物包不存在")


def _bad_request(e: PetPackageError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


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
        pet, _awarded = await svc.interact(user, user_pet_id)
    except PetNotFoundError as e:
        raise _not_found(e) from e
    pkg = await db.get(PetPackage, pet.package_id)
    assert pkg is not None
    return UserPetOut.from_pair(pet, pkg)


@router.post("/deactivate", status_code=204)
async def deactivate_pet(user: CurrentUser, db: DbSession) -> Response:
    svc = PetService(db)
    await svc.deactivate_all(user)
    return Response(status_code=204)


@router.get("/mine", response_model=list[UserPetOut])
async def list_my_pets(user: CurrentUser, db: DbSession) -> list[UserPetOut]:
    svc = PetService(db)
    pairs = await svc.list_my_pets(user)
    return [UserPetOut.from_pair(pet, pkg) for pet, pkg in pairs]


@router.get("/active", response_model=UserPetOut | None)
async def get_active_pet(user: CurrentUser, db: DbSession) -> UserPetOut | None:
    svc = PetService(db)
    pair = await svc.get_active(user)
    if pair is None:
        return None
    pet, pkg = pair
    return UserPetOut.from_pair(pet, pkg)


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
