"""LLM stream usage capture tests (no network)."""

from types import SimpleNamespace

from aio_agent_platform.llm.client import AnthropicProvider, OpenAIProvider


def _openai_provider() -> OpenAIProvider:
    return OpenAIProvider(model="gpt-test", base_url="http://x", api_key="k")


def test_openai_usage_only_chunk_parsed():
    provider = _openai_provider()
    event = SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    chunk = provider._parse_stream_event(event)
    assert chunk is not None
    assert chunk.type == "done"
    assert chunk.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }


def test_openai_empty_chunk_without_usage_ignored():
    provider = _openai_provider()
    event = SimpleNamespace(choices=[], usage=None)
    assert provider._parse_stream_event(event) is None


def test_openai_finish_chunk_with_usage():
    provider = _openai_provider()
    choice = SimpleNamespace(
        delta=None,
        finish_reason="stop",
    )
    event = SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=7, total_tokens=10),
    )
    chunk = provider._parse_stream_event(event)
    assert chunk is not None
    assert chunk.type == "done"
    assert chunk.usage["total_tokens"] == 10


def test_anthropic_message_delta_usage():
    provider = AnthropicProvider(model="claude-test", api_key="k")
    event = SimpleNamespace(
        type="message_delta",
        usage=SimpleNamespace(input_tokens=0, output_tokens=12),
        delta=SimpleNamespace(stop_reason="end_turn"),
    )
    chunk = provider._parse_stream_event(event)
    assert chunk is not None
    assert chunk.type == "done"
    assert chunk.usage["completion_tokens"] == 12
