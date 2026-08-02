"""Skill management routes — CRUD + search + versioning for the Web UI."""

from __future__ import annotations

import io
import json
import os
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Skill
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.skills.storage import SCRIPT_EXTENSIONS, SkillStorage

router = APIRouter(prefix="/api/skills", tags=["skills"])

# ---- File validation constants ----
MAX_FILE_SIZE = 1 * 1024 * 1024  # 1 MB per file
MAX_TOTAL_FILES_SIZE = 10 * 1024 * 1024  # 10 MB total
VALID_FILE_TYPES = {"script", "reference", "asset"}


# ---- Schemas ----


class SkillOut(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    content: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: str
    trigger_condition: str | None = None
    use_count: int
    success_count: int
    is_public: bool
    is_active: bool
    version: int
    files: list[dict] = Field(default_factory=list)
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, s: Skill) -> SkillOut:
        return cls(
            id=s.id,
            name=s.name,
            description=s.description,
            content=s.content,
            tags=s.tags or [],
            category=s.category,
            trigger_condition=s.trigger_condition,
            use_count=s.use_count,
            success_count=s.success_count,
            is_public=s.is_public,
            is_active=s.is_active,
            version=s.version,
            files=s.files or [],
            last_used_at=s.last_used_at,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )


class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2000)
    content: str = Field(..., min_length=1)
    tags: list[str] = Field(default_factory=list)
    category: str = Field(default="general", max_length=64)
    trigger_condition: str | None = None


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None
    category: str | None = Field(default=None, max_length=64)
    trigger_condition: str | None = None
    is_active: bool | None = None
    is_public: bool | None = None


class SkillListResponse(BaseModel):
    items: list[SkillOut]
    total: int
    category: str | None = None


class SkillVersionOut(BaseModel):
    id: UUID
    skill_id: UUID
    version: int
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillSearchResult(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    category: str
    tags: list[str] = Field(default_factory=list)
    use_count: int
    success_count: int
    version: int
    files: list[dict] = Field(default_factory=list)
    score: float


# ---- Helpers ----


def _get_storage() -> SkillStorage | None:
    """Get SkillStorage, returning None if MinIO is unavailable."""
    try:
        return SkillStorage()
    except Exception:
        return None


# ---- Endpoints ----


@router.get("/search", response_model=list[SkillSearchResult])
async def search_skills(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(..., min_length=1, max_length=500),
    category: str | None = Query(default=None),
    top_k: int = Query(default=10, ge=1, le=50),
) -> list[dict]:
    """Search skills by similarity."""
    results = await SkillService.search_skills(
        db, user.id, q, category=category, top_k=top_k
    )
    return [
        SkillSearchResult(
            id=s.id,
            name=s.name,
            description=s.description,
            category=s.category,
            tags=s.tags or [],
            use_count=s.use_count,
            success_count=s.success_count,
            version=s.version,
            files=s.files or [],
            score=round(score, 4),
        ).model_dump(mode="json")
        for s, score in results
    ]


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    category: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List skills with optional filters."""
    skills = await SkillService.list_skills(
        db, user.id, category=category, is_active=is_active, limit=limit, offset=offset
    )

    count_stmt = select(func.count()).select_from(Skill).where(Skill.user_id == user.id)
    if category:
        count_stmt = count_stmt.where(Skill.category == category)
    if is_active is not None:
        count_stmt = count_stmt.where(Skill.is_active == is_active)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar()

    return SkillListResponse(
        items=[SkillOut.from_model(s) for s in skills],
        total=total,
        category=category,
    ).model_dump(mode="json")


@router.post("/import", response_model=SkillOut, status_code=201)
async def import_skill(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> dict:
    """Import a skill from a zip file.

    The zip should contain a skill folder structure:
        SKILL.md              ← required
        scripts/              ← optional
        references/           ← optional
        assets/               ← optional

    Can be flat (SKILL.md at root) or nested (skill-name/SKILL.md).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    if len(data) > MAX_TOTAL_FILES_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Zip file exceeds 10 MB limit ({len(data)} bytes)",
        )

    # Validate it's a zip
    if data[:4] not in (b"PK\x03\x04", b"PK\x05\x06"):
        raise HTTPException(status_code=400, detail="Not a valid zip file")

    storage = _get_storage()

    # Parse the zip
    try:
        parsed = SkillStorage.parse_skill_zip(data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse zip: {e}")

    metadata = parsed.get("metadata", {})
    content = parsed.get("content", "").strip()
    files_raw = parsed.get("files", [])

    if not content:
        raise HTTPException(status_code=400, detail="SKILL.md is missing or empty")

    name = metadata.get("name", "")
    if not name:
        # Derive name from filename
        name = os.path.splitext(file.filename)[0] if file.filename else "Imported Skill"
    description = metadata.get("description", "")
    tags_raw = metadata.get("tags", [])
    tags = [t for t in tags_raw if isinstance(t, str)] if isinstance(tags_raw, list) else []
    category = metadata.get("category", "general")
    trigger_condition = metadata.get("trigger_condition", "")

    # Classify files by directory
    file_entries: list[dict] = []
    for f in files_raw:
        path = f["path"]
        file_content = f["content"]
        # Determine type from directory
        if path.startswith("scripts/"):
            ftype = "script"
        elif path.startswith("references/"):
            ftype = "reference"
        elif path.startswith("assets/"):
            ftype = "asset"
        else:
            # Unknown directory — treat as asset
            ftype = "asset"
        # Use basename for filename
        fname = path.split("/")[-1]
        file_entries.append({
            "filename": fname,
            "content": file_content,
            "type": ftype,
            "description": "",
        })

    skill = await SkillService.create_skill(
        db=db,
        user_id=user.id,
        name=name,
        description=description or None,
        content=content,
        tags=tags,
        category=category,
        trigger_condition=trigger_condition or None,
        storage=storage,
        files=file_entries if file_entries else None,
    )
    await db.commit()
    return SkillOut.from_model(skill).model_dump(mode="json")


@router.post("", response_model=SkillOut, status_code=201)
async def create_skill(
    req: SkillCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new skill."""
    storage = _get_storage()
    skill = await SkillService.create_skill(
        db=db,
        user_id=user.id,
        name=req.name,
        description=req.description,
        content=req.content,
        tags=req.tags,
        category=req.category,
        trigger_condition=req.trigger_condition,
        storage=storage,
    )
    await db.commit()
    return SkillOut.from_model(skill).model_dump(mode="json")


@router.get("/{skill_id}", response_model=SkillOut)
async def get_skill(
    skill_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a single skill by ID."""
    skill = await SkillService.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillOut.from_model(skill).model_dump(mode="json")


@router.put("/{skill_id}", response_model=SkillOut)
async def update_skill(
    skill_id: UUID,
    req: SkillUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update a skill. Automatically archives current version."""
    storage = _get_storage()
    skill = await SkillService.update_skill(
        db=db,
        skill_id=skill_id,
        user_id=user.id,
        name=req.name,
        description=req.description,
        content=req.content,
        tags=req.tags,
        category=req.category,
        trigger_condition=req.trigger_condition,
        is_active=req.is_active,
        is_public=req.is_public,
        storage=storage,
    )
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    await db.commit()
    return SkillOut.from_model(skill).model_dump(mode="json")


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a skill and all its MinIO zips."""
    storage = _get_storage()
    deleted = await SkillService.delete_skill(db, skill_id, user.id, storage=storage)
    if not deleted:
        raise HTTPException(status_code=404, detail="Skill not found")
    await db.commit()


@router.get("/{skill_id}/versions", response_model=list[SkillVersionOut])
async def list_versions(
    skill_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List version history for a skill."""
    versions = await SkillService.list_versions(db, skill_id, user.id)
    if not versions and not await SkillService.get_skill(db, skill_id, user.id):
        raise HTTPException(status_code=404, detail="Skill not found")
    return [
        SkillVersionOut.model_validate(v).model_dump(mode="json")
        for v in versions
    ]


@router.get("/{skill_id}/versions/{version}")
async def get_version(
    skill_id: UUID,
    version: int,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a specific version's content."""
    storage = _get_storage()
    content = await SkillService.get_version_content(
        db, skill_id, version, user.id, storage=storage
    )
    if content is None:
        raise HTTPException(status_code=404, detail="Version not found")

    ver = await SkillService.get_version(db, skill_id, version, user.id)
    return {
        "skill_id": str(skill_id),
        "version": version,
        "content": content,
        "created_at": ver.created_at.isoformat() if ver else None,
    }


@router.get("/{skill_id}/download")
async def download_skill(
    skill_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Download the current version's zip file."""
    skill = await SkillService.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    storage = _get_storage()
    if not storage or not skill.object_key:
        if not skill.content:
            raise HTTPException(status_code=404, detail="No content available")
        zip_bytes = SkillStorage.create_skill_zip(
            content=skill.content,
            name=skill.name,
            metadata={
                "description": skill.description or "",
                "tags": skill.tags or [],
                "category": skill.category,
                "trigger_condition": skill.trigger_condition or "",
            },
        )
    else:
        try:
            zip_bytes = storage.download_skill_zip(skill.object_key)
        except Exception:
            raise HTTPException(status_code=502, detail="Failed to download from storage")

    filename = f"{skill.name.replace(' ', '_')}_v{skill.version}.zip"
    from urllib.parse import quote
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    encoded_name = quote(filename)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )


# ---- File Management Endpoints ----


def _validate_file(filename: str, data: bytes, file_type: str) -> None:
    """Validate a file's name, size, and type."""
    # Check file type
    if file_type not in VALID_FILE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: '{file_type}'. Must be one of: {', '.join(sorted(VALID_FILE_TYPES))}",
        )
    # Check size
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File '{filename}' exceeds 1 MB limit ({len(data)} bytes)",
        )
    # Sanitize filename: reject path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename: '{filename}'. Use basename only.",
        )
    # Script files must have valid script extension
    if file_type == "script":
        ext = os.path.splitext(filename)[1].lower()
        if ext not in SCRIPT_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Script files must have one of: {', '.join(sorted(SCRIPT_EXTENSIONS))}. Got: {ext}",
            )


@router.post("/{skill_id}/files", status_code=201)
async def upload_skill_files(
    skill_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: list[UploadFile] = File(...),
    file_type: str = Form("script"),
    descriptions: str = Form("[]"),
) -> dict:
    """Upload files to an existing skill.

    - files: One or more files to upload
    - file_type: 'script' | 'reference' | 'asset'
    - descriptions: JSON array matching files, e.g. [{"description": "...", "language": "bash"}]
    """
    skill = await SkillService.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    # Parse descriptions
    try:
        desc_list = json.loads(descriptions)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'descriptions' field")

    if not isinstance(desc_list, list):
        raise HTTPException(status_code=400, detail="'descriptions' must be a JSON array")

    storage = _get_storage()
    if not storage:
        raise HTTPException(status_code=502, detail="Storage unavailable")

    # Read and validate all files
    file_data_list: list[tuple[str, bytes, dict]] = []
    total_size = 0
    for i, f in enumerate(files):
        fname = f.filename or f"file_{i}"
        data = await f.read()
        _validate_file(fname, data, file_type)
        total_size += len(data)
        desc_info = desc_list[i] if i < len(desc_list) else {}
        file_data_list.append((fname, data, desc_info))

    if total_size > MAX_TOTAL_FILES_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Total file size exceeds 10 MB limit ({total_size} bytes)",
        )

    # Upload each file
    for fname, data, desc_info in file_data_list:
        await SkillService.add_file_to_skill(
            db=db,
            skill_id=skill_id,
            user_id=user.id,
            filename=fname,
            file_content=data,
            file_type=file_type,
            description=desc_info.get("description", ""),
            language=desc_info.get("language"),
            storage=storage,
        )

    await db.commit()

    # Re-fetch to get updated files
    skill = await SkillService.get_skill(db, skill_id, user.id)
    return {
        "skill_id": str(skill_id),
        "files": skill.files or [],
        "message": f"Uploaded {len(file_data_list)} file(s)",
    }


@router.get("/{skill_id}/files")
async def list_skill_files(
    skill_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """List all files in a skill."""
    skill = await SkillService.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill.files or []


@router.delete("/{skill_id}/files/{file_path:path}", status_code=204)
async def delete_skill_file(
    skill_id: UUID,
    file_path: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Remove a specific file from a skill.

    file_path can be full path (e.g. 'scripts/deploy.sh') or basename (e.g. 'deploy.sh').
    """
    storage = _get_storage()
    skill = await SkillService.remove_file_from_skill(
        db=db,
        skill_id=skill_id,
        user_id=user.id,
        file_path=file_path,
        storage=storage,
    )
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    await db.commit()


@router.get("/{skill_id}/files/{file_path:path}/download")
async def download_skill_file(
    skill_id: UUID,
    file_path: str,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Download a specific file from a skill's zip.

    file_path can be full path (e.g. 'scripts/deploy.sh') or just the filename.
    """
    skill = await SkillService.get_skill(db, skill_id, user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    storage = _get_storage()
    if not storage or not skill.object_key:
        raise HTTPException(status_code=404, detail="No storage available")

    try:
        zip_bytes = storage.download_skill_zip(skill.object_key)
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to download from storage")

    all_files = SkillStorage.extract_all_files(zip_bytes)

    # Try exact path match, then try with common prefixes
    target = file_path
    if target not in all_files:
        # Try adding directory prefixes
        for dir_name in ("scripts", "references", "assets"):
            candidate = f"{dir_name}/{file_path}"
            if candidate in all_files:
                target = candidate
                break

    if target not in all_files:
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found")

    data = all_files[target]
    filename = target.split("/")[-1]
    from urllib.parse import quote
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    encoded_name = quote(filename)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}",
        },
    )
