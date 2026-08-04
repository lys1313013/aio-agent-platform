"""宠物包与领养的业务逻辑。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import PetExpLog, PetPackage, SystemConfig, User, UserPet
from aio_agent_platform.pets.package import ParsedPetPackage, PetPackageError
from aio_agent_platform.storage.client import ObjectStorage

MAX_PACKAGES_PER_USER = 20
INTERACT_DAILY_LIMIT = 5
MAX_LEVEL = 50
ADMIN_ROLES = {"admin", "superadmin"}

# 平台状态名（row_mapping 的合法 key），idle 必选。
# 互动菜单/点击播放直接按精灵图行操作，不走 row_mapping，故无额外状态。
VALID_ROW_STATES = {"idle", "think", "work", "wait", "celebrate", "sad", "sleep", "happy"}

# Codex 标准 9 行精灵图布局的默认 行→状态 映射（2026-08-02 用 ~/.codex/pets 下 4 个官方包 + spec 校准）。
# 行序：0 idle / 1 running-right / 2 running-left / 3 waving / 4 jumping /
#       5 failed / 6 waiting / 7 running(工作中) / 8 review(思考)。
# sleep 无对应行 → 不映射，运行期降级 idle。
DEFAULT_ROW_MAPPING = {
    "idle": 0,
    "think": 8,  # review
    "work": 7,   # running = 活跃工作循环
    "wait": 6,   # waiting
    "celebrate": 4,  # jumping
    "sad": 5,    # failed
    "happy": 3,  # waving
}

# 平台状态中文名（动作目录生成 + 智能体词汇表共用）
STATE_LABELS = {
    "idle": "待机",
    "think": "思考",
    "work": "工作",
    "wait": "等待",
    "celebrate": "庆祝",
    "sad": "沮丧",
    "sleep": "睡觉",
    "happy": "开心",
}

# Codex 标准 9 行精灵图的行名（2026-08-02 校准），缺省动作名占位「动画 N」
CODEX_ROW_NAMES = ["待机", "向右跑", "向左跑", "挥手", "跳跃", "失败", "等待", "工作", "思考"]
PLACEHOLDER_PREFIX = "动画"

# 智能体气泡每日配额（可经 SystemConfig 调整）
BUBBLE_DAILY_LIMIT_DEFAULT = 3000
KEY_PET_SMART_ENABLED = "pet_smart_enabled"
KEY_PET_BUBBLE_LIMIT = "pet_bubble_daily_limit"

# 智能体词汇表上限（注入气泡 prompt 的动作名数量）
AGENT_VOCAB_LIMIT = 12


def default_row_mapping(row_count: int) -> dict:
    """标准 9 行包返回完整默认映射；不足 9 行的自定义包只保证 idle（行 0）。"""
    if row_count >= 9:
        return dict(DEFAULT_ROW_MAPPING)
    return {"idle": 0}


def build_actions(row_mapping: dict, row_count: int) -> dict:
    """由状态映射自动生成动作目录 {str(row): {name, state}}。

    name = 状态中文名 → Codex 标准行名 → 「动画 N」；state = 反查到的状态（无则 null）。
    """
    state_to_row = {s: r for s, r in row_mapping.items() if not s.startswith("_")}
    actions: dict[str, dict] = {}
    for row in range(row_count):
        state = next((s for s, r in state_to_row.items() if r == row), None)
        if state:
            name = STATE_LABELS.get(state, state)
        elif row < len(CODEX_ROW_NAMES):
            name = CODEX_ROW_NAMES[row]
        else:
            name = f"{PLACEHOLDER_PREFIX} {row}"
        actions[str(row)] = {"name": name, "state": state}
    return actions


def level_from_exp(exp: int) -> int:
    """累计经验 → 等级。等级 n 需 100*(n-1)^1.5 累计经验，上限 MAX_LEVEL。"""
    level = 1
    while level < MAX_LEVEL and exp >= 100 * level ** 1.5:
        level += 1
    return level


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

        # 标准 9 行包自动套用 Codex 默认映射；用户显式传入的映射覆盖之
        mapping = validate_row_mapping(
            {**default_row_mapping(parsed.row_count), **row_mapping},
            parsed.row_count,
        )
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
            actions=build_actions(mapping, parsed.row_count),
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

    async def set_row_mapping(self, user: User, package_id: UUID, mapping: dict) -> PetPackage:
        pkg = await self._get_owned_package(user, package_id)
        clean = validate_row_mapping(mapping, pkg.row_count)
        # 保留平台附加字段（如 _row_frames）
        existing = {k: v for k, v in (pkg.row_mapping or {}).items() if k.startswith("_")}
        pkg.row_mapping = {**clean, **existing}
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

    async def remove_pet(self, user: User, user_pet_id: UUID) -> None:
        pet = await self.db.get(UserPet, user_pet_id)
        if pet is None or pet.user_id != user.id:
            raise PetNotFoundError(str(user_pet_id))
        await self.db.delete(pet)
        await self.db.flush()

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
        row = result.first()
        return (row[0], row[1]) if row is not None else None

    async def interact(self, user: User, user_pet_id: UUID) -> tuple[UserPet, bool]:
        """点击互动：+1 经验（每日上限 INTERACT_DAILY_LIMIT）。返回 (pet, 是否已加经验)。"""
        pet = await self.db.get(UserPet, user_pet_id)
        if pet is None or pet.user_id != user.id:
            raise PetNotFoundError(str(user_pet_id))

        today_count = await self.db.scalar(
            select(func.count())
            .select_from(PetExpLog)
            .where(
                PetExpLog.pet_id == user_pet_id,
                PetExpLog.reason == "interact",
                PetExpLog.created_at >= func.date_trunc("day", func.now()),
            )
        )
        if (today_count or 0) >= INTERACT_DAILY_LIMIT:
            return pet, False

        pet.exp += 1
        pet.level = level_from_exp(pet.exp)
        self.db.add(PetExpLog(user_id=user.id, pet_id=user_pet_id, delta=1, reason="interact"))
        await self.db.flush()
        return pet, True

    # ---- 绑定智能体 ----

    async def set_agent(self, user: User, user_pet_id: UUID, agent_id: UUID | None) -> UserPet:
        pet = await self.db.get(UserPet, user_pet_id)
        if pet is None or pet.user_id != user.id:
            raise PetNotFoundError(str(user_pet_id))
        if agent_id is not None:
            from aio_agent_platform.core.chat import load_agent

            agent = await load_agent(self.db, agent_id, user=user)
            if agent is None:
                raise PetPackageError("智能体不存在或对当前用户不可见")
        pet.agent_id = agent_id
        await self.db.flush()
        return pet

    async def set_default_agent(self, user: User, package_id: UUID, agent_id: UUID | None) -> PetPackage:
        pkg = await self._get_owned_package(user, package_id)
        if agent_id is not None:
            from aio_agent_platform.core.chat import load_agent

            agent = await load_agent(self.db, agent_id, user=user)
            if agent is None:
                raise PetPackageError("智能体不存在或对当前用户不可见")
        pkg.default_agent_id = agent_id
        await self.db.flush()
        return pkg

    async def resolve_agent(self, user: User, pet: UserPet, pkg: PetPackage):
        """解析绑定 Agent：实例绑定 → 包级默认 → None。实时可见性校验，失败即降级。"""
        agent_id = pet.agent_id or pkg.default_agent_id
        if agent_id is None:
            return None
        try:
            from aio_agent_platform.core.chat import load_agent

            return await load_agent(self.db, agent_id, user=user)
        except Exception:
            return None

    # ---- 动作系统 ----

    async def set_package_actions(self, user: User, package_id: UUID, actions: dict) -> PetPackage:
        """上传者改包级动作名：{row: name}。"""
        pkg = await self._get_owned_package(user, package_id)
        clean: dict[str, dict] = {}
        names: set[str] = set()
        for row_key, name in actions.items():
            try:
                row = int(row_key)
            except (TypeError, ValueError):
                raise PetPackageError(f"非法行号: {row_key}")
            if row < 0 or row >= pkg.row_count:
                raise PetPackageError(f"动作行号越界: {row}（共 {pkg.row_count} 行）")
            name = str(name).strip()
            if not name:
                raise PetPackageError(f"第 {row} 行动作名不能为空")
            if len(name) > 20:
                raise PetPackageError(f"第 {row} 行动作名过长（≤20 字）")
            if name in names:
                raise PetPackageError(f"动作名重复: {name}")
            names.add(name)
            existing = (pkg.actions or {}).get(str(row), {})
            state = existing.get("state") if isinstance(existing, dict) else None
            clean[str(row)] = {"name": name, "state": state}
        pkg.actions = clean
        await self.db.flush()
        return pkg

    async def set_pet_actions(
        self, user: User, user_pet_id: UUID, aliases: dict, state_mapping: dict | None
    ) -> UserPet:
        """领养者改实例级动作名覆盖 + 状态映射覆盖。aliases 空串 value 视为删除该条覆盖。"""
        pet = await self.db.get(UserPet, user_pet_id)
        if pet is None or pet.user_id != user.id:
            raise PetNotFoundError(str(user_pet_id))
        pkg = await self.db.get(PetPackage, pet.package_id)
        if pkg is None:
            raise PetNotFoundError(str(user_pet_id))

        clean_aliases: dict[str, str] = {}
        names: set[str] = set()
        for row_key, raw_name in aliases.items():
            try:
                row = int(row_key)
            except (TypeError, ValueError):
                raise PetPackageError(f"非法行号: {row_key}")
            if row < 0 or row >= pkg.row_count:
                raise PetPackageError(f"动作行号越界: {row}（共 {pkg.row_count} 行）")
            name = str(raw_name).strip()
            if not name:
                continue  # 空名删除该条覆盖
            if len(name) > 20:
                raise PetPackageError(f"第 {row} 行动作名过长（≤20 字）")
            if name in names:
                raise PetPackageError(f"动作名重复: {name}")
            names.add(name)
            clean_aliases[str(row)] = name
        pet.action_aliases = clean_aliases or None

        if state_mapping is not None:
            clean_map: dict[str, int] = {}
            for state, row in state_mapping.items():
                if state not in VALID_ROW_STATES:
                    raise PetPackageError(f"非法状态名: {state}")
                if not isinstance(row, int) or row < 0 or row >= pkg.row_count:
                    raise PetPackageError(f"状态 {state} 的行号越界: {row}")
                clean_map[state] = row
            pet.state_mapping = clean_map or None

        await self.db.flush()
        return pet

    def resolve_actions(self, pet: UserPet, pkg: PetPackage) -> tuple[list[dict], list[str]]:
        """解析动作目录（实例别名 > 包目录 > 状态名 > Codex 名 > 「动画 N」）。

        返回 (actions, vocab)：actions 为 [{row, name, state}] 最终展示；vocab 为智能体可寻址的名称列表。
        """
        pkg_actions = pkg.actions if isinstance(pkg.actions, dict) else {}
        aliases = pet.action_aliases if isinstance(pet.action_aliases, dict) else {}
        state_to_row = {
            s: r for s, r in (pkg.row_mapping or {}).items() if not s.startswith("_")
        }
        actions: list[dict] = []
        seen: set[str] = set()
        for row in range(pkg.row_count):
            key = str(row)
            pkg_entry = pkg_actions.get(key) if isinstance(pkg_actions, dict) else None
            pkg_name = pkg_entry.get("name") if isinstance(pkg_entry, dict) else None
            state = pkg_entry.get("state") if isinstance(pkg_entry, dict) else None
            if state is None:
                state = next((s for s, r in state_to_row.items() if r == row), None)
            if key in aliases:
                name = aliases[key]
            elif pkg_name:
                name = pkg_name
            elif state:
                name = STATE_LABELS.get(state, state)
            elif row < len(CODEX_ROW_NAMES):
                name = CODEX_ROW_NAMES[row]
            else:
                name = f"{PLACEHOLDER_PREFIX} {row}"
            actions.append({"row": row, "name": name, "state": state})
            if name and not name.startswith(PLACEHOLDER_PREFIX):
                seen.add(name)
        return actions, list(seen)[:AGENT_VOCAB_LIMIT]

    def resolve_state_mapping(self, pet: UserPet, pkg: PetPackage) -> dict:
        """解析状态映射：实例覆盖 > 包 row_mapping > 默认。idle 兜底。"""
        inst = dict(pet.state_mapping or {})
        pkg_map = {s: r for s, r in (pkg.row_mapping or {}).items() if not s.startswith("_")}
        merged = dict(DEFAULT_ROW_MAPPING)
        merged.update(pkg_map)
        merged.update(inst)
        merged.setdefault("idle", 0)
        return merged

    # ---- 智能互动开关与气泡配额 ----

    async def smart_enabled(self) -> bool:
        row = await self.db.scalar(
            select(SystemConfig).where(SystemConfig.key == KEY_PET_SMART_ENABLED)
        )
        if row is None or not row.value:
            return True
        return row.value.strip().lower() in ("1", "true", "yes", "on")

    async def _bubble_limit(self) -> int:
        row = await self.db.scalar(
            select(SystemConfig).where(SystemConfig.key == KEY_PET_BUBBLE_LIMIT)
        )
        if row is None or not row.value:
            return BUBBLE_DAILY_LIMIT_DEFAULT
        try:
            return int(row.value)
        except ValueError:
            return BUBBLE_DAILY_LIMIT_DEFAULT

    async def bubble_quota_remaining(self, user_id: UUID, user_pet_id: UUID) -> int:
        limit = await self._bubble_limit()
        used = await self.db.scalar(
            select(func.count())
            .select_from(PetExpLog)
            .where(
                PetExpLog.pet_id == user_pet_id,
                PetExpLog.reason == "bubble_llm",
                PetExpLog.created_at >= func.date_trunc("day", func.now()),
            )
        )
        return max(limit - (used or 0), 0)

    async def record_bubble(self, user_id: UUID, user_pet_id: UUID) -> None:
        """气泡成功生成后记一次配额流水（delta 0，仅计数）。"""
        self.db.add(PetExpLog(user_id=user_id, pet_id=user_pet_id, delta=0, reason="bubble_llm"))
        await self.db.flush()
