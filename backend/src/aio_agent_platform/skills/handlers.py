"""Skill tool handlers for ToolExecutor direct dispatch."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.skills.service import SkillService
from aio_agent_platform.skills.storage import SkillStorage

logger = logging.getLogger(__name__)

# Cap execution log at 100 entries
_MAX_EXECUTION_LOG = 100


async def _set_rls_context(db, user_id: str) -> None:
    """Set PostgreSQL RLS context."""
    await db.execute(select(func.set_config("app.current_user_id", user_id, True)))


def _get_storage() -> SkillStorage | None:
    """Get SkillStorage instance, returning None if MinIO is unavailable."""
    try:
        return SkillStorage()
    except Exception:
        return None


def _append_execution_log(skill, entry: dict) -> None:
    """Append an entry to the skill's execution log, capping at _MAX_EXECUTION_LOG."""
    current = list(skill.execution_log or [])
    current.append(entry)
    skill.execution_log = current[-_MAX_EXECUTION_LOG:]


def _safe_skill_name(name: str) -> str:
    """Sanitize skill name for filesystem paths."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _format_files_section(files: list[dict]) -> str:
    """Format files metadata into a readable section for agent responses."""
    if not files:
        return ""
    grouped: dict[str, list[dict]] = {"script": [], "reference": [], "asset": []}
    for f in files:
        ftype = f.get("type", "script")
        grouped.setdefault(ftype, []).append(f)

    lines = []
    type_labels = {"script": "Scripts", "reference": "References", "asset": "Assets"}
    for ftype, label in type_labels.items():
        items = grouped.get(ftype, [])
        if items:
            lines.append(f"\n### {label}")
            for f in items:
                fname = f.get("path", "").split("/")[-1]
                desc = f.get("description", "")
                lang = f.get("language", "")
                size = f.get("size", 0)
                info = f"- **{fname}**"
                if lang:
                    info += f" ({lang})"
                if size:
                    info += f" — {size} bytes"
                if desc:
                    info += f": {desc}"
                lines.append(info)
    return "\n".join(lines)


async def handle_search_skills(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Handle search_skills tool call."""
    query = arguments.get("query", "")
    category = arguments.get("category")
    top_k = min(arguments.get("top_k", 5), 20)

    if not query:
        return "Error: query parameter is required"

    uid = UUID(user_id)
    factory = get_session_factory()

    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)
        results = await SkillService.search_skills(
            db, uid, query, category=category, top_k=top_k
        )
        await db.commit()

    if not results:
        return (
            f"No matching skills found for '{query}'. "
            "There are no skills related to this topic. "
            "Try a different search query, or use search_skills with query='*' to list all available skills."
        )

    parts = [f"Found {len(results)} matching skills:\n"]
    for i, (skill, score) in enumerate(results, 1):
        success_rate = (
            f"{skill.success_count}/{skill.use_count}"
            if skill.use_count > 0
            else "N/A"
        )
        tags_str = ", ".join(skill.tags) if skill.tags else "none"

        # File summary by type
        files = skill.files or []
        file_counts: dict[str, int] = {}
        for f in files:
            t = f.get("type", "script")
            file_counts[t] = file_counts.get(t, 0) + 1
        file_info = ""
        if file_counts:
            parts_list = []
            if file_counts.get("script"):
                parts_list.append(f"{file_counts['script']} script(s)")
            if file_counts.get("reference"):
                parts_list.append(f"{file_counts['reference']} reference(s)")
            if file_counts.get("asset"):
                parts_list.append(f"{file_counts['asset']} asset(s)")
            file_info = f" | Files: {', '.join(parts_list)}"

        parts.append(
            f"{i}. **{skill.name}** (id: {skill.id}, relevance: {score:.2f})\n"
            f"   Category: {skill.category} | Tags: {tags_str}{file_info}\n"
            f"   {skill.description or 'No description'}\n"
            f"   Success rate: {success_rate} | Version: {skill.version}"
        )
    return "\n\n".join(parts)


async def handle_view_skill(arguments: dict, user_id: str, session_id: str,
                             tool_executor=None, workspace_id=None, workspace_slug=None, **kwargs) -> str:
    """Handle view_skill tool call — view full skill content and deploy files."""
    skill_id_str = arguments.get("skill_id", "")

    if not skill_id_str:
        return "Error: skill_id parameter is required"

    try:
        skill_id = UUID(skill_id_str)
    except ValueError:
        return "Error: skill_id must be a UUID (e.g. 'e1475952-f560-4b4c-befc-3cb949cf0c8b'). Use search_skills first to find skill IDs."

    uid = UUID(user_id)
    factory = get_session_factory()
    storage = _get_storage()

    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)

        skill = await SkillService.get_skill(db, skill_id, uid)
        if not skill:
            return "Error: skill not found"

        if not skill.is_active and not arguments.get("for_edit", False):
            from aio_agent_platform.skills.mutations import SkillToolOutput
            return SkillToolOutput({"success": False, "code": "inactive", "message": "技能已停用，仅可读取以修改", "skill_id": str(skill.id)})
        if arguments.get("for_edit", False):
            from aio_agent_platform.skills.contracts import SkillError
            from aio_agent_platform.skills.mutations import SkillToolOutput
            try:
                files = SkillService.read_files(skill, storage)
                text_files = []
                for f in files:
                    try:
                        text = f["content"].decode("utf-8")
                    except UnicodeDecodeError:
                        text = None
                    text_files.append({"path": f["path"], "content": text if text is not None and len(text) <= 3000 else None,
                                       "note": "内容较大或为二进制，使用 read_skill_file 按需读取" if text is None or len(text) > 3000 else ""})
                return SkillToolOutput({"success": True, "skill_id": str(skill.id), **SkillService.snapshot(skill), "attachment_contents": text_files})
            except SkillError as exc:
                return SkillToolOutput(exc.payload())
        # Update usage stats
        skill.use_count += 1
        skill.last_used_at = datetime.now(UTC)
        _append_execution_log(skill, {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": "viewed",
            "session_id": session_id,
        })

        # Deploy ALL files to sandbox if skill has files
        deployed_files: list[str] = []
        if skill.files and storage and skill.object_key and tool_executor:
            sandbox_mgr = getattr(tool_executor, "sandbox_mgr", None)
            if sandbox_mgr:
                try:
                    deployed_files = await SkillService.deploy_files_to_sandbox(
                        skill=skill,
                        storage=storage,
                        sandbox_mgr=sandbox_mgr,
                        user_id=user_id,
                        session_id=session_id,
                        workspace_id=workspace_id or user_id,
                        workspace_slug=workspace_slug,
                    )
                    _append_execution_log(skill, {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "action": "files_deployed",
                        "session_id": session_id,
                        "files": deployed_files,
                    })
                except Exception as e:
                    logger.warning("skill_file_deploy_failed", extra={
                        "skill_id": str(skill_id), "error": str(e),
                    })

        await db.commit()

    # Build response
    parts = [
        f"## {skill.name}",
        f"**ID:** {skill.id} | **Category:** {skill.category} | **Version:** {skill.version}",
        f"**Active:** {skill.is_active} | **Verification:** {(skill.verification or {}).get('status', 'unverified')}",
        f"**Tags:** {', '.join(skill.tags) if skill.tags else 'none'}",
    ]

    if skill.description:
        parts.append(f"\n**Description:** {skill.description}")

    if skill.trigger_condition:
        parts.append(f"**Trigger:** {skill.trigger_condition}")

    # Content from DB cache
    if skill.content:
        parts.append(f"\n---\n\n{skill.content}")
    elif storage and skill.object_key:
        try:
            zip_bytes = storage.download_skill_zip(skill.object_key)
            content = SkillStorage.extract_skill_md(zip_bytes)
            parts.append(f"\n---\n\n{content}")
        except Exception:
            parts.append("\n(Content unavailable)")
    else:
        parts.append("\n(No content available)")

    # File information
    if skill.files:
        parts.append(_format_files_section(skill.files))

        base_path = f"/workspace/{workspace_slug or 'default'}/skills/{skill.id}/v{skill.version}"

        if deployed_files:
            parts.append(f"\nAll files deployed to `{base_path}/`.")
            # Group deployed files by type
            scripts_deployed = [p for p in deployed_files if "/scripts/" in p]
            refs_deployed = [p for p in deployed_files if "/references/" in p]
            assets_deployed = [p for p in deployed_files if "/assets/" in p]
            if scripts_deployed:
                parts.append(f"- **Scripts**: `{base_path}/scripts/` — use `run_shell` to execute")
            if refs_deployed:
                parts.append(f"- **References**: `{base_path}/references/` — use `read_file` to load context")
            if assets_deployed:
                parts.append(f"- **Assets**: `{base_path}/assets/` — use as output resources")
        elif not deployed_files:
            parts.append(
                "\nNote: Files could not be auto-deployed (sandbox unavailable). "
                "Use `deploy_skill_files` to retry."
            )

    # Execution history summary
    if skill.execution_log:
        parts.append(f"\n---\n**Execution History:** {len(skill.execution_log)} executions recorded")

    success_rate = (
        f"{round(skill.success_count / skill.use_count * 100)}%"
        if skill.use_count > 0
        else "N/A"
    )
    parts.append(f"**Success Rate:** {success_rate} ({skill.success_count}/{skill.use_count})")

    parts.append(
        "\nAfter using this skill, call `report_skill_result` to track whether it was successful."
    )

    return "\n".join(parts)


async def handle_create_skill(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    from aio_agent_platform.skills.mutations import mutate_skill
    return await mutate_skill("create", arguments, user_id, session_id, _get_storage(), **kwargs)


async def handle_update_skill(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    from aio_agent_platform.skills.mutations import mutate_skill
    return await mutate_skill("update", arguments, user_id, session_id, _get_storage(), **kwargs)


async def handle_deploy_skill_files(arguments: dict, user_id: str, session_id: str,
                                     tool_executor=None, workspace_id=None, workspace_slug=None, **kwargs) -> str:
    """Handle deploy_skill_files tool call — push skill files to sandbox."""
    skill_id_str = arguments.get("skill_id", "")

    if not skill_id_str:
        return "Error: skill_id parameter is required"

    try:
        skill_id = UUID(skill_id_str)
    except ValueError:
        return "Error: skill_id must be a UUID (e.g. 'e1475952-f560-4b4c-befc-3cb949cf0c8b'). Use search_skills first to find skill IDs."

    uid = UUID(user_id)
    factory = get_session_factory()
    storage = _get_storage()

    if not storage:
        return "Error: storage unavailable, cannot deploy files"

    if not tool_executor:
        return "Error: tool executor unavailable, cannot deploy files"

    sandbox_mgr = getattr(tool_executor, "sandbox_mgr", None)
    if not sandbox_mgr:
        return "Error: sandbox manager unavailable, cannot deploy files"

    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)

        skill = await SkillService.get_skill(db, skill_id, uid)
        if not skill:
            return "Error: skill not found"

        if not skill.is_active:
            return "Error: skill is inactive"
        if not skill.files:
            return f"Skill '{skill.name}' has no files to deploy."

        try:
            deployed = await SkillService.deploy_files_to_sandbox(
                skill=skill,
                storage=storage,
                sandbox_mgr=sandbox_mgr,
                user_id=user_id,
                session_id=session_id,
                workspace_id=workspace_id or user_id,
                workspace_slug=workspace_slug,
            )
        except Exception as e:
            return f"Error deploying files: {e}"

        _append_execution_log(skill, {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": "files_redeployed",
            "session_id": session_id,
            "files": deployed,
        })
        await db.commit()

    base_path = f"/workspace/{workspace_slug or 'default'}/skills/{skill.id}/v{skill.version}"
    lines = [f"Deployed {len(deployed)} file(s) for skill '{skill.name}' to `{base_path}/`:\n"]
    for path in deployed:
        lines.append(f"- `/workspace/{path}`")
    lines.append("\nUse `run_shell` to execute scripts, `read_file` to read references.")
    return "\n".join(lines)


async def handle_report_skill_result(arguments: dict, user_id: str, session_id: str, **kwargs) -> str:
    """Handle report_skill_result — agent reports whether a skill succeeded."""
    skill_id_str = arguments.get("skill_id", "")
    success = arguments.get("success", False)
    note = arguments.get("note", "")

    if not skill_id_str:
        return "Error: skill_id parameter is required"

    try:
        skill_id = UUID(skill_id_str)
    except ValueError:
        return "Error: skill_id must be a UUID (e.g. 'e1475952-f560-4b4c-befc-3cb949cf0c8b'). Use search_skills first to find skill IDs."

    uid = UUID(user_id)
    factory = get_session_factory()

    async with factory() as db:
        current_user_id.set(user_id)
        await _set_rls_context(db, user_id)

        await SkillService.lock_user(db, uid)
        skill = await SkillService.get_skill(db, skill_id, uid)
        if not skill:
            return "Error: skill not found"

        evidence_id = arguments.get("evidence_tool_call_id")
        version = arguments.get("version")
        expected = arguments.get("expected_output", "")
        # Verification needs all three inputs. Reporting usage only is fine, but
        # a partial triple would otherwise silently skip verification and still
        # count as success, so it is rejected instead of downgraded.
        supplied = (evidence_id is not None, version is not None, bool(expected))
        if any(supplied) and not all(supplied):
            from aio_agent_platform.skills.mutations import SkillToolOutput
            return SkillToolOutput({"success": False, "code": "invalid_arguments",
                "message": "验证需要同时提交 version、evidence_tool_call_id 和 expected_output；仅上报使用结果时三项都应省略"})
        if evidence_id:
            from aio_agent_platform.skills.mutations import SkillToolOutput
            executor = kwargs.get("tool_executor")
            evidence = getattr(executor, "skill_execution_evidence", {}).get((user_id, session_id, evidence_id))
            if version != skill.version:
                return SkillToolOutput({"success": False, "code": "version_conflict", "message": "验证目标版本已变化，请重新加载", "current_version": skill.version})
            marker = f"/skills/{skill.id}/v{skill.version}/scripts/"
            if not evidence or marker not in str(evidence.get("arguments", {})) or not isinstance(expected, str) or not expected or len(expected) > 2000:
                return SkillToolOutput({"success": False, "code": "invalid_evidence", "message": "需要当前会话中执行本版本脚本的真实工具记录及预期输出"})
            success = bool(success and evidence["success"] and expected in evidence["output"])
            skill.verification = {"status": "passed" if success else "failed", "version": skill.version,
                "note": note or "仅验证声明的样例输出，不代表通用业务正确性",
                "expected_output": expected, "evidence_tool_call_id": evidence_id,
                "session_id": session_id, "at": datetime.now(UTC).isoformat(),
                "output_excerpt": evidence["output"][:2000]}
            if not success:
                skill.is_active = False
        if success:
            skill.success_count += 1

        skill.last_used_at = datetime.now(UTC)
        _append_execution_log(skill, {
            "timestamp": datetime.now(UTC).isoformat(),
            "action": "result_reported",
            "success": success,
            "note": note,
            "session_id": session_id,
        })
        await db.commit()

    success_rate = (
        f"{round(skill.success_count / skill.use_count * 100)}%"
        if skill.use_count > 0
        else "N/A"
    )
    status = "success" if success else "failure"
    return (
        f"Result recorded for skill '{skill.name}': **{status}**\n"
        f"Current success rate: {success_rate} ({skill.success_count}/{skill.use_count})"
    )


async def handle_read_skill_file(arguments, user_id, session_id, **kwargs):
    from aio_agent_platform.skills.contracts import SkillError, validate_path
    from aio_agent_platform.skills.mutations import SkillToolOutput
    try:
        path = validate_path(arguments.get("path", ""))
        offset = max(0, int(arguments.get("offset", 0)))
        limit = max(1, min(8000, int(arguments.get("limit", 8000))))
        async with get_session_factory()() as db:
            await _set_rls_context(db, user_id)
            skill = await SkillService.get_skill(db, UUID(arguments["skill_id"]), UUID(user_id))
            if not skill:
                raise SkillError("not_found", "技能不存在或不可访问")
            files = SkillService.read_files(skill, _get_storage())
            file = next((f for f in files if f["path"] == path), None)
            if file is None:
                raise SkillError("missing_file", "附件不存在")
            try:
                content = file["content"].decode("utf-8")
            except UnicodeDecodeError:
                return SkillToolOutput({"success": True, "path": path, "binary": True, "size": len(file["content"]), "version": skill.version})
            return SkillToolOutput({"success": True, "path": path, "version": skill.version,
                "content": content[offset:offset + limit], "offset": offset,
                "next_offset": offset + limit if offset + limit < len(content) else None})
    except SkillError as exc:
        return SkillToolOutput(exc.payload())
    except (ValueError, KeyError):
        return SkillToolOutput({"success": False, "code": "invalid_arguments", "message": "技能 ID 或读取参数无效"})


# Registry mapping tool_name -> handler function
SKILL_HANDLERS: dict[str, Callable] = {
    "search_skills": handle_search_skills,
    "view_skill": handle_view_skill,
    "read_skill_file": handle_read_skill_file,
    "create_skill": handle_create_skill,
    "update_skill": handle_update_skill,
    "deploy_skill_files": handle_deploy_skill_files,
    "report_skill_result": handle_report_skill_result,
}


def _business_result(handler):
    """Legacy skill handlers return error text; expose failures consistently."""
    from functools import wraps
    @wraps(handler)
    async def wrapped(*args, **kwargs):
        result = await handler(*args, **kwargs)
        if isinstance(result, str) and result.startswith("Error"):
            from aio_agent_platform.skills.mutations import SkillToolOutput
            return SkillToolOutput({"success": False, "code": "skill_error", "message": result})
        return result
    return wrapped


SKILL_HANDLERS = {name: _business_result(handler) for name, handler in SKILL_HANDLERS.items()}
