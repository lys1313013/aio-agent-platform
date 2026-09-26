"""Memory management routes — CRUD + search for the Web UI."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Agent, Memory, MemoryChange, MemoryVersion, Session
from aio_agent_platform.memory import history, organization
from aio_agent_platform.memory.service import MemoryService, memory_scope

router = APIRouter(prefix="/api/memories", tags=["memories"])


# ---- Schemas ----


class MemoryOut(BaseModel):
    agent_id: UUID | None = None
    id: UUID
    version: int = 1
    layer: str
    content: str
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_model(cls, m: Memory) -> MemoryOut:
        return cls(
            id=m.id,
            version=m.version,
            agent_id=m.agent_id,
            layer=m.layer,
            content=m.content,
            metadata=m.meta or {},
            created_at=m.created_at,
            updated_at=m.updated_at,
        )


class MemoryCreate(BaseModel):
    agent_id: UUID | None = None
    layer: str = Field(..., pattern="^(L1|L2|L3)$")
    content: str = Field(..., min_length=1, max_length=5000)
    metadata: dict | None = None


class MemoryUpdate(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)
    agent_id: UUID | None = None
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    layer: str | None = Field(default=None, pattern="^(L1|L2|L3)$")
    metadata: dict | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryOut]
    total: int
    layer: str | None = None


class MemoryBatchDelete(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, max_length=200)


class MemorySearchResult(MemoryOut):
    score: float


async def validate_memory_agent(db: AsyncSession, user, agent_id: UUID | None) -> None:
    if agent_id is None:
        return
    agent = await db.scalar(select(Agent.id).where(
        Agent.id == agent_id,
        Agent.tenant_id == user.tenant_id,
        or_(Agent.visibility == "tenant", Agent.created_by == user.id),
    ))
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")


# ---- Endpoints ----


@router.get("/search", response_model=list[MemorySearchResult])
async def search_memories(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(..., min_length=1, max_length=500),
    layer: str | None = Query(default=None, pattern="^(L1|L2|L3)$"),
    top_k: int = Query(default=10, ge=1, le=50),
    agent_id: UUID | None = Query(default=None),
) -> list[dict]:
    """Search memories by similarity."""
    layers = [layer] if layer else None
    results = await MemoryService.search_memories(
        db, user.id, q, layers=layers, top_k=top_k, agent_id=agent_id, include_shared=False
    )
    return [
        MemorySearchResult(
            id=m.id,
            agent_id=m.agent_id,
            layer=m.layer,
            content=m.content,
            score=round(score, 4),
            version=m.version,
            metadata=m.meta or {},
            created_at=m.created_at,
            updated_at=m.updated_at,
        ).model_dump(mode="json")
        for m, score in results
    ]


@router.get("/stats", response_model=dict[str, int])
async def memory_stats(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: UUID | None = Query(default=None),
) -> dict:
    """Return memory counts per layer."""
    rows = await db.execute(
        select(Memory.layer, func.count())
        .where(Memory.user_id == user.id, memory_scope(Memory, agent_id))
        .group_by(Memory.layer)
    )
    counts = {"L1": 0, "L2": 0, "L3": 0}
    for layer, count in rows:
        counts[layer] = count
    return counts


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    layer: str | None = Query(default=None, pattern="^(L1|L2|L3)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    agent_id: UUID | None = Query(default=None),
) -> dict:
    """List memories, optionally filtered by layer."""
    memories = await MemoryService.list_memories(
        db, user.id, layer=layer, limit=limit, offset=offset, agent_id=agent_id
    )

    # Count total
    count_stmt = select(func.count()).select_from(Memory).where(Memory.user_id == user.id, memory_scope(Memory, agent_id))
    if layer:
        count_stmt = count_stmt.where(Memory.layer == layer)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar()

    return MemoryListResponse(
        items=[MemoryOut.from_model(m) for m in memories],
        total=total,
        layer=layer,
    ).model_dump(mode="json")


@router.post("", response_model=MemoryOut, status_code=201)
async def create_memory(
    req: MemoryCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create a new memory."""
    await validate_memory_agent(db, user, req.agent_id)
    memory = await MemoryService.create_memory(
        db, user.id, req.layer, req.content, meta=req.metadata, tenant_id=user.tenant_id, agent_id=req.agent_id
    )
    return MemoryOut.from_model(memory).model_dump(mode="json")


@router.post("/batch-delete", response_model=dict[str, int])
async def batch_delete_memories(
    req: MemoryBatchDelete,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Delete multiple memories at once."""
    deleted = await MemoryService.delete_memories(db, user.id, req.ids)
    return {"deleted": deleted}


class OrganizePreviewRequest(BaseModel):
    layer: str = Field(default="L2", pattern="^(L1|L2)$")
    agent_id: UUID | None = None
    ids: list[UUID] | None = Field(default=None, min_length=2, max_length=30)
    offset: int = Field(default=0, ge=0)


class MergeSelection(BaseModel):
    id: str
    content: str = Field(min_length=1, max_length=5000)


class ApplyOrganizationRequest(BaseModel):
    selections: list[MergeSelection] = Field(min_length=1, max_length=100)


class RestoreMemoryRequest(BaseModel):
    expected_version: int = Field(ge=1)


@router.post("/organize/preview")
async def preview_organization(req: OrganizePreviewRequest, user: CurrentUser,
                               db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    await validate_memory_agent(db, user, req.agent_id)
    return await organization.preview(db, user.id, req.layer, req.agent_id, req.ids, req.offset)


@router.post("/organize/{plan_id}/apply")
async def apply_organization(plan_id: UUID, req: ApplyOrganizationRequest, user: CurrentUser,
                             db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    change = await organization.apply(db, user.id, plan_id, [item.model_dump() for item in req.selections])
    return {"change_id": str(change.id), "kind": change.kind}


@router.get("/changes")
async def list_changes(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)],
                       agent_id: UUID | None = None, layer: str | None = None,
                       offset: int = Query(default=0, ge=0)) -> list[dict]:
    versions = select(MemoryVersion.change_id).where(MemoryVersion.user_id == user.id,
        MemoryVersion.snapshot["agent_id"].as_string() == (str(agent_id) if agent_id else None))
    if layer:
        versions = versions.where(MemoryVersion.snapshot["layer"].as_string() == layer)
    changes = await db.scalars(select(MemoryChange).where(MemoryChange.user_id == user.id,
        MemoryChange.id.in_(versions)).order_by(MemoryChange.created_at.desc(), MemoryChange.id).offset(offset).limit(50))
    return [{"id": str(change.id), "kind": change.kind, "before": change.before,
             "after": change.after, "undone_by": str(change.undone_by) if change.undone_by else None,
             "created_at": change.created_at.isoformat()} for change in changes]


@router.post("/changes/{change_id}/undo")
async def undo_change(change_id: UUID, user: CurrentUser,
                      db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    change = await history.undo(db, user.id, change_id)
    return {"change_id": str(change.id), "kind": change.kind}


@router.get("/{memory_id}/versions")
async def memory_versions(memory_id: UUID, user: CurrentUser,
                           db: Annotated[AsyncSession, Depends(get_db)],
                           offset: int = Query(default=0, ge=0)) -> dict:
    versions = list((await db.scalars(select(MemoryVersion).where(
        MemoryVersion.memory_id == memory_id, MemoryVersion.user_id == user.id)
        .order_by(MemoryVersion.version.desc()).offset(offset).limit(50))).all())
    memory = await MemoryService.get_memory(db, memory_id, user.id)
    if not versions and not memory:
        raise HTTPException(404, "记忆不存在")
    sources = set()
    for version in versions:
        values = (version.snapshot.get("metadata") or {}).get("source_session") or []
        for value in values if isinstance(values, list) else [values]:
            try:
                sources.add(UUID(str(value)))
            except ValueError:
                pass
    sessions = await db.scalars(select(Session).where(Session.id.in_(sources), Session.user_id == user.id,
                                                       Session.source != "room")) if sources else []
    return {"current_version": memory.version if memory else None,
            "versions": [{"version": version.version, "kind": version.kind,
                          "change_id": str(version.change_id) if version.change_id else None,
                          "snapshot": version.snapshot, "created_at": version.created_at.isoformat()}
                         for version in versions],
            "sources": [{"id": str(session.id), "title": session.title} for session in sessions],
            "legacy_revisions": (memory.meta or {}).get("revisions", []) if memory else [],
            "has_more": len(versions) == 50}


@router.post("/{memory_id}/versions/{version}/restore")
async def restore_memory_version(memory_id: UUID, version: int, req: RestoreMemoryRequest,
                                 user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]) -> dict:
    # Restoring scope also requires current access to the historical agent.
    old = await db.scalar(select(MemoryVersion).where(MemoryVersion.memory_id == memory_id,
        MemoryVersion.user_id == user.id, MemoryVersion.version == version))
    if old:
        await validate_memory_agent(db, user, UUID(old.snapshot["agent_id"]) if old.snapshot.get("agent_id") else None)
    change = await history.restore(db, user.id, memory_id, version, req.expected_version)
    return {"change_id": str(change.id), "kind": change.kind}


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get a single memory by ID."""
    memory = await MemoryService.get_memory(db, memory_id, user.id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryOut.from_model(memory).model_dump(mode="json")


@router.put("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: UUID,
    req: MemoryUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Update an existing memory."""
    if "agent_id" in req.model_fields_set:
        await validate_memory_agent(db, user, req.agent_id)
    memory = await MemoryService.update_memory(
        db,
        memory_id,
        user.id,
        content=req.content,
        layer=req.layer,
        meta=req.metadata,
        expected_version=req.expected_version,
        agent_id=req.agent_id,
        set_agent="agent_id" in req.model_fields_set,
    )
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return MemoryOut.from_model(memory).model_dump(mode="json")


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a memory."""
    deleted = await MemoryService.delete_memory(db, memory_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
