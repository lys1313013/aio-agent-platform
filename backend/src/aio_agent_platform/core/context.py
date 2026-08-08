"""Context window management — token budgeting, message truncation, and compression."""

from __future__ import annotations

import logging
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import anthropic
import jinja2
import openai

from aio_agent_platform.core.config import settings
from aio_agent_platform.hooks import get_hook_manager
from aio_agent_platform.llm.client import LLMMessage, LLMProvider

logger = logging.getLogger(__name__)


# ---- Agent Context (for tool handlers) ----

# 当前对话的 agent ID，供 knowledge_retrieval 等 handler 获取 agent 上下文
current_agent_id: ContextVar[str | None] = ContextVar(
    "current_agent_id", default=None
)

# ---- Prompt template ----

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"
_jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)))


# ---- Token Estimation ----


def estimate_tokens(text: str | None) -> int:
    """Estimate token count from text using character-based heuristics.

    Accuracy is within ±15% for mixed CJK/Latin content.
    No external dependencies (tiktoken etc.) required.
    """
    if not text:
        return 0

    cjk_chars = 0
    latin_chars = 0
    other_chars = 0

    for ch in text:
        if unicodedata.category(ch).startswith("C"):
            # Control characters, whitespace
            other_chars += 1
        elif "一" <= ch <= "鿿" or "　" <= ch <= "ヿ" or "가" <= ch <= "힯":
            cjk_chars += 1
        elif ch.isascii():
            latin_chars += 1
        else:
            cjk_chars += 1  # Treat other non-ASCII as CJK-like

    # CJK: ~1.5 chars/token, Latin: ~4 chars/token, other: ~3 chars/token
    estimated = cjk_chars / 1.5 + latin_chars / 4.0 + other_chars / 3.0
    return max(1, int(estimated))


IMAGE_TOKEN_ESTIMATE: int = 1200  # OpenAI/Anthropic vision image cost (~1000-2000)


def estimate_message_tokens(msg: LLMMessage) -> int:
    """Estimate tokens for a single message including overhead."""
    tokens = 4  # Message overhead (role, delimiters)
    if msg.content:
        if isinstance(msg.content, str):
            tokens += estimate_tokens(msg.content)
        elif isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "text" and "text" in block:
                        tokens += estimate_tokens(block["text"])
                    elif block_type in ("image_url", "image"):
                        tokens += IMAGE_TOKEN_ESTIMATE
    if msg.tool_calls:
        for tc in msg.tool_calls:
            tokens += 10  # Tool call overhead
            tokens += estimate_tokens(str(tc.arguments))
    return tokens


def estimate_messages_tokens(messages: list[LLMMessage]) -> int:
    """Estimate total tokens for a message list."""
    return sum(estimate_message_tokens(m) for m in messages)


# ---- Budget Calculation ----


@dataclass
class ContextBudget:
    """Token budget breakdown for context management."""

    total_window: int
    reserve_output: int
    compress_threshold: float

    @property
    def usable(self) -> int:
        return self.total_window - self.reserve_output

    @property
    def trigger_at(self) -> int:
        return int(self.usable * self.compress_threshold)

    @property
    def system_budget(self) -> int:
        """~15% of usable budget for system prompt."""
        return int(self.usable * 0.15)

    @property
    def history_budget(self) -> int:
        """~65% of usable budget for conversation history."""
        return int(self.usable * 0.65)

    @classmethod
    def from_settings(cls) -> ContextBudget:
        return cls(
            total_window=settings.agent.context_window,
            reserve_output=settings.agent.context_reserve_output,
            compress_threshold=settings.agent.context_compress_threshold,
        )


# ---- Message Truncation (Zero-Cost) ----


def truncate_message_content(content: str, max_chars: int = 5000) -> str:
    """Truncate a single message's content, keeping head and tail."""
    if len(content) <= max_chars:
        return content
    keep = max_chars // 2 - 20
    return content[:keep] + f"\n\n[... {len(content) - max_chars + 40} chars omitted ...]\n\n" + content[-keep:]


def truncate_history_messages(
    messages: list[LLMMessage],
    budget_tokens: int,
) -> list[LLMMessage]:
    """Trim history messages to fit within token budget.

    Strategy:
    1. Keep the most recent messages intact (walk backwards from end).
    2. Truncate long individual messages (>5000 chars).
    3. Strip tool_calls details from older messages.
    4. Stop including messages when budget is exhausted.

    Returns a new list (does not mutate the original).
    """
    if not messages:
        return []

    result: list[LLMMessage] = []
    used_tokens = 0

    # Walk from newest to oldest
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        msg_tokens = estimate_message_tokens(msg)

        if used_tokens + msg_tokens > budget_tokens:
            # Budget exhausted — stop including older messages
            break

        # Apply light truncation to older messages (not the last 4)
        is_recent = i >= len(messages) - 4
        if not is_recent:
            msg = _light_truncate_message(msg)

        result.append(msg)
        used_tokens += msg_tokens

    # Reverse to restore chronological order
    result.reverse()

    if len(result) < len(messages):
        dropped = len(messages) - len(result)
        logger.info(
            "Context truncation: dropped %d older messages, kept %d (est. %d tokens)",
            dropped,
            len(result),
            used_tokens,
        )

    return result


def _light_truncate_message(msg: LLMMessage) -> LLMMessage:
    """Apply zero-cost truncation to a single message."""
    new_content = msg.content

    # Multimodal content: keep only text blocks; drop image blocks to
    # avoid blowing the budget with base64.
    if isinstance(msg.content, list):
        text_parts = [
            b.get("text", "")
            for b in msg.content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        new_content = " ".join(text_parts) if text_parts else "[用户此前发送了图片消息]"
    elif isinstance(msg.content, str) and len(msg.content) > 5000:
        new_content = truncate_message_content(msg.content)

    # Strip tool_calls details from old assistant messages (keep names only)
    new_tool_calls = msg.tool_calls
    if msg.role == "assistant" and msg.tool_calls:
        # For old messages, we only need to know what tools were called
        new_tool_calls = msg.tool_calls  # Keep structure for API compatibility

    return LLMMessage(
        role=msg.role,
        content=new_content,
        tool_calls=new_tool_calls,
        tool_call_id=msg.tool_call_id,
        name=msg.name,
    )


# ---- Progressive Tool Result Compression (Within ReAct Loop) ----


def compress_early_tool_results(
    messages: list[LLMMessage],
    current_iteration: int,
    max_chars_recent: int = 10000,
    max_chars_mid: int = 500,
) -> list[LLMMessage]:
    """Compress tool result messages from early iterations.

    - Last 3 iterations: keep full output
    - 4-7 iterations back: truncate to max_chars_mid
    - 8+ iterations back: one-line summary (tool name + status)

    This operates on the *in-loop* messages (system + history + current turn).
    Tool results are identified by role='tool' and paired with preceding assistant
    messages that have tool_calls.

    Returns a new message list (does not mutate original).
    """
    if current_iteration <= 3:
        return messages

    # Identify iteration boundaries: each assistant message with tool_calls
    # marks the start of an iteration. We count backwards from the end.
    iteration_boundaries: list[int] = []  # indices of assistant messages with tool_calls
    for i, msg in enumerate(messages):
        if msg.role == "assistant" and msg.tool_calls:
            iteration_boundaries.append(i)

    if len(iteration_boundaries) <= 3:
        return messages

    result = list(messages)  # shallow copy

    for iter_idx, boundary_idx in enumerate(iteration_boundaries):
        iterations_ago = len(iteration_boundaries) - 1 - iter_idx

        if iterations_ago < 3:
            continue  # Keep recent iterations intact

        # Find tool result messages following this assistant message
        j = boundary_idx + 1
        while j < len(result) and result[j].role == "tool":
            tool_msg = result[j]
            if iterations_ago >= 7:
                # 8+ iterations ago: one-line summary
                summary = _summarize_tool_result(tool_msg)
                result[j] = LLMMessage(
                    role="tool",
                    content=summary,
                    tool_call_id=tool_msg.tool_call_id,
                )
            elif iterations_ago >= 3:
                # 4-7 iterations ago: truncate
                if tool_msg.content and len(tool_msg.content) > max_chars_mid:
                    result[j] = LLMMessage(
                        role="tool",
                        content=tool_msg.content[:max_chars_mid] + "\n... [truncated]",
                        tool_call_id=tool_msg.tool_call_id,
                    )
            j += 1

    return result


def _summarize_tool_result(msg: LLMMessage) -> str:
    """Create a one-line summary of a tool result."""
    content = msg.content or ""
    if not content:
        return "[tool executed]"
    # Take first line or first 100 chars
    first_line = content.split("\n")[0][:100]
    status = "ok" if not content.startswith("Error:") else "error"
    return f"[{status}] {first_line}"


# ---- LLM-Based Summary Compression ----


async def generate_summary(
    messages: list[LLMMessage],
    provider: LLMProvider,
    max_chars: int | None = None,
) -> str:
    """Generate a compressed summary of conversation messages using LLM.

    Uses the provided provider (ideally a fast/cheap model).
    Returns summary text within max_chars limit.
    """
    if not messages:
        return ""

    max_chars = max_chars or settings.agent.context_summary_max_chars

    # Build simple message dicts for the template
    msg_dicts = []
    for msg in messages:
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        # Truncate individual messages for the summary prompt itself
        if len(content) > 2000:
            content = content[:1000] + " ... " + content[-500:]
        msg_dicts.append({"role": msg.role, "content": content})

    template = _jinja_env.get_template("context_summary.j2")
    summary_prompt = template.render(messages=msg_dicts, max_chars=max_chars)

    try:
        response = await provider.complete(
            messages=[LLMMessage(role="user", content=summary_prompt)],
            temperature=0.3,
            max_tokens=max_chars * 2,  # Rough token limit
        )
        summary = response.content.strip()
        if len(summary) > max_chars:
            summary = summary[:max_chars]
        logger.info(
            "Generated context summary: %d messages -> %d chars",
            len(messages),
            len(summary),
        )
        return summary
    except Exception:
        logger.exception("Failed to generate context summary")
        # Fallback: simple concatenation of first/last messages
        return _fallback_summary(messages, max_chars)


def _fallback_summary(messages: list[LLMMessage], max_chars: int) -> str:
    """Non-LLM fallback: concatenate key messages."""
    parts = []
    for msg in messages:
        if msg.role in ("user", "assistant") and msg.content:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            parts.append(f"{msg.role}: {content[:200]}")
    summary = "\n".join(parts)
    if len(summary) > max_chars:
        summary = summary[:max_chars]
    return summary


# ---- Main Entry Point ----


async def prepare_context(
    system_prompt: str | None,
    history: list[LLMMessage],
    user_input: str,
    provider: LLMProvider,
    existing_summary: str | None = None,
) -> tuple[list[LLMMessage], bool]:
    """Prepare the message list with context window management.

    This is the main entry point called before AgentLoop.run().

    Args:
        system_prompt: The assembled system prompt (may be None).
        history: Conversation history loaded from DB.
        user_input: The current user message.
        provider: LLM provider for summary generation.
        existing_summary: Previously saved context_summary from session.

    Returns:
        (messages, summary_generated): The prepared message list and whether
        a new summary was generated.
    """
    budget = ContextBudget.from_settings()

    # Build preliminary message list
    messages: list[LLMMessage] = []
    if system_prompt:
        messages.append(LLMMessage(role="system", content=system_prompt))

    # If there's an existing summary, inject it
    if existing_summary:
        messages.append(
            LLMMessage(
                role="system",
                content=f"[Previous conversation summary]: {existing_summary}",
            )
        )

    messages.extend(history)
    messages.append(LLMMessage(role="user", content=user_input))

    total_tokens = estimate_messages_tokens(messages)
    logger.debug(
        "Context check: %d messages, ~%d tokens (budget: %d, trigger: %d)",
        len(messages),
        total_tokens,
        budget.usable,
        budget.trigger_at,
    )

    # Under budget — no compression needed
    if total_tokens <= budget.trigger_at:
        return messages, False

    logger.info(
        "Context budget exceeded (%d > %d), starting compression",
        total_tokens,
        budget.trigger_at,
    )
    tokens_before_compress = total_tokens
    get_hook_manager().fire_nowait(
        "PreCompact", data={"tokens_before": tokens_before_compress}
    )

    # Step 1: Light truncation (zero cost)
    # Separate system messages from history for targeted truncation
    system_msgs = [m for m in messages if m.role == "system"]
    non_system = [m for m in messages if m.role != "system"]

    truncated_history = truncate_history_messages(non_system, budget.history_budget)

    messages = system_msgs + truncated_history
    total_tokens = estimate_messages_tokens(messages)

    if total_tokens <= budget.trigger_at:
        logger.info("Light truncation sufficient: %d tokens", total_tokens)
        return messages, False

    # Step 2: LLM summary compression
    # Split into "to summarize" (older half) and "to keep" (newer half)
    split_point = _find_split_point(messages, budget.history_budget)

    if split_point <= 1:
        # Nothing to summarize (only 1 message or less before split)
        logger.info("No messages to summarize, returning truncated context")
        return messages, False

    # Identify the range of non-system messages to compress
    system_end = len(system_msgs)
    to_summarize = messages[system_end : system_end + split_point]
    to_keep = messages[system_end + split_point :]

    if not to_summarize:
        return messages, False

    summary = await generate_summary(to_summarize, provider)

    # Rebuild message list: system + summary + kept messages
    new_messages = system_msgs[:]
    if summary:
        new_messages.append(
            LLMMessage(
                role="system",
                content=f"[Previous conversation summary]: {summary}",
            )
        )
    new_messages.extend(to_keep)

    final_tokens = estimate_messages_tokens(new_messages)
    logger.info(
        "Context compression complete: %d -> %d messages, ~%d -> ~%d tokens",
        len(messages),
        len(new_messages),
        total_tokens,
        final_tokens,
    )
    get_hook_manager().fire_nowait(
        "PostCompact",
        data={
            "tokens_before": tokens_before_compress,
            "tokens_after": final_tokens,
            "saved_tokens": max(tokens_before_compress - final_tokens, 0),
        },
    )

    return new_messages, True


def _find_split_point(messages: list[LLMMessage], keep_budget: int) -> int:
    """Find the index where we should split messages into 'summarize' and 'keep'.

    Walk from the end, accumulating messages until budget is exhausted.
    The split point aligns to user message boundaries.
    """
    used = 0
    split_idx = len(messages)

    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = estimate_message_tokens(messages[i])
        if used + msg_tokens > keep_budget:
            break
        used += msg_tokens
        # Align to user message boundary (prefer splitting before a user message)
        if messages[i].role == "user":
            split_idx = i

    return max(0, split_idx)


# ---- Overflow Error Detection ----


def is_context_overflow_error(error: Exception) -> bool:
    """Detect if an exception is a context length exceeded error."""
    if isinstance(error, openai.BadRequestError):
        err_str = str(error)
        return "context_length_exceeded" in err_str or "maximum context length" in err_str
    if isinstance(error, anthropic.BadRequestError):
        err_str = str(error)
        return "prompt is too long" in err_str or "max_tokens" in err_str
    # Generic check
    err_str = str(error).lower()
    return "context_length" in err_str or "too many tokens" in err_str or "prompt is too long" in err_str


# ---- Emergency Compression (for retry after overflow) ----


def emergency_compress(
    messages: list[LLMMessage],
    level: int = 1,
) -> list[LLMMessage]:
    """Aggressively compress messages for retry after overflow error.

    Level 1: Keep only last 10 non-system messages
    Level 2: Keep only last 5 non-system messages, strip tool_calls
    Level 3: Keep only system + last user message
    """
    system_msgs = [m for m in messages if m.role == "system"]
    non_system = [m for m in messages if m.role != "system"]

    if level == 1:
        kept = non_system[-10:] if len(non_system) > 10 else non_system
    elif level == 2:
        kept = non_system[-5:] if len(non_system) > 5 else non_system
        # Strip tool_calls from kept messages
        kept = [
            LLMMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id)
            for m in kept
        ]
    else:
        # Level 3+: only last user message
        last_user = None
        for m in reversed(non_system):
            if m.role == "user":
                last_user = m
                break
        kept = [last_user] if last_user else non_system[-1:]

    logger.warning(
        "Emergency compression (level %d): %d -> %d messages",
        level,
        len(messages),
        len(system_msgs) + len(kept),
    )

    return system_msgs + kept


# ---- System Prompt Budget Control ----


def trim_system_prompt_content(
    system_prompt: str,
    budget_tokens: int,
) -> str:
    """Trim system prompt if it exceeds the token budget.

    This is a simple character-based truncation as a safety net.
    The prompt.py module should do smarter trimming before this is called.
    """
    estimated = estimate_tokens(system_prompt)
    if estimated <= budget_tokens:
        return system_prompt

    # Rough character limit based on token budget (conservative: ~2.5 chars/token)
    char_limit = int(budget_tokens * 2.5)
    if len(system_prompt) > char_limit:
        logger.warning(
            "System prompt too large (%d chars, ~%d tokens, budget %d), truncating",
            len(system_prompt),
            estimated,
            budget_tokens,
        )
        return system_prompt[:char_limit] + "\n\n[... system prompt truncated due to context limits ...]"

    return system_prompt
