"""Validated room commands. Mentions use member ids, never model-generated text."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RoomCreate(Command):
    title: str = Field(default="新聊天室", min_length=1, max_length=512)
    goal: str = Field(min_length=1, max_length=10000)
    agent_ids: list[UUID] = Field(min_length=1, max_length=5)
    default_agent_id: UUID | None = None

    @model_validator(mode="after")
    def validate_members(self):
        if len(set(self.agent_ids)) != len(self.agent_ids):
            raise ValueError("不能重复添加同一智能体")
        if self.default_agent_id and self.default_agent_id not in self.agent_ids:
            raise ValueError("默认回答成员必须在房间内")
        return self


class RoomUpdate(Command):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    goal: str | None = Field(default=None, min_length=1, max_length=10000)
    agent_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=5)
    default_member_id: UUID | None = None
    default_agent_id: UUID | None = None
    is_pinned: bool | None = None
    is_archived: bool | None = None

    @model_validator(mode="after")
    def validate_members(self):
        if self.agent_ids and len(set(self.agent_ids)) != len(self.agent_ids):
            raise ValueError("不能重复添加同一智能体")
        return self


class ImageRef(Command):
    key: str = Field(max_length=512)
    url: str = Field(default="", max_length=4096)
    mime: Literal["image/jpeg", "image/png", "image/webp", "image/gif"]
    size: int = Field(ge=1, le=10 * 1024 * 1024)
    filename: str = Field(max_length=256)


class FileRef(Command):
    file_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=256)
    mime: str = Field(max_length=128)
    size: int = Field(ge=1, le=500 * 1024 * 1024)
    workspace_path: str = Field(min_length=1, max_length=512)


class RoomSend(Command):
    request_id: UUID
    message: str = Field(default="", max_length=50000)
    mode: Literal["default", "mentions", "all", "summary"] = "default"
    member_ids: list[UUID] = Field(default_factory=list, max_length=20)
    reply_to_id: UUID | None = None
    attachments: list[ImageRef] = Field(default_factory=list, max_length=4)
    file_attachments: list[FileRef] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_targets(self):
        if self.mode != "summary" and not (
            self.message or self.attachments or self.file_attachments
        ):
            raise ValueError("请输入消息或上传附件")
        if self.mode == "mentions" and not self.member_ids:
            raise ValueError("请选择被点名的成员")
        if self.mode in {"default", "all"} and self.member_ids:
            raise ValueError("全体回答、默认回答和点名不能混用")
        if self.mode == "summary" and len(self.member_ids) > 1:
            raise ValueError("总结只能指定一位成员")
        return self


class RoomRetry(Command):
    request_id: UUID
    acknowledge_side_effects: bool = False


class RoomConfirmation(Command):
    confirmation_id: str = Field(max_length=128)
    status: Literal["approved", "rejected", "modified"]
    selected_options: list[str] = Field(default_factory=list, max_length=100)
    user_input: str | None = Field(default=None, max_length=50000)
    table_data: list[dict] | None = Field(default=None, max_length=1000)
