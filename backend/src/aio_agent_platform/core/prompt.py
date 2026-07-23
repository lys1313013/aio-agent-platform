"""System prompt builder — renders Jinja2 template with dynamic context."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from aio_agent_platform.core.context import ContextBudget, estimate_tokens

logger = logging.getLogger(__name__)

# Resolve prompts directory relative to project root
_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"
_env = Environment(
    loader=FileSystemLoader(str(_PROMPTS_DIR)),
    autoescape=False,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Max L1 memories to include when under budget pressure
_MAX_L1_MEMORIES = 10


def build_system_prompt(
    tools: list | None = None,
    persistent_memories: list | None = None,
    relevant_memories: list | None = None,
    relevant_skills: list | None = None,
    user_name: str | None = None,
    user_portrait: str | None = None,
    agent_prompt: str | None = None,
    child_agents: list | None = None,
    workspace_files: list | None = None,
) -> str:
    """
    Build the system prompt for the agent.

    Args:
        tools: List of Tool objects with .name and .description
        persistent_memories: List of Memory objects with .content (L1 — always loaded)
        relevant_memories: List of Memory objects with .content and .layer (L2/L3 — by relevance)
        relevant_skills: List of Skill objects with .name and .description
        user_name: User's display name
        agent_prompt: Agent's custom system prompt (overrides default template)
        child_agents: List of Agent objects with .id, .name, .description for delegation
        workspace_files: List of dicts with filename, size, mime, workspace_path for
                         files the user has uploaded to the workspace.

    Returns:
        Rendered system prompt string.
    """
    if agent_prompt:
        # Use agent's custom prompt as base, then append memories/skills context
        parts = [agent_prompt]

        if user_portrait:
            parts.append("\n## 用户画像 (User Portrait)")
            parts.append("The following is a self-description provided by the user to help you understand them better. Use this context to personalize your responses, communication style, and advice. Reference it naturally — don't explicitly mention \"your portrait\" or \"your profile\" unless the user brings it up.")
            parts.append(user_portrait)

        if persistent_memories:
            persistent_memories = _trim_memories(persistent_memories)
            parts.append("\n## User Context (from memory)")
            for memory in persistent_memories:
                parts.append(f"- {memory.content}")

        if relevant_memories:
            relevant_memories = _trim_relevant_memories(relevant_memories)
            parts.append("\n## Relevant Memories")
            for memory in relevant_memories:
                parts.append(f"- [{memory.layer}] {memory.content}")

        if relevant_skills:
            relevant_skills = _trim_skills(relevant_skills)
            parts.append("\n## Relevant Experience")
            for skill in relevant_skills:
                parts.append(f"### {skill.name}")
                parts.append(skill.description or "")
                if hasattr(skill, "files") and skill.files:
                    file_summary = []
                    for f in skill.files:
                        fname = f.get("path", "").split("/")[-1]
                        ftype = f.get("type", "")
                        file_summary.append(f"{fname} ({ftype})")
                    parts.append(f"  Files: {', '.join(file_summary)}")
                    parts.append("  Use `view_skill` to read details and auto-deploy files to sandbox.")

        if workspace_files and len(workspace_files) > 0:
            parts.append(_build_files_section(workspace_files))

        parts.append(f"\nCurrent time: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")

        # Inject child agents for delegation awareness
        if child_agents:
            parts.append(_build_child_agents_section(child_agents))

        prompt = "\n".join(parts)
        return _enforce_prompt_budget(prompt)

    # Default: use Jinja2 template
    # Pre-trim dynamic content to control prompt size
    persistent_memories = _trim_memories(persistent_memories or [])
    relevant_memories = _trim_relevant_memories(relevant_memories or [])
    relevant_skills = _trim_skills(relevant_skills or [])

    template = _env.get_template("system_prompt.j2")

    prompt = template.render(
        tools=tools or [],
        persistent_memories=persistent_memories,
        relevant_memories=relevant_memories,
        relevant_skills=relevant_skills,
        child_agents=child_agents or [],
        current_datetime=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        user_name=user_name or "User",
        user_portrait=user_portrait or "",
    )

    if workspace_files and len(workspace_files) > 0:
        prompt += "\n" + _build_files_section(workspace_files)
    return _enforce_prompt_budget(prompt)


# ---- Budget-aware trimming helpers ----


def _build_child_agents_section(child_agents: list) -> str:
    """Build the child agents section for the system prompt."""
    lines = ["\n## Available Child Agents",
             "You can delegate specialized tasks to the following child agents using the `delegate_task` tool:\n"]
    for child in child_agents:
        name = getattr(child, "name", "Unknown")
        cid = getattr(child, "id", "")
        desc = getattr(child, "description", None) or "Specialized sub-agent"
        lines.append(f"- **{name}** (ID: `{cid}`): {desc}")
    lines.append("")
    lines.append(
        "**Delegation guidelines**:\n"
        "- Use delegation when a task matches a child agent's specialization\n"
        "- Provide clear task descriptions with sufficient context for independent execution\n"
        "- Child agents share your sandbox environment and can access files you've created\n"
        "- You can delegate multiple tasks in parallel by calling `delegate_task` multiple times"
    )
    return "\n".join(lines)


def _build_files_section(files: list) -> str:
    """Build the workspace files section for the system prompt.

    Only includes lightweight metadata (name, size, path) — never file content.
    The agent should use file_info to get detailed structure before processing.
    """
    lines = ["\n## 工作区文件", "以下文件已上传到工作区，可使用大文件工具访问：\n"]
    for i, f in enumerate(files, 1):
        name = f.get("filename", "unknown")
        size = f.get("size", 0)
        path = f.get("workspace_path", "")
        mime = f.get("mime", "")
        size_str = f"{size:,} 字节"
        if size > 1024 * 1024:
            size_str = f"{size / (1024*1024):.1f} MB"
        elif size > 1024:
            size_str = f"{size / 1024:.1f} KB"
        lines.append(f"{i}. **{name}** — {size_str}, 路径: `{path}` (相对于 /workspace)")
        if mime:
            lines.append(f"   类型: {mime}")
        lines.append("")
    lines.append(
        "使用 `file_info` 查看文件详细结构, 使用 `file_read`/`file_grep`/`file_query` "
        "按需访问内容。PDF 文件用 `read_pdf` 提取正文 (大文件按页码范围读取)。"
        "**不要直接读取大文件全文。**"
    )
    return "\n".join(lines)


def _trim_memories(memories: list) -> list:
    """Limit L1 memories to a reasonable count."""
    if len(memories) > _MAX_L1_MEMORIES:
        logger.info("L1 memories trimmed: %d -> %d", len(memories), _MAX_L1_MEMORIES)
        return memories[:_MAX_L1_MEMORIES]
    return memories


def _trim_relevant_memories(memories: list) -> list:
    """Limit L2/L3 relevant memories (lower priority, trim first)."""
    max_relevant = 5
    if len(memories) > max_relevant:
        logger.info("Relevant memories trimmed: %d -> %d", len(memories), max_relevant)
        return memories[:max_relevant]
    return memories


def _trim_skills(skills: list) -> list:
    """Limit skills to top 3, only include name + description."""
    max_skills = 3
    if len(skills) > max_skills:
        logger.info("Skills trimmed: %d -> %d", len(skills), max_skills)
        return skills[:max_skills]
    return skills


def _enforce_prompt_budget(prompt: str) -> str:
    """Final safety net: truncate prompt if it exceeds system prompt budget."""
    budget = ContextBudget.from_settings()
    estimated = estimate_tokens(prompt)
    if estimated <= budget.system_budget:
        return prompt
    # Rough character limit (conservative ~2.5 chars/token)
    char_limit = int(budget.system_budget * 2.5)
    if len(prompt) > char_limit:
        logger.warning(
            "System prompt exceeds budget (%d chars, ~%d tokens, budget %d tokens), truncating",
            len(prompt),
            estimated,
            budget.system_budget,
        )
        return prompt[:char_limit] + "\n\n[... system prompt truncated due to context limits ...]"
    return prompt
