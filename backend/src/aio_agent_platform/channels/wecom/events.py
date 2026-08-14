"""Normalize WeCom callback message XML into InboundEvent.

企业内部应用形态：无群聊、无 @，每个用户以 ``userid`` 与机器人单聊。
  - ``text``  → ``Content``
  - ``image`` → ``MediaId`` + ``PicUrl``
  - ``file``  → ``MediaId`` + ``FileName``（可缺失，兜底 ``"file"``）
其余 MsgType 跳过（不产生 InboundEvent）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from uuid import UUID

import structlog

from aio_agent_platform.channels.adapter import AttachmentInfo, ChatKind, InboundEvent

logger = structlog.get_logger()


def _xml_to_dict(xml_text: str) -> dict[str, str]:
    """Parse WeCom's flat message XML into a dict of tag → text."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    return {child.tag: child.text or "" for child in root}


def normalize_event(channel_id: UUID, xml_text: str) -> InboundEvent | None:
    """Convert a decrypted WeCom callback message XML into an InboundEvent.

    ``event_id`` falls back to ``{userid}:{CreateTime}`` when MsgId is absent
    so dedup still has a stable key.
    """
    msg = _xml_to_dict(xml_text)
    msg_type = msg.get("MsgType", "")
    from_user = msg.get("FromUserName", "")
    event_id = msg.get("MsgId", "") or f"{from_user}:{msg.get('CreateTime', '')}"
    if not from_user or not event_id:
        logger.warning("wecom_event_missing_identity", msg_type=msg_type)
        return None

    attachment = None
    text = ""
    if msg_type == "text":
        text = msg.get("Content", "")
    elif msg_type == "image":
        media_id = msg.get("MediaId", "")
        if not media_id:
            logger.debug("wecom_image_missing_media_id")
            return None
        attachment = AttachmentInfo(
            resource_key=media_id,
            resource_type="image",
            filename=msg.get("PicUrl", "").rsplit("/", 1)[-1] or "image",
        )
    elif msg_type == "file":
        media_id = msg.get("MediaId", "")
        if not media_id:
            logger.debug("wecom_file_missing_media_id")
            return None
        attachment = AttachmentInfo(
            resource_key=media_id,
            resource_type="file",
            filename=msg.get("FileName", "") or "file",
        )
    else:
        logger.debug("wecom_non_text_skipped", msg_type=msg_type)
        return None

    return InboundEvent(
        channel_id=channel_id,
        event_id=event_id,
        chat_id=from_user,  # 企业内部应用无群聊，单聊会话即用户
        external_id=from_user,
        text=text,
        chat_kind=ChatKind.DIRECT,
        message_id=msg.get("MsgId") or None,
        attachment=attachment,
        raw=msg,
    )
