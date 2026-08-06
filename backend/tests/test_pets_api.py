"""宠物系统 API 测试 — 可见性隔离 / 领养 / 激活。

存储层用内存 fake 替代 MinIO；DB 不可达时整体 skip（conftest 行为）。
"""

import io
import json
import uuid
import zipfile
from typing import ClassVar

import pytest
import pytest_asyncio
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.auth.dependencies import get_current_user
from aio_agent_platform.db.models import User
from aio_agent_platform.interface.api import app

TENANT_A = uuid.UUID("10000000-0000-0000-0000-00000000000a")
TENANT_B = uuid.UUID("10000000-0000-0000-0000-00000000000b")

OWNER_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
SAME_TENANT_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
OTHER_TENANT_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


class FakeStorage:
    """内存 ObjectStorage 替代。"""

    objects: ClassVar[dict[str, bytes]] = {}

    def __init__(self, *args, **kwargs):
        pass

    def put(self, key, data, content_type="application/octet-stream"):
        FakeStorage.objects[key] = data
        return key

    def get(self, key):
        return FakeStorage.objects[key]

    def delete_prefix(self, prefix):
        keys = [k for k in FakeStorage.objects if k.startswith(prefix)]
        for k in keys:
            del FakeStorage.objects[k]
        return len(keys)


@pytest_asyncio.fixture(autouse=True)
def fake_storage(monkeypatch):
    FakeStorage.objects = {}
    monkeypatch.setattr(
        "aio_agent_platform.pets.service.ObjectStorage", FakeStorage
    )


def _make_user(user_id: uuid.UUID, tenant_id: uuid.UUID, role: str = "user") -> User:
    return User(
        id=user_id,
        username=f"u-{user_id.hex[:8]}",
        email=f"{user_id.hex[:8]}@test.com",
        password_hash="fake",
        role=role,
        is_active=True,
        tenant_id=tenant_id,
    )


async def _login_as(client: AsyncClient, db: AsyncSession, user: User):
    db.add(user)
    await db.flush()

    async def override():
        return user

    app.dependency_overrides[get_current_user] = override


def _make_pet_zip(pet_id: str = "testpet", rows: int = 2, cols: int = 8) -> bytes:
    im = Image.new("RGBA", (192 * cols, 208 * rows), (0, 0, 0, 0))
    for x in range(80, 110):
        for y in range(90, 120):
            im.putpixel((x, y), (255, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format="WEBP", lossless=True)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr(
            f"{pet_id}/pet.json",
            json.dumps({"id": pet_id, "displayName": pet_id, "spritesheetPath": "spritesheet.webp"}),
        )
        zf.writestr(f"{pet_id}/spritesheet.webp", buf.getvalue())
    return zip_buf.getvalue()


async def _upload(client: AsyncClient, pet_id: str = "testpet", visibility: str = "private") -> dict:
    resp = await client.post(
        "/api/pets/packages",
        files={"file": (f"{pet_id}.zip", _make_pet_zip(pet_id), "application/zip")},
        data={"row_mapping": json.dumps({"idle": 0, "work": 1}), "visibility": visibility},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_upload_defaults_private_and_auto_adopts(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)

    pkg = await _upload(client)
    assert pkg["visibility"] == "private"
    assert pkg["row_mapping"]["idle"] == 0
    assert pkg["frame_width"] == 192 and pkg["row_count"] == 2

    mine = (await client.get("/api/pets/mine")).json()
    assert len(mine) == 1 and mine[0]["package_id"] == pkg["id"]
    assert mine[0]["is_active"] is False  # 上传不自动激活


@pytest.mark.asyncio
async def test_private_package_invisible_to_others(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client)

    # 同租户其他用户：不可见、不可领养、资源 404
    other = _make_user(SAME_TENANT_ID, TENANT_A)
    await _login_as(client, db_session, other)
    market = (await client.get("/api/pets/market")).json()
    assert all(p["id"] != pkg["id"] for p in market)
    assert (await client.post(f"/api/pets/{pkg['id']}/adopt")).status_code == 404
    assert (await client.get(f"/api/pets/packages/{pkg['id']}/spritesheet")).status_code == 404


@pytest.mark.asyncio
async def test_tenant_visibility_scoped_to_tenant(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client, visibility="tenant")

    # 同租户可见可领养
    same = _make_user(SAME_TENANT_ID, TENANT_A)
    await _login_as(client, db_session, same)
    market = (await client.get("/api/pets/market")).json()
    assert any(p["id"] == pkg["id"] for p in market)
    adopt_resp = await client.post(f"/api/pets/{pkg['id']}/adopt")
    assert adopt_resp.status_code == 200
    assert (await client.get(f"/api/pets/packages/{pkg['id']}/spritesheet")).status_code == 200

    # 其他租户：404
    outsider = _make_user(OTHER_TENANT_ID, TENANT_B)
    await _login_as(client, db_session, outsider)
    market = (await client.get("/api/pets/market")).json()
    assert all(p["id"] != pkg["id"] for p in market)
    assert (await client.post(f"/api/pets/{pkg['id']}/adopt")).status_code == 404


@pytest.mark.asyncio
async def test_revoke_visibility_keeps_existing_adoptions(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client, visibility="public")

    adopter = _make_user(OTHER_TENANT_ID, TENANT_B)
    await _login_as(client, db_session, adopter)
    pet = (await client.post(f"/api/pets/{pkg['id']}/adopt")).json()

    # 创建人收回为私有
    await _login_as(client, db_session, owner)
    resp = await client.put(
        f"/api/pets/packages/{pkg['id']}/visibility", json={"visibility": "private"}
    )
    assert resp.status_code == 200

    # 已领养用户：保留宠物、可激活、资源可访问
    await _login_as(client, db_session, adopter)
    mine = (await client.get("/api/pets/mine")).json()
    assert len(mine) == 1
    assert (await client.post(f"/api/pets/{pet['id']}/activate")).status_code == 200
    active = (await client.get("/api/pets/active")).json()
    assert active["id"] == pet["id"]

    # 新用户（同租户）不可见
    newcomer = _make_user(uuid.uuid4(), TENANT_B)
    await _login_as(client, db_session, newcomer)
    market = (await client.get("/api/pets/market")).json()
    assert all(p["id"] != pkg["id"] for p in market)


@pytest.mark.asyncio
async def test_activate_is_exclusive(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg1 = await _upload(client, "pet-one")
    pkg2 = await _upload(client, "pet-two")

    mine = (await client.get("/api/pets/mine")).json()
    pet1 = next(p for p in mine if p["package_id"] == pkg1["id"])
    pet2 = next(p for p in mine if p["package_id"] == pkg2["id"])

    await client.post(f"/api/pets/{pet1['id']}/activate")
    await client.post(f"/api/pets/{pet2['id']}/activate")
    active = (await client.get("/api/pets/active")).json()
    assert active["id"] == pet2["id"]

    await client.post("/api/pets/deactivate")
    assert (await client.get("/api/pets/active")).json() is None


@pytest.mark.asyncio
async def test_remove_pet_deletes_adoption(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client, "pet-to-remove")
    mine = (await client.get("/api/pets/mine")).json()
    pet = next(p for p in mine if p["package_id"] == pkg["id"])

    await client.post(f"/api/pets/{pet['id']}/activate")
    resp = await client.delete(f"/api/pets/{pet['id']}")
    assert resp.status_code == 204
    assert (await client.get("/api/pets/active")).json() is None
    mine_after = (await client.get("/api/pets/mine")).json()
    assert all(p["id"] != pet["id"] for p in mine_after)
    # 删除后仍可重新领养
    readopt = await client.post(f"/api/pets/{pkg['id']}/adopt")
    assert readopt.status_code == 200


@pytest.mark.asyncio
async def test_remove_pet_requires_owner(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    pkg = await _upload(client, "pet-not-yours")
    mine = (await client.get("/api/pets/mine")).json()
    pet = next(p for p in mine if p["package_id"] == pkg["id"])

    other = _make_user(uuid.uuid4(), TENANT_A)
    await _login_as(client, db_session, other)
    assert (await client.delete(f"/api/pets/{pet['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_official_upload_requires_admin(client: AsyncClient, db_session: AsyncSession):
    user = _make_user(OWNER_ID, TENANT_A, role="user")
    await _login_as(client, db_session, user)
    resp = await client.post(
        "/api/pets/packages",
        files={"file": ("p.zip", _make_pet_zip(), "application/zip")},
        data={"row_mapping": json.dumps({"idle": 0}), "visibility": "official"},
    )
    assert resp.status_code == 400

    admin = _make_user(uuid.uuid4(), TENANT_A, role="admin")
    await _login_as(client, db_session, admin)
    pkg = await _upload(client, "official-pet", visibility="official")
    assert pkg["visibility"] == "official"


@pytest.mark.asyncio
async def test_standard_9row_package_gets_default_codex_mapping(client: AsyncClient, db_session: AsyncSession):
    """标准 9 行 Codex 包上传后自动套用默认 行→状态 映射（无需手动配置）。"""
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)

    resp = await client.post(
        "/api/pets/packages",
        files={"file": ("std.zip", _make_pet_zip("std", rows=9), "application/zip")},
        data={"row_mapping": json.dumps({}), "visibility": "private"},
    )
    assert resp.status_code == 201, resp.text
    mapping = resp.json()["row_mapping"]
    assert mapping["idle"] == 0
    assert mapping["think"] == 8   # review
    assert mapping["work"] == 7    # running（活跃工作循环）
    assert mapping["wait"] == 6    # waiting
    assert mapping["celebrate"] == 4  # jumping
    assert mapping["sad"] == 5     # failed
    assert mapping["happy"] == 3   # waving
    # 显式传入的映射覆盖默认值
    resp2 = await client.post(
        "/api/pets/packages",
        files={"file": ("std2.zip", _make_pet_zip("std2", rows=9), "application/zip")},
        data={"row_mapping": json.dumps({"work": 1}), "visibility": "private"},
    )
    assert resp2.status_code == 201, resp2.text
    assert resp2.json()["row_mapping"]["work"] == 1
    assert resp2.json()["row_mapping"]["idle"] == 0  # 默认仍保留


@pytest.mark.asyncio
async def test_invalid_row_mapping_state_rejected(client: AsyncClient, db_session: AsyncSession):
    """非法状态名被拒绝。"""
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    bad = await client.post(
        "/api/pets/packages",
        files={"file": ("bad.zip", _make_pet_zip("bad", rows=9), "application/zip")},
        data={"row_mapping": json.dumps({"idle": 0, "bogus_state": 1})},
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_interact_no_exp_reward(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    await _upload(client, "interact-pet")
    pet_id = (await client.get("/api/pets/mine")).json()[0]["id"]

    # interact 仅校验归属并刷新实例数据，无经验奖励、无每日上限
    resp = await client.post(f"/api/pets/{pet_id}/interact")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == pet_id
    assert "exp" not in data
    assert "level" not in data

    # 重复互动不改变任何字段（无经验累积）
    resp2 = await client.post(f"/api/pets/{pet_id}/interact")
    assert resp2.json()["id"] == pet_id


@pytest.mark.asyncio
async def test_interact_requires_owner(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    await _upload(client, "interact-owner")
    pet_id = (await client.get("/api/pets/mine")).json()[0]["id"]

    other = _make_user(SAME_TENANT_ID, TENANT_A)
    await _login_as(client, db_session, other)
    assert (await client.post(f"/api/pets/{pet_id}/interact")).status_code == 404


@pytest.mark.asyncio
async def test_download_roundtrip_preserves_zip(client: AsyncClient, db_session: AsyncSession):
    owner = _make_user(OWNER_ID, TENANT_A)
    await _login_as(client, db_session, owner)
    raw = _make_pet_zip()
    resp = await client.post(
        "/api/pets/packages",
        files={"file": ("testpet.zip", raw, "application/zip")},
        data={"row_mapping": json.dumps({"idle": 0})},
    )
    pkg = resp.json()
    downloaded = await client.get(f"/api/pets/packages/{pkg['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == raw  # 逐字节一致，可放回 ~/.codex/pets
