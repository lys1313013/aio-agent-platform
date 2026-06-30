"""Generic object storage layer — wraps MinIO/S3 client."""

from aio_agent_platform.storage.client import ObjectStorage, ObjectInfo
from aio_agent_platform.storage.workspace import WorkspaceStorage, SyncStats, FileEntry

__all__ = [
    "ObjectStorage",
    "ObjectInfo",
    "WorkspaceStorage",
    "SyncStats",
    "FileEntry",
]
