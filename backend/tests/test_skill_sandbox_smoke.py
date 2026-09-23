"""Opt-in real MinIO/Docker skill-package smoke; only uses newly created sandboxes."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from aio_agent_platform.core.config import settings
from aio_agent_platform.db.models import Workspace
from aio_agent_platform.sandbox import SandboxManager
from aio_agent_platform.skills.contracts import SkillError
from aio_agent_platform.skills.mutations import mutate_skill
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.skills.storage import SkillStorage
from aio_agent_platform.skills.workspace import read_workspace_file
from aio_agent_platform.storage.client import ObjectStorage
from aio_agent_platform.storage.workspace import WorkspaceStorage

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.environ.get("SKILL_DOCKER_SMOKE") != "1", reason="requires dedicated MinIO and Docker")]


async def test_real_package_survives_source_deletion_and_sandbox_recreation(db_session, engine, monkeypatch):
    # Never discover or reclaim an existing user's sandbox namespace.
    monkeypatch.setattr(settings.sandbox, "namespace", f"codex-skill-smoke-{uuid4().hex[:8]}")
    uid, wid, sid = uuid4(), uuid4(), uuid4()
    db_session.add(Workspace(id=wid, user_id=uid, name="Skill test", slug="default", is_default=True))
    await db_session.commit()
    manager, storage = SandboxManager(WorkspaceStorage(ObjectStorage())), SkillStorage()
    sandbox = await manager.get_or_create(str(uid), str(sid), str(wid), "default")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    skill_id = None
    try:
        result = await manager.execute(sandbox, "python3 -c \"from pathlib import Path; p=Path('/workspace/default/template.bin'); p.write_bytes(bytes(range(256))*4000)\"")
        assert result.exit_code == 0
        args = {"name": "真实文件测试", "content": "执行 `scripts/check.py`，模板 `assets/template.bin`。", "files": [
            {"path": "scripts/check.py", "content": "print('skill-v1-ok')"},
            {"path": "assets/template.bin", "source_path": "template.bin"}]}
        with patch("aio_agent_platform.skills.mutations.get_session_factory", return_value=factory):
            created = json.loads(await mutate_skill("create", args, str(uid), str(sid), storage,
                tool_executor=SimpleNamespace(sandbox_mgr=manager), workspace_id=str(wid), workspace_slug="default"))
            assert created["success"], created
            skill_id = UUID(created["skill_id"])
            edited = json.loads(await mutate_skill("update", {"skill_id": str(skill_id), "expected_version": 1,
                "files": [{"path": "scripts/check.py", "content": "print('skill-v2-ok')"}], "change_summary": "更新脚本"},
                str(uid), str(sid), storage))
            assert edited["success"] and edited["version"] == 2, edited
        await manager.execute(sandbox, "rm /workspace/default/template.bin")
        await manager.destroy(sandbox, sync=False)
        sandbox = await manager.get_or_create(str(uid), str(sid), str(wid), "default")
        async with factory() as db:
            skill = await SkillService.get_skill(db, skill_id, uid)
            deployed = await SkillService.deploy_files_to_sandbox(skill, storage, manager, str(uid), str(sid), str(wid), "default")
            assert len(deployed) == 2
            data = await read_workspace_file(manager, str(uid), str(sid), str(wid), "default", f"skills/{skill_id}/v2/assets/template.bin")
            assert data == bytes(range(256)) * 4000
            executed = await manager.execute(sandbox, f"python3 /workspace/default/skills/{skill_id}/v2/scripts/check.py")
            assert executed.exit_code == 0 and executed.stdout.strip() == "skill-v2-ok"
            old = await SkillService.get_version(db, skill_id, 1, uid)
            assert SkillStorage.extract_all_files(storage.download_skill_zip(old.object_key))["scripts/check.py"] == b"print('skill-v1-ok')"
        await manager.execute(sandbox, "ln -s /etc/passwd /workspace/default/escape")
        with pytest.raises(SkillError):
            await read_workspace_file(manager, str(uid), str(sid), str(wid), "default", "escape")
        # A symlinked parent also cannot redirect deployment outside the workspace.
        await manager.execute(sandbox, f"mv /workspace/default/skills/{skill_id}/v2 /workspace/default/old-version && ln -s /tmp /workspace/default/skills/{skill_id}/v2")
        async with factory() as db:
            skill = await SkillService.get_skill(db, skill_id, uid)
            with pytest.raises(SkillError):
                await SkillService.deploy_files_to_sandbox(skill, storage, manager, str(uid), str(sid), str(wid), "default")
    finally:
        await manager.destroy(sandbox, sync=False)
        if skill_id:
            storage.delete_skill_zips(uid, skill_id)
