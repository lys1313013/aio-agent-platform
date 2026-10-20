"""Validated, atomic edits of a user's tenant-scoped portrait."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import PortraitVersion, UserProfile


class PortraitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["create", "append", "replace", "clear", "overwrite"]
    portrait: str | None = None
    old_str: str | None = None
    new_str: str | None = None
    expected_portrait: str | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        fields = {
            "create": {"portrait"},
            "append": {"portrait"},
            "replace": {"old_str", "new_str"},
            "clear": {"expected_portrait"},
            "overwrite": {"expected_portrait", "portrait"},
        }[self.operation]
        if self.model_fields_set != fields | {"operation"}:
            raise ValueError(f"{self.operation} requires exactly these fields: {sorted(fields)}")
        for field in fields:
            value = getattr(self, field)
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(f"{field} must be a string without NUL characters")
            if field != "new_str" and not value.strip():
                raise ValueError(f"{field} must not be empty; use operation='clear' to clear a portrait")
        return self


def edit_portrait(current: str, request: PortraitUpdate) -> str:
    """Apply an explicit operation; never guess a missing or ambiguous target."""
    if request.operation == "create":
        if current.strip():
            raise ValueError("画像已存在，请读取后使用 replace 或 append")
        assert request.portrait is not None
        return request.portrait
    if not current.strip():
        raise ValueError("画像为空，请使用 create 创建")
    if request.operation == "append":
        assert request.portrait is not None
        return current + ("" if current.endswith("\n") else "\n") + request.portrait
    if request.operation == "replace":
        assert request.old_str is not None and request.new_str is not None
        first = current.find(request.old_str)
        if first < 0 or current.find(request.old_str, first + 1) >= 0:
            raise ValueError("old_str 必须精确且唯一匹配；原文不存在或匹配多处，请重新读取画像")
        result = current.replace(request.old_str, request.new_str, 1)
        if not result.strip():
            raise ValueError("替换会清空画像，请显式使用 clear 并提供 expected_portrait")
        return result
    if request.expected_portrait != current:
        raise ValueError("画像已变更，expected_portrait 与当前全文不一致；请重新读取画像")
    if request.operation == "clear":
        return ""
    assert request.portrait is not None
    return request.portrait


async def update_portrait(
    db: AsyncSession, user_id: UUID, tenant_id: UUID, request: PortraitUpdate,
) -> dict:
    scope = (UserProfile.user_id == user_id, UserProfile.tenant_id == tenant_id)
    row = (await db.execute(select(UserProfile.personal_portrait).where(*scope))).one_or_none()
    before = row[0] if row is not None else None
    after = edit_portrait(before or "", request)
    if after == (before or ""):
        return {"action": "unchanged", "portrait": after}

    if row is None:
        db.add(UserProfile(user_id=user_id, tenant_id=tenant_id, personal_portrait=after))
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise ValueError("画像被并发创建，请重新读取后编辑") from exc
    else:
        # Compare-and-swap prevents another chat/settings write being lost between
        # the read and write. The snapshot commits only with the successful edit.
        result = await db.execute(
            update(UserProfile).where(*scope, UserProfile.personal_portrait == before)
            .values(personal_portrait=after or None)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await db.rollback()
            raise ValueError("画像被并发修改，请重新读取后编辑")

    if before and before.strip():
        db.add(PortraitVersion(user_id=user_id, tenant_id=tenant_id, content=before, source="ai"))
    await db.commit()
    return {"action": request.operation, "portrait": after}
