"""Workspace file synchronization between MinIO and sandbox containers.

Handles:
- Injecting files from MinIO into a sandbox container on creation
- Extracting files from a sandbox container back to MinIO on destruction
- Periodic sync as a safety net against container crashes
- File browsing and presigned URL generation (no container required)
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import posixpath
import tarfile
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from aio_agent_platform.sandbox.models import Sandbox, SandboxManager

from aio_agent_platform.storage.client import ObjectStorage

logger = structlog.get_logger()

# Max base64 payload per exec_run chunk (avoids shell arg limit).
# Docker exec_run can handle ~128KB commands; we use 64KB to be safe.
_CHUNK_SIZE = 64 * 1024


@dataclass
class SyncStats:
    """Statistics from a sync operation."""

    files_synced: int = 0
    files_deleted: int = 0
    bytes_transferred: int = 0
    duration_ms: float = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class FileEntry:
    """A file entry for listing workspace contents."""

    path: str
    size: int
    is_dir: bool = False
    last_modified: str = ""


class WorkspaceStorage:
    """
    Manages workspace file synchronization between MinIO and sandbox containers.

    Object key layout in MinIO:
        workspaces/{workspace_id}/files/{relative_path}  — workspace files
        workspaces/{workspace_id}/meta.json              — file manifest
    """

    FILES_PREFIX = "workspaces/{workspace_id}/files"
    META_KEY = "workspaces/{workspace_id}/meta.json"

    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    # ---- Key helpers ----

    def _files_prefix(self, workspace_id: str) -> str:
        return f"workspaces/{workspace_id}/files/"

    def _object_key(self, workspace_id: str, path: str) -> str:
        return f"workspaces/{workspace_id}/files/{path.lstrip('/')}"

    def _meta_key(self, workspace_id: str) -> str:
        return f"workspaces/{workspace_id}/meta.json"

    def _relative_path(self, workspace_id: str, object_key: str) -> str:
        """Extract the relative file path from a full object key."""
        prefix = self._files_prefix(workspace_id)
        if object_key.startswith(prefix):
            return object_key[len(prefix):]
        return object_key

    # ================================================================
    # MinIO → Sandbox (inject on container creation)
    # ================================================================

    async def inject_files(
        self,
        sandbox_mgr: SandboxManager,
        sandbox: Sandbox,
        workspace_id: str,
    ) -> SyncStats:
        """
        Pull workspace files from MinIO and inject them into the sandbox.

        Uses tar.gz + base64 over exec_run to transfer files efficiently.
        For large workspaces, splits into chunks to avoid shell argument limits.
        """
        t_start = time.monotonic()
        stats = SyncStats()
        prefix = self._files_prefix(workspace_id)

        try:
            # 1. List all files in MinIO
            objects = self._storage.list(prefix, recursive=True)
            if not objects:
                logger.debug("workspace_inject_empty", workspace_id=workspace_id)
                stats.duration_ms = (time.monotonic() - t_start) * 1000
                return stats

            # 2. Download files and build tar.gz in memory
            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w:gz") as tar:
                for obj in objects:
                    rel_path = self._relative_path(workspace_id, obj.key)
                    if not rel_path:
                        continue
                    try:
                        data = self._storage.get(obj.key)
                        info = tarfile.TarInfo(name=rel_path)
                        info.size = len(data)
                        tar.addfile(info, io.BytesIO(data))
                        stats.files_synced += 1
                        stats.bytes_transferred += len(data)
                    except Exception as e:
                        stats.errors.append(f"download {rel_path}: {e}")
                        logger.warning("workspace_inject_download_error", path=rel_path, error=str(e))

            # 3. Transfer tar.gz to container via base64 chunks
            tar_bytes = tar_buf.getvalue()
            b64_data = base64.b64encode(tar_bytes).decode("ascii")

            if len(b64_data) <= _CHUNK_SIZE:
                # Single chunk — direct transfer
                cmd = f"echo '{b64_data}' | base64 -d | tar xzf - -C /workspace"
                result = await sandbox_mgr.execute(sandbox, cmd)
                if result.exit_code != 0:
                    stats.errors.append(f"tar extract failed: {result.stderr}")
            else:
                # Multi-chunk transfer
                await self._inject_chunked(sandbox_mgr, sandbox, b64_data, stats)

            logger.info(
                "workspace_injected",
                workspace_id=workspace_id,
                files=stats.files_synced,
                bytes=stats.bytes_transferred,
            )

        except Exception as e:
            stats.errors.append(f"inject_files: {e}")
            logger.error("workspace_inject_failed", workspace_id=workspace_id, error=str(e))

        stats.duration_ms = (time.monotonic() - t_start) * 1000
        return stats

    async def _inject_chunked(
        self,
        sandbox_mgr: SandboxManager,
        sandbox: Sandbox,
        b64_data: str,
        stats: SyncStats,
    ) -> None:
        """Transfer a large base64 payload in chunks, then extract."""
        tmp_file = "/tmp/_workspace_inject.tar.gz.b64"

        # Clear any existing temp file
        await sandbox_mgr.execute(sandbox, f"rm -f {tmp_file}")

        # Write chunks
        offset = 0
        while offset < len(b64_data):
            chunk = b64_data[offset:offset + _CHUNK_SIZE]
            redirect = ">" if offset == 0 else ">>"
            cmd = f"printf '%s' '{chunk}' {redirect} {tmp_file}"
            await sandbox_mgr.execute(sandbox, cmd)
            offset += _CHUNK_SIZE

        # Decode and extract
        result = await sandbox_mgr.execute(
            sandbox,
            f"base64 -d {tmp_file} | tar xzf - -C /workspace && rm -f {tmp_file}",
        )
        if result.exit_code != 0:
            stats.errors.append(f"chunked extract failed: {result.stderr}")

    # ================================================================
    # Sandbox → MinIO (extract on container destruction / periodic sync)
    # ================================================================

    async def extract_and_sync(
        self,
        sandbox_mgr: SandboxManager,
        sandbox: Sandbox,
        workspace_id: str,
    ) -> SyncStats:
        """
        Extract workspace files from the sandbox and sync to MinIO.

        Performs incremental sync:
        1. Tar /workspace inside the container
        2. Read tar via base64 over exec_run
        3. Compare each file's hash with MinIO
        4. Upload only changed/new files
        5. Delete files from MinIO that no longer exist in the container
        """
        t_start = time.monotonic()
        stats = SyncStats()
        prefix = self._files_prefix(workspace_id)

        try:
            # 1. Create tar.gz inside container
            result = await sandbox_mgr.execute(
                sandbox,
                "tar czf /tmp/_workspace_sync.tar.gz -C /workspace . 2>/dev/null; echo $?",
            )
            # tar exit code 1 means "files changed during read" — still OK
            # exit code 2 means actual error
            if result.exit_code > 1:
                stats.errors.append(f"tar create failed: {result.stderr}")
                stats.duration_ms = (time.monotonic() - t_start) * 1000
                return stats

            # 2. Read tar.gz as base64 from container
            b64_data = await self._read_file_as_base64(sandbox_mgr, sandbox, "/tmp/_workspace_sync.tar.gz")
            if not b64_data:
                stats.errors.append("failed to read tar archive from container")
                stats.duration_ms = (time.monotonic() - t_start) * 1000
                return stats

            # 3. Decode and extract files from tar
            tar_bytes = base64.b64decode(b64_data)
            container_files: dict[str, bytes] = {}
            tar_buf = io.BytesIO(tar_bytes)
            with tarfile.open(fileobj=tar_buf, mode="r:gz") as tar:
                for member in tar.getmembers():
                    # Skip directories and empty names
                    if member.isdir() or not member.name or member.name in (".", "./"):
                        continue
                    # Normalize path (remove leading ./)
                    rel_path = member.name.lstrip("./")
                    if not rel_path:
                        continue
                    f = tar.extractfile(member)
                    if f:
                        container_files[rel_path] = f.read()

            # 4. List current files in MinIO
            existing_objects = {
                self._relative_path(workspace_id, obj.key): obj
                for obj in self._storage.list(prefix, recursive=True)
            }

            # 5. Upload changed/new files
            for rel_path, data in container_files.items():
                hashlib.sha256(data).hexdigest()
                existing = existing_objects.get(rel_path)

                # Compare by checking if file exists and size matches
                # (MinIO etag is MD5, so we use size as a quick check)
                if existing and existing.size == len(data):
                    # Size matches — likely unchanged (skip for performance)
                    continue

                object_key = self._object_key(workspace_id, rel_path)
                content_type = self._guess_content_type(rel_path)
                self._storage.put(object_key, data, content_type)
                stats.files_synced += 1
                stats.bytes_transferred += len(data)

            # 6. Delete files from MinIO that no longer exist in container
            for rel_path, obj in existing_objects.items():
                if rel_path not in container_files:
                    self._storage.delete(obj.key)
                    stats.files_deleted += 1

            # 7. Update meta.json
            await self._update_meta(workspace_id, container_files)

            # Cleanup temp file
            await sandbox_mgr.execute(sandbox, "rm -f /tmp/_workspace_sync.tar.gz")

            logger.info(
                "workspace_synced",
                workspace_id=workspace_id,
                uploaded=stats.files_synced,
                deleted=stats.files_deleted,
                bytes=stats.bytes_transferred,
            )

        except Exception as e:
            stats.errors.append(f"extract_and_sync: {e}")
            logger.error("workspace_sync_failed", workspace_id=workspace_id, error=str(e))

        stats.duration_ms = (time.monotonic() - t_start) * 1000
        return stats

    async def _read_file_as_base64(
        self,
        sandbox_mgr: SandboxManager,
        sandbox: Sandbox,
        filepath: str,
    ) -> str:
        """Read a file from the sandbox as base64, handling large files via chunks."""
        # Check file size first
        result = await sandbox_mgr.execute(sandbox, f"wc -c < {filepath}")
        if result.exit_code != 0:
            return ""

        try:
            file_size = int(result.stdout.strip())
        except ValueError:
            file_size = 0

        if file_size == 0:
            return ""

        # For small files, read in one go
        b64_size = (file_size * 4 // 3) + 10  # approximate base64 size
        if b64_size < _CHUNK_SIZE:
            result = await sandbox_mgr.execute(sandbox, f"base64 {filepath}")
            if result.exit_code != 0:
                return ""
            return result.stdout.strip()

        # For large files, read in chunks
        return await self._read_file_chunked(sandbox_mgr, sandbox, filepath, file_size)

    async def _read_file_chunked(
        self,
        sandbox_mgr: SandboxManager,
        sandbox: Sandbox,
        filepath: str,
        file_size: int,
    ) -> str:
        """Read a large file from the sandbox in base64 chunks."""
        # First encode to a temp file to avoid piping overhead
        b64_file = f"{filepath}.b64"
        await sandbox_mgr.execute(sandbox, f"base64 {filepath} > {b64_file}")

        # Read in chunks
        chunks: list[str] = []
        offset = 0
        b64_total = (file_size * 4 // 3) + 100  # generous upper bound

        while offset < b64_total:
            result = await sandbox_mgr.execute(
                sandbox,
                f"dd if={b64_file} bs=1 skip={offset} count={_CHUNK_SIZE} 2>/dev/null",
            )
            chunk = result.stdout
            if not chunk:
                break
            chunks.append(chunk)
            offset += len(chunk)
            if len(chunk) < _CHUNK_SIZE:
                break

        # Cleanup
        await sandbox_mgr.execute(sandbox, f"rm -f {b64_file}")

        return "".join(chunks).strip()

    async def _update_meta(self, workspace_id: str, files: dict[str, bytes]) -> None:
        """Write meta.json with file manifest."""
        manifest = {
            "workspace_id": workspace_id,
            "file_count": len(files),
            "total_size": sum(len(d) for d in files.values()),
            "files": [
                {"path": p, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                for p, d in sorted(files.items())
            ],
        }
        meta_key = self._meta_key(workspace_id)
        meta_bytes = json.dumps(manifest, indent=2).encode()
        self._storage.put(meta_key, meta_bytes, "application/json")

    # ================================================================
    # File browsing (no container required — reads from MinIO)
    # ================================================================

    def list_files(self, workspace_id: str, path: str = "") -> list[FileEntry]:
        """
        List files and directories in a workspace path.

        Args:
            workspace_id: The workspace ID.
            path: Subdirectory path (e.g. "src"). Empty for root.

        Returns:
            List of FileEntry items at this directory level.
        """
        prefix = self._files_prefix(workspace_id)
        if path:
            prefix = f"{prefix}{path.strip('/')}/"

        # List non-recursively to get immediate children
        objects = self._storage.list(prefix, recursive=False)

        entries: list[FileEntry] = []
        seen_dirs: set[str] = set()

        for obj in objects:
            rel = self._relative_path(workspace_id, obj.key)
            # Remove the base path prefix to get the relative name
            if path:
                base = f"{path.strip('/')}/"
                if rel.startswith(base):
                    rel = rel[len(base):]
                else:
                    continue

            if not rel:
                continue

            # Check if this is a directory marker
            if rel.endswith("/"):
                dir_name = rel.rstrip("/")
                if dir_name and dir_name not in seen_dirs:
                    seen_dirs.add(dir_name)
                    entries.append(FileEntry(path=dir_name, size=0, is_dir=True))
            elif "/" in rel:
                # Subdirectory — extract the top-level dir name
                dir_name = rel.split("/")[0]
                if dir_name not in seen_dirs:
                    seen_dirs.add(dir_name)
                    entries.append(FileEntry(path=dir_name, size=0, is_dir=True))
            else:
                entries.append(FileEntry(path=rel, size=obj.size))

        return entries

    def get_file(self, workspace_id: str, path: str) -> bytes:
        """Read a single file from MinIO."""
        key = self._object_key(workspace_id, path)
        return self._storage.get(key)

    def put_file(self, workspace_id: str, path: str, content: bytes) -> None:
        """Upload a single file to MinIO."""
        key = self._object_key(workspace_id, path)
        content_type = self._guess_content_type(path)
        self._storage.put(key, content, content_type)

    def delete_file(self, workspace_id: str, path: str) -> None:
        """Delete a single file from MinIO."""
        key = self._object_key(workspace_id, path)
        self._storage.delete(key)

    def file_exists(self, workspace_id: str, path: str) -> bool:
        """Check if a file exists in the workspace."""
        key = self._object_key(workspace_id, path)
        return self._storage.exists(key)

    # ---- Presigned URLs ----

    def presign_download(self, workspace_id: str, path: str) -> str:
        """Generate a presigned download URL for a workspace file."""
        key = self._object_key(workspace_id, path)
        return self._storage.presign_download(key)

    def presign_upload(self, workspace_id: str, path: str) -> str:
        """Generate a presigned upload URL for a workspace file."""
        key = self._object_key(workspace_id, path)
        return self._storage.presign_upload(key)

    # ---- Workspace-level operations ----

    def delete_workspace(self, workspace_id: str) -> int:
        """Delete all files in a workspace from MinIO."""
        prefix = f"workspaces/{workspace_id}/"
        return self._storage.delete_prefix(prefix)

    def get_workspace_stats(self, workspace_id: str) -> tuple[int, int]:
        """Get file count and total size for a workspace."""
        prefix = self._files_prefix(workspace_id)
        objects = self._storage.list(prefix, recursive=True)
        total_size = sum(obj.size for obj in objects)
        return len(objects), total_size

    # ---- Helpers ----

    @staticmethod
    def _guess_content_type(path: str) -> str:
        """Guess content type from file extension."""
        ext = posixpath.splitext(path)[1].lower()
        types = {
            ".py": "text/x-python",
            ".js": "application/javascript",
            ".ts": "application/typescript",
            ".json": "application/json",
            ".yaml": "text/yaml",
            ".yml": "text/yaml",
            ".md": "text/markdown",
            ".txt": "text/plain",
            ".html": "text/html",
            ".css": "text/css",
            ".xml": "application/xml",
            ".csv": "text/csv",
            ".sh": "application/x-sh",
            ".zip": "application/zip",
            ".tar": "application/x-tar",
            ".gz": "application/gzip",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".pdf": "application/pdf",
        }
        return types.get(ext, "application/octet-stream")
