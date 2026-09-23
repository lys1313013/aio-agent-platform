"""SkillService — CRUD, search, and versioning for the skill system."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
from datetime import UTC, datetime
from uuid import UUID

import rjieba
import structlog
from sqlalchemy import delete, event, func, literal, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Skill, SkillVersion
from aio_agent_platform.skills.contracts import (
    SkillError,
    digest,
    file_metadata,
    normalize_files,
    normalize_name,
    validate_content,
    validate_path,
    validate_references,
)
from aio_agent_platform.skills.storage import SkillStorage

logger = structlog.get_logger()

# Map file type to directory name
TYPE_TO_DIR = {"script": "scripts", "reference": "references", "asset": "assets"}
DIR_TO_TYPE = {v: k for k, v in TYPE_TO_DIR.items()}


class SkillService:
    """
    Stateless skill service — all methods take an explicit db session and user_id.

    Skills are reusable methodologies that the Agent learns from completed tasks.
    Each skill is a package with:
        - SKILL.md (required) — methodology content
        - scripts/ (optional) — executable scripts
        - references/ (optional) — reference docs loaded on demand
        - assets/ (optional) — output resources (templates, images, etc.)
    """

    # ---- Tokenization ----

    @staticmethod
    def _tokenize(*fields: str | None) -> str:
        """Tokenize multiple text fields with jieba, return space-separated string."""
        combined = " ".join(f for f in fields if f)
        tokens = rjieba.cut(combined)
        tokens = [
            t.strip()
            for t in tokens
            if t.strip() and not t.strip().isspace() and any(c.isalnum() for c in t.strip())
        ]
        return " ".join(tokens)

    # ---- File metadata helpers ----

    @staticmethod
    def _build_files_metadata(files: list[dict]) -> list[dict]:
        """Build files JSONB metadata from upload file dicts.

        Each input dict: {filename, content, type, description, language}
        Output dict: {path, type, description, language, size}
        """
        return file_metadata(normalize_files(files))

    @staticmethod
    def _sync_scripts_column(skill: Skill) -> None:
        """Derive the deprecated scripts column from files for backward compat."""
        skill.scripts = [
            {"path": f["path"], "description": f.get("description", ""), "language": f.get("language", "")}
            for f in (skill.files or [])
            if f.get("type") == "script"
        ]

    # ---- CRUD ----

    @staticmethod
    async def list_skills(
        db: AsyncSession,
        user_id: UUID,
        category: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Skill]:
        """List skills for a user with optional filters."""
        stmt = (
            select(Skill)
            .where(Skill.user_id == user_id)
            .order_by(Skill.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if category:
            stmt = stmt.where(Skill.category == category)
        if is_active is not None:
            stmt = stmt.where(Skill.is_active == is_active)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_skill(
        db: AsyncSession,
        skill_id: UUID,
        user_id: UUID,
    ) -> Skill | None:
        """Get a single skill by ID with ownership check."""
        result = await db.execute(
            select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def lock_user(db: AsyncSession, user_id: UUID) -> None:
        # All write entry points share this transaction lock (also protects names).
        key = int.from_bytes(hashlib.sha256(f"skills:{user_id}".encode()).digest()[:8], "big", signed=True)
        await db.execute(select(func.set_config("lock_timeout", "5s", True)))
        try:
            await db.execute(select(func.pg_advisory_xact_lock(key)))
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise SkillError("busy", "技能正在被其他请求修改，请稍后重试") from exc
            raise

    @staticmethod
    def snapshot(skill: Skill) -> dict:
        fields = ("name", "description", "content", "tags", "category", "trigger_condition",
                  "is_active", "is_public", "version", "files", "provenance", "verification")
        return {key: getattr(skill, key, None) for key in fields}

    @staticmethod
    def read_files(skill: Skill, storage: SkillStorage | None) -> list[dict]:
        if not skill.files:
            return []
        if not storage or not skill.object_key:
            raise SkillError("storage_unavailable", "附件存储不可用，原版本未修改")
        try:
            data = SkillStorage.extract_all_files(storage.download_skill_zip(skill.object_key))
        except Exception as exc:
            raise SkillError("storage_unavailable", "无法读取原技能附件，原版本未修改") from exc
        entries = []
        for meta in skill.files:
            path = validate_path(meta["path"])
            if path not in data:
                raise SkillError("missing_file", f"原技能包缺少附件：{path}")
            entries.append({**meta, "content": data[path]})
        return normalize_files(entries)

    @staticmethod
    def _track_upload(db, storage, key):
        # Unique object keys make rollback cleanup safe even after retries.
        session = db.sync_session
        session.info.setdefault("skill_uploads", []).append((storage, key))
        if session.info.get("skill_upload_hooks"):
            return
        session.info["skill_upload_hooks"] = True
        def committed(session):
            session.info.pop("skill_uploads", None)
        def rolled_back(session):
            for store, object_key in session.info.pop("skill_uploads", []):
                try:
                    store.delete_version(object_key)
                except Exception:
                    logger.exception("orphan_skill_object", object_key=object_key)
        event.listen(session, "after_commit", committed)
        event.listen(session, "after_rollback", rolled_back)

    @staticmethod
    def _upload(db, skill, files, storage):
        if not storage:
            if files:
                raise SkillError("storage_unavailable", "带附件的技能需要对象存储")
            return None
        try:
            package = SkillStorage.create_skill_zip(skill.content, skill.name, SkillService.snapshot(skill), files)
            key = storage.upload_skill_zip(skill.user_id, skill.id, skill.version, package)
        except Exception as exc:
            if files:
                raise SkillError("storage_unavailable", "技能附件保存失败") from exc
            logger.warning("skill_text_only_storage_fallback", skill_id=str(skill.id))
            return None
        SkillService._track_upload(db, storage, key)
        return key

    @staticmethod
    async def _same_name(db, user_id, name, exclude=None):
        # Normalize in Python so Unicode names and legacy rows have the same semantics.
        rows = (await db.execute(select(Skill).where(Skill.user_id == user_id)
                                .execution_options(populate_existing=True))).scalars().all()
        return next((s for s in rows if s.id != exclude and normalize_name(s.name) == normalize_name(name)), None)

    @staticmethod
    async def create_skill(
        db: AsyncSession, user_id: UUID, name: str, description: str | None = None,
        content: str | None = None, tags: list[str] | None = None, category: str = "general",
        trigger_condition: str | None = None, storage: SkillStorage | None = None,
        files: list[dict] | None = None, source: dict | None = None,
    ) -> Skill:
        validate_content(name, content, description)
        files = normalize_files(files)
        validate_references(content, files)
        metadata = file_metadata(files)
        await SkillService.lock_user(db, user_id)
        old = await SkillService._same_name(db, user_id, name)
        proposed = {"name": name.strip(), "description": description or "", "content": content,
                    "tags": tags or [], "category": category, "trigger_condition": trigger_condition or "",
                    "files": metadata}
        if old:
            current = {k: getattr(old, k) for k in proposed}
            current["name"] = name.strip()  # normalized name already matched
            current["description"] = current["description"] or ""
            current["trigger_condition"] = current["trigger_condition"] or ""
            current["files"] = file_metadata(SkillService.read_files(old, storage))
            if digest(current) == digest(proposed):
                old.mutation_status = "existing"
                return old
            raise SkillError("name_conflict", "同名技能已存在，请先读取后修改，或使用其他名称另存", skill_id=str(old.id))
        source = {**(source or {"type": "manual"}), "at": datetime.now(UTC).isoformat()}
        skill = Skill(user_id=user_id, **proposed, version=1,
                      provenance={"created": source, "modified": source},
                      verification={"status": "unverified"}, is_active=True, is_public=False,
                      search_vec=SkillService._tokenize(name, description, content))
        SkillService._sync_scripts_column(skill)
        db.add(skill)
        await db.flush()
        skill.object_key = SkillService._upload(db, skill, files, storage)
        await db.flush()
        await db.refresh(skill)
        skill.mutation_status = "created"
        return skill

    @staticmethod
    async def update_skill(
        db: AsyncSession, skill_id: UUID, user_id: UUID,
        name: str | None = None, description: str | None = None, content: str | None = None,
        tags: list[str] | None = None, category: str | None = None, trigger_condition: str | None = None,
        is_active: bool | None = None, is_public: bool | None = None,
        storage: SkillStorage | None = None, files: list[dict] | None = None,
        files_metadata: list[dict] | None = None, expected_version: int | None = None,
        file_changes: list[dict] | None = None, remove_files: list[str] | None = None,
        source: dict | None = None, change_summary: str = "",
    ) -> Skill | None:
        await SkillService.lock_user(db, user_id)
        skill = (await db.execute(select(Skill).where(Skill.id == skill_id, Skill.user_id == user_id)
                                 .execution_options(populate_existing=True))).scalar_one_or_none()
        if not skill:
            return None
        if expected_version is not None and skill.version != expected_version:
            raise SkillError("version_conflict", "技能已被修改，请重新读取后再提交", current_version=skill.version)
        before = SkillService.snapshot(skill)
        values = {"name": name, "description": description, "content": content, "tags": tags,
                  "category": category, "trigger_condition": trigger_condition,
                  "is_active": is_active, "is_public": is_public}
        values = {k: v for k, v in values.items() if v is not None}
        if "name" in values:
            values["name"] = values["name"].strip()
            if await SkillService._same_name(db, user_id, values["name"], skill.id):
                raise SkillError("name_conflict", "该名称已被其他技能使用")
        after = {**before, **values}
        validate_content(after["name"], after["content"], after["description"])
        original_files = SkillService.read_files(skill, storage)
        final_files = normalize_files(files) if files is not None else original_files
        merged = {f["path"]: f for f in final_files}
        for path in remove_files or []:
            validate_path(path)
            if path not in merged:
                raise SkillError("missing_file", f"待移除附件不存在：{path}")
            del merged[path]
        for f in normalize_files(file_changes):
            if f["path"] in (remove_files or []):
                raise SkillError("invalid_file", f"同一路径不能同时移除和替换：{f['path']}")
            merged[f["path"]] = f
        final_files = normalize_files(list(merged.values()))
        after["files"] = file_metadata(final_files)
        validate_references(after["content"], final_files)
        compare_before = {**before, "files": file_metadata(original_files)}
        if digest(compare_before) == digest(after):
            skill.mutation_status = "unchanged"
            skill.previous_version = skill.version
            return skill
        changed = [k for k in values if before[k] != after[k]]
        files_changed = digest(compare_before["files"]) != digest(after["files"])
        for k, v in values.items():
            setattr(skill, k, v)
        skill.files = after["files"]
        skill.version += 1
        if files_changed or any(k in changed for k in ("content", "trigger_condition", "description")):
            skill.verification = {"status": "unverified"}
        src = {**(source or {"type": "manual"}), "at": datetime.now(UTC).isoformat(),
               "summary": change_summary or "更新技能", "fields": changed,
               "files_added": sorted(set(merged) - {f["path"] for f in original_files}),
               "files_removed": sorted({f["path"] for f in original_files} - set(merged)),
               "files_modified": sorted(f["path"] for f in original_files if f["path"] in merged and digest({**f, "content": f["content"].hex()}) != digest({**merged[f["path"]], "content": merged[f["path"]]["content"].hex()}))}
        skill.provenance = {**(skill.provenance or {}), "modified": src}
        skill.search_vec = SkillService._tokenize(skill.name, skill.description, skill.content)
        SkillService._sync_scripts_column(skill)
        # Old object is never overwritten; archive complete metadata before promotion.
        old_key = skill.object_key
        skill.object_key = SkillService._upload(db, skill, final_files, storage)
        db.add(SkillVersion(skill_id=skill.id, version=before["version"],
                            content=before["content"] or "", object_key=old_key, snapshot=before))
        await db.flush()
        await db.refresh(skill)
        skill.previous_version = before["version"]
        skill.mutation_status = "updated"
        return skill

    @staticmethod
    async def delete_skill(db, skill_id, user_id, storage=None) -> bool:
        await SkillService.lock_user(db, user_id)
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return False
        # Keep immutable packages until deletion is committed; storage cleanup is best effort.
        await db.execute(delete(SkillVersion).where(SkillVersion.skill_id == skill_id))
        from aio_agent_platform.db.models import AgentSkill
        await db.execute(delete(AgentSkill).where(AgentSkill.skill_id == skill_id))
        await db.delete(skill)
        await db.flush()
        if storage:
            marker = f"delete_skill:{skill_id}"
            db.sync_session.info[marker] = True
            def cleanup(session):
                if not session.info.pop(marker, False):
                    return
                try:
                    storage.delete_skill_zips(user_id, skill_id)
                except Exception:
                    logger.exception("deleted_skill_objects_cleanup_failed", skill_id=str(skill_id))
            def cancel_cleanup(session):
                session.info.pop(marker, None)
            event.listen(db.sync_session, "after_commit", cleanup, once=True)
            event.listen(db.sync_session, "after_rollback", cancel_cleanup, once=True)
        return True

    # ---- Versioning ----

    @staticmethod
    async def list_versions(
        db: AsyncSession,
        skill_id: UUID,
        user_id: UUID,
    ) -> list[SkillVersion]:
        """List all versions for a skill, newest first."""
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return []

        result = await db.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(SkillVersion.version.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_version(
        db: AsyncSession,
        skill_id: UUID,
        version: int,
        user_id: UUID,
        storage: SkillStorage | None = None,
    ) -> SkillVersion | None:
        """Get a specific version record."""
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return None

        result = await db.execute(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill_id,
                SkillVersion.version == version,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_version_content(
        db: AsyncSession,
        skill_id: UUID,
        version: int,
        user_id: UUID,
        storage: SkillStorage | None = None,
    ) -> str | None:
        """Get the SKILL.md content for a specific version."""
        ver = await SkillService.get_version(db, skill_id, version, user_id)
        if not ver:
            return None

        # DB snapshot contains the Markdown body. ZIP SKILL.md also has
        # frontmatter, which must not be rendered as part of historical prose.
        if ver.content:
            return ver.content
        if storage and ver.object_key:
            return SkillStorage.parse_skill_zip(storage.download_skill_zip(ver.object_key))["content"]
        return ver.content

    # ---- File Management ----

    @staticmethod
    async def add_file_to_skill(db, skill_id, user_id, filename, file_content,
                                file_type="script", description="", language=None, storage=None,
                                expected_version=None):
        return await SkillService.update_skill(db, skill_id, user_id, storage=storage,
            expected_version=expected_version, file_changes=[{"filename": filename,
                "content": file_content, "type": file_type, "description": description,
                "language": language}], change_summary=f"更新附件 {filename}")

    @staticmethod
    async def remove_file_from_skill(db, skill_id, user_id, file_path, storage=None,
                                     expected_version=None):
        path = file_path if "/" in file_path else f"scripts/{file_path}"
        return await SkillService.update_skill(db, skill_id, user_id, storage=storage,
            expected_version=expected_version, remove_files=[path], change_summary=f"移除附件 {path}")

    @staticmethod
    async def deploy_files_to_sandbox(
        skill: Skill,
        storage: SkillStorage,
        sandbox_mgr,
        user_id: str,
        session_id: str,
        workspace_id: str,
        workspace_slug: str | None = None,
    ) -> list[str]:
        """Extract all files from the skill's zip and push them to the sandbox.

        Deploys preserving directory structure:
            /workspace/{workspace_slug}/skills/{skill_name}/scripts/
            /workspace/{workspace_slug}/skills/{skill_name}/references/
            /workspace/{workspace_slug}/skills/{skill_name}/assets/

        Returns:
            List of deployed file paths (relative to /workspace/{workspace_slug}).
        """
        if not skill.files or not skill.object_key:
            return []

        files = SkillService.read_files(skill, storage)
        ws_slug = workspace_slug or "default"
        validate_path(ws_slug, package=False)
        sandbox = await sandbox_mgr.get_or_create(user_id, session_id, workspace_id, ws_slug)
        base_path = f"{ws_slug}/skills/{skill.id}/v{skill.version}"
        from aio_agent_platform.skills.workspace import WRITE_PACKAGE_SCRIPT
        # Keep each argv well below OS ARG_MAX, including binary templates.
        for f in files:
            data = f["content"]
            for offset in range(0, max(1, len(data)), 32 * 1024):
                payload = {"base": base_path, "files": [{"path": f["path"], "offset": offset,
                    "data": base64.b64encode(data[offset:offset + 32 * 1024]).decode()}]}
                cmd = "python3 -c " + shlex.quote(WRITE_PACKAGE_SCRIPT) + " " + shlex.quote(json.dumps(payload))
                result = await sandbox_mgr.execute(sandbox, cmd)
                if result.exit_code != 0:
                    raise SkillError("deployment_failed", "技能附件部署失败")
        return [f"{base_path}/{f['path']}" for f in files]

    # ---- Search ----

    @staticmethod
    async def search_skills(
        db: AsyncSession,
        user_id: UUID,
        query: str,
        category: str | None = None,
        top_k: int = 5,
        threshold: float = 0.1,
    ) -> list[tuple[Skill, float]]:
        """
        Search skills using pg_trgm similarity on jieba-tokenized search_vec.
        """
        stripped_query = query.strip()
        is_wildcard = not stripped_query or stripped_query in ("*", "%")

        if is_wildcard:
            stmt = (
                select(Skill, literal(1.0).label("score"))
                .where(
                    Skill.user_id == user_id,
                    Skill.is_active == True,  # noqa: E712
                )
                .order_by(Skill.updated_at.desc())
                .limit(top_k)
            )
            if category:
                stmt = stmt.where(Skill.category == category)

            result = await db.execute(stmt)
            return [(row.Skill, 1.0) for row in result]

        tokenized_query = SkillService._tokenize(query)
        if not tokenized_query:
            return []

        # Use COALESCE so skills with NULL search_vec fall back to raw text
        search_col = func.coalesce(
            Skill.search_vec,
            func.concat_ws(" ", Skill.name, Skill.description, Skill.content),
        )
        sim_score = func.similarity(search_col, tokenized_query).label("score")

        stmt = (
            select(Skill, sim_score)
            .where(
                Skill.user_id == user_id,
                Skill.is_active == True,  # noqa: E712
                or_(
                    func.similarity(search_col, tokenized_query) > threshold,
                    # Fallback: direct ILIKE match on name for better coverage
                    Skill.name.ilike(f"%{query}%"),
                ),
            )
            .order_by(sim_score.desc())
            .limit(top_k)
        )
        if category:
            stmt = stmt.where(Skill.category == category)

        result = await db.execute(stmt)
        return [(row.Skill, float(row.score)) for row in result]

    # ---- Chat Integration ----

    @staticmethod
    async def get_skills_for_prompt(
        db: AsyncSession,
        user_id: UUID,
        user_message: str,
        top_k: int = 3,
        bound_skills: list | None = None,
    ) -> list[Skill]:
        """Get relevant skills to inject into the system prompt.

        Relevant skills match the current message, so they keep the first slots.
        Bound skills are the ones the agent is configured with, so they are
        never crowded out entirely: when both sources have candidates the last
        slot is reserved for the bound list and the previous one stays with a
        relevant skill, which keeps both an existing binding and a freshly
        created skill reachable in the same prompt.
        """
        results = await SkillService.search_skills(
            db, user_id, user_message, top_k=top_k
        )
        relevant = [skill for skill, _score in results]
        bound_ids = list(dict.fromkeys(s.id for s in bound_skills or []))
        bound: list[Skill] = []
        if bound_ids:
            rows = (await db.execute(select(Skill).where(Skill.id.in_(bound_ids),
                    Skill.user_id == user_id, Skill.is_active == True))).scalars().all()  # noqa: E712
            by_id = {s.id: s for s in rows}
            bound = [by_id[i] for i in bound_ids if i in by_id]
        bound_set = {s.id for s in bound}
        fresh = [s for s in relevant if s.id not in bound_set]
        if not bound:
            return fresh[:top_k]
        if not fresh or top_k < 2:
            return (fresh + bound)[:top_k]
        fresh_slots = min(len(fresh), top_k - 1)
        return [*fresh[:fresh_slots], *bound[: top_k - fresh_slots]]
