"""Sandbox lifecycle regressions, with an in-memory Docker daemon (no Docker/DB I/O)."""

import asyncio
import os
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from docker.errors import APIError, NotFound

from aio_agent_platform.core.config import settings
from aio_agent_platform.sandbox.models import SandboxManager
from aio_agent_platform.storage.workspace import SyncStats, WorkspaceStorage


class Container:
    def __init__(self, daemon, image, **kwargs):
        self.daemon = daemon
        self.id = str(len(daemon.created) + 1)
        self.name = kwargs["name"]
        self.labels = kwargs["labels"]
        self.status = "created"
        self.attrs = {"Created": "2026-09-21T00:00:00.000000000Z"}
        self.commands = []
        self.removed = False
        self.block_started = threading.Event()
        self.block_release = threading.Event()

    def start(self):
        self.status = "running"

    def stop(self, **kwargs):
        self.status = "exited"

    def remove(self, force=False):
        if self.status == "running" and not force:
            raise APIError("container running")
        self.removed = True
        self.daemon.items.pop(self.id, None)

    def exec_run(self, argv, **kwargs):
        command = argv[-1]
        self.commands.append(command)
        if command == "block":
            self.block_started.set()
            assert self.block_release.wait(5), "test did not release command"
        return SimpleNamespace(exit_code=0, output=(b"ok", b""))


class Daemon:
    def __init__(self):
        self.containers = self
        self.items = {}
        self.created = []
        self.close = Mock()

    def create(self, image, command, **kwargs):
        if any(c.name == kwargs["name"] for c in self.items.values()):
            raise APIError("name conflict")
        container = Container(self, image, **kwargs)
        self.items[container.id] = container
        self.created.append(container)
        return container

    def get(self, container_id):
        if container_id not in self.items:
            raise NotFound("missing")
        return self.items[container_id]

    def list(self, all=False, filters=None):
        result = []
        for container in self.items.values():
            if not all and container.status != "running":
                continue
            if any(
                container.labels.get(label.split("=", 1)[0]) != label.split("=", 1)[1]
                for label in filters.get("label", [])
            ):
                continue
            result.append(container)
        return result


@pytest.fixture
def setup(monkeypatch, tmp_path):
    daemon = Daemon()
    monkeypatch.setattr("aio_agent_platform.sandbox.models.docker.from_env", lambda: daemon)
    monkeypatch.setattr(settings.sandbox, "lock_dir", str(tmp_path))
    monkeypatch.setattr(settings.sandbox, "namespace", "test-deployment")
    storage = SimpleNamespace(
        inject_files=AsyncMock(return_value=SyncStats()),
        extract_and_sync=AsyncMock(return_value=SyncStats()),
    )
    return daemon, storage


async def obtain(manager, user="user", workspace="workspace", slug="default"):
    return await manager.get_or_create(user, "session", workspace, slug)


async def test_uploaded_file_refreshes_current_workspace_without_starting_container(setup, monkeypatch):
    daemon, storage = setup
    manager = SandboxManager(workspace_storage=storage)
    write = AsyncMock(return_value=True)
    monkeypatch.setattr(WorkspaceStorage, "write_file_live", write)
    assert not await manager.inject_uploaded_file("user", "workspace", "default", "uploads/a.pdf", b"pdf")
    assert not daemon.created
    sandbox = await obtain(manager)
    assert await manager.inject_uploaded_file("user", "workspace", "default", "uploads/a.pdf", b"pdf")
    write.assert_awaited_once_with(manager, sandbox, "uploads/a.pdf", b"pdf", "default")
    assert len(daemon.created) == 1
    # A backend restart must also refresh a container recovered from Docker.
    restarted = SandboxManager(workspace_storage=storage)
    assert await restarted.inject_uploaded_file("user", "workspace", "default", "uploads/b.pdf", b"new")
    assert write.await_args.args[2:] == ("uploads/b.pdf", b"new", "default")


async def test_result_file_is_synced_before_write_returns(setup, monkeypatch):
    _, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    writes = []

    async def live_write(*args):
        assert manager._owners[sandbox.user_id] is asyncio.current_task()
        writes.append("sandbox")
        return True

    storage.put_file = Mock(side_effect=lambda *_: writes.append("storage"))
    monkeypatch.setattr("aio_agent_platform.storage.workspace.WorkspaceStorage.write_file_live", live_write)
    assert await manager.write_workspace_file(sandbox, "workspace", "default", "result.txt", b"full")
    assert writes == ["sandbox", "storage"]
    storage.put_file.assert_called_once_with("workspace", "result.txt", b"full")


async def test_result_file_storage_failure_is_propagated(setup, monkeypatch):
    _, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    storage.put_file = Mock(side_effect=RuntimeError("storage failed"))
    monkeypatch.setattr(
        "aio_agent_platform.storage.workspace.WorkspaceStorage.write_file_live", AsyncMock(return_value=True),
    )
    with pytest.raises(RuntimeError, match="storage failed"):
        await manager.write_workspace_file(sandbox, "workspace", "default", "result.txt", b"full")


def expire(manager, sandbox):
    sandbox.last_used_at = datetime.now(UTC) - timedelta(seconds=settings.sandbox.session_ttl + 10)
    stamp = sandbox.last_used_at.timestamp()
    os.utime(manager._lock_path(sandbox.user_id), (stamp, stamp))


async def test_parallel_requests_across_managers_create_one_container(setup):
    daemon, storage = setup
    first, second = SandboxManager(storage), SandboxManager(storage)
    results = await asyncio.gather(*(obtain(first if i % 2 else second) for i in range(20)))
    assert len({result.container_id for result in results}) == 1
    assert len(daemon.created) == 1
    assert storage.inject_files.await_count == 1


async def test_shutdown_restart_adopts_without_reinjecting_or_losing_files(setup):
    daemon, storage = setup
    first = SandboxManager(storage)
    sandbox = await obtain(first)
    container = daemon.get(sandbox.container_id)
    await first.shutdown()
    assert container.status == "running"
    second = SandboxManager(storage)
    await second._recover()
    adopted = await obtain(second)
    assert adopted.container_id == sandbox.container_id
    assert storage.inject_files.await_count == 1
    assert len(daemon.created) == 1


@pytest.mark.parametrize("state", ["exited", "missing"])
async def test_dead_cached_container_is_replaced(setup, state):
    daemon, storage = setup
    manager = SandboxManager(storage)
    old = await obtain(manager)
    container = daemon.get(old.container_id)
    if state == "missing":
        container.remove(force=True)
    else:
        container.stop()
    new = await obtain(manager)
    assert new.container_id != old.container_id
    assert container.removed
    assert len(daemon.items) == 1


async def test_cleanup_removes_idle_but_not_recent_user_activity(setup):
    daemon, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    expire(manager, sandbox)
    await manager.execute(sandbox, "true")
    assert await manager.cleanup_expired() == 0
    expire(manager, sandbox)
    assert await manager.cleanup_expired() == 1
    assert not daemon.items
    assert not manager._active


async def test_sync_does_not_extend_idle_ttl(setup):
    _, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    expire(manager, sandbox)
    async with manager._user_lock(sandbox.user_id):
        await manager._sync(sandbox)
    assert await manager.cleanup_expired() == 1


async def test_other_worker_activity_prevents_cleanup(setup):
    _, storage = setup
    first, second = SandboxManager(storage), SandboxManager(storage)
    old = await obtain(first)
    current = await obtain(second)
    expire(first, old)
    await second.execute(current, "true")
    assert await first.cleanup_expired() == 0


async def test_cancelled_execution_keeps_lock_until_docker_finishes(setup):
    daemon, storage = setup
    first, second = SandboxManager(storage), SandboxManager(storage)
    sandbox = await obtain(first)
    await obtain(second)
    container = daemon.get(sandbox.container_id)
    expire(first, sandbox)
    task = asyncio.create_task(first.execute(sandbox, "block"))
    try:
        assert await asyncio.to_thread(container.block_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert await first.cleanup_expired() == 0
        assert await second.cleanup_expired() == 0
        assert not container.removed
    finally:
        container.block_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await first.cleanup_expired() == 0


@pytest.mark.parametrize("failure", ["errors", "exception", "unavailable"])
async def test_sync_failure_preserves_container_and_registration(setup, failure):
    daemon, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    expire(manager, sandbox)
    if failure == "errors":
        storage.extract_and_sync.return_value = SyncStats(errors=["upload failed"])
    elif failure == "exception":
        storage.extract_and_sync.side_effect = RuntimeError("upload failed")
    else:
        manager._workspace_storage = None
    assert await manager.cleanup_expired() == 0
    assert manager._active["user:user"] is sandbox
    assert daemon.get(sandbox.container_id).status == "running"


@pytest.mark.parametrize("failure", ["errors", "exception", "cancel"])
async def test_failed_initialization_removes_only_new_container(setup, failure):
    daemon, storage = setup
    manager = SandboxManager(storage)
    if failure == "errors":
        storage.inject_files.return_value = SyncStats(errors=["download failed"])
    else:
        storage.inject_files.side_effect = (
            asyncio.CancelledError() if failure == "cancel" else RuntimeError("download failed")
        )
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await obtain(manager)
    assert not daemon.items
    assert not manager._active
    storage.extract_and_sync.assert_not_awaited()


async def test_cancel_during_docker_create_cleans_up_after_thread_finishes(setup, monkeypatch):
    daemon, storage = setup
    started, release = threading.Event(), threading.Event()
    create = daemon.create

    def blocking_create(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return create(*args, **kwargs)

    monkeypatch.setattr(daemon, "create", blocking_create)
    manager = SandboxManager(storage)
    task = asyncio.create_task(obtain(manager))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not daemon.items
    assert not manager._active


async def test_all_workspaces_are_recovered_and_synced_before_removal(setup):
    daemon, storage = setup
    first = SandboxManager(storage)
    sandbox = await obtain(first)
    await obtain(first, workspace="other-workspace", slug="other")
    second = SandboxManager(storage)
    adopted = await obtain(second)
    assert adopted.workspaces == {"workspace": "default", "other-workspace": "other"}
    await second.destroy(adopted)
    assert [(call.args[2], call.args[3]) for call in storage.extract_and_sync.await_args_list] == [
        ("workspace", "default"),
        ("other-workspace", "other"),
    ]
    assert daemon.created[0].removed
    assert sandbox.container_id == adopted.container_id


async def test_recovery_cleans_only_scoped_stopped_containers(setup):
    daemon, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    scoped = daemon.get(sandbox.container_id)
    scoped.stop()
    foreign = daemon.create(
        "image",
        "sleep",
        name="foreign",
        labels={
            "aio.namespace": "another-deployment",
            "aio.user_id": "user",
            "aio.ephemeral": "true",
        },
    )
    legacy = daemon.create(
        "image", "sleep", name="legacy", labels={"aio.user_id": "user", "aio.ephemeral": "true"}
    )
    foreign.stop()
    legacy.stop()
    await SandboxManager(storage)._recover()
    assert scoped.removed
    assert not foreign.removed and not legacy.removed


async def test_ambiguous_or_incomplete_running_container_is_preserved(setup):
    daemon, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    container = daemon.get(sandbox.container_id)
    manager._lock_path(sandbox.user_id).with_suffix(".json").unlink()
    with pytest.raises(RuntimeError, match="incomplete"):
        await obtain(SandboxManager(storage))
    assert not container.removed
    assert len(daemon.created) == 1


async def test_cleanup_scheduler_runs_without_storage(setup, monkeypatch):
    manager = SandboxManager()
    cleaned = asyncio.Event()

    async def cleanup():
        cleaned.set()
        return 0

    monkeypatch.setattr(manager, "cleanup_expired", cleanup)
    await manager.start_periodic_sync(interval_seconds=0.01)
    try:
        await asyncio.wait_for(cleaned.wait(), 1)
    finally:
        await manager.shutdown()


async def test_separate_process_lock_prevents_reaping(setup):
    import sys

    _, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    expire(manager, sandbox)
    script = (
        "import fcntl,sys; "
        'f=open(sys.argv[1], "a+b"); fcntl.flock(f, fcntl.LOCK_EX); '
        'print("locked", flush=True); sys.stdin.read()'
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(manager._lock_path(sandbox.user_id)),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 3) == b"locked\n"
        assert await manager.cleanup_expired() == 0
    finally:
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 3)
    assert await manager.cleanup_expired() == 1


async def test_running_legacy_container_is_not_duplicated_or_deleted(setup):
    daemon, storage = setup
    legacy = daemon.create(
        "image",
        "sleep",
        name="legacy",
        labels={
            "aio.user_id": "user",
            "aio.ephemeral": "true",
            "com.docker.compose.project": settings.sandbox.namespace,
        },
    )
    legacy.start()
    with pytest.raises(RuntimeError, match="legacy"):
        await obtain(SandboxManager(storage))
    assert not legacy.removed
    assert len(daemon.created) == 1


async def test_changed_config_or_duplicate_running_containers_are_preserved(setup):
    daemon, storage = setup
    manager = SandboxManager(storage)
    sandbox = await obtain(manager)
    container = daemon.get(sandbox.container_id)
    container.labels["aio.config"] = "old-config"
    with pytest.raises(RuntimeError, match="configuration"):
        await obtain(SandboxManager(storage))
    duplicate = daemon.create("image", "sleep", name="duplicate", labels=container.labels.copy())
    duplicate.start()
    with pytest.raises(RuntimeError, match="Multiple"):
        await obtain(SandboxManager(storage))
    assert not container.removed and not duplicate.removed


async def test_missing_storage_cannot_publish_empty_workspace(setup):
    daemon, _ = setup
    manager = SandboxManager()
    with pytest.raises(RuntimeError, match="Object storage unavailable"):
        await obtain(manager)
    assert not daemon.items
    assert not manager._active
