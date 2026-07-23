"""Workspace management routes — CRUD + file browsing + presigned URLs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage
from aio_agent_platform.workspaces.service import WorkspaceService

logger = structlog.get_logger()

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


# ---- Helpers ----


def _get_workspace_storage() -> WorkspaceStorage | None:
    """Create a WorkspaceStorage instance. Returns None if MinIO is unavailable."""
    try:
        storage = ObjectStorage()
        return WorkspaceStorage(storage)
    except Exception as e:
        logger.warning("workspace_storage_unavailable", error=str(e))
        return None


# ---- Schemas ----


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str | None = None
    file_count: int = 0
    total_size_bytes: int = 0
    is_default: bool = False
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FileEntryOut(BaseModel):
    path: str
    size: int
    is_dir: bool = False


class PresignedUrlOut(BaseModel):
    url: str
    object_key: str | None = None
    expires_in: int


class UploadRequest(BaseModel):
    path: str = Field(..., description="Target file path in workspace")


# ---- Workspace CRUD ----


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    req: WorkspaceCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOut:
    """Create a new workspace."""
    workspace = await WorkspaceService.create_workspace(
        db=db,
        user_id=user.id,
        name=req.name,
        description=req.description,
    )
    return WorkspaceOut.model_validate(workspace)


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[WorkspaceOut]:
    """List all workspaces for the current user."""
    workspaces = await WorkspaceService.list_workspaces(
        db=db,
        user_id=user.id,
        limit=limit,
        offset=offset,
    )
    return [WorkspaceOut.model_validate(w) for w in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOut:
    """Get workspace details."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceOut.model_validate(workspace)


@router.put("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: UUID,
    req: WorkspaceUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOut:
    """Update workspace metadata."""
    workspace = await WorkspaceService.update_workspace(
        db=db,
        workspace_id=workspace_id,
        user_id=user.id,
        name=req.name,
        description=req.description,
    )
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return WorkspaceOut.model_validate(workspace)


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a workspace and all its files from MinIO."""
    ws_storage = _get_workspace_storage()
    if ws_storage:
        ws_storage.delete_workspace(str(workspace_id))

    deleted = await WorkspaceService.delete_workspace(db, workspace_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")


# ---- File browsing ----


@router.get("/{workspace_id}/files", response_model=list[FileEntryOut])
async def list_files(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    path: str = Query("", description="Subdirectory path to list"),
) -> list[FileEntryOut]:
    """List files and directories in a workspace (reads from MinIO, no container needed)."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_storage = _get_workspace_storage()
    if not ws_storage:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    entries = ws_storage.list_files(str(workspace_id), path)
    return [
        FileEntryOut(path=e.path, size=e.size, is_dir=e.is_dir)
        for e in entries
    ]


# ---- Presigned URL upload/download ----


@router.post("/{workspace_id}/files/upload", response_model=PresignedUrlOut)
async def presign_upload(
    workspace_id: UUID,
    req: UploadRequest,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PresignedUrlOut:
    """Generate a presigned URL for direct file upload to MinIO."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_storage = _get_workspace_storage()
    if not ws_storage:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    url = ws_storage.presign_upload(str(workspace_id), req.path)
    from aio_agent_platform.core.config import settings
    return PresignedUrlOut(
        url=url,
        object_key=ws_storage._object_key(str(workspace_id), req.path),
        expires_in=settings.storage.presign_expire_seconds,
    )


@router.get("/{workspace_id}/files/download", response_model=PresignedUrlOut)
async def presign_download(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    path: str = Query(..., description="File path to download"),
) -> PresignedUrlOut:
    """Generate a presigned URL for direct file download from MinIO."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_storage = _get_workspace_storage()
    if not ws_storage:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    if not ws_storage.file_exists(str(workspace_id), path):
        raise HTTPException(status_code=404, detail="File not found in workspace")

    url = ws_storage.presign_download(str(workspace_id), path)
    from aio_agent_platform.core.config import settings
    return PresignedUrlOut(
        url=url,
        expires_in=settings.storage.presign_expire_seconds,
    )


# ---- Server-proxied upload/download (fallback) ----


@router.post("/{workspace_id}/files/content", status_code=201)
async def upload_file_content(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    path: str = Query(..., description="Target file path in workspace"),
) -> dict:
    """Upload a file directly through the server (fallback for small files)."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_storage = _get_workspace_storage()
    if not ws_storage:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    content = await file.read()
    ws_storage.put_file(str(workspace_id), path, content)

    return {"path": path, "size": len(content)}


@router.get("/{workspace_id}/files/content")
async def download_file_content(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    path: str = Query(..., description="File path to download"),
) -> Response:
    """Download a file directly through the server (fallback)."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_storage = _get_workspace_storage()
    if not ws_storage:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    if not ws_storage.file_exists(str(workspace_id), path):
        raise HTTPException(status_code=404, detail="File not found in workspace")

    content = ws_storage.get_file(str(workspace_id), path)

    # Guess filename from path
    filename = path.rsplit("/", 1)[-1] if "/" in path else path

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.delete("/{workspace_id}/files", status_code=204)
async def delete_file(
    workspace_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    path: str = Query(..., description="File path to delete"),
) -> None:
    """Delete a single file from the workspace."""
    workspace = await WorkspaceService.get_workspace(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_storage = _get_workspace_storage()
    if not ws_storage:
        raise HTTPException(status_code=503, detail="Object storage unavailable")

    if not ws_storage.file_exists(str(workspace_id), path):
        raise HTTPException(status_code=404, detail="File not found in workspace")

    ws_storage.delete_file(str(workspace_id), path)
