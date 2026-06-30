"""WorkspaceService — CRUD and file management for workspaces."""

from __future__ import annotations

import re
import unicodedata
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Workspace

logger = structlog.get_logger()


class WorkspaceService:
    """
    Stateless workspace service — all methods take an explicit db session and user_id.

    Workspaces are named file storage entities that provide project-level isolation.
    Each workspace has:
        - Metadata in PostgreSQL (name, slug, description, stats)
        - Files stored in MinIO under workspaces/{workspace_id}/files/...
        - A manifest (meta.json) tracking file inventory

    Multiple sessions can share a workspace, enabling cross-session file persistence.
    """

    # ---- Slug generation ----

    @staticmethod
    def _slugify(name: str) -> str:
        """
        Convert a workspace name to a URL-friendly slug.

        Examples:
            "My App" → "my-app"
            "数据分析" → "数据分析"  (CJK preserved)
            "Project #1!" → "project-1"
        """
        # NFKD normalization + strip combining marks (accents)
        nfkd = unicodedata.normalize("NFKD", name)
        stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
        # Lowercase
        lower = stripped.lower()
        # Replace non-alphanumeric (keep CJK) with dash
        slug = re.sub(r"[^a-z0-9一-鿿぀-ゟ゠-ヿ]+", "-", lower)
        # Strip leading/trailing dashes
        slug = slug.strip("-")
        # Collapse multiple dashes
        slug = re.sub(r"-+", "-", slug)
        # Truncate to leave room for uniqueness suffix (column is VARCHAR(64))
        slug = slug[:48].strip("-")
        return slug or "workspace"

    @staticmethod
    async def _unique_slug(db: AsyncSession, user_id: UUID, base_slug: str) -> str:
        """Ensure slug is unique within the user's workspaces, appending suffix if needed."""
        slug = base_slug
        suffix = 1
        while True:
            result = await db.execute(
                select(Workspace).where(
                    Workspace.user_id == user_id,
                    Workspace.slug == slug,
                )
            )
            if result.scalar_one_or_none() is None:
                return slug
            slug = f"{base_slug}-{suffix}"
            suffix += 1

    # ---- CRUD ----

    @staticmethod
    async def list_workspaces(
        db: AsyncSession,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Workspace]:
        """List all workspaces for a user, ordered by updated_at desc."""
        stmt = (
            select(Workspace)
            .where(Workspace.user_id == user_id)
            .order_by(Workspace.is_default.desc(), Workspace.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_workspace(
        db: AsyncSession,
        workspace_id: UUID,
        user_id: UUID,
    ) -> Workspace | None:
        """Get a single workspace by ID with ownership check."""
        result = await db.execute(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_default_workspace(
        db: AsyncSession,
        user_id: UUID,
    ) -> Workspace | None:
        """Get the user's default workspace."""
        result = await db.execute(
            select(Workspace).where(
                Workspace.user_id == user_id,
                Workspace.is_default == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_or_create_default(
        db: AsyncSession,
        user_id: UUID,
    ) -> Workspace:
        """
        Get or create the user's default workspace.

        Called when a session has no workspace_id, ensuring backward compatibility.
        """
        existing = await WorkspaceService.get_default_workspace(db, user_id)
        if existing:
            return existing

        workspace = Workspace(
            user_id=user_id,
            name="Default",
            slug="default",
            description="Default workspace",
            is_default=True,
        )
        db.add(workspace)
        await db.flush()
        logger.info("default_workspace_created", user_id=str(user_id), workspace_id=str(workspace.id))
        return workspace

    @staticmethod
    async def create_workspace(
        db: AsyncSession,
        user_id: UUID,
        name: str,
        description: str | None = None,
    ) -> Workspace:
        """
        Create a new workspace.

        Auto-generates a URL-friendly slug from the name.
        """
        base_slug = WorkspaceService._slugify(name)
        slug = await WorkspaceService._unique_slug(db, user_id, base_slug)

        workspace = Workspace(
            user_id=user_id,
            name=name,
            slug=slug,
            description=description,
        )
        db.add(workspace)
        await db.flush()
        logger.info("workspace_created", user_id=str(user_id), workspace_id=str(workspace.id), slug=slug)
        return workspace

    @staticmethod
    async def update_workspace(
        db: AsyncSession,
        workspace_id: UUID,
        user_id: UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> Workspace | None:
        """Update workspace metadata. Returns None if not found or not owned."""
        workspace = await WorkspaceService.get_workspace(db, workspace_id, user_id)
        if not workspace:
            return None

        if name is not None:
            workspace.name = name
        if description is not None:
            workspace.description = description

        await db.flush()
        return workspace

    @staticmethod
    async def delete_workspace(
        db: AsyncSession,
        workspace_id: UUID,
        user_id: UUID,
    ) -> bool:
        """
        Delete a workspace from the database.

        Note: MinIO files must be deleted separately via WorkspaceStorage.delete_workspace().
        The caller (route handler) is responsible for orchestrating both.

        Returns True if deleted, False if not found or not owned.
        """
        workspace = await WorkspaceService.get_workspace(db, workspace_id, user_id)
        if not workspace:
            return False

        if workspace.is_default:
            logger.warning("default_workspace_delete_blocked", user_id=str(user_id))
            raise ValueError("Cannot delete the default workspace")

        await db.delete(workspace)
        await db.flush()
        logger.info("workspace_deleted", user_id=str(user_id), workspace_id=str(workspace_id))
        return True

    # ---- Stats update (called after file sync) ----

    @staticmethod
    async def update_stats(
        db: AsyncSession,
        workspace_id: UUID,
        file_count: int,
        total_size_bytes: int,
    ) -> None:
        """Update file stats after a sync operation."""
        result = await db.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.file_count = file_count
            workspace.total_size_bytes = total_size_bytes
            await db.flush()
