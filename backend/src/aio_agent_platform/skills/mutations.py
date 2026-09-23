"""Agent-facing mutation contract; identity is supplied only by the runtime."""
from __future__ import annotations

import json
import logging
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select

from aio_agent_platform.core.context import current_agent_id
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import Session as ChatSession
from aio_agent_platform.db.models import SkillMutation, Workspace
from aio_agent_platform.skills.contracts import SkillError, digest, normalize_files
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.skills.workspace import read_workspace_file


class SkillToolOutput(str):
    def __new__(cls, payload):
        value = super().__new__(cls, json.dumps(payload, ensure_ascii=False))
        value.success = payload["success"]
        value.error = None if value.success else str(value)
        return value


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str | None = None
    source_path: str | None = None
    description: str = ""

    @model_validator(mode="after")
    def one_source(self):
        if (self.content is None) == (self.source_path is None):
            raise ValueError("content 和 source_path 必须且只能提供一个")
        return self


class CreateSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)
    description: str = Field(default="", max_length=2000)
    category: Literal["general", "coding", "ops", "research", "writing"] = "general"
    tags: list[str] = Field(default_factory=list, max_length=30)
    trigger_condition: str = ""
    files: list[Attachment] = Field(default_factory=list, max_length=50)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class UpdateSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: UUID
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    content: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, max_length=2000)
    category: Literal["general", "coding", "ops", "research", "writing"] | None = None
    tags: list[str] | None = Field(default=None, max_length=30)
    trigger_condition: str | None = None
    files: list[Attachment] = Field(default_factory=list, max_length=50)
    remove_files: list[str] = Field(default_factory=list, max_length=50)
    change_summary: str = Field(min_length=1, max_length=2000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


def result_for(skill, operation):
    return {"success": True, "operation": operation, "status": skill.mutation_status,
            "skill_id": str(skill.id), "name": skill.name, "description": skill.description,
            "version": skill.version, "previous_version": getattr(skill, "previous_version", None),
            "files": skill.files, "verification": skill.verification or {"status": "unverified"},
            "is_active": skill.is_active, "visibility": "public" if skill.is_public else "private",
            "changes": (skill.provenance or {}).get("modified", {}) if operation == "update" else {},
            "url": f"/skills/{skill.id}"}


async def mutate_skill(operation, arguments, user_id, session_id, storage, *,
                       tool_executor=None, workspace_id=None, workspace_slug=None, **kwargs):
    try:
        model = CreateSkillInput if operation == "create" else UpdateSkillInput
        req = model.model_validate(arguments)
        payload = req.model_dump(mode="json", exclude={"request_id"})
        request_digest = digest({"operation": operation, **payload})
        request_id = f"{session_id}:{req.request_id or request_digest}"
        uid = UUID(user_id)
        factory = get_session_factory()
        async with factory() as db:
            try:
                await db.execute(select(func.set_config("app.current_user_id", user_id, True)))
                await SkillService.lock_user(db, uid)
                receipt = await db.get(SkillMutation, (uid, request_id))
                if receipt:
                    if receipt.request_digest != request_digest:
                        raise SkillError("idempotency_conflict", "该请求标识已用于不同内容")
                    if not await SkillService.get_skill(db, UUID(receipt.result["skill_id"]), uid):
                        raise SkillError("not_found", "原操作的技能已删除或不可访问")
                    return SkillToolOutput(receipt.result)
                # Never infer ownership/agent identity from model arguments.
                session = (await db.execute(select(ChatSession).where(ChatSession.id == UUID(session_id),
                                                ChatSession.user_id == uid))).scalar_one_or_none()
                # Some API entry points create the trusted session in an outer
                # transaction. Missing provenance must not block that first turn.
                source = {"type": "agent", "user_id": user_id, "session_id": session_id,
                          "agent_id": current_agent_id.get() or (str(session.agent_id) if session and session.agent_id else None),
                          "tool_call_id": kwargs.get("tool_call_id"), "request_id": request_id}
                files = []
                for attachment in req.files:
                    entry = attachment.model_dump(exclude_none=True)
                    if attachment.source_path is not None:
                        if not tool_executor or not workspace_id:
                            raise SkillError("workspace_unavailable", "当前会话没有可用工作区")
                        workspace = (await db.execute(select(Workspace).where(Workspace.id == UUID(workspace_id),
                                                    Workspace.user_id == uid))).scalar_one_or_none()
                        if not workspace or workspace.slug != workspace_slug:
                            raise SkillError("permission_denied", "工作区不可访问")
                        entry["content"] = await read_workspace_file(tool_executor.sandbox_mgr, user_id, session_id,
                                                  workspace_id, workspace_slug, attachment.source_path)
                    files.append(entry)
                files = normalize_files(files)
                if operation == "create":
                    params = req.model_dump(exclude={"files", "request_id"})
                    skill = await SkillService.create_skill(db, uid, **params, files=files, storage=storage, source=source)
                else:
                    params = req.model_dump(exclude={"files", "request_id", "skill_id"}, exclude_none=True)
                    skill = await SkillService.update_skill(db, req.skill_id, uid, **params,
                                                           file_changes=files, storage=storage, source=source)
                    if not skill:
                        raise SkillError("not_found", "技能不存在或无修改权限")
                result = result_for(skill, operation)
                db.add(SkillMutation(user_id=uid, request_id=request_id, request_digest=request_digest, result=result))
                await db.commit()
                return SkillToolOutput(result)
            except BaseException:
                await db.rollback()
                raise
    except ValidationError as exc:
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors(include_input=False)]
        return SkillToolOutput({"success": False, "code": "invalid_arguments", "message": "技能参数无效", "errors": errors})
    except SkillError as exc:
        return SkillToolOutput(exc.payload())
    except Exception:
        logging.getLogger(__name__).exception("skill_mutation_failed", extra={"operation": operation})
        return SkillToolOutput({"success": False, "code": "persistence_failed", "message": "技能保存失败，请稍后使用相同请求重试"})
