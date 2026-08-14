"""Channel file-send tool — lets an agent push a workspace file to the IM user.

The pipeline sets ``current_channel_send_ctx`` around the AgentLoop run so the
``send_file_to_user`` direct handler knows which chat/adapter to deliver to.
Only channels whose adapter ``supports_file_send`` inject the tool schema, so
web conversations never see the tool.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

import structlog

from aio_agent_platform.channels.adapter import ChannelAdapter, InboundEvent
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.tools.executor import ToolExecutor

logger = structlog.get_logger()

SEND_FILE_TOOL_NAME = "send_file_to_user"

SEND_FILE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SEND_FILE_TOOL_NAME,
        "description": (
            "当用户要求把某个文件发给他/发给我时使用，通过当前聊天渠道把工作区中的文件发送给用户。"
            "file_path 为文件在工作区中的相对路径（如 uploads/xxx.csv 或你刚才生成的文件名）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要发送的文件在工作区中的相对路径（含文件名，如 uploads/result.csv）。",
                },
                "file_name": {
                    "type": "string",
                    "description": "可选，发送时显示的文件名（含后缀），默认取 file_path 的文件名。",
                },
            },
            "required": ["file_path"],
        },
    },
}


@dataclass
class ChannelSendContext:
    """Carrier for the current channel conversation's outbound destination."""

    adapter: ChannelAdapter
    event: InboundEvent


current_channel_send_ctx: contextvars.ContextVar[ChannelSendContext | None] = (
    contextvars.ContextVar("current_channel_send_ctx", default=None)
)


async def _read_workspace_file(
    tool_executor: ToolExecutor | None,
    user_id: str,
    session_id: str,
    workspace_id: str | None,
    workspace_slug: str | None,
    path: str,
) -> bytes | None:
    """Read a workspace file's bytes, preferring the live sandbox.

    Agent-written files live in the running container; user-uploaded files also
    live in MinIO. Trying the sandbox first covers both after injection.
    """
    data: bytes | None = None
    sandbox_mgr = getattr(tool_executor, "sandbox_mgr", None)
    if sandbox_mgr is not None and workspace_id:
        try:
            sandbox = await sandbox_mgr.get_or_create(
                user_id, session_id, workspace_id, workspace_slug
            )
            data = await WorkspaceStorage.read_file_live(
                sandbox_mgr, sandbox, path, workspace_slug
            )
        except Exception:
            logger.warning("channel_file_read_sandbox_failed", path=path)
            data = None
    if data is None and workspace_id:
        try:
            data = WorkspaceStorage(ObjectStorage()).get_file(workspace_id, path)
        except Exception:
            data = None
    return data


async def handle_send_file(
    args: dict,
    user_id: str,
    session_id: str,
    *,
    tool_executor: ToolExecutor | None = None,
    workspace_id: str | None = None,
    workspace_slug: str | None = None,
    **kwargs,
) -> str:
    """Direct handler for ``send_file_to_user``."""
    ctx = current_channel_send_ctx.get()
    if ctx is None:
        return "当前会话不在聊天渠道中，无法直接发送文件。请告知用户通过工作区文件面板下载。"

    raw_path = (args.get("file_path") or args.get("path") or "").strip()
    if not raw_path:
        return "请提供要发送的文件路径（file_path 参数）。"

    # Normalize to a workspace-relative path (accepts /workspace/... absolute forms).
    if workspace_slug:
        path = ToolExecutor._sandbox_path(raw_path, workspace_slug)
    else:
        path = raw_path.strip("/")

    filename = (args.get("file_name") or "").strip()
    if not filename:
        filename = path.rsplit("/", 1)[-1] or "file"

    data = await _read_workspace_file(
        tool_executor, user_id, session_id, workspace_id, workspace_slug, path
    )
    if data is None:
        return f"无法读取文件 {filename}，请确认 file_path 是否正确。"
    if not data:
        return f"文件 {filename} 内容为空，无法发送。"
    limit = ctx.adapter.max_file_size_bytes
    if isinstance(limit, int) and len(data) > limit:
        return f"文件 {filename} 超过当前渠道上传上限（{limit // (1024 * 1024)}MB），无法发送。"

    message_id = await ctx.adapter.send_file(ctx.event, filename, data)
    if not message_id:
        return "❌ 发送失败，请稍后重试。"
    return f"✅ 已通过当前渠道发送 {filename}。"
