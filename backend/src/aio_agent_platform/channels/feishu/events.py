"""Normalize Feishu event payloads into InboundEvent.

Feishu's ``im.message.receive_v1`` event carries a ``message`` object with:
  - ``chat_id``, ``chat_type`` ("p2p" / "group")
  - ``message_id``
  - ``message_type`` ("text" / "post" / ...)
  - ``content`` — a JSON string, e.g. '{"text":"@_user_1 hello"}'
  - ``mentions`` — optional list of {"id":{"open_id":"ou_xxx"}, "name":"Bot"}

This module converts that into the transport-agnostic ``InboundEvent`` shape
the pipeline consumes. Non-text messages are skipped (we only support text
in this milestone).
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog

from aio_agent_platform.channels.adapter import AttachmentInfo, ChatKind, InboundEvent

logger = structlog.get_logger()


def _parse_attachment(message_type: str, content: dict) -> AttachmentInfo | None:
    """Extract file/image resource info from a Feishu message content."""
    if message_type == "file":
        file_key = content.get("file_key", "")
        if not file_key:
            return None
        return AttachmentInfo(
            resource_key=file_key,
            resource_type="file",
            filename=content.get("file_name", "") or "file",
        )
    if message_type == "image":
        image_key = content.get("image_key", "")
        if not image_key:
            return None
        return AttachmentInfo(
            resource_key=image_key,
            resource_type="image",
            filename="image",  # 扩展名下载后按 magic bytes 判定
        )
    return None


def normalize_event(
    channel_id: UUID,
    event_id: str,
    event: dict,
    bot_app_id: str,
) -> InboundEvent | None:
    """Convert a Feishu ``im.message.receive_v1`` payload into an InboundEvent.

    Supports text, file and image messages. ``file`` / ``image`` messages carry
    an ``attachment`` (resource_key + filename) so the pipeline can download
    and store the resource; other non-text types are skipped.
    """
    msg = event.get("event", {}).get("message", {}) or {}

    message_type = msg.get("message_type")

    try:
        content = json.loads(msg.get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        content = {}

    attachment = None
    if message_type == "text":
        raw_text = content.get("text", "") or ""
    elif message_type in ("file", "image"):
        attachment = _parse_attachment(message_type, content)
        if attachment is None:
            logger.debug(
                "feishu_attachment_missing_key",
                message_type=message_type,
                chat_id=msg.get("chat_id"),
            )
            return None
        raw_text = ""
    else:
        logger.debug(
            "feishu_non_text_skipped",
            message_type=message_type,
            chat_id=msg.get("chat_id"),
        )
        return None

    # Parse mentions.
    mentions = msg.get("mentions") or []
    bot_mentioned = False
    cleaned_text = raw_text
    for m in mentions:
        mid = (m.get("id") or {}).get("open_id") or (m.get("id") or {}).get("app_id")
        if mid == bot_app_id:
            bot_mentioned = True
        # Strip the @_user_N placeholder so the agent sees clean text.
        placeholder = m.get("key", "")  # e.g. "@_user_1"
        if placeholder:
            cleaned_text = cleaned_text.replace(placeholder, "").strip()

    chat_type = msg.get("chat_type", "p2p")
    chat_kind = ChatKind.GROUP if chat_type == "group" else ChatKind.DIRECT

    sender = event.get("event", {}).get("sender", {}) or {}
    external_id = (sender.get("sender_id") or {}).get("open_id", "")
    if not external_id:
        logger.warning("feishu_event_missing_sender", event_id=event_id)
        return None

    return InboundEvent(
        channel_id=channel_id,
        event_id=event_id,
        chat_id=msg.get("chat_id", ""),
        external_id=external_id,
        text=cleaned_text,
        chat_kind=chat_kind,
        message_id=msg.get("message_id"),
        mentions_bot=bot_mentioned,
        attachment=attachment,
        raw=event,
    )
