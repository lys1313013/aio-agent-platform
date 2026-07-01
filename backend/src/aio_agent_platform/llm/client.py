"""LLM Provider — unified interface for OpenAI and Anthropic."""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

import anthropic
import httpx
import openai
import structlog
from langfuse import Langfuse

logger = structlog.get_logger()

# Retry configuration
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds, doubles each attempt: 1s → 2s → 4s


def _is_retryable_error(e: Exception) -> bool:
    """Check if an error is transient and worth retrying."""
    # OpenAI transient errors
    if isinstance(e, (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )):
        return True
    # Anthropic transient errors
    if isinstance(e, (
        anthropic.APIConnectionError,
        anthropic.APITimeoutError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )):
        return True
    # httpx-level transient errors
    if isinstance(e, (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout)):
        return True
    return False


async def _retry_with_backoff(
    fn_name: str,
    model: str,
    attempt_fn,  # async callable returning T
):
    """Execute attempt_fn with exponential backoff on transient failures."""
    last_error = None
    for attempt in range(1, _MAX_RETRIES + 2):  # 1, 2, 3, 4 (last is non-retry)
        try:
            return await attempt_fn()
        except Exception as e:
            last_error = e
            if attempt <= _MAX_RETRIES and _is_retryable_error(e):
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "LLM API call failed, retrying",
                    fn=fn_name,
                    model=model,
                    attempt=attempt,
                    max_retries=_MAX_RETRIES,
                    delay=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)
            else:
                break
    raise last_error


# ---- Exceptions ----


class LLMStreamError(Exception):
    """Raised when the LLM streaming connection is lost mid-stream."""

    pass


# ---- Data Types ----


@dataclass
class ToolCall:
    """A single tool call from the model."""

    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """Result from a tool execution."""

    tool_call_id: str
    output: str
    is_error: bool = False


@dataclass
class LLMChunk:
    """Streaming chunk from the model."""

    type: Literal[
        "text_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "done",
    ]
    content: str | None = None
    tool_call: ToolCall | None = None
    argument_delta: str | None = None  # Raw argument string delta for tool_call_delta
    usage: dict | None = None
    stop_reason: str | None = None


@dataclass
class LLMResponse:
    """Complete (non-streaming) response from the model."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict | None = None


@dataclass
class LLMMessage:
    """Unified message format for both providers."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


# ---- Provider Interface ----


class LLMProvider(ABC):
    """Abstract LLM provider."""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Non-streaming completion."""

    @abstractmethod
    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """Streaming completion."""

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Override per-provider to convert OpenAI tool format."""
        return tools


# ---- OpenAI Provider ----


class OpenAIProvider(LLMProvider):
    """
    Supports: OpenAI, DeepSeek, OpenRouter, Ollama, vLLM, LocalAI
    All services compatible with OpenAI Chat Completions API.
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
        enable_retry: bool = True,
        langfuse_client: Langfuse | None = None,
    ):
        self.client = openai.AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        self.model = model
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.enable_retry = enable_retry
        self.langfuse_client = langfuse_client

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        openai_messages = self._to_openai_messages(messages)

        async def _call():
            return await self.client.chat.completions.create(
                model=self.model,
                messages=openai_messages,
                tools=tools,
                temperature=temperature or self.default_temperature,
                max_tokens=max_tokens or self.default_max_tokens,
                stream=False,
            )

        if self.enable_retry:
            resp = await _retry_with_backoff("OpenAI.complete", self.model, _call)
        else:
            resp = await _call()
        return self._parse_response(resp)

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        openai_messages = self._to_openai_messages(messages)
        temp = temperature if temperature is not None else self.default_temperature
        mt = max_tokens or self.default_max_tokens

        # Start Langfuse generation if available
        gen_obs = None
        if self.langfuse_client:
            from aio_agent_platform.observation import get_current_observation
            parent = get_current_observation()
            factory = parent.start_observation if parent else self.langfuse_client.start_observation
            gen_obs = factory(
                name="OpenAI Chat Completion",
                as_type="generation",
                model=self.model,
                model_parameters={
                    "temperature": temp,
                    "max_tokens": str(mt) if mt else None,
                },
                input=openai_messages,
            )

        accumulated_text = ""
        accumulated_tool_calls: list[dict] = []
        last_usage = None

        try:
            async def _open_stream():
                return await self.client.chat.completions.create(
                    model=self.model,
                    messages=openai_messages,
                    tools=tools,
                    temperature=temp,
                    max_tokens=mt,
                    stream=True,
                )

            try:
                if self.enable_retry:
                    stream = await _retry_with_backoff("OpenAI.stream", self.model, _open_stream)
                else:
                    stream = await _open_stream()
            except Exception as e:
                logger.warning("LLM stream connection failed after retries (OpenAI)", model=self.model, error=str(e))
                if gen_obs:
                    gen_obs.update(level="ERROR", status_message=str(e))
                    gen_obs.end()
                raise LLMStreamError(f"LLM 流式连接失败: {e}") from e

            try:
                async for event in stream:
                    chunk = self._parse_stream_event(event)
                    if chunk:
                        if chunk.type == "text_delta" and chunk.content:
                            accumulated_text += chunk.content
                        elif chunk.type in ("tool_call_start", "tool_call_delta"):
                            pass  # tool calls tracked below
                        elif chunk.type == "done":
                            last_usage = chunk.usage
                        yield chunk
            except httpx.RemoteProtocolError as e:
                logger.warning("LLM stream connection lost (OpenAI)", model=self.model, error=str(e))
                if gen_obs:
                    gen_obs.update(level="ERROR", status_message=str(e))
                    gen_obs.end()
                raise LLMStreamError(f"LLM 流式连接中断: {e}") from e
            except openai.APIConnectionError as e:
                logger.warning("LLM API connection error (OpenAI)", model=self.model, error=str(e))
                if gen_obs:
                    gen_obs.update(level="ERROR", status_message=str(e))
                    gen_obs.end()
                raise LLMStreamError(f"LLM API 连接失败: {e}") from e
            except openai.APITimeoutError as e:
                logger.warning("LLM API timeout (OpenAI)", model=self.model, error=str(e))
                if gen_obs:
                    gen_obs.update(level="ERROR", status_message=str(e))
                    gen_obs.end()
                raise LLMStreamError(f"LLM API 超时: {e}") from e
        finally:
            if gen_obs:
                output = accumulated_text
                if accumulated_tool_calls:
                    output = json.dumps(
                        {"text": accumulated_text, "tool_calls": accumulated_tool_calls},
                        ensure_ascii=False,
                    )
                usage_details = None
                if last_usage:
                    usage_details = {
                        "input": last_usage.get("prompt_tokens", 0),
                        "output": last_usage.get("completion_tokens", 0),
                        "total": last_usage.get("total_tokens", 0),
                    }
                gen_obs.update(
                    output=output,
                    usage_details=usage_details,
                )
                gen_obs.end()

    def _to_openai_messages(self, messages: list[LLMMessage]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.role == "tool":
                result.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id,
                    }
                )
            elif msg.role == "assistant" and msg.tool_calls:
                result.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": __import__("json").dumps(tc.arguments),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

    def _parse_response(self, resp) -> LLMResponse:
        msg = resp.choices[0].message
        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    import json

                    args = json.loads(args)
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )
        usage = None
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            stop_reason=resp.choices[0].finish_reason,
            usage=usage,
        )

    def _parse_stream_event(self, event) -> LLMChunk | None:
        if not event.choices:
            return None
        choice = event.choices[0]
        delta = choice.delta
        if not delta:
            # Check for finish_reason without delta (some providers)
            if choice.finish_reason:
                usage = None
                if event.usage:
                    usage = {
                        "prompt_tokens": event.usage.prompt_tokens,
                        "completion_tokens": event.usage.completion_tokens,
                        "total_tokens": event.usage.total_tokens,
                    }
                return LLMChunk(
                    type="done",
                    stop_reason=choice.finish_reason,
                    usage=usage,
                )
            return None

        # Tool call events
        if delta.tool_calls:
            tc = delta.tool_calls[0]
            has_name = bool(tc.function.name)
            has_args = tc.function.arguments is not None and tc.function.arguments != ""

            if has_name:
                if has_args:
                    # Complete tool call in single chunk (some APIs like mimo-v2.5-pro)
                    # Yield as start with arguments included
                    return LLMChunk(
                        type="tool_call_start",
                        argument_delta=tc.function.arguments,
                        tool_call=ToolCall(
                            id=tc.id or "",
                            name=tc.function.name,
                            arguments={},
                        ),
                    )
                else:
                    # Streaming start without arguments (standard OpenAI)
                    return LLMChunk(
                        type="tool_call_start",
                        tool_call=ToolCall(
                            id=tc.id or "",
                            name=tc.function.name,
                            arguments={},
                        ),
                    )
            elif has_args:
                # Delta chunk: raw argument string fragment
                return LLMChunk(
                    type="tool_call_delta",
                    argument_delta=tc.function.arguments,
                    tool_call=ToolCall(
                        id=tc.id or "",
                        name="",
                        arguments={},
                    ),
                )

        # Text delta
        if delta.content:
            return LLMChunk(type="text_delta", content=delta.content)

        # Done
        if choice.finish_reason:
            usage = None
            if event.usage:
                usage = {
                    "prompt_tokens": event.usage.prompt_tokens,
                    "completion_tokens": event.usage.completion_tokens,
                    "total_tokens": event.usage.total_tokens,
                }
            return LLMChunk(
                type="done",
                stop_reason=choice.finish_reason,
                usage=usage,
            )

        return None


# ---- Anthropic Provider ----


class AnthropicProvider(LLMProvider):
    """
    Supports: Anthropic Claude series models.
    Uses Anthropic Messages API with native format.
    """

    SYSTEM_PROMPT_PLACEHOLDER = "__SYSTEM__"

    def __init__(
        self,
        model: str,
        api_key: str,
        default_temperature: float = 0.7,
        default_max_tokens: int = 4096,
        enable_retry: bool = True,
        langfuse_client: Langfuse | None = None,
    ):
        self.client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=120.0,
        )
        self.model = model
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self.enable_retry = enable_retry
        self.langfuse_client = langfuse_client

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        system_content, anthropic_messages = self._to_anthropic_format(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None

        async def _call():
            return await self.client.messages.create(
                model=self.model,
                system=system_content,
                messages=anthropic_messages,
                tools=anthropic_tools,
                max_tokens=max_tokens or self.default_max_tokens,
                temperature=temperature or self.default_temperature,
            )

        if self.enable_retry:
            resp = await _retry_with_backoff("Anthropic.complete", self.model, _call)
        else:
            resp = await _call()
        return self._parse_response(resp)

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMChunk]:
        system_content, anthropic_messages = self._to_anthropic_format(messages)
        anthropic_tools = self._convert_tools(tools) if tools else None
        temp = temperature if temperature is not None else self.default_temperature
        mt = max_tokens or self.default_max_tokens

        # Start Langfuse generation if available
        gen_obs = None
        if self.langfuse_client:
            from aio_agent_platform.observation import get_current_observation
            model_input = {"system": system_content, "messages": anthropic_messages}
            parent = get_current_observation()
            factory = parent.start_observation if parent else self.langfuse_client.start_observation
            gen_obs = factory(
                name="Anthropic Messages",
                as_type="generation",
                model=self.model,
                model_parameters={
                    "temperature": temp,
                    "max_tokens": mt,
                },
                input=model_input,
            )

        accumulated_text = ""
        accumulated_tool_calls: list[dict] = []
        last_usage = None

        async def _open_stream():
            return self.client.messages.stream(
                model=self.model,
                system=system_content,
                messages=anthropic_messages,
                tools=anthropic_tools,
                max_tokens=mt,
                temperature=temp,
            )

        try:
            if self.enable_retry:
                stream_ctx = await _retry_with_backoff("Anthropic.stream", self.model, _open_stream)
            else:
                stream_ctx = await _open_stream()
        except Exception as e:
            logger.warning("LLM stream connection failed after retries (Anthropic)", model=self.model, error=str(e))
            if gen_obs:
                gen_obs.update(level="ERROR", status_message=str(e))
                gen_obs.end()
            raise LLMStreamError(f"LLM 流式连接失败: {e}") from e

        try:
            async with stream_ctx as stream:
                async for event in stream:
                    chunk = self._parse_stream_event(event)
                    if chunk:
                        if chunk.type == "text_delta" and chunk.content:
                            accumulated_text += chunk.content
                        elif chunk.type == "done":
                            last_usage = chunk.usage
                        yield chunk
        except httpx.RemoteProtocolError as e:
            logger.warning("LLM stream connection lost (Anthropic)", model=self.model, error=str(e))
            if gen_obs:
                gen_obs.update(level="ERROR", status_message=str(e))
                gen_obs.end()
            raise LLMStreamError(f"LLM 流式连接中断: {e}") from e
        except anthropic.APIConnectionError as e:
            logger.warning("LLM API connection error (Anthropic)", model=self.model, error=str(e))
            if gen_obs:
                gen_obs.update(level="ERROR", status_message=str(e))
                gen_obs.end()
            raise LLMStreamError(f"LLM API 连接失败: {e}") from e
        except anthropic.APITimeoutError as e:
            logger.warning("LLM API timeout (Anthropic)", model=self.model, error=str(e))
            if gen_obs:
                gen_obs.update(level="ERROR", status_message=str(e))
                gen_obs.end()
            raise LLMStreamError(f"LLM API 超时: {e}") from e
        finally:
            if gen_obs:
                output = accumulated_text
                if accumulated_tool_calls:
                    output = json.dumps(
                        {"text": accumulated_text, "tool_calls": accumulated_tool_calls},
                        ensure_ascii=False,
                    )
                usage_details = None
                if last_usage:
                    usage_details = {
                        "input": last_usage.get("prompt_tokens", 0),
                        "output": last_usage.get("completion_tokens", 0),
                        "total": last_usage.get("total_tokens", 0),
                    }
                gen_obs.update(
                    output=output,
                    usage_details=usage_details,
                )
                gen_obs.end()

    def _to_anthropic_format(self, messages: list[LLMMessage]) -> tuple[str | None, list[dict]]:
        """Split messages into system prompt + message array."""
        system_parts = []
        result: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                system_parts.append(msg.content)
            elif msg.role == "assistant" and msg.tool_calls:
                blocks = []
                if msg.content:
                    blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                result.append({"role": "assistant", "content": blocks})
            elif msg.role == "tool":
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id,
                                "content": msg.content or "",
                            }
                        ],
                    }
                )
            else:
                result.append({"role": msg.role, "content": msg.content})
        system = "\n\n".join(system_parts) if system_parts else None
        return system, result

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI function format to Anthropic tool format."""
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"]["parameters"],
            }
            for t in tools
        ]

    def _parse_response(self, resp) -> LLMResponse:
        content = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )
        usage = {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        }
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason,
            usage=usage,
        )

    def _parse_stream_event(self, event) -> LLMChunk | None:
        if event.type == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                return LLMChunk(
                    type="tool_call_start",
                    tool_call=ToolCall(
                        id=block.id or "",
                        name=block.name,
                        arguments={},
                    ),
                )
            elif block.type == "text":
                return LLMChunk(type="text_delta", content="")
        elif event.type == "content_block_delta":
            delta = event.delta
            if delta.type == "text_delta":
                return LLMChunk(type="text_delta", content=delta.text)
            elif delta.type == "tool_use_delta":
                return LLMChunk(
                    type="tool_call_delta",
                    tool_call=ToolCall(
                        id=delta.id or "",
                        name="",
                        arguments=delta.input or {},
                    ),
                )
        elif event.type == "message_delta":
            usage = None
            if event.usage:
                usage = {
                    "prompt_tokens": getattr(event.usage, "input_tokens", 0),
                    "completion_tokens": getattr(event.usage, "output_tokens", 0),
                    "total_tokens": 0,
                }
            return LLMChunk(
                type="done",
                stop_reason=event.delta.stop_reason,
                usage=usage,
            )
        return None


# ---- Multimodal Helpers ----

VISION_CAPABLE_PATTERNS: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4-vision",
    "gpt-5",
    "claude-3",
    "claude-4",
    "qwen-vl",
    "qwen2-vl",
    "gemini",
    "sonnet",
    "opus",
    "haiku",
    "glm-4v",
)
NON_VISION_PATTERNS: tuple[str, ...] = (
    "deepseek-coder",
    "gpt-3.5-turbo",
    "text-embedding",
    "text-ada",
)


def supports_vision(provider_type: str, model_name: str) -> bool:
    """Best-effort check whether the model can accept image content."""
    name = (model_name or "").lower()
    if any(p in name for p in NON_VISION_PATTERNS):
        return False
    if provider_type == "anthropic":
        return True
    if "vl" in name:
        return True
    return any(p in name for p in VISION_CAPABLE_PATTERNS)


def build_image_url_refs(attachments: list[dict] | None) -> str:
    """Build markdown image reference links from attachment metadata.

    Used for non-multimodal models so they can see image URLs and invoke
    OCR tools to process images.
    """
    if not attachments:
        return ""
    refs: list[str] = []
    for a in attachments:
        label = a.get("filename") or "image"
        refs.append(f"[图片: {label}]({a['url']})")
    return "\n".join(refs)


def build_user_content(
    text: str,
    attachments: list[dict] | None,
    provider_type: str,
    to_data_uri: "callable[[str, str], str]",
) -> str | list[dict]:
    """Compile a user message's content payload, with optional images.

    - No attachments → returns the original ``text`` (string) for backward
      compatibility with every existing call site.
    - With attachments → always converts images to base64 data URIs for
      sending to the LLM (avoids URL accessibility issues with external
      providers).  Image URL references are appended to the text portion
      so both the model and the user can see where each image lives.

    Args:
        text: The user's text portion. May be empty.
        attachments: List of {key, url, mime, size, filename} dicts.
        provider_type: ``"openai"`` (or openai-compatible) or ``"anthropic"``.
        to_data_uri: Required ``callable(key, mime) -> "data:..."`` that
            downloads image bytes and returns a base64 data URI.  Used for
            both new messages and history re-hydration.
    """
    if not attachments:
        return text

    is_anthropic = provider_type == "anthropic"

    # Always resolve images to base64 data URIs for the multimodal blocks,
    # and collect URL references to embed in the text portion.
    resolved: list[tuple[dict, str]] = []  # (attachment, data_uri)
    url_refs: list[str] = []
    for a in attachments:
        uri = to_data_uri(a["key"], a["mime"])
        resolved.append((a, uri))
        label = a.get("filename") or "image"
        url_refs.append(f"[图片: {label}]({a['url']})")

    # Build the text block: original text + image URL references
    final_text = text
    if url_refs:
        refs_block = "\n".join(url_refs)
        final_text = f"{text}\n\n{refs_block}" if text else refs_block

    blocks: list[dict] = []
    if final_text:
        blocks.append({"type": "text", "text": final_text})

    for a, uri in resolved:
        if is_anthropic:
            # Anthropic base64 source: data:<mime>;base64,<payload>
            payload = uri.split(",", 1)[1] if "," in uri else uri
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": a["mime"],
                        "data": payload,
                    },
                }
            )
        else:
            # OpenAI / openai-compatible — use base64 data URI directly
            blocks.append({"type": "image_url", "image_url": {"url": uri}})

    return blocks


# ---- Provider Factory ----


def create_provider(
    provider: str,
    model: str,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.7,
    enable_retry: bool = True,
    langfuse_client: Langfuse | None = None,
) -> LLMProvider:
    """Create an LLM provider instance.

    All parameters (provider, model, base_url, api_key) must be supplied
    by the caller — there are no global defaults.
    """
    if provider == "anthropic":
        return AnthropicProvider(
            model=model,
            api_key=api_key or "",
            default_temperature=temperature,
            enable_retry=enable_retry,
            langfuse_client=langfuse_client,
        )
    else:
        return OpenAIProvider(
            model=model,
            base_url=base_url or "",
            api_key=api_key or "",
            default_temperature=temperature,
            enable_retry=enable_retry,
            langfuse_client=langfuse_client,
        )
