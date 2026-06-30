"""Tests for chat attachment upload and multimodal support."""

import io
import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from aio_agent_platform.llm.client import (
    LLMMessage,
    build_user_content,
    supports_vision,
)
from aio_agent_platform.core.context import (
    estimate_message_tokens,
    _light_truncate_message,
    IMAGE_TOKEN_ESTIMATE,
)


class TestBuildUserContent:
    """Test build_user_content helper."""

    @staticmethod
    def _mock_to_data_uri(key: str, mime: str) -> str:
        return f"data:{mime};base64,iVBORw0KGgo="

    def test_no_attachments_returns_text(self):
        """No attachments should return plain text."""
        result = build_user_content("hello", None, "openai", self._mock_to_data_uri)
        assert result == "hello"

    def test_empty_attachments_returns_text(self):
        """Empty attachments list should return plain text."""
        result = build_user_content("hello", [], "openai", self._mock_to_data_uri)
        assert result == "hello"

    def test_openai_format_uses_base64(self):
        """OpenAI format should always use base64 data URI for images."""
        attachments = [
            {
                "key": "k1",
                "url": "https://example.com/image.png",
                "mime": "image/png",
                "size": 1000,
                "filename": "test.png",
            }
        ]
        result = build_user_content("hello", attachments, "openai", self._mock_to_data_uri)
        assert isinstance(result, list)
        assert len(result) == 2
        # Text block includes image URL reference
        assert result[0]["type"] == "text"
        assert "hello" in result[0]["text"]
        assert "[图片: test.png](https://example.com/image.png)" in result[0]["text"]
        # Image block uses base64 data URI
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == "data:image/png;base64,iVBORw0KGgo="

    def test_openai_format_text_only_no_attachments(self):
        """Without attachments, text is returned as-is (string, not blocks)."""
        result = build_user_content("hello", None, "openai", self._mock_to_data_uri)
        assert result == "hello"

    def test_anthropic_format_uses_base64(self):
        """Anthropic format should always use base64 source for images."""
        attachments = [
            {
                "key": "k1",
                "url": "https://example.com/image.png",
                "mime": "image/png",
                "size": 1000,
                "filename": "test.png",
            }
        ]
        result = build_user_content("hello", attachments, "anthropic", self._mock_to_data_uri)
        assert isinstance(result, list)
        assert len(result) == 2
        # Text block includes image URL reference
        assert result[0]["type"] == "text"
        assert "[图片: test.png](https://example.com/image.png)" in result[0]["text"]
        # Image block uses base64
        assert result[1]["type"] == "image"
        assert result[1]["source"]["type"] == "base64"
        assert result[1]["source"]["data"] == "iVBORw0KGgo="
        assert result[1]["source"]["media_type"] == "image/png"

    def test_multiple_attachments(self):
        """Should handle multiple attachments with URL references in text."""
        attachments = [
            {
                "key": f"k{i}",
                "url": f"https://example.com/image{i}.png",
                "mime": "image/png",
                "size": 1000,
                "filename": f"test{i}.png",
            }
            for i in range(3)
        ]
        result = build_user_content("hello", attachments, "openai", self._mock_to_data_uri)
        assert len(result) == 4  # 1 text + 3 images
        # Text block contains all 3 image URL references
        text_block = result[0]["text"]
        for i in range(3):
            assert f"[图片: test{i}.png](https://example.com/image{i}.png)" in text_block

    def test_empty_text_with_attachments(self):
        """When text is empty, only image URL references form the text block."""
        attachments = [
            {
                "key": "k1",
                "url": "https://example.com/image.png",
                "mime": "image/png",
                "size": 1000,
                "filename": "photo.png",
            }
        ]
        result = build_user_content("", attachments, "openai", self._mock_to_data_uri)
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "[图片: photo.png](https://example.com/image.png)"


class TestSupportsVision:
    """Test supports_vision helper."""

    def test_openai_gpt4o(self):
        assert supports_vision("openai", "gpt-4o") is True
        assert supports_vision("openai", "gpt-4o-mini") is True

    def test_openai_gpt4_vision(self):
        assert supports_vision("openai", "gpt-4-vision-preview") is True

    def test_openai_deepseek_coder(self):
        assert supports_vision("openai", "deepseek-coder") is False

    def test_openai_gpt35(self):
        assert supports_vision("openai", "gpt-3.5-turbo") is False

    def test_anthropic_claude(self):
        assert supports_vision("anthropic", "claude-3-sonnet-20240229") is True
        assert supports_vision("anthropic", "claude-3-opus-20240229") is True
        assert supports_vision("anthropic", "claude-3-haiku-20240307") is True

    def test_qwen_vl(self):
        assert supports_vision("openai", "qwen-vl-max") is True
        assert supports_vision("openai", "qwen2-vl-72b") is True


class TestEstimateMessageTokensMultimodal:
    """Test estimate_message_tokens with multimodal content."""

    def test_plain_text_backward_compat(self):
        """Plain text messages should work as before."""
        msg = LLMMessage(role="user", content="hello world")
        tokens = estimate_message_tokens(msg)
        assert tokens < 20  # Small number

    def test_multimodal_with_images(self):
        """Multimodal messages should count image tokens."""
        msg = LLMMessage(
            role="user",
            content=[
                {"type": "text", "text": "hello world"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                {"type": "image_url", "image_url": {"url": "https://example.com/img2.png"}},
            ],
        )
        tokens = estimate_message_tokens(msg)
        # 4 (overhead) + text tokens + 2 * IMAGE_TOKEN_ESTIMATE
        assert tokens > 2400  # At least 2 * 1200 = 2400


class TestLightTruncateMessageMultimodal:
    """Test _light_truncate_message with multimodal content."""

    def test_multimodal_extracts_text_only(self):
        """Should extract only text blocks from multimodal content."""
        msg = LLMMessage(
            role="user",
            content=[
                {"type": "text", "text": "hello world"},
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
        )
        truncated = _light_truncate_message(msg)
        assert isinstance(truncated.content, str)
        assert truncated.content == "hello world"

    def test_multimodal_images_only_placeholder(self):
        """Should use placeholder when only images are present."""
        msg = LLMMessage(
            role="user",
            content=[
                {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
            ],
        )
        truncated = _light_truncate_message(msg)
        assert isinstance(truncated.content, str)
        assert "图片" in truncated.content

    def test_plain_text_backward_compat(self):
        """Plain text should pass through unchanged."""
        msg = LLMMessage(role="user", content="hello world")
        truncated = _light_truncate_message(msg)
        assert truncated.content == "hello world"
