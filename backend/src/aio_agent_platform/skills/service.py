"""SkillService — CRUD, search, and versioning for the skill system."""

from __future__ import annotations

import base64
import os
from uuid import UUID

import rjieba
import structlog
from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.models import Skill, SkillVersion
from aio_agent_platform.skills.storage import SCRIPT_EXTENSIONS, SkillStorage

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
        result = []
        for f in files:
            fname = f["filename"]
            ftype = f.get("type", "script")
            dir_name = TYPE_TO_DIR.get(ftype, "scripts")
            content = f.get("content", b"")
            if isinstance(content, str):
                size = len(content.encode("utf-8"))
            else:
                size = len(content)
            lang = ""
            if ftype == "script":
                lang = f.get("language") or SkillStorage._detect_language(fname)
            result.append({
                "path": f"{dir_name}/{fname}",
                "type": ftype,
                "description": f.get("description", ""),
                "language": lang,
                "size": size,
            })
        return result

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
    async def create_skill(
        db: AsyncSession,
        user_id: UUID,
        name: str,
        description: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        category: str = "general",
        trigger_condition: str | None = None,
        storage: SkillStorage | None = None,
        files: list[dict] | None = None,
    ) -> Skill:
        """
        Create a new skill.

        Args:
            files: Optional list of {filename, content, type, description, language}
                for files to include in the skill package.
        """
        search_vec = SkillService._tokenize(name, description, content)

        files_metadata = SkillService._build_files_metadata(files) if files else []

        skill = Skill(
            user_id=user_id,
            name=name,
            description=description,
            content=content,
            tags=tags or [],
            category=category,
            trigger_condition=trigger_condition,
            version=1,
            search_vec=search_vec,
            files=files_metadata,
        )
        SkillService._sync_scripts_column(skill)
        db.add(skill)
        await db.flush()

        # Upload zip to MinIO
        if storage and content:
            zip_bytes = SkillStorage.create_skill_zip(
                content=content,
                name=name,
                metadata={
                    "description": description or "",
                    "tags": tags or [],
                    "category": category,
                    "trigger_condition": trigger_condition or "",
                },
                files=files,
            )
            object_key = storage.upload_skill_zip(user_id, skill.id, 1, zip_bytes)
            skill.object_key = object_key
            await db.flush()

        await db.refresh(skill)
        logger.info("skill_created", skill_id=str(skill.id), name=name)
        return skill

    @staticmethod
    async def update_skill(
        db: AsyncSession,
        skill_id: UUID,
        user_id: UUID,
        name: str | None = None,
        description: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        trigger_condition: str | None = None,
        is_active: bool | None = None,
        is_public: bool | None = None,
        storage: SkillStorage | None = None,
        files: list[dict] | None = None,
        files_metadata: list[dict] | None = None,
    ) -> Skill | None:
        """
        Update an existing skill.

        - Archives current version to skill_versions
        - Increments version number
        - Uploads new zip to MinIO
        - Re-tokenizes search_vec

        Args:
            files: Optional list of {filename, content, type, description}
                to replace ALL files in the zip.
            files_metadata: Optional explicit files metadata for the JSONB column.
                If not provided and files is given, metadata is derived from files.
        """
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return None

        # Archive current version
        version_record = SkillVersion(
            skill_id=skill.id,
            version=skill.version,
            content=skill.content or "",
            object_key=skill.object_key,
        )
        db.add(version_record)

        # Apply updates
        if name is not None:
            skill.name = name
        if description is not None:
            skill.description = description
        if content is not None:
            skill.content = content
        if tags is not None:
            skill.tags = tags
        if category is not None:
            skill.category = category
        if trigger_condition is not None:
            skill.trigger_condition = trigger_condition
        if is_active is not None:
            skill.is_active = is_active
        if is_public is not None:
            skill.is_public = is_public

        # Handle files metadata
        if files_metadata is not None:
            skill.files = files_metadata
        elif files is not None:
            skill.files = SkillService._build_files_metadata(files)
        SkillService._sync_scripts_column(skill)

        # Increment version
        skill.version += 1

        # Re-tokenize
        skill.search_vec = SkillService._tokenize(
            skill.name, skill.description, skill.content
        )

        # Upload new zip to MinIO
        if storage and skill.content:
            zip_bytes = SkillStorage.create_skill_zip(
                content=skill.content,
                name=skill.name,
                metadata={
                    "description": skill.description or "",
                    "tags": skill.tags or [],
                    "category": skill.category,
                    "trigger_condition": skill.trigger_condition or "",
                },
                files=files,
            )
            object_key = storage.upload_skill_zip(
                user_id, skill.id, skill.version, zip_bytes
            )
            skill.object_key = object_key

        await db.flush()
        await db.refresh(skill)
        logger.info("skill_updated", skill_id=str(skill.id), new_version=skill.version)
        return skill

    @staticmethod
    async def delete_skill(
        db: AsyncSession,
        skill_id: UUID,
        user_id: UUID,
        storage: SkillStorage | None = None,
    ) -> bool:
        """Delete a skill and all its MinIO zips. Returns True if deleted."""
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return False

        if storage:
            storage.delete_skill_zips(user_id, skill_id)

        await db.delete(skill)
        await db.flush()
        logger.info("skill_deleted", skill_id=str(skill_id))
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

        if storage and ver.object_key:
            try:
                zip_bytes = storage.download_skill_zip(ver.object_key)
                return SkillStorage.extract_skill_md(zip_bytes)
            except Exception as e:
                logger.warning(
                    "skill_version_zip_download_failed",
                    skill_id=str(skill_id),
                    version=version,
                    error=str(e),
                )

        return ver.content

    # ---- File Management ----

    @staticmethod
    async def add_file_to_skill(
        db: AsyncSession,
        skill_id: UUID,
        user_id: UUID,
        filename: str,
        file_content: bytes,
        file_type: str = "script",
        description: str = "",
        language: str | None = None,
        storage: SkillStorage | None = None,
    ) -> Skill | None:
        """Add a file to an existing skill.

        Downloads the current zip, adds the file, re-uploads, and updates metadata.

        Args:
            file_type: 'script' | 'reference' | 'asset'
        """
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return None

        dir_name = TYPE_TO_DIR.get(file_type, "scripts")
        file_path = f"{dir_name}/{filename}"
        size = len(file_content)
        lang = ""
        if file_type == "script":
            lang = language or SkillStorage._detect_language(filename)

        # Update files metadata
        current_files = list(skill.files or [])
        new_entry = {
            "path": file_path,
            "type": file_type,
            "description": description,
            "language": lang,
            "size": size,
        }
        # Replace if same path exists, otherwise append
        replaced = False
        for i, f in enumerate(current_files):
            if f.get("path") == file_path:
                current_files[i] = new_entry
                replaced = True
                break
        if not replaced:
            current_files.append(new_entry)
        skill.files = current_files
        SkillService._sync_scripts_column(skill)

        # Re-build zip with the new file
        if storage and skill.object_key:
            try:
                existing_zip = storage.download_skill_zip(skill.object_key)
            except Exception:
                existing_zip = None

            if existing_zip:
                new_zip = SkillStorage.add_file_to_zip(existing_zip, file_path, file_content)
                object_key = storage.upload_skill_zip(
                    user_id, skill.id, skill.version, new_zip
                )
                skill.object_key = object_key

        await db.flush()
        await db.refresh(skill)
        logger.info("skill_file_added", skill_id=str(skill_id), path=file_path)
        return skill

    @staticmethod
    async def remove_file_from_skill(
        db: AsyncSession,
        skill_id: UUID,
        user_id: UUID,
        file_path: str,
        storage: SkillStorage | None = None,
    ) -> Skill | None:
        """Remove a file from a skill.

        Args:
            file_path: Full relative path (e.g. 'scripts/deploy.sh' or 'deploy.sh')
        """
        skill = await SkillService.get_skill(db, skill_id, user_id)
        if not skill:
            return None

        # Normalize path: if no directory prefix, assume scripts/
        if "/" not in file_path:
            file_path = f"scripts/{file_path}"

        # Update files metadata
        current_files = list(skill.files or [])
        skill.files = [f for f in current_files if f.get("path") != file_path]
        SkillService._sync_scripts_column(skill)

        # Remove from zip
        if storage and skill.object_key:
            try:
                existing_zip = storage.download_skill_zip(skill.object_key)
                new_zip = SkillStorage.remove_file_from_zip(existing_zip, file_path)
                object_key = storage.upload_skill_zip(
                    user_id, skill.id, skill.version, new_zip
                )
                skill.object_key = object_key
            except Exception as e:
                logger.warning(
                    "skill_file_remove_zip_failed",
                    skill_id=str(skill_id),
                    path=file_path,
                    error=str(e),
                )

        await db.flush()
        await db.refresh(skill)
        logger.info("skill_file_removed", skill_id=str(skill_id), path=file_path)
        return skill

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

        # Download zip and extract all files
        zip_bytes = storage.download_skill_zip(skill.object_key)
        all_files = SkillStorage.extract_all_files(zip_bytes)
        if not all_files:
            return []

        # Get or create sandbox
        ws_slug = workspace_slug or "default"
        sandbox = await sandbox_mgr.get_or_create(user_id, session_id, workspace_id, ws_slug)

        deployed_paths = []
        # Sanitize skill name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in skill.name)

        for rel_path, data in all_files.items():
            target = f"skills/{safe_name}/{rel_path}"
            b64 = base64.b64encode(data).decode("ascii")

            # Create directory and write file
            cmd = (
                f"mkdir -p /workspace/$(dirname {target}) && "
                f"echo '{b64}' | base64 -d > /workspace/{target}"
            )
            await sandbox_mgr.execute(sandbox, cmd)

            # Make script files executable
            fname = rel_path.split("/")[-1]
            ext = os.path.splitext(fname)[1].lower()
            if rel_path.startswith("scripts/") and ext in SCRIPT_EXTENSIONS:
                await sandbox_mgr.execute(sandbox, f"chmod +x /workspace/{target}")

            deployed_paths.append(target)
            logger.info(
                "skill_file_deployed",
                skill_id=str(skill.id),
                path=target,
            )

        return deployed_paths

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
                    or_(
                        Skill.user_id == user_id,
                        Skill.is_public == True,  # noqa: E712
                    ),
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
                or_(
                    Skill.user_id == user_id,
                    Skill.is_public == True,  # noqa: E712
                ),
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
    ) -> list[Skill]:
        """Get relevant skills to inject into the system prompt."""
        results = await SkillService.search_skills(
            db, user_id, user_message, top_k=top_k
        )
        return [skill for skill, _score in results]
