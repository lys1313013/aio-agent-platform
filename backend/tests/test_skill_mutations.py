"""Skill package invariants and real PostgreSQL mutation/permission tests."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aio_agent_platform.db.models import Skill, SkillMutation, SkillVersion
from aio_agent_platform.skills.contracts import SkillError, normalize_files
from aio_agent_platform.skills.mutations import mutate_skill
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.skills.storage import SkillStorage
from aio_agent_platform.tools.builtin import UPDATE_SKILL
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import ToolRegistry


class Storage:
    def __init__(self):
        self.objects = {}
        self.fail = False

    def upload_skill_zip(self, user_id, skill_id, version, data):
        if self.fail:
            raise OSError("offline")
        key = f"{user_id}/{skill_id}/{version}/{uuid4()}"
        self.objects[key] = data
        return key

    def download_skill_zip(self, key):
        return self.objects[key]

    def delete_version(self, key):
        self.objects.pop(key, None)

    def delete_skill_zips(self, user_id, skill_id):
        for key in list(self.objects):
            if key.startswith(f"{user_id}/{skill_id}/"):
                del self.objects[key]


def files():
    return [{"path": "scripts/nested/check.py", "content": "print('pass')"},
            {"path": "assets/template.bin", "content": b'\x00\xff\x01template'}]


def test_package_preserves_nested_paths_and_metadata():
    data = SkillStorage.create_skill_zip("# steps", 'Name: "quoted"', {"tags": ["a", "b"], "description": "line1\nline2"}, files())
    assert SkillStorage.extract_all_files(data) == {"scripts/nested/check.py": b"print('pass')", "assets/template.bin": b'\x00\xff\x01template'}
    parsed = SkillStorage.parse_skill_zip(data)
    assert parsed["metadata"]["name"] == 'Name: "quoted"'
    assert parsed["metadata"]["description"] == 'line1\nline2'
    assert parsed["metadata"]["tags"] == ["a", "b"]


@pytest.mark.parametrize("path", ["/etc/passwd", "scripts/../a", "scripts//a", "scripts/./a", "scripts/a\\b", "scripts/a\x00", "unknown/a"])
def test_unsafe_paths_rejected(path):
    with pytest.raises(SkillError):
        normalize_files([{"path": path, "content": "x"}])


def test_duplicate_and_oversize_files_rejected():
    with pytest.raises(SkillError, match="重复"):
        normalize_files([{"path": "assets/a", "content": "x"}] * 2)
    with pytest.raises(SkillError, match="1 MiB"):
        normalize_files([{"path": "assets/a", "content": b"a" * (1024 * 1024 + 1)}])


async def test_executor_uses_business_failure_status():
    from aio_agent_platform.skills.mutations import SkillToolOutput
    registry = ToolRegistry()
    registry.register(UPDATE_SKILL)
    executor = ToolExecutor(registry, SimpleNamespace())
    async def invalid(*args, **kwargs):
        return SkillToolOutput({"success": False, "message": "版本冲突", "code": "version_conflict"})
    executor.register_direct_handler("update_skill", invalid)
    result = await executor._execute_impl("update_skill", {}, "call", str(uuid4()), str(uuid4()))
    assert not result.success
    assert json.loads(result.error)["code"] == "version_conflict"


@pytest.mark.postgres
async def test_text_update_preserves_binary_and_history(db_session):
    uid, storage = uuid4(), Storage()
    skill = await SkillService.create_skill(db_session, uid, "对账", content="# old", files=files(), storage=storage)
    await db_session.commit()
    old_key, sid = skill.object_key, skill.id
    updated = await SkillService.update_skill(db_session, sid, uid, content="# new", storage=storage, expected_version=1)
    await db_session.commit()
    assert updated.id == sid and updated.version == 2
    assert updated.object_key != old_key
    assert SkillStorage.extract_all_files(storage.objects[old_key]) == SkillStorage.extract_all_files(storage.objects[updated.object_key])
    versions = await SkillService.list_versions(db_session, sid, uid)
    assert versions[0].content == "# old"
    assert versions[0].snapshot["name"] == "对账"
    assert len(versions[0].snapshot["files"]) == 2
    assert updated.verification["status"] == "unverified"


@pytest.mark.postgres
async def test_partial_file_mutations_and_noop(db_session):
    uid, storage = uuid4(), Storage()
    skill = await SkillService.create_skill(db_session, uid, "files", content="# method", files=files(), storage=storage)
    await db_session.commit()
    skill = await SkillService.update_skill(db_session, skill.id, uid, expected_version=1,
        file_changes=[{"path": "scripts/new.py", "content": "print(2)"}], remove_files=["scripts/nested/check.py"], storage=storage)
    await db_session.commit()
    contents = SkillStorage.extract_all_files(storage.objects[skill.object_key])
    assert "scripts/nested/check.py" not in contents and contents["scripts/new.py"] == b"print(2)"
    assert contents["assets/template.bin"] == b'\x00\xff\x01template'
    skill = await SkillService.update_skill(db_session, skill.id, uid, expected_version=2, content="# method", storage=storage)
    assert skill.version == 2 and skill.mutation_status == "unchanged"


@pytest.mark.postgres
async def test_conflict_and_access_control(db_session):
    uid, stranger = uuid4(), uuid4()
    skill = await SkillService.create_skill(db_session, uid, "mine", content="# old")
    await db_session.commit()
    assert await SkillService.update_skill(db_session, skill.id, stranger, content="hack", expected_version=1) is None
    assert await SkillService.get_skill(db_session, skill.id, stranger) is None
    assert await SkillService.search_skills(db_session, stranger, "*") == []
    with pytest.raises(SkillError) as exc:
        await SkillService.update_skill(db_session, skill.id, uid, content="new", expected_version=9)
    assert exc.value.code == "version_conflict"
    assert skill.content == "# old"


@pytest.mark.postgres
async def test_same_name_dedupe_and_conflict(db_session):
    uid = uuid4()
    first = await SkillService.create_skill(db_session, uid, "Ａ Skill", content="# old")
    await db_session.commit()
    again = await SkillService.create_skill(db_session, uid, "a skill", content="# old")
    assert again.id == first.id and again.mutation_status == "existing"
    with pytest.raises(SkillError, match="同名"):
        await SkillService.create_skill(db_session, uid, "A Skill", content="# different")


@pytest.mark.postgres
async def test_failed_upload_does_not_promote_and_rollback_cleans_objects(db_session):
    uid, storage = uuid4(), Storage()
    skill = await SkillService.create_skill(db_session, uid, "atomic", content="old", files=files(), storage=storage)
    await db_session.commit()
    sid, old_key = skill.id, skill.object_key
    storage.fail = True
    with pytest.raises(SkillError):
        await SkillService.update_skill(db_session, sid, uid, content="new", expected_version=1, storage=storage)
    await db_session.rollback()
    fresh = await SkillService.get_skill(db_session, sid, uid)
    assert fresh.version == 1 and fresh.object_key == old_key
    storage.fail = False
    await SkillService.update_skill(db_session, sid, uid, content="new", expected_version=1, storage=storage)
    assert len(storage.objects) == 2
    await db_session.rollback()
    assert list(storage.objects) == [old_key]


@pytest.mark.postgres
async def test_missing_reference_and_storage_fail_closed(db_session):
    uid = uuid4()
    with pytest.raises(SkillError, match="不存在"):
        await SkillService.create_skill(db_session, uid, "bad", content="Run `scripts/missing.py`")
    with pytest.raises(SkillError, match="对象存储"):
        await SkillService.create_skill(db_session, uid, "bad", content="ok", files=files())
    await db_session.rollback()
    assert (await db_session.execute(select(func.count()).select_from(Skill))).scalar_one() == 0


@pytest.mark.postgres
async def test_concurrent_update_one_winner(db_session, engine):
    uid = uuid4()
    skill = await SkillService.create_skill(db_session, uid, "race", content="old")
    await db_session.commit()
    sid = skill.id
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def edit(content):
        async with factory() as db:
            try:
                await SkillService.update_skill(db, sid, uid, content=content, expected_version=1)
                await db.commit()
                return "ok"
            except SkillError as exc:
                await db.rollback()
                return exc.code
    assert sorted(await asyncio.gather(edit("first"), edit("second"))) == ["ok", "version_conflict"]
    async with factory() as db:
        assert len(await SkillService.list_versions(db, sid, uid)) == 1


@pytest.mark.postgres
async def test_concurrent_create_deduplicates(db_session, engine):
    uid = uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def create():
        async with factory() as db:
            result = await SkillService.create_skill(db, uid, "race-create", content="same")
            await db.commit()
            return result.id
    ids = await asyncio.gather(create(), create())
    assert ids[0] == ids[1]


@pytest.mark.postgres
async def test_mutation_receipt_replay_after_later_version(db_session, engine):
    uid, session_id = uuid4(), uuid4()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    with patch("aio_agent_platform.skills.mutations.get_session_factory", return_value=factory):
        created = json.loads(await mutate_skill("create", {"name": "agent", "content": "old"}, str(uid), str(session_id), None))
        args = {"skill_id": created["skill_id"], "expected_version": 1, "content": "new", "change_summary": "改正文", "request_id": "update-one"}
        result = json.loads(await mutate_skill("update", args, str(uid), str(session_id), None))
        assert result["version"] == 2 and result["previous_version"] == 1
        replay = json.loads(await mutate_skill("update", args, str(uid), str(session_id), None))
        assert replay == result
        conflict = json.loads(await mutate_skill("update", {**args, "content": "different"}, str(uid), str(session_id), None))
        assert not conflict["success"] and conflict["code"] == "idempotency_conflict"
    async with factory() as db:
        assert (await db.execute(select(func.count()).select_from(SkillMutation))).scalar_one() == 2
        assert (await db.execute(select(func.count()).select_from(SkillVersion))).scalar_one() == 1


@pytest.mark.postgres
async def test_bound_skills_do_not_hide_personal_or_leak_other_user(db_session):
    uid = uuid4()
    bound = await SkillService.create_skill(db_session, uid, "旧绑定", content="old")
    personal = await SkillService.create_skill(db_session, uid, "对账", content="对账方法")
    foreign = await SkillService.create_skill(db_session, uuid4(), "他人", content="private")
    await db_session.commit()
    results = await SkillService.get_skills_for_prompt(db_session, uid, "对账", bound_skills=[bound, foreign])
    assert results[0].id == personal.id
    assert foreign.id not in {s.id for s in results}


@pytest.mark.postgres
async def test_bound_skills_survive_when_search_fills_budget(db_session):
    uid = uuid4()
    for i in range(3):
        await SkillService.create_skill(db_session, uid, f"对账流程{i}", content="对账方法")
    bound = [await SkillService.create_skill(db_session, uid, f"绑定技能{i}", content="bound") for i in range(2)]
    await db_session.commit()
    results = await SkillService.get_skills_for_prompt(db_session, uid, "对账", bound_skills=bound)
    ids = [s.id for s in results]
    assert len(ids) == 3, "relevant hits and a bound skill must share the budget"
    assert bound[0].id in ids, "search results must not crowd out every bound skill"
    # With nothing relevant, the bound list still fills the budget in its own order.
    only_bound = await SkillService.get_skills_for_prompt(db_session, uid, "zz不匹配zz", bound_skills=bound)
    assert [s.id for s in only_bound] == [s.id for s in bound]


@pytest.mark.postgres
async def test_http_manual_edit_conflict_and_attachment_history(client, db_session):
    from aio_agent_platform.auth.dependencies import get_current_user
    from aio_agent_platform.db.models import User
    from aio_agent_platform.interface.api import app
    user = User(id=uuid4(), username="skill-editor", email="skill-editor@test.local", password_hash="test", tenant_id=uuid4(), is_active=True)
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_current_user] = lambda: user
    storage = Storage()
    with patch("aio_agent_platform.interface.routes.skills._get_storage", return_value=storage):
        created = await client.post("/api/skills", json={"name": "API 技能", "content": "正文"})
        assert created.status_code == 201
        sid = created.json()["id"]
        added = await client.post(f"/api/skills/{sid}/files", data={"file_type": "asset", "expected_version": 1}, files={"files": ("template.bin", b'\x00\xff', "application/octet-stream")})
        assert added.status_code == 201
        updated = await client.put(f"/api/skills/{sid}", json={"content": "新正文", "expected_version": 2})
        assert updated.status_code == 200 and updated.json()["version"] == 3
        assert len(updated.json()["files"]) == 1
        conflict = await client.put(f"/api/skills/{sid}", json={"content": "stale", "expected_version": 2})
        assert conflict.status_code == 409
        old = await client.get(f"/api/skills/{sid}/versions/2/download")
        assert old.status_code == 200
        assert SkillStorage.extract_all_files(old.content)["assets/template.bin"] == b'\x00\xff'
        current = await client.get(f"/api/skills/{sid}/download")
        assert SkillStorage.extract_all_files(current.content) == SkillStorage.extract_all_files(old.content)
        deleted_file = await client.delete(f"/api/skills/{sid}/files/assets/template.bin?expected_version=3")
        assert deleted_file.status_code == 204
        assert (await client.get(f"/api/skills/{sid}")).json()["files"] == []
        assert SkillStorage.extract_all_files((await client.get(f"/api/skills/{sid}/versions/3/download")).content)["assets/template.bin"] == b'\x00\xff'


@pytest.mark.postgres
async def test_delete_rollback_does_not_delete_live_packages(db_session):
    uid, storage = uuid4(), Storage()
    skill = await SkillService.create_skill(db_session, uid, "keep", content="old", files=files(), storage=storage)
    await db_session.commit()
    sid, key = skill.id, skill.object_key
    await SkillService.delete_skill(db_session, sid, uid, storage)
    await db_session.rollback()
    await db_session.commit()
    assert key in storage.objects
    assert await SkillService.get_skill(db_session, sid, uid)


@pytest.mark.postgres
async def test_inactive_edit_and_source_preservation(db_session):
    uid = uuid4()
    skill = await SkillService.create_skill(db_session, uid, "inactive", content="old", source={"type": "agent", "agent_id": "original"})
    await db_session.commit()
    skill = await SkillService.update_skill(db_session, skill.id, uid, is_active=False, expected_version=1)
    await db_session.commit()
    skill = await SkillService.update_skill(db_session, skill.id, uid, content="new", expected_version=2, source={"type": "agent", "agent_id": "editor"})
    assert not skill.is_active and skill.provenance["created"]["agent_id"] == "original"
    assert skill.provenance["modified"]["agent_id"] == "editor"


@pytest.mark.postgres
async def test_verification_requires_real_scoped_evidence(db_session, engine):
    from aio_agent_platform.skills.handlers import handle_report_skill_result
    uid, sid = uuid4(), uuid4()
    storage = Storage()
    skill = await SkillService.create_skill(db_session, uid, "verify", content="run", files=files(), storage=storage)
    await db_session.commit()
    skill_id = skill.id
    factory = async_sessionmaker(engine, expire_on_commit=False)
    arguments = {"skill_id": str(skill_id), "success": True, "version": 1,
                 "evidence_tool_call_id": "test-run", "expected_output": "PASS"}
    executor = SimpleNamespace(skill_execution_evidence={})
    with patch("aio_agent_platform.skills.handlers.get_session_factory", return_value=factory):
        forged = json.loads(await handle_report_skill_result(arguments, str(uid), str(sid), tool_executor=executor))
        assert not forged["success"] and forged["code"] == "invalid_evidence"
        executor.skill_execution_evidence[(str(uid), str(sid), "test-run")] = {
            "arguments": {"command": f"python /workspace/default/skills/{skill_id}/v1/scripts/check.py"},
            "success": True, "output": "PASS"}
        await handle_report_skill_result(arguments, str(uid), str(sid), tool_executor=executor)
        async with factory() as db:
            verified = await SkillService.get_skill(db, skill_id, uid)
            assert verified.verification["status"] == "passed"
        # A real run whose output fails the declared check must not be marked passed.
        executor.skill_execution_evidence[(str(uid), str(sid), "test-run")]["output"] = "FAIL"
        await handle_report_skill_result(arguments, str(uid), str(sid), tool_executor=executor)
        async with factory() as db:
            failed = await SkillService.get_skill(db, skill_id, uid)
            assert failed.verification["status"] == "failed" and not failed.is_active


@pytest.mark.postgres
async def test_partial_verification_evidence_is_rejected(db_session, engine):
    from aio_agent_platform.skills.handlers import handle_report_skill_result
    uid, sid = uuid4(), uuid4()
    skill = await SkillService.create_skill(db_session, uid, "partial verify", content="run")
    await db_session.commit()
    skill_id = skill.id
    factory = async_sessionmaker(engine, expire_on_commit=False)
    executor = SimpleNamespace(skill_execution_evidence={})
    with patch("aio_agent_platform.skills.handlers.get_session_factory", return_value=factory):
        for partial in ({"version": 1}, {"evidence_tool_call_id": "test-run"}, {"expected_output": "PASS"},
                        {"version": 1, "expected_output": "PASS"}):
            result = json.loads(await handle_report_skill_result(
                {"skill_id": str(skill_id), "success": True, **partial}, str(uid), str(sid), tool_executor=executor))
            assert not result["success"] and result["code"] == "invalid_arguments", partial
        async with factory() as db:
            untouched = await SkillService.get_skill(db, skill_id, uid)
            assert untouched.verification.get("status") == "unverified" and untouched.success_count == 0, \
                "a partial triple must not count as success or a verification result"
        # Reporting usage without any evidence input stays supported.
        usage = await handle_report_skill_result(
            {"skill_id": str(skill_id), "success": True}, str(uid), str(sid), tool_executor=executor)
        assert isinstance(usage, str) and "Result recorded" in usage


@pytest.mark.postgres
async def test_http_atomic_create_and_edit_with_files(client, db_session):
    from aio_agent_platform.auth.dependencies import get_current_user
    from aio_agent_platform.db.models import User
    from aio_agent_platform.interface.api import app
    user = User(id=uuid4(), username="atomic-editor", email="atomic@test.local", password_hash="test", tenant_id=uuid4(), is_active=True)
    db_session.add(user)
    await db_session.flush()
    app.dependency_overrides[get_current_user] = lambda: user
    storage = Storage()
    with patch("aio_agent_platform.interface.routes.skills._get_storage", return_value=storage):
        created = await client.post("/api/skills", json={"name": "atomic", "content": "original", "files": [{"path": "assets/binary", "content_base64": "AP8="}]})
        assert created.status_code == 201 and len(created.json()["files"]) == 1
        sid = created.json()["id"]
        changed = await client.put(f"/api/skills/{sid}", json={"content": "new", "expected_version": 1,
            "files": [{"path": "scripts/check.py", "content": "print(2)"}]})
        assert changed.status_code == 200 and changed.json()["version"] == 2
        assert len(changed.json()["files"]) == 2
        assert len((await client.get(f"/api/skills/{sid}/versions")).json()) == 1
        assert (await client.get(f"/api/skills/{sid}/versions/1")).json()["content"] == "original"
