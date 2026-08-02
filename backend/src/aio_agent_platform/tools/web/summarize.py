"""LLM-based summarization for oversized fetched pages.

Uses the default LLM model configured in the admin console. Any failure
(no model configured, API error) returns None so the caller can fall
back to plain truncation.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from aio_agent_platform.db.connection import get_session_factory
from aio_agent_platform.db.models import LLMModel
from aio_agent_platform.llm import LLMMessage, create_provider

logger = structlog.get_logger()

_PROMPT = """请把以下网页正文压缩为不超过 {max_chars} 字符的中文摘要。
要求：
- 保留关键事实、数据、结论和重要细节，去除导航、广告、样板文本
- 保持原文的信息结构（分节/列表），直接输出摘要，不要加"以下是摘要"之类的前缀

网页正文：
{text}"""


async def summarize_content(text: str, max_chars: int) -> str | None:
    """Summarize text to fit max_chars using the default LLM. None on failure."""
    try:
        factory = get_session_factory()
        async with factory() as db:
            result = await db.execute(
                select(LLMModel)
                .options(selectinload(LLMModel.provider))
                .where(LLMModel.is_default, LLMModel.is_active)
                .limit(1)
            )
            model = result.scalar_one_or_none()

        if not model or not model.provider:
            logger.warning("web summary skipped: no default LLM model configured")
            return None

        provider = create_provider(
            provider=model.provider.provider_type,
            model=model.model_name,
            base_url=model.provider.base_url,
            api_key=model.provider.api_key_encrypted,
            temperature=0.3,
        )
        # Keep the prompt itself within a sane size — summarize in one shot.
        prompt = _PROMPT.format(max_chars=max_chars, text=text[:60000])
        response = await provider.complete(
            messages=[LLMMessage(role="user", content=prompt)],
            temperature=0.3,
            max_tokens=max_chars * 2,
        )
        summary = response.content.strip()
        if not summary:
            return None
        if len(summary) > max_chars:
            summary = summary[:max_chars]
        return summary
    except Exception:
        logger.exception("web page summarization failed, falling back to truncation")
        return None
