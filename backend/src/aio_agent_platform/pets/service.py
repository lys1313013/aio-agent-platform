"""宠物包与领养的业务逻辑。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import PetPackage, User, UserPet
from aio_agent_platform.pets.package import ParsedPetPackage, PetPackageError
from aio_agent_platform.storage.client import ObjectStorage

MAX_PACKAGES_PER_USER = 20
ADMIN_ROLES = {"admin", "superadmin"}

# 平台状态名（row_mapping 的合法 key），idle 必选
VALID_ROW_STATES = {"idle", "think", "work", "wait", "celebrate", "sad", "sleep", "happy"}


class PetNotFoundError(LookupError):
    """包不存在或对当前用户不可见（对外统一 404）。"""


class PetPermissionError(PermissionError):
    """拥有者校验失败。"""


def visible_packages_query(user: User):
    """当前用户可见的宠物包查询条件（应用层可见性判定）。"""
    return select(PetPackage).where(
        PetPackage.status == "active",
        or_(
            PetPackage.visibility.in_(("public", "official")),
            (PetPackage.visibility == "tenant") & (PetPackage.tenant_id == user.tenant_id),
            PetPackage.owner_id == user.id,
        ),
    )


def validate_row_mapping(mapping: dict, row_count: int) -> dict:
    """校验 状态→行号 映射，返回规范化结果（含 _row_frames 由调用方补充）。"""
    if not isinstance(mapping, dict):
        raise PetPackageError("row_mapping 必须是对象")
    clean: dict[str, int] = {}
    for state, row in mapping.items():
        if state not in VALID_ROW_STATES:
            raise PetPackageError(f"非法状态名: {state}")
        if not isinstance(row, int) or row < 0 or row >= row_count:
            raise PetPackageError(f"状态 {state} 的行号越界: {row}（共 {row_count} 行）")
        clean[state] = row
    if "idle" not in clean:
        raise PetPackageError("必须为 idle 状态指定精灵图行")
    return clean


class PetService:
    def __init__(self, db: AsyncSession, storage: ObjectStorage | None = None) -> None:
        self.db = db
        self.storage = storage or ObjectStorage()

    # ---- 包管理 ----

    async def create_package(
        self,
        user: User,
        parsed: ParsedPetPackage,
        zip_bytes: bytes,
        row_mapping: dict,
        visibility: str = "private",
    ) -> PetPackage:
        if visibility not in ("private", "tenant", "public", "official"):
            raise PetPackageError(f"非法可见性: {visibility}")
        if visibility == "official" and user.role not in ADMIN_ROLES:
            raise PetPackageError("仅管理员可上传官方宠物包")

        count = await self.db.scalar(
            select(func.count()).select_from(PetPackage).where(PetPackage.owner_id == user.id)
        )
        if (count or 0) >= MAX_PACKAGES_PER_USER and user.role not in ADMIN_ROLES:
            raise PetPackageError(f"上传数量超过限制（{MAX_PACKAGES_PER_USER} 个）")

        mapping = validate_row_mapping(row_mapping, parsed.row_count)
        mapping["_row_frames"] = parsed.row_frames

        pkg = PetPackage(
            name=parsed.name,
            display_name=parsed.display_name,
            description=parsed.description,
            kind=parsed.kind,
            owner_id=user.id,
            tenant_id=user.tenant_id,
            visibility=visibility,
            manifest=parsed.manifest,
            row_mapping=mapping,
            frame_width=parsed.frame_width,
            frame_height=parsed.frame_height,
            col_count=parsed.col_count,
            row_count=parsed.row_count,
            spritesheet_key="",
            package_key="",
        )
        self.db.add(pkg)
        await self.db.flush()

        prefix = f"pets/{pkg.tenant_id}/{pkg.owner_id}/{pkg.id}"
        pkg.package_key = f"{prefix}/package.zip"
        pkg.spritesheet_key = f"{prefix}/spritesheet{parsed.spritesheet_ext}"
        self.storage.put(pkg.package_key, zip_bytes, "application/zip")
        self.storage.put(pkg.spritesheet_key, parsed.spritesheet_bytes, "image/webp" if parsed.spritesheet_ext == ".webp" else "image/png")

        # 上传者自动领养（不自动激活）
        self.db.add(UserPet(user_id=user.id, package_id=pkg.id))
        await self.db.flush()
        return pkg

    async def get_visible_package(self, user: User, package_id: UUID) -> PetPackage:
        result = await self.db.execute(
            visible_packages_query(user).where(PetPackage.id == package_id)
        )
        pkg = result.scalar_one_or_none()
        if pkg is None:
            raise PetNotFoundError(str(package_id))
        return pkg

    async def list_market(self, user: User) -> list[PetPackage]:
        result = await self.db.execute(
            visible_packages_query(user).order_by(
                # official 置顶，其余按创建时间倒序
                (PetPackage.visibility != "official"),
                PetPackage.created_at.desc(),
            )
        )
        return list(result.scalars().all())

    async def list_my_packages(self, user: User) -> list[PetPackage]:
        result = await self.db.execute(
            select(PetPackage)
            .where(PetPackage.owner_id == user.id)
            .order_by(PetPackage.created_at.desc())
        )
        return list(result.scalars().all())

    async def set_visibility(self, user: User, package_id: UUID, visibility: str) -> PetPackage:
        if visibility not in ("private", "tenant", "public"):
            raise PetPackageError(f"非法可见性: {visibility}（official 仅管理员可设）")
        pkg = await self._get_owned_package(user, package_id)
        pkg.visibility = visibility
        await self.db.flush()
        return pkg

    async def admin_set_visibility(self, package_id: UUID, visibility: str) -> PetPackage:
        if visibility not in ("private", "tenant", "public", "official"):
            raise PetPackageError(f"非法可见性: {visibility}")
        pkg = await self.db.get(PetPackage, package_id)
        if pkg is None:
            raise PetNotFoundError(str(package_id))
        pkg.visibility = visibility
        await self.db.flush()
        return pkg

    async def admin_takedown(self, package_id: UUID, taken_down: bool) -> PetPackage:
        pkg = await self.db.get(PetPackage, package_id)
        if pkg is None:
            raise PetNotFoundError(str(package_id))
        pkg.status = "taken_down" if taken_down else "active"
        await self.db.flush()
        return pkg

    async def delete_package(self, user: User, package_id: UUID) -> None:
        pkg = await self._get_owned_package(user, package_id)
        prefix = f"pets/{pkg.tenant_id}/{pkg.owner_id}/{pkg.id}"
        await self.db.delete(pkg)
        await self.db.flush()
        self.storage.delete_prefix(prefix)

    async def _get_owned_package(self, user: User, package_id: UUID) -> PetPackage:
        pkg = await self.db.get(PetPackage, package_id)
        if pkg is None or (pkg.owner_id != user.id and user.role not in ADMIN_ROLES):
            raise PetNotFoundError(str(package_id))
        return pkg

    # ---- 领养 / 激活 ----

    async def adopt(self, user: User, package_id: UUID) -> UserPet:
        await self.get_visible_package(user, package_id)  # 可见性校验
        result = await self.db.execute(
            select(UserPet).where(UserPet.user_id == user.id, UserPet.package_id == package_id)
        )
        pet = result.scalar_one_or_none()
        if pet is not None:
            return pet
        pet = UserPet(user_id=user.id, package_id=package_id)
        self.db.add(pet)
        await self.db.flush()
        return pet

    async def activate(self, user: User, user_pet_id: UUID) -> UserPet:
        pet = await self.db.get(UserPet, user_pet_id)
        if pet is None or pet.user_id != user.id:
            raise PetNotFoundError(str(user_pet_id))
        await self.db.execute(
            update(UserPet).where(UserPet.user_id == user.id).values(is_active=False)
        )
        pet.is_active = True
        await self.db.flush()
        return pet

    async def deactivate_all(self, user: User) -> None:
        await self.db.execute(
            update(UserPet).where(UserPet.user_id == user.id).values(is_active=False)
        )

    async def list_my_pets(self, user: User) -> list[tuple[UserPet, PetPackage]]:
        result = await self.db.execute(
            select(UserPet, PetPackage)
            .join(PetPackage, PetPackage.id == UserPet.package_id)
            .where(UserPet.user_id == user.id)
            .order_by(UserPet.adopted_at.desc())
        )
        return list(result.all())

    async def get_active(self, user: User) -> tuple[UserPet, PetPackage] | None:
        result = await self.db.execute(
            select(UserPet, PetPackage)
            .join(PetPackage, PetPackage.id == UserPet.package_id)
            .where(UserPet.user_id == user.id, UserPet.is_active.is_(True))
        )
        return result.first()
