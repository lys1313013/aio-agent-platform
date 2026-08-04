"""宠物智能互动测试 — 动作目录解析 / 绑定 / 气泡降级。

绑定智能体的 Agent 使用内存 fake 存储；气泡生成在无可用 LLM 模型时静默回退（降级路径）。
"""

import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Agent, PetPackage, UserPet
from aio_agent_platform.pets.service import PetService, build_actions
from aio_agent_platform.pets.smart import parse_bubble_output
from tests.test_pets_api import (
    OWNER_ID,
    TENANT_A,
    _login_as,
    _make_user,
    _upload,
)

AGENT_ID = uuid.UUID("dddddddd-0000-0000-0000-000000000001")

OTHER_USER_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000099")

# 本文件创建的用户，测试后清理（避免污染共享测试库，破坏依赖空 User 表的其他测试）
_CLEANUP_USER_IDS = [OWNER_ID, OTHER_USER_ID]


@pytest_asyncio.fixture(autouse=True)
def fake_storage(monkeypatch):
    from tests.test_pets_api import FakeStorage

    FakeStorage.objects = {}
    monkeypatch.setattr("aio_agent_platform.pets.service.ObjectStorage", FakeStorage)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_records(db_session: AsyncSession):
    yield
    from aio_agent_platform.db.models import (
        PetExpLog,
        PetPackage,
        User,
        UserPet,
    )

    await db_session.execute(PetExpLog.__table__.delete().where(PetExpLog.user_id.in_(_CLEANUP_USER_IDS)))
    await db_session.execute(UserPet.__table__.delete().where(UserPet.user_id.in_(_CLEANUP_USER_IDS)))
    await db_session.execute(PetPackage.__table__.delete().where(PetPackage.owner_id.in_(_CLEANUP_USER_IDS)))
    await db_session.execute(Agent.__table__.delete().where(Agent.id == AGENT_ID))
    await db_session.execute(User.__table__.delete().where(User.id.in_(_CLEANUP_USER_IDS)))
    await db_session.commit()


# ---- 单元：动作目录 / 输出解析 ----


def test_build_actions_from_mapping():
    actions = build_actions({"idle": 0, "work": 1}, 2)
    assert actions["0"] == {"name": "待机", "state": "idle"}
    # 行 1 被映射到 work 状态 → 取状态名「工作」
    assert actions["1"] == {"name": "工作", "state": "work"}


def test_parse_bubble_output():
    text, action = parse_bubble_output('{"text": "主人好", "action": "挥手"}')
    assert text == "主人好" and action == "挥手"
    text2, action2 = parse_bubble_output("不是JSON的兜底文本")
    assert text2 == "不是JSON的兜底文本" and action2 is None
    text3, action3 = parse_bubble_output('{"action": "跳跃"}')
    assert text3 == '{"action": "跳跃"}' and action3 == "跳跃"


@pytest.mark.asyncio
async def test_resolve_actions_instance_alias_wins(db_session: AsyncSession):
    svc = PetService(db_session)
    pet = UserPet(action_aliases={"3": "你好呀"})
    pkg = PetPackage(
        actions={"3": {"name": "挥手", "state": "happy"}},
        row_mapping={"happy": 3},
        row_count=4,
    )
    actions, vocab = svc.resolve_actions(pet, pkg)
    row3 = next(a for a in actions if a["row"] == 3)
    assert row3["name"] == "你好呀"
    assert "你好呀" in vocab and "挥手" not in vocab
    # 占位名不进入智能体词汇表
    _, vocab2 = svc.resolve_actions(UserPet(), pkg)
    assert "你好呀" not in vocab2


@pytest.mark.asyncio
async def test_resolve_state_mapping_override(db_session: AsyncSession):
    svc = PetService(db_session)
    pkg = PetPackage(row_mapping={"idle": 0, "work": 7}, row_count=8)
    pet = UserPet(state_mapping={"work": 3})
    merged = svc.resolve_state_mapping(pet, pkg)
    assert merged["work"] == 3
    assert merged["idle"] == 0


# ---- 接口：上传自动生成动作目录 / 包级改动作名 / 实例级改动作名 ----


@pytest.mark.asyncio
async def test_upload_generates_actions(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client)
    assert pkg["actions"]["0"]["name"] == "待机"
    assert pkg["actions"]["0"]["state"] == "idle"
    assert pkg["actions"]["1"]["name"] == "工作"  # 行 1 映射到 work 状态


@pytest.mark.asyncio
async def test_package_actions_update(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client)

    resp = await client.put(
        f"/api/pets/packages/{pkg['id']}/actions",
        json={"actions": {"0": "打盹", "1": "奔跑"}},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["actions"]["0"]["name"] == "打盹"
    assert updated["actions"]["0"]["state"] == "idle"  # 状态保留


@pytest.mark.asyncio
async def test_pet_actions_update_instance_level(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    _ = await _upload(client)
    mine = (await client.get("/api/pets/mine")).json()
    pet_id = mine[0]["id"]

    resp = await client.put(
        f"/api/pets/{pet_id}/actions",
        json={"aliases": {"0": "呼呼"}, "state_mapping": {"work": 1}},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["actions"][0]["name"] == "呼呼"
    assert updated["state_mapping"]["work"] == 1
    assert updated["state_mapping"]["idle"] == 0  # 包级 idle 保留


@pytest.mark.asyncio
async def test_pet_actions_rejects_duplicate_names(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    _ = await _upload(client)
    mine = (await client.get("/api/pets/mine")).json()
    pet_id = mine[0]["id"]

    resp = await client.put(
        f"/api/pets/{pet_id}/actions",
        json={"aliases": {"0": "同名", "1": "同名"}},
    )
    assert resp.status_code == 400


# ---- 接口：绑定 / 气泡降级 ----


async def _make_agent(db: AsyncSession, user_id=OWNER_ID, visibility="tenant") -> Agent:
    agent = Agent(
        id=AGENT_ID,
        tenant_id=TENANT_A,
        name="人设Agent",
        icon="robot",
        system_prompt="你是活泼的宠物伙伴",
        created_by=user_id,
        visibility=visibility,
        is_active=True,
    )
    db.add(agent)
    await db.flush()
    return agent


@pytest.mark.asyncio
async def test_bind_agent_requires_visible(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    _ = await _upload(client)
    mine = (await client.get("/api/pets/mine")).json()
    pet_id = mine[0]["id"]

    resp = await client.put(
        f"/api/pets/{pet_id}/agent", json={"agent_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 400  # 智能体不存在或不可见


@pytest.mark.asyncio
async def test_bind_agent_and_active_returns_agent(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    _ = await _upload(client)
    mine = (await client.get("/api/pets/mine")).json()
    pet_id = mine[0]["id"]
    await _make_agent(db_session)

    resp = await client.put(f"/api/pets/{pet_id}/agent", json={"agent_id": str(AGENT_ID)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent"]["id"] == str(AGENT_ID)
    assert resp.json()["agent"]["level"] == "instance"

    # 激活后 active 返回绑定信息
    await client.post(f"/api/pets/{pet_id}/activate")
    active = (await client.get("/api/pets/active")).json()
    assert active["agent"]["id"] == str(AGENT_ID)


@pytest.mark.asyncio
async def test_bubble_fallback_when_unbound(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    _ = await _upload(client)
    mine = (await client.get("/api/pets/mine")).json()
    pet_id = mine[0]["id"]

    resp = await client.post(f"/api/pets/{pet_id}/bubble")
    assert resp.status_code == 200
    assert resp.json()["fallback"] is True  # 未绑定 → 静态气泡


@pytest.mark.asyncio
async def test_bubble_fallback_when_no_model(client: AsyncClient, db_session: AsyncSession):
    """绑定 Agent 但无可用 LLM 模型 → 静默回退，不报错。"""
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    _ = await _upload(client)
    mine = (await client.get("/api/pets/mine")).json()
    pet_id = mine[0]["id"]
    await _make_agent(db_session)
    await client.put(f"/api/pets/{pet_id}/agent", json={"agent_id": str(AGENT_ID)})

    resp = await client.post(f"/api/pets/{pet_id}/bubble")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback"] is True  # 无模型 → 降级静态气泡


@pytest.mark.asyncio
async def test_bubble_rejects_other_user_pet(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client)

    other = _make_user(OTHER_USER_ID, TENANT_A)
    await _login_as(client, db_session, other)
    resp = await client.post(f"/api/pets/{pkg['id']}/bubble")
    assert resp.status_code == 404  # 非本人宠物
