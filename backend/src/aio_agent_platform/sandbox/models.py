"""Sandbox execution — Docker container lifecycle management (stateless).

Containers are fully ephemeral: no Docker volumes are mounted.
/workspace is backed by tmpfs and files are synchronized to/from MinIO
via WorkspaceStorage on container creation and destruction.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import docker
import structlog
from docker.errors import NotFound

from aio_agent_platform.core.config import settings

if TYPE_CHECKING:
    import docker.models.containers

    from aio_agent_platform.storage.workspace import WorkspaceStorage

logger = structlog.get_logger()


@dataclass
class ExecResult:
    """Result from sandbox command execution."""

    stdout: str
    stderr: str
    exit_code: int


@dataclass
class Sandbox:
    """A running sandbox container."""

    container_id: str
    user_id: str
    session_id: str
    workspace_id: str
    created_at: datetime

    def is_expired(self) -> bool:
        """Check if sandbox has exceeded TTL."""
        ttl_seconds = settings.sandbox.session_ttl
        return (datetime.utcnow() - self.created_at).total_seconds() > ttl_seconds


class SandboxManager:
    """
    Manages ephemeral Docker sandbox containers per user/session/workspace.

    Principles:
    1. All commands run in containers, never touch the host.
    2. Containers are fully ephemeral — /workspace is tmpfs (no Docker volumes).
    3. Files persisted via MinIO (WorkspaceStorage injects on create, extracts on destroy).
    4. Same session reuses the same container (preserves pip install, etc.).
    5. Different sessions get fully isolated containers.
    6. Periodic sync protects against container crashes.
    """

    def __init__(self, workspace_storage: "WorkspaceStorage | None" = None):
        self._client = docker.from_env()
        # key: "{workspace_id}:{session_id}"
        self._active: dict[str, Sandbox] = {}
        self._workspace_storage = workspace_storage
        self._sync_task: asyncio.Task | None = None

    def _key(self, workspace_id: str, session_id: str) -> str:
        return f"{workspace_id}:{session_id}"

    # ---- Public API ----

    async def get_or_create(
        self,
        user_id: str,
        session_id: str,
        workspace_id: str,
    ) -> Sandbox:
        """Get or create a sandbox for the given user/session/workspace."""
        key = self._key(workspace_id, session_id)

        # Reuse existing sandbox
        if key in self._active:
            sandbox = self._active[key]
            if not sandbox.is_expired():
                return sandbox
            await self.destroy(sandbox, sync=True)

        # Create new sandbox
        sandbox = await self._create(user_id, session_id, workspace_id)
        self._active[key] = sandbox
        return sandbox

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int | None = None,
    ) -> ExecResult:
        """Execute a command in the sandbox container."""
        timeout = timeout or settings.sandbox.command_timeout
        container = self._client.containers.get(sandbox.container_id)

        # Run in thread pool since docker SDK is sync
        loop = asyncio.get_event_loop()
        exec_result = await loop.run_in_executor(
            None,
            lambda: container.exec_run(
                ["bash", "-c", command],
                workdir="/workspace",
                user="sandbox",
                stdout=True,
                stderr=True,
                demux=True,
            ),
        )
        stdout_b, stderr_b = exec_result.output
        return ExecResult(
            stdout=(stdout_b or b"").decode("utf-8", errors="replace"),
            stderr=(stderr_b or b"").decode("utf-8", errors="replace"),
            exit_code=exec_result.exit_code,
        )

    async def destroy(self, sandbox: Sandbox, sync: bool = True) -> None:
        """
        Destroy a sandbox container.

        If sync=True (default), extracts workspace files to MinIO before destruction.
        """
        # Extract files before destroying container
        if sync and self._workspace_storage:
            try:
                await self._workspace_storage.extract_and_sync(self, sandbox, sandbox.workspace_id)
            except Exception as e:
                logger.warning(
                    "sandbox_destroy_sync_failed",
                    session_id=sandbox.session_id,
                    workspace_id=sandbox.workspace_id,
                    error=str(e),
                )

        key = self._key(sandbox.workspace_id, sandbox.session_id)
        self._active.pop(key, None)

        try:
            container = self._client.containers.get(sandbox.container_id)
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info(
                "sandbox_destroyed",
                container_id=sandbox.container_id[:12],
                session_id=sandbox.session_id,
            )
        except NotFound:
            pass  # Already gone

    async def destroy_all_for_user(self, user_id: str) -> None:
        """Destroy all active sandboxes for a user."""
        for key, sandbox in list(self._active.items()):
            if sandbox.user_id == user_id:
                await self.destroy(sandbox)

    async def cleanup_expired(self) -> int:
        """Destroy all expired sandboxes. Returns count destroyed."""
        destroyed = 0
        for key, sandbox in list(self._active.items()):
            if sandbox.is_expired():
                await self.destroy(sandbox)
                destroyed += 1
        return destroyed

    async def shutdown(self) -> None:
        """Destroy all active sandboxes and stop periodic sync (platform shutdown)."""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        for sandbox in list(self._active.values()):
            await self.destroy(sandbox)

    # ---- Periodic Sync ----

    async def start_periodic_sync(self, interval_seconds: int | None = None) -> None:
        """Start background periodic sync task."""
        if self._sync_task is not None:
            return  # Already running
        interval = interval_seconds or settings.storage.sync_interval_seconds
        self._sync_task = asyncio.create_task(self._periodic_sync_loop(interval))
        logger.info("sandbox_periodic_sync_started", interval_seconds=interval)

    async def _periodic_sync_loop(self, interval: int) -> None:
        """Background loop: sync all active sandboxes every `interval` seconds."""
        while True:
            await asyncio.sleep(interval)
            for key, sandbox in list(self._active.items()):
                if self._workspace_storage:
                    try:
                        stats = await self._workspace_storage.extract_and_sync(
                            self, sandbox, sandbox.workspace_id
                        )
                        if stats.files_synced > 0:
                            logger.info(
                                "sandbox_periodic_sync",
                                session_id=sandbox.session_id,
                                files=stats.files_synced,
                            )
                    except Exception as e:
                        logger.warning(
                            "sandbox_periodic_sync_failed",
                            key=key,
                            error=str(e),
                        )

    # ---- Internal ----

    async def _create(self, user_id: str, session_id: str, workspace_id: str) -> Sandbox:
        """Create a new sandbox container with tmpfs /workspace (no Docker volumes)."""
        container_name = f"aio-sandbox-{uuid4().hex[:8]}"

        # Both /tmp and /workspace are tmpfs — fully ephemeral
        # uid=1000,gid=1000 matches the sandbox user created in the Dockerfile
        tmpfs = {
            "/tmp": f"size={settings.sandbox.tmpfs_size},uid=1000,gid=1000",
            "/workspace": f"size={settings.sandbox.workspace_quota_mb}m,uid=1000,gid=1000",
        }

        loop = asyncio.get_event_loop()
        container: "docker.models.containers.Container" = await loop.run_in_executor(
            None,
            lambda: self._client.containers.run(
                settings.sandbox.image,
                "sleep infinity",
                detach=True,
                name=container_name,
                user="sandbox",
                read_only=True,
                mounts=[],  # No volume mounts — stateless
                tmpfs=tmpfs,
                mem_limit=settings.sandbox.memory_limit,
                cpu_quota=int(settings.sandbox.cpu_limit * 100_000),
                network_disabled=settings.sandbox.network_disabled,
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
                labels={
                    "aio.user_id": user_id,
                    "aio.session_id": session_id,
                    "aio.workspace_id": workspace_id,
                    "aio.ephemeral": "true",
                },
            ),
        )

        sandbox = Sandbox(
            container_id=container.id,
            user_id=user_id,
            session_id=session_id,
            workspace_id=workspace_id,
            created_at=datetime.utcnow(),
        )

        logger.info(
            "sandbox_created",
            container_id=container.id[:12],
            session_id=session_id,
            workspace_id=workspace_id,
        )

        # Inject workspace files from MinIO
        if self._workspace_storage:
            try:
                stats = await self._workspace_storage.inject_files(self, sandbox, workspace_id)
                if stats.files_synced > 0:
                    logger.info(
                        "sandbox_files_injected",
                        workspace_id=workspace_id,
                        files=stats.files_synced,
                        bytes=stats.bytes_transferred,
                    )
            except Exception as e:
                # Non-fatal: start with empty workspace
                logger.warning(
                    "sandbox_inject_files_failed",
                    workspace_id=workspace_id,
                    error=str(e),
                )

        return sandbox
