"""宠物智能互动：气泡生成（单轮 LLM）、闲聊人设合成、pet_action 工具。

设计约束（[[02]]/[[04]]）：
- 气泡：单轮、无工具、无历史，直接调 LLM client（不走 AgentLoop）；结构化输出 {text, action}。
- 闲聊：复用现有 AgentLoop，注入宠物人设 + pet_action 工具。
- 全部静默降级：任何失败返回 None，由路由回退静态文案。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.core.usage import record_llm_usage
from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import LLMModel, PetPackage, Session, UserPet
from aio_agent_platform.llm import LLMMessage, create_provider
from aio_agent_platform.pets.service import PetService

logger = logging.getLogger("aio_agent_platform.pets.smart")

MAX_BUBBLE_TEXT = 60

# 气泡人设包装：追加到绑定 Agent 的 system_prompt 之后（平台内置，用户不可改）
BUBBLE_WRAPPER = """\n\n---
你现在是用户的桌面宠物「{display_name}」（{kind}）。
当前状态：{mood}。
以宠物口吻说话：简短、口语化、有性格，单次回复不超过 {text_limit} 字。
不要用列表、代码块、markdown；不要暴露你是 AI 助手或提到"系统提示词"。
可用动作（只从这些里选）：{vocab}。
回复必须是合法 JSON：{{"text": "一句话", "action": "<动作名或 null>"}}。
action 与文本情绪匹配：开心→选开心类动作，难过→选沮丧类动作，拿不准→null。"""

# 闲聊人设包装：放宽字数，注入宠物口吻 + pet_action 工具提示
CHAT_PERSONA_WRAPPER = """\n\n---
你现在是用户的桌面宠物「{display_name}」（{kind}）。
以宠物口吻说话：口语化、有性格、有陪伴感，简短一些。
不要暴露你是 AI 助手或提到"系统提示词"。
可以调用 pet_action 工具让宠物表演动作来配合表达情绪。
可用动作名：{vocab}。"""

PET_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "pet_action",
        "description": "让宠物播放一个动作动画来配合当前对话表达。name 必须是可用的动作名之一。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "动作名"},
            },
            "required": ["name"],
        },
    },
}

# 宠物闲聊工具白名单：记忆类 + 动作触发，禁用沙箱/网页抓取等高成本工具
PET_CHAT_WHITELIST = {"memory_read", "memory_write", "pet_action"}


def pet_action_tool_schema() -> dict:
    return PET_ACTION_TOOL


async def load_pet_chat_context(db: AsyncSession, session) -> tuple | None:
    """从 pet 会话加载宠物上下文 (pet, pkg, vocab)。非 pet 会话返回 None。"""
    if session is None or getattr(session, "source", "chat") != "pet" or session.pet_id is None:
        return None
    pet = await db.get(UserPet, session.pet_id)
    pkg = await db.get(PetPackage, pet.package_id) if pet else None
    if pet is None or pkg is None:
        return None
    svc = PetService(db)
    _, vocab = svc.resolve_actions(pet, pkg)
    return pet, pkg, vocab


async def resolve_bubble_provider(db: AsyncSession, agent):
    """从绑定 Agent 的模型构造 provider；无模型/无 provider 时回退默认模型。"""
    model = None
    if agent is not None and agent.model_id:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.id == agent.model_id, LLMModel.is_active, LLMModel.tenant_id == agent.tenant_id)
        )
        model = result.scalar_one_or_none()
    if not model:
        result = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_default, LLMModel.is_active, LLMModel.tenant_id == agent.tenant_id)
            .limit(1)
        )
        model = result.scalar_one_or_none()
    if not model or not model.provider:
        return None, None
    provider = create_provider(
        provider=model.provider.provider_type,
        model=model.model_name,
        base_url=model.provider.base_url,
        api_key=model.provider.api_key_encrypted,
        temperature=0.8,
        enable_retry=True,
    )
    return provider, model.model_name


def build_bubble_prompt(agent, pet: UserPet, pkg: PetPackage, mood: str, vocab: list[str]) -> str:
    wrapper = BUBBLE_WRAPPER.format(
        display_name=pkg.display_name,
        kind=pkg.kind or "角色型",
        mood=mood,
        text_limit=MAX_BUBBLE_TEXT,
        vocab="、".join(vocab) or "无",
    )
    base = (agent.system_prompt or "").strip() if agent else ""
    return f"{base}\n\n{wrapper}" if base else wrapper


def build_chat_persona(agent, pet: UserPet, pkg: PetPackage, vocab: list[str]) -> str:
    wrapper = CHAT_PERSONA_WRAPPER.format(
        display_name=pkg.display_name,
        kind=pkg.kind or "角色型",
        vocab="、".join(vocab) or "无",
    )
    base = (agent.system_prompt or "").strip() if agent else ""
    return f"{base}\n\n{wrapper}" if base else wrapper


def parse_bubble_output(raw: str) -> tuple[str, str | None]:
    """解析 LLM 结构化输出 {text, action}。失败时 text=原文、action=None。"""
    text = (raw or "").strip()
    if not text:
        return "", None
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        candidates.insert(0, m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            t = str(data.get("text", "")).strip()
            a = data.get("action")
            a = str(a).strip() if a else None
            return (t or text), (a or None)
    return text, None


async def stream_bubble(
    db: AsyncSession,
    user_id: UUID,
    agent,
    pet: UserPet,
    pkg: PetPackage,
    provider,
    model_name: str | None,
    mood: str = "happy",
):
    """流式生成智能气泡事件。yield 事件 dict，正常结束时记录配额/用量，失败时回退 error。

    与旧的 generate_bubble 区别：直接消费 provider.stream()，首 token 一到即吐字，
    前端不再等整段生成完。气泡输入始终是固定 prompt，无需历史。
    """
    svc = PetService(db)
    actions, vocab = svc.resolve_actions(pet, pkg)
    actions_map = {a["name"]: a["row"] for a in actions}
    prompt = build_bubble_prompt(agent, pet, pkg, mood, vocab)

    raw = ""
    shown = ""
    usage = None
    text_complete = False
    try:
        async for chunk in provider.stream(
            messages=[
                LLMMessage(role="system", content=prompt),
                LLMMessage(role="user", content="主人戳了你一下，回应一句话并挑个动作"),
            ],
            temperature=0.8,
            max_tokens=80,
        ):
            if chunk.type == "text_delta" and chunk.content:
                raw += chunk.content
                if not text_complete:
                    cur, complete = _extract_text_field(raw)
                    if cur is not None and len(cur) > len(shown):
                        delta = cur[len(shown) :]
                        shown = cur
                        for i in range(0, len(delta), 8):
                            yield {"type": "text_delta", "text": delta[i : i + 8]}
                    if complete:
                        text_complete = True
            elif chunk.type == "done":
                usage = chunk.usage
    except Exception as e:
        logger.warning("pet_bubble_stream_failed error=%s", e, exc_info=True)
        if shown:
            yield {"type": "bubble_done", "text": shown}
        else:
            yield {"type": "error"}
        return

    text, action = parse_bubble_output(raw)
    if not text:
        logger.warning("pet_bubble_generate reason=empty_text raw=%s", (raw or "")[:200])
        if shown:
            yield {"type": "bubble_done", "text": shown}
        else:
            yield {"type": "error"}
        return
    if action and action not in actions_map:
        action = None
    text = text[:MAX_BUBBLE_TEXT]
    # 增量提取失败但解析成功时，把最终文本补吐一遍，保证气泡可见
    if not shown:
        for i in range(0, len(text), 8):
            yield {"type": "text_delta", "text": text[i : i + 8]}
    await svc.record_bubble(user_id, pet.id)
    if model_name and usage:
        record_llm_usage(user_id, model_name, usage)
    if action is not None:
        yield {"type": "pet_action", "name": action, "row": actions_map[action]}
    yield {"type": "bubble_done", "text": text}


def _extract_text_field(buf: str) -> tuple[str | None, bool]:
    """从可能未写完的 JSON 前缀中提取 "text" 字段值。

    返回 (value, complete)。value 是当前已见的部分值；complete 表示值已闭合、后续不再变化。
    找不到 "text" 键时 value 为 None。
    """
    idx = buf.find('"text"')
    while idx != -1:
        rest = buf[idx + 6 :].lstrip()
        if not rest.startswith(":"):
            idx = buf.find('"text"', idx + 6)
            continue
        rest = rest[1:].lstrip()
        if not rest.startswith('"'):
            return None, False
        return _parse_json_string_tail(rest[1:])
    return None, False


def _parse_json_string_tail(s: str) -> tuple[str, bool]:
    """从字符串值内容开头解析 JSON 字符串；返回 (值, 是否已闭合)。未闭合时返回部分值。"""
    out: list[str] = []
    escapes = {
        "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
        '"': '"', "\\": "\\", "/": "/",
    }
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 >= n:
                return "".join(out), False  # 反斜杠悬空，等下一块
            out.append(escapes.get(s[i + 1], s[i + 1]))
            i += 2
            continue
        if c == '"':
            return "".join(out), True
        out.append(c)
        i += 1
    return "".join(out), False


# ---- pet_action 工具 ----

_async_lock = asyncio.Lock()


async def pet_action_handler(
    args,
    user_id,
    session_id,
    delegation=None,
    tool_executor=None,
    event_queue=None,
    tool_call_id=None,
    workspace_id=None,
    workspace_slug=None,
) -> str:
    """direct handler：闲聊中智能体主动触发宠物动作。按名称解析行号并推送 SSE 事件。"""
    name = (args or {}).get("name")
    if not name:
        return "需要提供动作名 name（可用动作名见工具描述）"
    try:
        factory = get_session_factory()
    except Exception:
        return "宠物动作服务不可用"
    try:
        async with factory() as db:
            session = await db.get(Session, UUID(str(session_id)))
            if session is None or session.source != "pet" or session.pet_id is None:
                return "当前会话不是宠物闲聊会话，无法触发宠物动作"
            pet = await db.get(UserPet, session.pet_id)
            pkg = await db.get(PetPackage, pet.package_id) if pet else None
            if pet is None or pkg is None:
                return "宠物数据不存在"
            svc = PetService(db)
            actions, vocab = svc.resolve_actions(pet, pkg)
            row = next((a["row"] for a in actions if a["name"] == name), None)
            if row is None:
                return f"动作「{name}」不存在，可用动作：{'、'.join(vocab) or '无'}"
            if event_queue is not None:
                await event_queue.put({"type": "pet_action", "name": name, "row": row})
            return f"已让宠物表演「{name}」"
    except Exception as e:
        logger.exception("pet_action handler failed")
        return f"触发宠物动作失败：{e}"


def ensure_pet_tools_registered(tool_executor) -> None:
    """把 pet_action direct handler 注册到共享 tool_executor（幂等）。"""
    if tool_executor is None:
        return
    if "pet_action" not in getattr(tool_executor, "direct_handlers", {}):
        tool_executor.register_direct_handler("pet_action", pet_action_handler)
