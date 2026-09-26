"""Memory organization/undo transactions against an isolated DB, never business data."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from aio_agent_platform.db.models import (
    Memory,
    MemoryChange,
    MemoryOrganizePlan,
    MemoryVersion,
    Session,
)
from aio_agent_platform.memory import history, organization
from aio_agent_platform.memory.service import MemoryService, _merge_meta


@compiles(JSONB, "sqlite")
def sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}")
    async with engine.begin() as connection:
        for model in (Memory, MemoryChange, MemoryVersion, MemoryOrganizePlan, Session):
            await connection.run_sync(model.__table__.create)
    monkeypatch.setattr(
        organization, "create_default_provider_for_user", AsyncMock(return_value=None)
    )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create(db, user, content="用户喜欢简洁中文回复", **kwargs):
    return await MemoryService.create_memory(
        db, user, kwargs.pop("layer", "L2"), content, tenant_id=uuid4(), **kwargs
    )


async def setup_pair(factory):
    user = uuid4()
    async with factory() as db:
        one = await create(
            db, user, meta={"tags": ["a"], "source_session": "s1", "custom": {"a": 1}}
        )
        two = await create(
            db, user, meta={"tags": ["b"], "source_session": ["s2", "s1"], "custom": {"b": 2}}
        )
        originals = {str(row.id): history.snapshot(row) for row in (one, two)}
        await db.commit()
        preview = await organization.preview(db, user, "L2", None)
        await db.commit()
    return user, originals, preview


def selections(preview):
    return [{"id": group["id"], "content": group["content"]} for group in preview["groups"]]


async def test_preview_merge_and_atomic_undo_preserve_all_original_metadata(factory):
    user, originals, preview = await setup_pair(factory)
    assert len(preview["groups"]) == 1
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Memory)) == 2
        assert await db.scalar(select(func.count()).select_from(MemoryVersion)) == 2
        change = await organization.apply(db, user, UUID(preview["id"]), selections(preview))
        change_id = change.id
        await db.commit()
    async with factory() as db:
        rows = list(await db.scalars(select(Memory)))
        assert len(rows) == 1
        assert set(rows[0].meta["source_session"]) == {"s1", "s2"}
        assert set(rows[0].meta["tags"]) == {"a", "b"}
        assert rows[0].version == 2
        assert (
            await organization.apply(db, user, UUID(preview["id"]), selections(preview))
        ).id == change_id
        reverted = await history.undo(db, user, change_id)
        assert (await history.undo(db, user, change_id)).id == reverted.id
        await db.commit()
    async with factory() as db:
        rows = list(await db.scalars(select(Memory)))
        assert len(rows) == 2
        for row in rows:
            old = originals[str(row.id)]
            assert row.meta == old["metadata"]
            assert row.content == old["content"]
            assert str(row.tenant_id) == old["tenant_id"]
            assert history.timestamp(row.created_at) == old["created_at"]
            assert row.version == 3
        assert await db.scalar(select(func.count()).select_from(MemoryVersion)) == 6
        # Undoing the undo is also audited and restores the merged state.
        await history.undo(db, user, reverted.id)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(Memory)) == 1


@pytest.mark.parametrize("mutation", ["edit", "delete", "source"])
async def test_preview_rejects_stale_contents_and_sources(factory, mutation):
    user, originals, preview = await setup_pair(factory)
    key = UUID(next(iter(originals)))
    async with factory() as db:
        if mutation == "delete":
            await MemoryService.delete_memory(db, key, user)
        else:
            await MemoryService.update_memory(
                db,
                key,
                user,
                **(
                    {"content": "新事实"}
                    if mutation == "edit"
                    else {"meta": {"source_session": "new"}}
                ),
            )
        await db.commit()
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await organization.apply(db, user, UUID(preview["id"]), selections(preview))
        assert exc.value.status_code == 409
        await db.rollback()
        assert (
            await db.scalar(
                select(func.count()).select_from(MemoryChange).where(MemoryChange.kind == "merge")
            )
            == 0
        )


async def test_full_versions_remain_after_many_corrections_and_restore_scope(factory):
    user, agent = uuid4(), uuid4()
    async with factory() as db:
        row = await create(
            db, user, "初始事实", meta={"tags": ["original"], "source_session": "source"}
        )
        key = row.id
        for index in range(12):
            row = await MemoryService.update_memory(
                db,
                key,
                user,
                content=f"纠错 {index}",
                meta={"tags": [str(index)]},
                layer="L1",
                set_agent=True,
                agent_id=agent,
                expected_version=index + 1,
            )
        assert row.version == 13
        await db.commit()
        await history.restore(db, user, key, 1, 13)
        await db.commit()
        await db.refresh(row)
        assert (row.content, row.layer, row.agent_id, row.version) == ("初始事实", "L2", None, 14)
        assert row.meta == {"tags": ["original"], "source_session": "source"}
        assert await db.scalar(select(func.count()).select_from(MemoryVersion)) == 14
        with pytest.raises(HTTPException) as exc:
            await history.restore(db, user, key, 2, 13)
        assert exc.value.status_code == 409


async def test_undo_refuses_later_edits_even_when_content_was_restored(factory):
    user, _, preview = await setup_pair(factory)
    async with factory() as db:
        change = await organization.apply(db, user, UUID(preview["id"]), selections(preview))
        key = UUID(preview["groups"][0]["target_id"])
        await db.commit()
        await MemoryService.update_memory(db, key, user, content="later")
        await history.restore(db, user, key, 2, 3)
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await history.undo(db, user, change.id)
        assert exc.value.status_code == 409


async def test_delete_can_be_undone_but_not_restored_individually(factory):
    user = uuid4()
    async with factory() as db:
        row = await create(db, user)
        key = row.id
        await MemoryService.delete_memory(db, key, user)
        deleted = await db.scalar(select(MemoryChange).where(MemoryChange.kind == "delete"))
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await history.restore(db, user, key, 1, 1)
        assert exc.value.status_code == 409
        await history.undo(db, user, deleted.id)
        await db.commit()
        assert (await db.get(Memory, key)).version == 3


@pytest.mark.parametrize("boundary", ["user", "agent", "layer"])
async def test_selected_preview_cannot_cross_scope(factory, boundary):
    user = uuid4()
    async with factory() as db:
        one = await create(db, user)
        two = await create(
            db,
            uuid4() if boundary == "user" else user,
            agent_id=uuid4() if boundary == "agent" else None,
            layer="L1" if boundary == "layer" else "L2",
        )
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await organization.preview(db, user, "L2", None, [one.id, two.id])
        assert exc.value.status_code == 404
        assert await db.scalar(select(func.count()).select_from(MemoryOrganizePlan)) == 0


async def test_expired_preview_and_changed_idempotency_payload_are_rejected(factory):
    user, _, preview = await setup_pair(factory)
    async with factory() as db:
        plan = await db.get(MemoryOrganizePlan, UUID(preview["id"]))
        plan.created_at = datetime.now(UTC) - timedelta(hours=2)
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await organization.apply(db, user, plan.id, selections(preview))
        assert exc.value.status_code == 409
        plan.created_at = datetime.now(UTC)
        await organization.apply(db, user, plan.id, selections(preview))
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await organization.apply(
                db, user, plan.id, [{**selections(preview)[0], "content": "different"}]
            )
        assert exc.value.status_code == 409


async def test_foreign_history_and_plan_are_inaccessible(factory):
    user, originals, preview = await setup_pair(factory)
    async with factory() as db:
        change = await organization.apply(db, user, UUID(preview["id"]), selections(preview))
        await db.commit()
        for action in (
            organization.apply(db, uuid4(), UUID(preview["id"]), selections(preview)),
            history.undo(db, uuid4(), change.id),
            history.restore(db, uuid4(), UUID(next(iter(originals))), 1, 1),
        ):
            with pytest.raises(HTTPException) as exc:
                await action
            assert exc.value.status_code == 404


async def test_model_suggestions_validated_and_failure_falls_back_without_writes(
    factory, monkeypatch
):
    user = uuid4()
    async with factory() as db:
        one = await create(db, user, "喜欢中文回复")
        two = await create(db, user, "要求简洁")
        await db.commit()
        provider = SimpleNamespace(
            complete=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(
                        {
                            "groups": [
                                {
                                    "ids": [str(one.id), str(uuid4())],
                                    "content": "伪造建议",
                                    "reason": "bad",
                                }
                            ]
                        }
                    )
                )
            )
        )
        monkeypatch.setattr(
            organization, "create_default_provider_for_user", AsyncMock(return_value=provider)
        )
        result = await organization.preview(db, user, "L2", None, [one.id, two.id])
        assert result["warnings"]
        assert set(result["groups"][0]["content"].split("\n\n")) == {one.content, two.content}
        assert await db.scalar(select(func.count()).select_from(MemoryVersion)) == 2
        provider.complete.return_value.content = '{"groups": []}'
        result = await organization.preview(db, user, "L2", None, [one.id, two.id])
        assert result["groups"] == []  # Model-declared conflicts are not forcibly merged.


async def test_optimistic_update_cannot_silently_overwrite_concurrent_edit(factory):
    user = uuid4()
    async with factory() as db:
        row = await create(db, user)
        key = row.id
        await db.commit()
    async with factory() as stale, factory() as fresh:
        old = await stale.get(Memory, key)
        await stale.commit()  # retain stale identity while releasing SQLite read transaction
        await MemoryService.update_memory(fresh, key, user, content="winner")
        await fresh.commit()
        before = {str(key): history.snapshot(old)}
        old.content = "lost update"
        with pytest.raises(HTTPException) as exc:
            await history.record_change(stale, user, "edit", before, [old])
        assert exc.value.status_code == 409
    async with factory() as db:
        assert (await db.get(Memory, key)).content == "winner"


async def test_history_sources_only_link_owned_non_room_sessions(factory):
    from aio_agent_platform.interface.routes.memories import list_changes, memory_versions

    user = uuid4()
    async with factory() as db:
        owned, foreign, room = uuid4(), uuid4(), uuid4()
        db.add_all(
            [
                Session(id=owned, user_id=user, title="来源", source="web"),
                Session(id=foreign, user_id=uuid4(), title="秘密", source="web"),
                Session(id=room, user_id=user, title="房间", source="room"),
            ]
        )
        row = await create(
            db, user, meta={"source_session": [str(owned), str(foreign), str(room), "invalid"]}
        )
        await db.commit()
        result = await memory_versions(row.id, SimpleNamespace(id=user), db, offset=0)
        assert result["sources"] == [{"id": str(owned), "title": "来源"}]
        changes = await list_changes(SimpleNamespace(id=user), db, offset=0)
        assert len(changes) == 1
        assert changes[0]["kind"] == "create"


def test_merging_sources_flattens_lists_without_mutating_original():
    original = {"source_session": ["a"], "tags": ["x"]}
    assert _merge_meta(original, {"source_session": ["a", "b"], "tags": ["y"]}) == {
        "source_session": ["a", "b"],
        "tags": ["x", "y"],
    }
    assert original == {"source_session": ["a"], "tags": ["x"]}


async def test_multiple_merge_groups_validate_all_before_any_write(factory):
    user = uuid4()
    async with factory() as db:
        for content in ("A", "A", "B", "B"):
            await create(db, user, content)
        await db.commit()
        result = await organization.preview(db, user, "L2", None)
        await db.commit()
        assert len(result["groups"]) == 2
        changed = UUID(result["groups"][-1]["ids"][-1])
        await MemoryService.update_memory(db, changed, user, content="新的事实")
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await organization.apply(db, user, UUID(result["id"]), selections(result))
        assert exc.value.status_code == 409
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(Memory)) == 4
        assert (
            await db.scalar(
                select(func.count()).select_from(MemoryChange).where(MemoryChange.kind == "merge")
            )
            == 0
        )


async def test_valid_model_proposal_and_user_correction_are_audited(factory, monkeypatch):
    user = uuid4()
    async with factory() as db:
        one = await create(db, user, "中文回复")
        two = await create(db, user, "回复简洁")
        await db.commit()
        provider = SimpleNamespace(
            complete=AsyncMock(
                return_value=SimpleNamespace(
                    content=json.dumps(
                        {
                            "groups": [
                                {
                                    "ids": [str(one.id), str(two.id)],
                                    "content": "简洁中文回复",
                                    "reason": "同一偏好",
                                }
                            ]
                        }
                    )
                )
            )
        )
        monkeypatch.setattr(
            organization, "create_default_provider_for_user", AsyncMock(return_value=provider)
        )
        result = await organization.preview(db, user, "L2", None, [one.id, two.id])
        await db.commit()
        change = await organization.apply(
            db,
            user,
            UUID(result["id"]),
            [{"id": result["groups"][0]["id"], "content": "仅工作对话要求简洁中文回复"}],
        )
        await db.commit()
        assert change.after[str(one.id)]["content"] == "仅工作对话要求简洁中文回复"
        assert change.before[str(one.id)]["content"] == "中文回复"


async def test_batch_delete_undo_and_creation_undo_keep_monotonic_revisions(factory):
    user = uuid4()
    async with factory() as db:
        one = await create(db, user)
        two = await create(db, user, agent_id=uuid4())
        foreign = await create(db, uuid4())
        await db.commit()
        assert await MemoryService.delete_memories(db, user, [one.id, two.id, foreign.id]) == 2
        deleted = await db.scalar(select(MemoryChange).where(MemoryChange.kind == "delete"))
        await history.undo(db, user, deleted.id)
        await db.commit()
        assert await db.scalar(select(func.count()).select_from(Memory)) == 3
        assert (await db.get(Memory, foreign.id)).version == 1
        new = await create(db, user, "新条目")
        creation = await db.scalar(
            select(MemoryChange)
            .where(MemoryChange.kind == "create")
            .order_by(MemoryChange.created_at.desc())
            .limit(1)
        )
        removal = await history.undo(db, user, creation.id)
        await db.commit()
        assert await db.get(Memory, new.id) is None
        await history.undo(db, user, removal.id)
        await db.commit()
        assert (await db.get(Memory, new.id)).version == 3
