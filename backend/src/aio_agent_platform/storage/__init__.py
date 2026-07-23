"""Generic object storage layer — wraps MinIO/S3 client."""

from aio_agent_platform.storage.client import ObjectInfo, ObjectStorage
from aio_agent_platform.storage.workspace import FileEntry, SyncStats, WorkspaceStorage

__all__ = [
    "FileEntry",
    "ObjectInfo",
    "ObjectStorage",
    "SyncStats",
    "WorkspaceStorage",
]
