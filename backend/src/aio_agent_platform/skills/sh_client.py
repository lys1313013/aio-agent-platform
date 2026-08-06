"""Client for discovering and fetching skills from the skills.sh ecosystem.

skills.sh (Vercel Labs "Agent Skills Directory") is a marketplace where every
entry is a folder (in a public GitHub repo) containing a SKILL.md file plus
optional scripts/, references/, assets/. skills.sh exposes two unauthenticated
HTTP endpoints we rely on:

    GET /api/search?q=&limit=   — search the catalog
    GET /api/download/{source}/{skill}  — full skill files as JSON

Using the download endpoint means we never touch the GitHub API, so we avoid
its per-IP rate limits.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile

import httpx
import structlog

from aio_agent_platform.core.config import settings
from aio_agent_platform.skills.storage import SkillStorage

logger = structlog.get_logger()

SKILLS_SH_SEARCH_URL = "https://skills.sh/api/search"
SKILLS_SH_DOWNLOAD_URL = "https://skills.sh/api/download/{source}/{skill}"
GITHUB_REPO_URL = "https://api.github.com/repos/{owner}/{repo}"

HTTP_TIMEOUT = httpx.Timeout(30.0)

MAX_TOTAL_SIZE = 10 * 1024 * 1024  # mirror the zip import limit

REPO_CACHE_TTL = 6 * 3600  # cache repo metadata for 6 hours
SHIELDS_CACHE_TTL = 3600  # shields.io fallback is rougher, re-check GitHub sooner
_repo_meta_cache: dict[str, tuple[float, dict]] = {}


def clear_repo_cache() -> None:
    """Clear the skills.sh repo-metadata cache (skills themselves are DB-live)."""
    _repo_meta_cache.clear()

SHIELDS_STARS_URL = "https://img.shields.io/github/stars/{owner}/{repo}.json"
SHIELDS_FORKS_URL = "https://img.shields.io/github/forks/{owner}/{repo}.json"
SHIELDS_LICENSE_URL = "https://img.shields.io/github/license/{owner}/{repo}.json"

_EMPTY_REPO_META = {
    "stars": None,
    "forks": None,
    "description": None,
    "language": None,
    "license": None,
}

# GitHub namespaces only allow these characters
_REF_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SkillsShError(Exception):
    """Raised when a skills.sh operation fails (network, not found, bad input)."""


# ---- Input parsing ----


def parse_skills_sh_input(value: str) -> tuple[str, str]:
    """Parse a skills.sh URL or ``owner/repo/skill`` reference into (source, skill_id).

    Examples:
        https://www.skills.sh/vercel-labs/skills/find-skills -> ("vercel-labs/skills", "find-skills")
        vercel-labs/skills/find-skills                      -> ("vercel-labs/skills", "find-skills")
    """
    text = value.strip().strip("/")
    if not text:
        raise SkillsShError("请输入 skills.sh 链接 或 owner/repo/skill")
    if "skills.sh" in text:
        m = re.search(r"skills\.sh/([^?#]+)", text)
        if not m:
            raise SkillsShError("无法解析 skills.sh 链接")
        text = m.group(1).strip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) < 3:
        raise SkillsShError("格式应为 skills.sh 链接 或 owner/repo/skill")
    owner, repo, skill = parts[0], parts[1], parts[2]
    if not _REF_RE.match(owner) or not _REF_RE.match(repo) or not _REF_RE.match(skill):
        raise SkillsShError("链接包含非法字符")
    return f"{owner}/{repo}", skill


# ---- Search ----


async def search_skills_sh(query: str, limit: int = 20) -> list[dict]:
    """Search the skills.sh catalog. Returns lightweight skill descriptors."""
    query = query.strip()
    if not query:
        raise SkillsShError("请输入搜索关键词")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            SKILLS_SH_SEARCH_URL,
            params={"q": query, "limit": min(limit, 50)},
        )
    if resp.status_code != 200:
        raise SkillsShError(f"skills.sh 搜索失败 (HTTP {resp.status_code})")
    result: list[dict] = []
    for s in resp.json().get("skills", []):
        source = s.get("source", "")
        skill_id = s.get("skillId", "")
        if not source or not skill_id:
            continue
        result.append({
            "skill_id": skill_id,
            "name": s.get("name") or skill_id,
            "source": source,
            "installs": s.get("installs", 0),
            "url": f"https://www.skills.sh/{source}/{skill_id}",
        })
    return await _enrich_repos(result)


# ---- Repo metadata enrichment ----


async def _fetch_repo_meta(owner: str, repo: str) -> dict:
    """Fetch GitHub repo metadata, falling back to shields.io badges.

    GitHub API is preferred: exact numbers, description, language. On any failure
    — rate limit, missing repo, network error — we fall back to shields.io, which
    serves rounded counts (``"28k"``) and no description/language. GitHub successes
    are cached 6h; shields fallbacks 1h so the exact data is re-attempted soon;
    total failures only 5min so a transient rate limit can't stick.
    """
    key = f"{owner}/{repo}"
    now = time.monotonic()
    cached = _repo_meta_cache.get(key)
    if cached and now < cached[0]:
        return cached[1]

    headers = {"Accept": "application/vnd.github+json"}
    if settings.server.github_token:
        headers["Authorization"] = f"Bearer {settings.server.github_token}"

    meta = dict(_EMPTY_REPO_META)
    via_github = False
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                GITHUB_REPO_URL.format(owner=owner, repo=repo), headers=headers
            )
        if resp.status_code == 200:
            d = resp.json()
            lic = d.get("license") or {}
            meta = {
                "stars": d.get("stargazers_count"),
                "forks": d.get("forks_count"),
                "description": d.get("description"),
                "language": d.get("language"),
                "license": lic.get("spdx_id") if isinstance(lic, dict) else None,
            }
            via_github = True
    except Exception:
        logger.warning("skills_sh_repo_meta_fetch_failed", repo=key)

    if not via_github:
        meta = await _fetch_shields_meta(owner, repo)

    if via_github:
        ttl = REPO_CACHE_TTL
    elif any(meta[f] is not None for f in ("stars", "forks", "license")):
        ttl = SHIELDS_CACHE_TTL
    else:
        ttl = 300
    _repo_meta_cache[key] = (now + ttl, meta)
    return meta


def _parse_shields_count(value: object) -> int | None:
    """Parse a shields.io count like ``"28k"``, ``"2.4k"``, ``"1,234"`` into an int."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if not v:
        return None
    try:
        return int(v.replace(",", ""))
    except ValueError:
        pass
    m = re.fullmatch(r"([\d.]+)\s*([km])?", v)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1000
    elif suffix == "m":
        num *= 1_000_000
    return int(num)


async def _fetch_shields_meta(owner: str, repo: str) -> dict:
    """Fallback repo metadata from shields.io badges (rounded, no description/language)."""
    meta = dict(_EMPTY_REPO_META)
    urls = {
        "stars": SHIELDS_STARS_URL.format(owner=owner, repo=repo),
        "forks": SHIELDS_FORKS_URL.format(owner=owner, repo=repo),
        "license": SHIELDS_LICENSE_URL.format(owner=owner, repo=repo),
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        responses = await asyncio.gather(
            *[client.get(u) for u in urls.values()], return_exceptions=True
        )
    for field, resp in zip(urls, responses, strict=True):
        if isinstance(resp, Exception) or resp.status_code != 200:
            continue
        try:
            value = resp.json().get("value")
        except Exception:
            continue
        if field in ("stars", "forks"):
            meta[field] = _parse_shields_count(value)
        elif value and value.strip().lower() not in {
            "not specified", "unknown", "missing", "n/a",
        }:
            meta[field] = value.strip()
    return meta


async def _enrich_repos(items: list[dict]) -> list[dict]:
    """Attach repo metadata to skill descriptors, keyed by ``source``."""
    sources = {it.get("source", "") for it in items if "/" in it.get("source", "")}
    metas = dict(zip(
        sources,
        await asyncio.gather(*[
            _fetch_repo_meta(src.split("/", 1)[0], src.split("/", 1)[1])
            for src in sources
        ], return_exceptions=True),
        strict=True,
    ))
    for it in items:
        meta = metas.get(it.get("source"))
        if not isinstance(meta, dict):
            meta = _EMPTY_REPO_META
        it["stars"] = meta.get("stars")
        it["forks"] = meta.get("forks")
        it["repo_description"] = meta.get("description")
        it["language"] = meta.get("language")
        it["license"] = meta.get("license")
    return items


# ---- Download & parse ----


def _classify_files(files: list[dict]) -> list[dict]:
    """Map parsed skill files (relative paths) to platform file entries."""
    result: list[dict] = []
    for f in files:
        path = f["path"]
        dir_name = path.split("/", 1)[0] if "/" in path else ""
        ftype = {"scripts": "script", "references": "reference", "assets": "asset"}.get(
            dir_name, "asset"
        )
        result.append({
            "filename": path.rsplit("/", 1)[-1],
            "content": f["content"],
            "type": ftype,
            "description": "",
        })
    return result


def _guess_category(description: str) -> str:
    """Guess a platform category from the SKILL.md description keywords."""
    text = description.lower()
    coding = ("code", "program", "deploy", "api", "kubernetes", "aws", "docker",
              "script", "debug", "git", "python", "javascript", "typescript",
              "database", "sql", "test", "frontend", "backend", "dev")
    research = ("research", "paper", "arxiv", "analysis", "report", "data")
    writing = ("write", "writing", "article", "blog", "copy", "documentation")
    if any(k in text for k in coding):
        return "coding"
    if any(k in text for k in writing):
        return "writing"
    if any(k in text for k in research):
        return "research"
    return "general"


async def fetch_skill(source: str, skill_id: str) -> dict:
    """Download and parse a skill from the skills.sh download API.

    Returns:
        {
            "source", "skill_id", "name", "description", "content",
            "tags", "category", "trigger_condition",
            "files": [{filename, content(bytes), type, description}],
        }
    """
    if "/" not in source:
        raise SkillsShError(f"无效的来源: {source}")
    owner, repo = source.split("/", 1)
    if not _REF_RE.match(owner) or not _REF_RE.match(repo) or not _REF_RE.match(skill_id):
        raise SkillsShError("来源或技能名包含非法字符")

    url = SKILLS_SH_DOWNLOAD_URL.format(source=source, skill=skill_id)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code == 404:
        raise SkillsShError(f"在 skills.sh 中未找到技能 {skill_id}（{source}）")
    if resp.status_code != 200:
        raise SkillsShError(f"skills.sh 下载失败 (HTTP {resp.status_code})")

    data = resp.json()
    raw_files = data.get("files", [])

    # Guard against huge skills and build a flat zip that the shared parser understands.
    total = sum(len(f.get("contents", "")) for f in raw_files)
    if total > MAX_TOTAL_SIZE:
        raise SkillsShError(f"技能 {skill_id} 文件总大小超过 10MB 限制")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in raw_files:
            path = f.get("path", "")
            if not path or path.startswith("/") or ".." in path.split("/"):
                continue
            # Only SKILL.md may sit at the root: parse_skill_zip treats any
            # root-level .md as skill content, so extra root files are pushed
            # into assets/.
            if path != "SKILL.md" and "/" not in path:
                path = f"assets/{path}"
            zf.writestr(path, (f.get("contents") or "").encode("utf-8"))

    parsed = SkillStorage.parse_skill_zip(buf.getvalue())

    content = (parsed.get("content") or "").strip()
    if not content:
        raise SkillsShError(f"技能 {skill_id} 缺少 SKILL.md 内容")

    metadata = parsed.get("metadata", {})
    raw_tags = metadata.get("tags", [])
    tags = [t for t in raw_tags if isinstance(t, str)] if isinstance(raw_tags, list) else []
    description = (metadata.get("description") or "").strip()
    trigger_condition = (metadata.get("trigger_condition") or "").strip()

    repo_meta = await _fetch_repo_meta(owner, repo)
    return {
        "source": source,
        "skill_id": skill_id,
        "name": (metadata.get("name") or skill_id).strip(),
        "description": description or None,
        "content": content,
        "tags": tags,
        "category": metadata.get("category") or _guess_category(description),
        "trigger_condition": trigger_condition or None,
        "files": _classify_files(parsed.get("files", [])),
        "stars": repo_meta.get("stars"),
        "forks": repo_meta.get("forks"),
        "repo_description": repo_meta.get("description"),
        "language": repo_meta.get("language"),
        "license": repo_meta.get("license"),
    }
