"""Attachment authorization regressions: no live DB, storage, or model requests."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from aio_agent_platform.core.chat import load_conversation_history
from aio_agent_platform.interface.routes import chat
from aio_agent_platform.llm.client import build_user_content
from aio_agent_platform.rooms import service
from aio_agent_platform.rooms.schemas import RoomSend
from aio_agent_platform.storage.chat_attachments import (
    AttachmentAccessError,
    ChatAttachmentStorage,
    validate_attachment_key,
    validate_attachment_refs,
)
from aio_agent_platform.storage.client import ObjectInfo

OWNER, SESSION, FOREIGN = str(uuid4()), str(uuid4()), str(uuid4())


def ref(key=None, **overrides):
    return {
        "key": key or f"chat-attachments/{OWNER}/{SESSION}/image.png",
        "url": "https://untrusted.invalid/secret",
        "mime": "image/png", "size": 5, "filename": "image.png", **overrides,
    }


BAD_KEYS = [
    f"chat-attachments/{FOREIGN}/{SESSION}/image.png",
    f"chat-attachments/{OWNER}/{FOREIGN}/image.png",
    f"workspaces/{OWNER}/{SESSION}/image.png",
    f"chat-attachments/{OWNER}/{SESSION}/../image.png",
    f"chat-attachments/{OWNER}/{SESSION}/..",
    f"chat-attachments/{OWNER}/{SESSION}/%2e%2e",
    f"chat-attachments/{OWNER}/{SESSION}/image.png?token=bad",
    f"chat-attachments/{OWNER}/{SESSION}/image\\.png",
    f"chat-attachments/{OWNER}/{SESSION}/image.png\n",
    "data:image/png;base64,AAAA",
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_storage_rejects_unowned_or_malformed_key_before_any_io(key):
    objects = Mock()
    storage = ChatAttachmentStorage(objects)
    with pytest.raises(AttachmentAccessError):
        storage.to_data_uri(key, "image/png", user_id=OWNER, session_id=SESSION)
    objects.stat.assert_not_called()
    objects.get.assert_not_called()


@pytest.mark.parametrize("sid", [SESSION, "_pending"])
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_authorized_image_uses_server_mime_and_url(sid, provider):
    key = f"chat-attachments/{OWNER}/{sid}/image.png"
    objects = Mock()
    objects.stat.return_value = ObjectInfo(key=key, size=5, etag="etag", content_type="image/jpeg")
    objects.get.return_value = b"image"
    storage = ChatAttachmentStorage(objects)
    attachments = validate_attachment_refs([ref(key)], user_id=OWNER, session_id=SESSION)
    content = build_user_content(
        "hello", attachments, provider,
        partial(storage.to_data_uri, user_id=OWNER, session_id=SESSION),
    )
    assert "untrusted.invalid" not in str(content)
    assert f"/api/public/images/{key}" in content[0]["text"]
    if provider == "anthropic":
        assert content[1]["source"]["media_type"] == "image/jpeg"
    else:
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;")
    objects.get.assert_called_once_with(key)


def test_metadata_rejects_non_image_object_before_download():
    objects = Mock()
    key = ref()["key"]
    objects.stat.return_value = ObjectInfo(key=key, size=5, etag="etag", content_type="text/plain")
    with pytest.raises(AttachmentAccessError):
        ChatAttachmentStorage(objects).to_data_uri(key, "image/png", user_id=OWNER, session_id=SESSION)
    objects.get.assert_not_called()


@pytest.mark.parametrize("endpoint,message", [(chat.chat, "hello"), (chat.chat_stream, "hello"),
                                               (chat.chat_stream, "/context")])
@pytest.mark.parametrize("key", BAD_KEYS[:4])
async def test_entry_points_reject_before_db_storage_or_model(endpoint, message, key, monkeypatch):
    objects = Mock(side_effect=AssertionError("storage must not be initialized"))
    monkeypatch.setattr(chat, "ChatAttachmentStorage", objects)
    db = Mock()
    req = chat.ChatRequest(session_id=SESSION, message=message, attachments=[ref(key)])
    with pytest.raises(HTTPException) as error:
        await endpoint(req, Mock(), SimpleNamespace(id=OWNER), db)
    assert error.value.status_code == 403
    assert not db.mock_calls
    objects.assert_not_called()


async def test_upload_rejects_foreign_session_before_reading_bytes(monkeypatch):
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    file = SimpleNamespace(read=AsyncMock())
    objects = Mock(side_effect=AssertionError("storage must not be initialized"))
    monkeypatch.setattr(chat, "ChatAttachmentStorage", objects)
    with pytest.raises(HTTPException) as error:
        await chat.upload_chat_attachment(SimpleNamespace(id=OWNER), db, file, SESSION)
    assert error.value.status_code == 404
    file.read.assert_not_awaited()
    objects.assert_not_called()


def history_db(attachment, owner=OWNER):
    message = SimpleNamespace(role="user", content="hello", attachments=[attachment], tool_calls=None)
    messages = Mock()
    messages.scalars.return_value.all.return_value = [message]
    session = Mock()
    session.scalar_one_or_none.return_value = SimpleNamespace(id=SESSION, user_id=owner, context_summary=None)
    return SimpleNamespace(execute=AsyncMock(side_effect=[messages, session]))


@pytest.mark.parametrize("allow_images", [True, False])
async def test_polluted_history_is_not_rehydrated_or_rendered_as_url(allow_images, monkeypatch):
    objects = Mock(side_effect=AssertionError("storage must not be initialized"))
    monkeypatch.setattr("aio_agent_platform.storage.chat_attachments.ChatAttachmentStorage", objects)
    db = history_db(ref(BAD_KEYS[0]))
    with pytest.raises(AttachmentAccessError):
        await load_conversation_history(db, SESSION, allow_images=allow_images)
    objects.assert_not_called()


async def test_text_model_history_uses_canonical_url_without_storage(monkeypatch):
    monkeypatch.setattr("aio_agent_platform.storage.client.ObjectStorage.__init__",
                        Mock(side_effect=AssertionError("storage must not be initialized")))
    history, _ = await load_conversation_history(history_db(ref()), SESSION, allow_images=False)
    assert "untrusted.invalid" not in history[0].content
    assert f"/api/public/images/{ref()['key']}" in history[0].content


def test_room_uses_shared_validation_and_keeps_pending_uploads():
    key = f"chat-attachments/{OWNER}/_pending/image.png"
    data = service.validate_attachments(
        RoomSend(request_id=uuid4(), attachments=[ref(key)]),
        SimpleNamespace(id=SESSION), SimpleNamespace(id=OWNER),
    )
    assert "untrusted.invalid" not in data["attachments"][0]["url"]
    with pytest.raises(HTTPException) as error:
        service.validate_attachments(
            RoomSend(request_id=uuid4(), attachments=[ref(BAD_KEYS[1])]),
            SimpleNamespace(id=SESSION), SimpleNamespace(id=OWNER),
        )
    assert error.value.status_code == 403


def test_new_conversation_only_accepts_pending_namespace():
    with pytest.raises(AttachmentAccessError):
        validate_attachment_key(ref()["key"], user_id=OWNER, session_id=None)
    validate_attachment_key(f"chat-attachments/{OWNER}/_pending/image.png", user_id=OWNER, session_id=None)
