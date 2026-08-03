"""Auto session-title generation — model/prompt in system_config, enable switch per agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aio_agent_platform.db.models import LLMModel, SystemConfig
from aio_agent_platform.llm import LLMMessage, create_provider

logger = structlog.get_logger()

KEY_MODEL_ID = "auto_title_model_id"
KEY_PROMPT = "auto_title_prompt"

DEFAULT_PROMPT = """请根据用户的第一条消息，为这次对话生成一个简短的标题。
要求：
- 不超过 20 个字符
- 直接输出标题本身，不要加引号、书名号或任何前缀后缀
- 使用与用户消息一致的语言

用户消息：
{message}"""

MAX_TITLE_LENGTH = 100


@dataclass
class AutoTitleConfig:
    model_id: UUID | None
    prompt: str


async def load_auto_title_config(db: AsyncSession) -> AutoTitleConfig:
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.key.in_([KEY_MODEL_ID, KEY_PROMPT])
        )
    )
    rows = {row.key: row.value for row in result.scalars().all()}

    model_id: UUID | None = None
    raw_model_id = rows.get(KEY_MODEL_ID, "").strip()
    if raw_model_id:
        try:
            model_id = UUID(raw_model_id)
        except ValueError:
            logger.warning("auto_title invalid model_id in system_config", value=raw_model_id)

    return AutoTitleConfig(
        model_id=model_id,
        prompt=rows.get(KEY_PROMPT, "").strip() or DEFAULT_PROMPT,
    )


async def save_auto_title_config(
    db: AsyncSession, *, model_id: UUID | None, prompt: str
) -> None:
    values = {
        KEY_MODEL_ID: str(model_id) if model_id else "",
        KEY_PROMPT: prompt,
    }
    for key, value in values.items():
        row = await db.scalar(select(SystemConfig).where(SystemConfig.key == key))
        if row:
            row.value = value
        else:
            db.add(SystemConfig(key=key, value=value))
    await db.flush()


def _clean_title(raw: str) -> str | None:
    first_line = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    # Strip trailing punctuation the model may append, then surrounding quotes
    title = re.sub(r"[。！？!?.]+$", "", first_line).strip()
    title = title.strip('"\'「」『』《》').strip()
    if not title:
        return None
    return title[:MAX_TITLE_LENGTH]


async def generate_session_title(message: str) -> str | None:
    """Generate a session title from the first user message. None on failure."""
    from aio_agent_platform.db.connection import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as db:
            config = await load_auto_title_config(db)

            model = None
            if config.model_id:
                result = await db.execute(
                    select(LLMModel)
                    .options(selectinload(LLMModel.provider))
                    .where(LLMModel.id == config.model_id, LLMModel.is_active)
                )
                model = result.scalar_one_or_none()
            if not model:
                result = await db.execute(
                    select(LLMModel)
                    .options(selectinload(LLMModel.provider))
                    .where(LLMModel.is_default, LLMModel.is_active)
                    .limit(1)
                )
                model = result.scalar_one_or_none()

        if not model or not model.provider:
            logger.warning("auto_title skipped: no usable LLM model")
            return None

        provider = create_provider(
            provider=model.provider.provider_type,
            model=model.model_name,
            base_url=model.provider.base_url,
            api_key=model.provider.api_key_encrypted,
            temperature=0.3,
        )
        prompt = config.prompt.replace("{message}", message[:4000])
        response = await provider.complete(
            messages=[LLMMessage(role="user", content=prompt)],
            temperature=0.3,
            max_tokens=100,
        )
        return _clean_title(response.content)
    except Exception:
        logger.exception("auto_title generation failed")
        return None
