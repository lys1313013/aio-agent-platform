"""WeCom bot (API-mode long-connection) inbound frame normalization.

``aibot_msg_callback`` frames arrive from the WebSocket as JSON::

    {
      "cmd": "aibot_msg_callback",
      "headers": {"req_id": "aibot_msg_callback_<ts>_<rand>"},
      "body": {
        "msgid": "...", "aibotid": "...",
        "chatid": "...", "chattype": "single" | "group",
        "from": {"userid": "...", "corpid": "..."},
        "msgtype": "text" | "image" | "file" | ...,
        "text": {"content": "..."},
        "image": {"url": "...", "aeskey": "..."},
        "file": {"url": "...", "aeskey": "..."},
        ...
      }
    }

The transport stores the callback's ``req_id`` (needed to reply) and ``chatid``
(aeskey for media decryption) in ``InboundEvent.raw`` for the adapter.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from aio_agent_platform.channels.adapter import AttachmentInfo, ChatKind, InboundEvent


def normalize_event(channel_id: UUID, frame: dict[str, Any]) -> InboundEvent | None:
    """Convert an ``aibot_msg_callback`` frame into an ``InboundEvent``.

    Returns None for non-message callbacks (events) or message types the
    platform does not handle yet (voice/video/mixed).
    """
    if not isinstance(frame, dict) or frame.get("cmd") != "aibot_msg_callback":
        return None
    headers = frame.get("headers") or {}
    body = frame.get("body") or {}
    if not isinstance(body, dict):
        return None

    from_user = body.get("from") or {}
    external_id = from_user.get("userid") or ""
    chatid = body.get("chatid") or external_id
    if not external_id or not chatid:
        return None

    chat_kind = ChatKind.GROUP if body.get("chattype") == "group" else ChatKind.DIRECT
    message_id = body.get("msgid") or ""
    req_id = headers.get("req_id") or ""
    raw: dict[str, Any] = {
        "req_id": req_id,
        "chatid": chatid,
        "aibotid": body.get("aibotid"),
        "chattype": body.get("chattype"),
    }

    msgtype = body.get("msgtype")
    text = ""
    attachment: AttachmentInfo | None = None
    if msgtype == "text":
        text = (body.get("text") or {}).get("content") or ""
    elif msgtype == "image":
        media = body.get("image") or {}
        url = media.get("url") or ""
        if url:
            raw["aeskey"] = media.get("aeskey")
            attachment = AttachmentInfo(
                resource_key=url, resource_type="image", filename=_basename(url) or "image"
            )
    elif msgtype == "file":
        media = body.get("file") or {}
        url = media.get("url") or ""
        if url:
            raw["aeskey"] = media.get("aeskey")
            attachment = AttachmentInfo(
                resource_key=url, resource_type="file", filename=_basename(url) or "file"
            )
    else:
        # voice / video / mixed / event：v1 暂不支持。
        return None

    if not text and attachment is None:
        return None

    return InboundEvent(
        channel_id=channel_id,
        event_id=message_id or f"{external_id}:{chatid}:{req_id}",
        chat_id=chatid,
        external_id=external_id,
        text=text,
        chat_kind=chat_kind,
        message_id=message_id or None,
        mentions_bot=True,  # 单聊必是本机器人接收；群聊 v1 放宽为全员响应
        attachment=attachment,
        raw=raw,
    )


def _basename(url: str) -> str:
    """Extract a filename from a media URL path (strips query params)."""
    path = url.split("?", 1)[0].rstrip("/")
    name = path.rsplit("/", 1)[-1]
    return name or ""
