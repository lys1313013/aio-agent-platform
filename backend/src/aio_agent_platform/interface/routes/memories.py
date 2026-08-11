"""Memory management routes — CRUD + search for the Web UI."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import CurrentUser
from aio_agent_platform.db.connection import get_db
from aio_agent_platform.db.models import Memory
from aio_agent_platform.memory.service import MemoryService

router = APIRouter(prefix="/api/memories", tags=["memories"])


# ---- Schemas ----


class MemoryOut(BaseModel):
    id: UUID
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
            layer=m.layer,
            content=m.content,
            metadata=m.meta or {},
            created_at=m.created_at,
            updated_at=m.updated_at,
        )


class MemoryCreate(BaseModel):
    layer: str = Field(..., pattern="^(L1|L2|L3)$")
    content: str = Field(..., min_length=1, max_length=5000)
    metadata: dict | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    layer: str | None = Field(default=None, pattern="^(L1|L2|L3)$")
    metadata: dict | None = None


class MemoryListResponse(BaseModel):
    items: list[MemoryOut]
    total: int
    layer: str | None = None


class MemoryBatchDelete(BaseModel):
    ids: list[UUID] = Field(..., min_length=1, max_length=200)


class MemorySearchResult(BaseModel):
    id: UUID
    layer: str
    content: str
    score: float
    created_at: datetime


# ---- Endpoints ----


@router.get("/search", response_model=list[MemorySearchResult])
async def search_memories(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query(..., min_length=1, max_length=500),
    layer: str | None = Query(default=None, pattern="^(L1|L2|L3)$"),
    top_k: int = Query(default=10, ge=1, le=50),
) -> list[dict]:
    """Search memories by similarity."""
    layers = [layer] if layer else None
    results = await MemoryService.search_memories(
        db, user.id, q, layers=layers, top_k=top_k
    )
    return [
        MemorySearchResult(
            id=m.id,
            layer=m.layer,
            content=m.content,
            score=round(score, 4),
            created_at=m.created_at,
        ).model_dump(mode="json")
        for m, score in results
    ]


@router.get("/stats", response_model=dict[str, int])
async def memory_stats(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Return memory counts per layer."""
    rows = await db.execute(
        select(Memory.layer, func.count())
        .where(Memory.user_id == user.id)
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
) -> dict:
    """List memories, optionally filtered by layer."""
    memories = await MemoryService.list_memories(
        db, user.id, layer=layer, limit=limit, offset=offset
    )

    # Count total
    count_stmt = select(func.count()).select_from(Memory).where(Memory.user_id == user.id)
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
    memory = await MemoryService.create_memory(
        db, user.id, req.layer, req.content, meta=req.metadata, tenant_id=user.tenant_id
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
    memory = await MemoryService.update_memory(
        db,
        memory_id,
        user.id,
        content=req.content,
        layer=req.layer,
        meta=req.metadata,
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
