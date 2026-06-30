"""Tests for remote tools template engine."""

import pytest

from aio_agent_platform.tools.remote.template import render_url, render_body


class TestRenderUrl:
    """Test URL template rendering."""

    def test_simple_variable(self):
        url, remaining = render_url("https://api.example.com/users/{user_id}", {"user_id": "123"})
        assert url == "https://api.example.com/users/123"
        assert remaining == {}

    def test_multiple_variables(self):
        url, remaining = render_url(
            "https://api.example.com/users/{user_id}/posts/{post_id}",
            {"user_id": "u1", "post_id": "p2"}
        )
        assert url == "https://api.example.com/users/u1/posts/p2"
        assert remaining == {}

    def test_remaining_args(self):
        url, remaining = render_url(
            "https://api.example.com/users/{user_id}",
            {"user_id": "123", "status": "active", "limit": 10}
        )
        assert url == "https://api.example.com/users/123"
        assert remaining == {"status": "active", "limit": 10}

    def test_no_variables(self):
        url, remaining = render_url("https://api.example.com/health", {"foo": "bar"})
        assert url == "https://api.example.com/health"
        assert remaining == {"foo": "bar"}

    def test_missing_variable(self):
        url, remaining = render_url("https://api.example.com/users/{user_id}", {})
        assert url == "https://api.example.com/users/{user_id}"
        assert remaining == {}

    def test_type_conversion(self):
        url, remaining = render_url(
            "https://api.example.com/page/{page}",
            {"page": 42}
        )
        assert url == "https://api.example.com/page/42"
        assert remaining == {}


class TestRenderBody:
    """Test body template rendering."""

    def test_simple_string(self):
        result = render_body({"name": "{{name}}"}, {"name": "Alice"})
        assert result == {"name": "Alice"}

    def test_nested_dict(self):
        result = render_body(
            {"user": {"name": "{{name}}", "age": "{{age}}"}},
            {"name": "Bob", "age": "30"}
        )
        assert result == {"user": {"name": "Bob", "age": "30"}}

    def test_list_with_variables(self):
        result = render_body(
            {"items": ["{{item1}}", "{{item2}}"]},
            {"item1": "a", "item2": "b"}
        )
        assert result == {"items": ["a", "b"]}

    def test_type_preservation_array(self):
        """Array variables should remain arrays, not become strings."""
        result = render_body(
            {"data": "{{items}}"},
            {"items": [1, 2, 3]}
        )
        assert result == {"data": [1, 2, 3]}

    def test_type_preservation_object(self):
        """Object variables should remain objects."""
        result = render_body(
            {"config": "{{settings}}"},
            {"settings": {"timeout": 30}}
        )
        assert result == {"config": {"timeout": 30}}

    def test_type_preservation_number(self):
        """Number variables should remain numbers."""
        result = render_body(
            {"count": "{{n}}"},
            {"n": 42}
        )
        assert result == {"count": 42}

    def test_type_preservation_boolean(self):
        """Boolean variables should remain booleans."""
        result = render_body(
            {"enabled": "{{flag}}"},
            {"flag": True}
        )
        assert result == {"enabled": True}

    def test_string_interpolation(self):
        """Multiple variables in a string should be interpolated."""
        result = render_body(
            {"greeting": "Hello {{name}}, you are {{age}} years old"},
            {"name": "Alice", "age": "30"}
        )
        assert result == {"greeting": "Hello Alice, you are 30 years old"}

    def test_missing_variable(self):
        """Missing variables should remain as placeholders."""
        result = render_body({"name": "{{name}}"}, {})
        assert result == {"name": "{{name}}"}

    def test_partial_interpolation(self):
        """Partial string interpolation should work."""
        result = render_body(
            {"text": "Hello {{name}}, welcome!"},
            {"name": "Bob"}
        )
        assert result == {"text": "Hello Bob, welcome!"}

    def test_complex_openai_vision_format(self):
        """Test the OpenAI vision API format from the documentation example."""
        template = {
            "model": "voucher-ocr-model",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "{{prompt}}"},
                        {"type": "image_url", "image_url": {"url": "{{image_url}}"}}
                    ]
                }
            ]
        }
        args = {
            "prompt": "Extract invoice details",
            "image_url": "https://example.com/invoice.jpg"
        }
        result = render_body(template, args)

        assert result["model"] == "voucher-ocr-model"
        assert result["messages"][0]["content"][0]["text"] == "Extract invoice details"
        assert result["messages"][0]["content"][1]["image_url"]["url"] == "https://example.com/invoice.jpg"

    def test_empty_template(self):
        result = render_body({}, {"foo": "bar"})
        assert result == {}

    def test_none_template(self):
        result = render_body(None, {"foo": "bar"})
        assert result is None
