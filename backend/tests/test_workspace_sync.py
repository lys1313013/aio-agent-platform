"""Regression tests for content-based workspace synchronization (no external services)."""

import base64
import hashlib
import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from aio_agent_platform.storage.client import ObjectInfo, ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage


def sync_fixture(old: bytes | None, new: bytes):
    key = "workspaces/w/files/report.txt"
    objects = {key: old} if old is not None else {}
    storage = Mock(spec=ObjectStorage)
    storage.list.return_value = (
        [ObjectInfo(key, len(old), "multipart-etag-2")] if old is not None else []
    )
    storage.get.side_effect = objects.__getitem__
    storage.put.side_effect = lambda key, data, content_type: objects.__setitem__(key, data)
    workspace = WorkspaceStorage(storage)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        member = tarfile.TarInfo("./report.txt")
        member.size = len(new)
        tar.addfile(member, io.BytesIO(new))
    workspace._read_file_as_base64 = AsyncMock(
        return_value=base64.b64encode(archive.getvalue()).decode()
    )
    manager = SimpleNamespace(execute=AsyncMock(
        return_value=SimpleNamespace(exit_code=0, stderr="")
    ))
    return workspace, storage, manager, objects, key


@pytest.mark.parametrize(("old", "new", "uploads"), [
    (b"abc", b"xyz", 1),
    (b"abc", b"abc", 0),
    (b"abc", b"longer", 1),
    (None, b"new", 1),
    (b"", b"", 0),
])
async def test_sync_persists_content_and_skips_only_unchanged_files(old, new, uploads):
    workspace, storage, manager, objects, key = sync_fixture(old, new)

    stats = await workspace.extract_and_sync(manager, object(), "w")

    assert stats.errors == []
    assert stats.files_synced == uploads
    assert stats.bytes_transferred == len(new) * uploads
    assert objects[key] == new
    assert sum(call.args[0] == key for call in storage.put.call_args_list) == uploads
    manifest = json.loads(objects["workspaces/w/meta.json"])
    assert manifest["files"][0]["sha256"] == hashlib.sha256(new).hexdigest()


@pytest.mark.parametrize("failed_operation", ["get", "put"])
async def test_storage_failure_does_not_report_success_or_update_manifest(failed_operation):
    workspace, storage, manager, objects, key = sync_fixture(b"abc", b"xyz")
    getattr(storage, failed_operation).side_effect = RuntimeError("storage unavailable")

    stats = await workspace.extract_and_sync(manager, object(), "w")

    assert stats.errors and "storage unavailable" in stats.errors[0]
    assert stats.files_synced == 0
    assert objects[key] == b"abc"
    assert "workspaces/w/meta.json" not in objects
    storage.delete.assert_not_called()
