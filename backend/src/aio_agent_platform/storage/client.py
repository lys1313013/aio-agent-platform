"""Generic MinIO object storage client.

Provides a unified interface for all object storage operations across
workspaces, skills, and exports. Replaces the raw MinIO client usage
that was previously embedded in SkillStorage.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import timedelta

import structlog
from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import S3Error

from aio_agent_platform.core.config import settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class ObjectInfo:
    """Metadata for a stored object."""

    key: str
    size: int
    etag: str
    content_type: str = ""


class ObjectStorage:
    """
    Generic MinIO object storage client.

    Provides put/get/delete/list/presign operations on a single bucket.
    Used as the foundation layer for WorkspaceStorage, SkillStorage, etc.
    """

    def __init__(self, bucket: str | None = None) -> None:
        self._bucket = bucket or settings.storage.workspace_bucket
        self._client = Minio(
            endpoint=settings.storage.endpoint,
            access_key=settings.storage.access_key,
            secret_key=settings.storage.secret_key,
            secure=settings.storage.secure,
        )
        self._ensure_bucket()

    # ---- Bucket lifecycle ----

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist."""
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("object_storage_bucket_created", bucket=self._bucket)
        except Exception as e:
            logger.warning("object_storage_bucket_init_failed", bucket=self._bucket, error=str(e))

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def client(self) -> Minio:
        """Expose underlying Minio client for advanced usage."""
        return self._client

    # ---- Core CRUD ----

    def put(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """
        Upload bytes to the bucket.

        Returns:
            The object key.
        """
        stream = io.BytesIO(data)
        self._client.put_object(
            bucket_name=self._bucket,
            object_name=key,
            data=stream,
            length=len(data),
            content_type=content_type,
        )
        logger.debug("object_storage_put", key=key, size=len(data))
        return key

    def get(self, key: str) -> bytes:
        """Download an object as bytes."""
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def delete(self, key: str) -> None:
        """Delete a single object."""
        try:
            self._client.remove_object(self._bucket, key)
            logger.debug("object_storage_delete", key=key)
        except S3Error as e:
            logger.warning("object_storage_delete_failed", key=key, error=str(e))

    def exists(self, key: str) -> bool:
        """Check if an object exists."""
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error:
            return False
        except Exception:
            return False

    def stat(self, key: str) -> ObjectInfo | None:
        """Get object metadata without downloading content."""
        try:
            obj = self._client.stat_object(self._bucket, key)
            return ObjectInfo(
                key=obj.object_name,
                size=obj.size,
                etag=obj.etag,
                content_type=obj.content_type or "",
            )
        except S3Error:
            return None
        except Exception:
            return None

    # ---- List ----

    def list(self, prefix: str, recursive: bool = True) -> list[ObjectInfo]:
        """
        List objects under a prefix.

        Args:
            prefix: Object key prefix (acts as directory path).
            recursive: If True, list all nested objects. If False, list only
                       immediate children (directories appear as prefix-only entries).

        Returns:
            List of ObjectInfo for each object found.
        """
        results: list[ObjectInfo] = []
        objects = self._client.list_objects(self._bucket, prefix=prefix, recursive=recursive)
        for obj in objects:
            # Skip directory markers (size=0, ends with /)
            if obj.object_name.endswith("/") and obj.size == 0:
                continue
            results.append(
                ObjectInfo(
                    key=obj.object_name,
                    size=obj.size or 0,
                    etag=obj.etag or "",
                    content_type=obj.content_type or "",
                )
            )
        return results

    # ---- Batch operations ----

    def put_many(self, items: list[tuple[str, bytes]], content_type: str = "application/octet-stream") -> None:
        """Upload multiple objects."""
        for key, data in items:
            self.put(key, data, content_type)

    def get_many(self, keys: list[str]) -> dict[str, bytes]:
        """Download multiple objects. Skips keys that don't exist."""
        result: dict[str, bytes] = {}
        for key in keys:
            try:
                result[key] = self.get(key)
            except Exception as e:
                logger.warning("object_storage_get_many_skip", key=key, error=str(e))
        return result

    def delete_prefix(self, prefix: str) -> int:
        """
        Delete all objects under a prefix.

        Returns:
            Number of objects deleted.
        """
        objects = self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
        delete_list = [DeleteObject(obj.object_name) for obj in objects]
        if not delete_list:
            return 0

        errors = list(self._client.remove_objects(self._bucket, delete_list))
        deleted = len(delete_list) - len(errors)
        if errors:
            logger.warning(
                "object_storage_delete_prefix_errors",
                prefix=prefix,
                errors=[str(e) for e in errors],
            )
        else:
            logger.debug("object_storage_delete_prefix", prefix=prefix, count=deleted)
        return deleted

    # ---- Presigned URLs ----

    def presign_download(self, key: str, expires: int | None = None) -> str:
        """
        Generate a presigned URL for downloading an object.

        Args:
            key: Object key.
            expires: Expiry in seconds (default from settings).

        Returns:
            Presigned URL string.
        """
        expires = expires or settings.storage.presign_expire_seconds
        url = self._client.presigned_get_object(
            bucket_name=self._bucket,
            object_name=key,
            expires=timedelta(seconds=expires),
        )
        return url

    def presign_upload(self, key: str, expires: int | None = None) -> str:
        """
        Generate a presigned URL for uploading an object.

        Args:
            key: Object key.
            expires: Expiry in seconds (default from settings).

        Returns:
            Presigned URL string.
        """
        expires = expires or settings.storage.presign_expire_seconds
        url = self._client.presigned_put_object(
            bucket_name=self._bucket,
            object_name=key,
            expires=timedelta(seconds=expires),
        )
        return url
