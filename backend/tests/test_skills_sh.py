"""Integration tests for skills.sh sync endpoints (network calls mocked)."""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from aio_agent_platform.auth.dependencies import get_current_user
from aio_agent_platform.db.models import Skill, User
from aio_agent_platform.interface.api import app
from aio_agent_platform.skills import sh_client

# The routes import the functions directly, so patch at the route module level.
ROUTE_FETCH = "aio_agent_platform.interface.routes.skills.fetch_skill"
ROUTE_SEARCH = "aio_agent_platform.interface.routes.skills.search_skills_sh"
ROUTE_STORAGE = "aio_agent_platform.interface.routes.skills._get_storage"


def _make_user(user_id: UUID, tenant_id: UUID) -> User:
    return User(
        id=user_id,
        username=f"sh-{user_id.hex[:8]}",
        email=f"{user_id.hex[:8]}@sh.test",
        password_hash="fake",
        is_active=True,
        tenant_id=tenant_id,
    )


async def _login_as(client, db, user: User) -> None:
    db.add(user)
    await db.flush()

    async def override():
        return user

    app.dependency_overrides[get_current_user] = override


SEARCH_PAYLOAD = [
    {
        "skill_id": "find-skills",
        "name": "find-skills",
        "source": "vercel-labs/skills",
        "installs": 2801522,
        "url": "https://www.skills.sh/vercel-labs/skills/find-skills",
        "stars": 27955,
        "forks": 2354,
        "repo_description": "The open agent skills tool",
        "language": "TypeScript",
        "license": "MIT",
    },
    {
        "skill_id": "wayfinder",
        "name": "wayfinder",
        "source": "mattpocock/skills",
        "installs": 214628,
        "url": "https://www.skills.sh/mattpocock/skills/wayfinder",
        "stars": None,
        "forks": None,
        "repo_description": None,
        "language": None,
        "license": None,
    },
]

FETCH_PAYLOAD = {
    "source": "vercel-labs/skills",
    "skill_id": "find-skills",
    "name": "find-skills",
    "description": "Helps users discover and install agent skills.",
    "content": "When invoked, search the agent skills ecosystem and recommend matching skills.",
    "tags": ["discovery"],
    "category": "coding",
    "trigger_condition": None,
    "files": [],
    "stars": 27955,
    "forks": 2354,
    "repo_description": "The open agent skills tool",
    "language": "TypeScript",
    "license": "MIT",
}


@pytest.mark.asyncio
async def test_sh_search_returns_marketplace_items(client, db_session):
    user = _make_user(uuid4(), uuid4())
    await _login_as(client, db_session, user)

    with patch(ROUTE_SEARCH, new=AsyncMock(return_value=SEARCH_PAYLOAD)):
        resp = await client.get("/api/skills/sh/search", params={"q": "find"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["skill_id"] == "find-skills"
    assert data[0]["source"] == "vercel-labs/skills"
    assert data[0]["installs"] == 2801522


@pytest.mark.asyncio
async def test_sh_search_surfaces_upstream_error(client, db_session):
    user = _make_user(uuid4(), uuid4())
    await _login_as(client, db_session, user)

    with patch(ROUTE_SEARCH, new=AsyncMock(side_effect=sh_client.SkillsShError("搜索失败"))):
        resp = await client.get("/api/skills/sh/search", params={"q": "find"})

    assert resp.status_code == 502
    assert resp.json()["detail"] == "搜索失败"


@pytest.mark.asyncio
async def test_sh_resolve_returns_preview(client, db_session):
    user = _make_user(uuid4(), uuid4())
    await _login_as(client, db_session, user)

    with patch(ROUTE_FETCH, new=AsyncMock(return_value=FETCH_PAYLOAD)), patch(
        ROUTE_SEARCH, new=AsyncMock(return_value=SEARCH_PAYLOAD)
    ):
        resp = await client.get(
            "/api/skills/sh/resolve",
            params={"url": "https://www.skills.sh/vercel-labs/skills/find-skills"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "find-skills"
    assert data["source"] == "vercel-labs/skills"
    assert data["category"] == "coding"
    assert data["content_preview"]
    assert data["installs"] == 2801522
    assert data["stars"] == 27955
    assert data["license"] == "MIT"


@pytest.mark.asyncio
async def test_sh_import_creates_skill_in_db(client, db_session):
    user = _make_user(uuid4(), uuid4())
    await _login_as(client, db_session, user)

    with patch(ROUTE_FETCH, new=AsyncMock(return_value=FETCH_PAYLOAD)), patch(
        ROUTE_STORAGE, return_value=None
    ):
        resp = await client.post(
            "/api/skills/sh/import",
            json={"entries": [{"source": "vercel-labs/skills", "skill_id": "find-skills"}]},
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert len(data["imported"]) == 1
    assert data["errors"] == []
    assert data["imported"][0]["name"] == "find-skills"

    result = await db_session.execute(select(Skill).where(Skill.user_id == user.id))
    skill = result.scalars().first()
    assert skill is not None
    assert skill.name == "find-skills"
    assert skill.category == "coding"


@pytest.mark.asyncio
async def test_sh_import_reports_per_skill_errors(client, db_session):
    user = _make_user(uuid4(), uuid4())
    await _login_as(client, db_session, user)

    async def flaky(source, skill_id):
        if skill_id == "find-skills":
            return FETCH_PAYLOAD
        raise sh_client.SkillsShError("未找到技能")

    with patch(ROUTE_FETCH, new=flaky), patch(ROUTE_STORAGE, return_value=None):
        resp = await client.post(
            "/api/skills/sh/import",
            json={
                "entries": [
                    {"source": "vercel-labs/skills", "skill_id": "find-skills"},
                    {"source": "acme/repo", "skill_id": "missing"},
                ]
            },
        )

    assert resp.status_code == 201
    data = resp.json()
    assert len(data["imported"]) == 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["skill_id"] == "missing"
    assert "未找到技能" in data["errors"][0]["error"]


@pytest.mark.asyncio
async def test_sh_import_rejects_empty_entries(client, db_session):
    user = _make_user(uuid4(), uuid4())
    await _login_as(client, db_session, user)

    resp = await client.post("/api/skills/sh/import", json={"entries": []})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_enrich_repos_attaches_meta(monkeypatch):
    async def fake_fetch(owner, repo):
        return {"stars": 7, "forks": 3, "description": "desc", "language": "Go", "license": "MIT"}

    monkeypatch.setattr(sh_client, "_fetch_repo_meta", fake_fetch)
    items = [{"source": "vercel-labs/skills", "skill_id": "x"}]
    out = await sh_client._enrich_repos(items)
    assert out[0]["stars"] == 7
    assert out[0]["repo_description"] == "desc"
    assert out[0]["license"] == "MIT"


@pytest.mark.asyncio
async def test_repo_meta_fetch_is_cached(monkeypatch):
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append(url)
        resp = httpx.Response(
            200,
            json={
                "stargazers_count": 5,
                "forks_count": 2,
                "description": "d",
                "language": "Go",
                "license": {"spdx_id": "MIT"},
            },
        )
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    sh_client._repo_meta_cache.clear()
    m1 = await sh_client._fetch_repo_meta("vercel-labs", "skills")
    m2 = await sh_client._fetch_repo_meta("vercel-labs", "skills")
    assert len(calls) == 1
    assert m1["stars"] == 5 and m2["stars"] == 5


@pytest.mark.asyncio
async def test_repo_meta_degrades_on_error(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(403, json={"message": "rate limited"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    sh_client._repo_meta_cache.clear()
    meta = await sh_client._fetch_repo_meta("vercel-labs", "skills")
    assert meta["stars"] is None
    assert meta["description"] is None
    assert meta["license"] is None


@pytest.mark.asyncio
async def test_repo_meta_falls_back_to_shields(monkeypatch):
    async def fake_get(self, url, **kwargs):
        if "api.github.com" in url:
            return httpx.Response(403, json={"message": "rate limited"})
        field = url.split("/")[4]  # https://img.shields.io/github/{field}/{owner}/{repo}.json
        values = {"stars": "28k", "forks": "2.4k", "license": "MIT"}
        return httpx.Response(200, json={"value": values[field]})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    sh_client._repo_meta_cache.clear()
    meta = await sh_client._fetch_repo_meta("vercel-labs", "skills")
    assert meta["stars"] == 28000
    assert meta["forks"] == 2400
    assert meta["license"] == "MIT"
    # shields.io exposes no description or language name
    assert meta["description"] is None
    assert meta["language"] is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("28k", 28000),
        ("2.4k", 2400),
        ("1.5m", 1500000),
        ("512", 512),
        ("1,234", 1234),
        ("", None),
        ("abc", None),
        (None, None),
        (42, None),
    ],
)
def test_parse_shields_count(value, expected):
    assert sh_client._parse_shields_count(value) == expected
