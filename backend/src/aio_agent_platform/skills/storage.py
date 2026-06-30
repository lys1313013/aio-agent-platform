"""MinIO storage client for skill zip packages.

Skill package structure:
    skill-name/
    ├── SKILL.md              ← required
    ├── scripts/              ← optional: executable scripts (.sh, .py, .js, .ts)
    ├── references/           ← optional: reference docs loaded into context on demand
    └── assets/               ← optional: output resources (templates, images, fonts, etc.)
"""

from __future__ import annotations

import io
import re
import zipfile
from uuid import UUID

import structlog

from aio_agent_platform.core.config import settings
from aio_agent_platform.storage.client import ObjectStorage

logger = structlog.get_logger()


# Valid subdirectories for skill files
SKILL_FILE_DIRS = ("scripts", "references", "assets")

# Script extensions (used for chmod +x and language detection)
SCRIPT_EXTENSIONS = {".sh", ".py", ".js", ".ts", ".bash"}


class SkillStorage:
    """
    Manages skill zip packages in MinIO.

    Object key pattern: skills/{user_id}/{skill_id}/v{version}.zip

    Zip structure:
        SKILL.md        — YAML frontmatter + Markdown body
        scripts/        — Executable scripts
        references/     — Reference documents
        assets/         — Output resources (templates, images, etc.)
    """

    def __init__(self) -> None:
        self._storage = ObjectStorage(bucket=settings.storage.bucket)

    @staticmethod
    def _object_key(user_id: UUID, skill_id: UUID, version: int) -> str:
        return f"skills/{user_id}/{skill_id}/v{version}.zip"

    @staticmethod
    def _prefix(user_id: UUID, skill_id: UUID) -> str:
        return f"skills/{user_id}/{skill_id}/"

    # ---- Helpers ----

    @staticmethod
    def _detect_language(filename: str) -> str:
        """Detect language from file extension."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return {
            "sh": "bash",
            "bash": "bash",
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
        }.get(ext, "unknown")

    @staticmethod
    def _is_script(filename: str) -> bool:
        """Check if filename has a script extension."""
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return ext in SCRIPT_EXTENSIONS

    # ---- Zip creation ----

    @staticmethod
    def create_skill_zip(
        content: str,
        name: str,
        metadata: dict,
        files: list[dict] | None = None,
    ) -> bytes:
        """
        Build a zip package in-memory.

        Args:
            content: Markdown body of the skill (steps, notes, etc.)
            name: Skill name (used in SKILL.md title)
            metadata: Dict with keys: description, tags, category, trigger_condition
            files: Optional list of dicts with keys:
                filename (str) — basename only
                content (bytes or str) — file content
                type (str) — 'script' | 'reference' | 'asset'
                description (str) — optional
                language (str) — optional, auto-detected for scripts

        Returns:
            Raw zip bytes.
        """
        # Build YAML frontmatter
        tags = metadata.get("tags", [])
        tags_yaml = "\n".join(f"  - {t}" for t in tags) if tags else "[]"

        # Build files section for frontmatter (grouped by type)
        files_yaml = ""
        if files:
            file_lines = ["files:"]
            for f in files:
                fname = f["filename"]
                ftype = f.get("type", "script")
                # Determine directory
                dir_name = {"script": "scripts", "reference": "references", "asset": "assets"}.get(ftype, "scripts")
                path = f"{dir_name}/{fname}"
                desc = f.get("description", "")
                lang = f.get("language") or SkillStorage._detect_language(fname) if ftype == "script" else ""
                file_lines.append(f"  - path: {path}")
                file_lines.append(f"    type: {ftype}")
                if desc:
                    file_lines.append(f"    description: {desc}")
                if lang:
                    file_lines.append(f"    language: {lang}")
            files_yaml = "\n" + "\n".join(file_lines)

        frontmatter = (
            f"name: {name}\n"
            f"description: {metadata.get('description', '')}\n"
            f"tags:\n{tags_yaml}\n"
            f"category: {metadata.get('category', 'general')}\n"
            f"trigger_condition: {metadata.get('trigger_condition', '')}"
            f"{files_yaml}"
        )

        skill_md = f"---\n{frontmatter}\n---\n\n{content}\n"

        # Create zip in memory
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", skill_md)

            if files:
                for f in files:
                    fname = f["filename"]
                    # Sanitize: only basename, no path traversal
                    fname = fname.replace("\\", "/").split("/")[-1]
                    ftype = f.get("type", "script")
                    dir_name = {"script": "scripts", "reference": "references", "asset": "assets"}.get(ftype, "scripts")
                    file_content = f.get("content", b"")
                    if isinstance(file_content, str):
                        file_content = file_content.encode("utf-8")
                    zf.writestr(f"{dir_name}/{fname}", file_content)

        return buf.getvalue()

    # ---- Upload / Download / Delete ----

    def upload_skill_zip(
        self,
        user_id: UUID,
        skill_id: UUID,
        version: int,
        zip_bytes: bytes,
    ) -> str:
        """Upload a skill zip to MinIO. Returns the object_key."""
        object_key = self._object_key(user_id, skill_id, version)
        self._storage.put(object_key, zip_bytes, content_type="application/zip")
        logger.info("skill_zip_uploaded", object_key=object_key, size=len(zip_bytes))
        return object_key

    def download_skill_zip(self, object_key: str) -> bytes:
        """Download a skill zip from MinIO."""
        return self._storage.get(object_key)

    def delete_skill_zips(self, user_id: UUID, skill_id: UUID) -> None:
        """Delete all version zips for a skill."""
        prefix = self._prefix(user_id, skill_id)
        count = self._storage.delete_prefix(prefix)
        if count:
            logger.info("skill_zips_deleted", prefix=prefix, count=count)

    def delete_version(self, object_key: str) -> None:
        """Delete a single version zip."""
        self._storage.delete(object_key)
        logger.info("skill_version_zip_deleted", object_key=object_key)

    # ---- Zip extraction ----

    @staticmethod
    def extract_skill_md(zip_bytes: bytes) -> str:
        """Extract SKILL.md content from a zip package."""
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            if "SKILL.md" in zf.namelist():
                return zf.read("SKILL.md").decode("utf-8")
            # Fallback: return first .md file at root
            for name in zf.namelist():
                if name.endswith(".md") and "/" not in name:
                    return zf.read(name).decode("utf-8")
        return ""

    @staticmethod
    def extract_all_files(zip_bytes: bytes) -> dict[str, bytes]:
        """Extract all non-SKILL.md files from a zip package.

        Returns:
            Dict mapping relative path (e.g. 'scripts/deploy.sh') to file bytes.
        """
        result = {}
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            for name in zf.namelist():
                if name == "SKILL.md" or name.endswith("/"):
                    continue
                result[name] = zf.read(name)
        return result

    @staticmethod
    def extract_files_by_dir(zip_bytes: bytes, dir_name: str) -> dict[str, bytes]:
        """Extract files from a specific directory.

        Args:
            zip_bytes: Zip content
            dir_name: Directory name without trailing slash (e.g. 'scripts')

        Returns:
            Dict mapping relative path to file bytes.
        """
        prefix = f"{dir_name}/"
        result = {}
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            for name in zf.namelist():
                if name.startswith(prefix) and not name.endswith("/"):
                    result[name] = zf.read(name)
        return result

    @staticmethod
    def extract_script_files(zip_bytes: bytes) -> dict[str, bytes]:
        """Extract all script files from scripts/ directory."""
        return SkillStorage.extract_files_by_dir(zip_bytes, "scripts")

    @staticmethod
    def remove_file_from_zip(zip_bytes: bytes, file_path: str) -> bytes:
        """Remove a specific file from a zip and return new zip bytes.

        Args:
            zip_bytes: Original zip bytes
            file_path: Full relative path in zip (e.g. 'scripts/deploy.sh')

        Returns:
            New zip bytes without the specified file.
        """
        src_buf = io.BytesIO(zip_bytes)
        dst_buf = io.BytesIO()

        with zipfile.ZipFile(src_buf, "r") as src_zf:
            with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst_zf:
                for name in src_zf.namelist():
                    if name != file_path:
                        dst_zf.writestr(name, src_zf.read(name))

        return dst_buf.getvalue()

    @staticmethod
    def parse_skill_zip(zip_bytes: bytes) -> dict:
        """Parse a user-uploaded skill zip into structured data.

        Accepts two zip layouts:
          1. Flat: SKILL.md, scripts/foo.sh, references/bar.md at root
          2. Nested: skill-name/SKILL.md, skill-name/scripts/foo.sh (single root dir)

        Returns:
            {
                "skill_md": str,           # SKILL.md raw content (empty if missing)
                "metadata": dict,          # parsed frontmatter fields
                "files": [                 # all non-SKILL.md files
                    {"path": "scripts/foo.sh", "content": bytes}, ...
                ],
            }
        """
        import re

        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]

        # Detect single root directory (e.g. "skill-name/SKILL.md")
        root_prefix = ""
        if names:
            top_dirs = set()
            for n in names:
                parts = n.split("/")
                if len(parts) > 1:
                    top_dirs.add(parts[0])
            # If all files share one top directory, strip it
            if len(top_dirs) == 1:
                candidate = top_dirs.pop()
                if all(n.startswith(candidate + "/") for n in names):
                    root_prefix = candidate + "/"

        # Extract files, stripping root prefix
        skill_md = ""
        files: list[dict] = []

        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                rel_path = name
                if root_prefix and rel_path.startswith(root_prefix):
                    rel_path = rel_path[len(root_prefix):]

                # Normalize: remove leading ./
                if rel_path.startswith("./"):
                    rel_path = rel_path[2:]

                data = zf.read(name)

                if rel_path == "SKILL.md" or (rel_path.endswith(".md") and "/" not in rel_path):
                    skill_md = data.decode("utf-8", errors="replace")
                else:
                    files.append({"path": rel_path, "content": data})

        # Parse YAML frontmatter from SKILL.md
        metadata: dict = {}
        body = skill_md
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", skill_md, re.DOTALL)
        if fm_match:
            raw_fm = fm_match.group(1)
            body = fm_match.group(2)
            # Parse simple key: value, handling tags as a list
            tags: list[str] = []
            in_tags = False
            for line in raw_fm.split("\n"):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                # Indented list item under tags
                if in_tags and re.match(r"^\s+-\s+", line):
                    tag_val = re.sub(r"^\s+-\s+", "", line).strip()
                    if tag_val:
                        tags.append(tag_val)
                    continue
                # New key encountered — flush tags if we were collecting
                if in_tags:
                    metadata["tags"] = tags
                    in_tags = False
                    tags = []
                if ":" in line and not line.startswith(" "):
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if key == "tags":
                        in_tags = True
                    elif key not in ("scripts", "files"):
                        metadata[key] = val
            # Flush remaining tags
            if in_tags:
                metadata["tags"] = tags
        else:
            body = skill_md

        return {
            "skill_md": skill_md,
            "metadata": metadata,
            "content": body,
            "files": files,
        }

    @staticmethod
    def add_file_to_zip(zip_bytes: bytes, file_path: str, file_content: bytes) -> bytes:
        """Add or replace a file in a zip and return new zip bytes."""
        src_buf = io.BytesIO(zip_bytes)
        dst_buf = io.BytesIO()

        with zipfile.ZipFile(src_buf, "r") as src_zf:
            with zipfile.ZipFile(dst_buf, "w", zipfile.ZIP_DEFLATED) as dst_zf:
                for name in src_zf.namelist():
                    if name != file_path:
                        dst_zf.writestr(name, src_zf.read(name))
                # Add the new/replacement file
                dst_zf.writestr(file_path, file_content)

        return dst_buf.getvalue()
