"""Shared skill validation, canonical packages and mutation errors."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

MAX_FILE_SIZE = 1024 * 1024
MAX_TOTAL_SIZE = 10 * MAX_FILE_SIZE
MAX_FILES = 50
MAX_CONTENT_SIZE = 128 * 1024
TYPE_TO_DIR = {"script": "scripts", "reference": "references", "asset": "assets"}
DIR_TO_TYPE = {v: k for k, v in TYPE_TO_DIR.items()}


class SkillError(ValueError):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def payload(self) -> dict:
        return {"success": False, "code": self.code, "message": str(self), **self.details}


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def validate_path(path: str, *, package: bool = True) -> str:
    if not isinstance(path, str) or not path or len(path) > 512:
        raise SkillError("invalid_path", "文件路径为空或过长")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts) or re.search(r'[\\\x00-\x1f\x7f]', path):
        raise SkillError("invalid_path", f"非法相对路径：{path}")
    if package and (len(parts) < 2 or parts[0] not in DIR_TO_TYPE):
        raise SkillError("invalid_path", f"附件必须位于 scripts/、references/ 或 assets/：{path}")
    return path


def normalize_files(files: list[dict] | None) -> list[dict]:
    if files is None:
        return []
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise SkillError("file_limit", f"附件数量不能超过 {MAX_FILES}")
    result, seen, total = [], set(), 0
    for f in files:
        if not isinstance(f, dict):
            raise SkillError("invalid_file", "附件必须为对象")
        kind = f.get("type", "script")
        if kind not in TYPE_TO_DIR:
            raise SkillError("invalid_file", f"未知附件类型：{kind}")
        path = validate_path(f.get("path") or f"{TYPE_TO_DIR[kind]}/{f.get('filename', '')}")
        kind = DIR_TO_TYPE[path.split("/", 1)[0]]
        if path in seen:
            raise SkillError("duplicate_path", f"附件路径重复：{path}")
        seen.add(path)
        data = f.get("content", b"")
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, bytes):
            raise SkillError("invalid_file", f"附件内容无效：{path}")
        if len(data) > MAX_FILE_SIZE:
            raise SkillError("file_limit", f"附件超过 1 MiB：{path}")
        total += len(data)
        result.append({**f, "path": path, "filename": path.split("/", 1)[1], "type": kind, "content": data})
    if total > MAX_TOTAL_SIZE:
        raise SkillError("file_limit", "附件总大小超过 10 MiB")
    return sorted(result, key=lambda f: f["path"])


def validate_content(name: str, content: str | None, description: str | None = None) -> None:
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise SkillError("invalid_name", "名称不能为空，且不能超过 256 字符")
    if not isinstance(content, str) or not content.strip():
        raise SkillError("invalid_content", "技能正文不能为空")
    if "\x00" in name + content + (description or ""):
        raise SkillError("invalid_content", "技能内容不能包含 NUL 字符")
    if len(content.encode("utf-8")) > MAX_CONTENT_SIZE:
        raise SkillError("content_limit", "技能正文超过 128 KiB")
    if description and len(description) > 2000:
        raise SkillError("invalid_description", "描述不能超过 2000 字符")


def validate_references(content: str, files: list[dict]) -> None:
    paths = {f["path"] for f in files}
    # Validate explicit Markdown links and backtick paths, not prose examples/globs.
    refs = re.findall(r'\]\((?:\./)?((?:scripts|references|assets)/[^)\s]+)\)', content)
    refs += re.findall(r'`(?:\./)?((?:scripts|references|assets)/[^`\s]+)`', content)
    for path in refs:
        if not path.endswith("/") and not any(c in path for c in "*{}<>") and path not in paths:
            raise SkillError("missing_reference", f"正文引用的附件不存在：{path}")


def digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def file_metadata(files: list[dict]) -> list[dict]:
    from aio_agent_platform.skills.storage import SkillStorage
    return [{"path": f["path"], "type": f["type"], "description": f.get("description", ""),
             "language": f.get("language") or (SkillStorage._detect_language(f["path"]) if f["type"] == "script" else ""),
             "size": len(f["content"]), "sha256": hashlib.sha256(f["content"]).hexdigest()} for f in files]
