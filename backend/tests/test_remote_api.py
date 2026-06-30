"""Tests for remote tools API endpoints - schema and validation focused."""

import pytest
from uuid import uuid4

from aio_agent_platform.interface.routes.remote_tools import (
    RemoteToolCreate,
    RemoteToolUpdate,
    RemoteToolOut,
    RemoteToolTestRequest,
    _mask_auth_config,
)


class TestRemoteToolCreateSchema:
    """Test RemoteToolCreate Pydantic schema."""

    def test_minimal_valid(self):
        """Minimal valid configuration."""
        data = {
            "name": "test_tool",
            "label": "测试工具",
            "description": "A test tool",
            "method": "POST",
            "url_template": "https://api.example.com/test",
        }
        tool = RemoteToolCreate(**data)
        assert tool.name == "test_tool"
        assert tool.label == "测试工具"
        assert tool.method == "POST"
        assert tool.timeout == 30  # default
        assert tool.is_active is True  # default
        assert tool.auth_type == "none"  # default

    def test_full_config(self):
        """Full configuration with all fields."""
        data = {
            "name": "full_tool",
            "label": "完整工具",
            "description": "Full configuration",
            "method": "POST",
            "url_template": "https://api.example.com/{id}",
            "parameters_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"]
            },
            "headers": {"Accept": "application/json"},
            "auth_type": "bearer",
            "auth_config": {"token": "secret123"},
            "body_template": {"input": "{{data}}"},
            "response_extract": "$.result",
            "timeout": 60,
            "is_active": False,
        }
        tool = RemoteToolCreate(**data)
        assert tool.name == "full_tool"
        assert tool.label == "完整工具"
        assert tool.auth_type == "bearer"
        assert tool.timeout == 60
        assert tool.is_active is False

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH"])
    def test_valid_methods(self, method):
        """All HTTP methods should be accepted."""
        data = {
            "name": "test",
            "label": "test",
            "description": "test",
            "method": method,
            "url_template": "https://example.com",
        }
        tool = RemoteToolCreate(**data)
        assert tool.method == method

    def test_invalid_method(self):
        """Invalid HTTP method should be rejected."""
        data = {
            "name": "test",
            "label": "test",
            "description": "test",
            "method": "INVALID",
            "url_template": "https://example.com",
        }
        with pytest.raises(Exception):  # Pydantic ValidationError
            RemoteToolCreate(**data)

    @pytest.mark.parametrize("auth_type", ["none", "bearer", "api_key", "basic", "custom_header"])
    def test_valid_auth_types(self, auth_type):
        """All auth types should be accepted."""
        data = {
            "name": "test",
            "label": "test",
            "description": "test",
            "method": "GET",
            "url_template": "https://example.com",
            "auth_type": auth_type,
        }
        tool = RemoteToolCreate(**data)
        assert tool.auth_type == auth_type

    def test_invalid_auth_type(self):
        """Invalid auth type should be rejected."""
        data = {
            "name": "test",
            "label": "test",
            "description": "test",
            "method": "GET",
            "url_template": "https://example.com",
            "auth_type": "invalid",
        }
        with pytest.raises(Exception):
            RemoteToolCreate(**data)

    @pytest.mark.parametrize("invalid_name", [
        "has space",
        "special@char",
        "中文",
        "dash-!",
        "",
        "a" * 65,
    ])
    def test_invalid_names(self, invalid_name):
        """Invalid tool names should be rejected."""
        data = {
            "name": invalid_name,
            "label": "test",
            "description": "test",
            "method": "GET",
            "url_template": "https://example.com",
        }
        with pytest.raises(Exception):
            RemoteToolCreate(**data)

    @pytest.mark.parametrize("valid_name", [
        "simple",
        "with_underscore",
        "with-dash",
        "CamelCase",
        "mixed123",
        "a",
        "a" * 64,
    ])
    def test_valid_names(self, valid_name):
        """Valid tool names should be accepted."""
        data = {
            "name": valid_name,
            "label": "test",
            "description": "test",
            "method": "GET",
            "url_template": "https://example.com",
        }
        tool = RemoteToolCreate(**data)
        assert tool.name == valid_name

    def test_timeout_range(self):
        """Timeout must be between 5 and 600."""
        data = {
            "name": "test",
            "label": "test",
            "description": "test",
            "method": "GET",
            "url_template": "https://example.com",
        }

        # Too low
        with pytest.raises(Exception):
            RemoteToolCreate(**{**data, "timeout": 4})

        # Too high
        with pytest.raises(Exception):
            RemoteToolCreate(**{**data, "timeout": 601})

        # Valid
        tool = RemoteToolCreate(**{**data, "timeout": 30})
        assert tool.timeout == 30


class TestRemoteToolUpdateSchema:
    """Test RemoteToolUpdate Pydantic schema."""

    def test_all_fields_optional(self):
        """All fields should be optional."""
        tool = RemoteToolUpdate()
        assert tool.name is None
        assert tool.label is None
        assert tool.description is None
        assert tool.method is None

    def test_partial_update(self):
        """Partial updates should work."""
        data = {"description": "Updated description", "timeout": 45}
        tool = RemoteToolUpdate(**data)
        assert tool.description == "Updated description"
        assert tool.timeout == 45
        assert tool.name is None

    def test_validation_still_applies(self):
        """Validation should still apply to provided fields."""
        data = {"name": "invalid name"}
        with pytest.raises(Exception):
            RemoteToolUpdate(**data)


class TestRemoteToolOutSchema:
    """Test RemoteToolOut Pydantic schema."""

    def test_full_output(self):
        """Complete output schema."""
        data = {
            "id": uuid4(),
            "name": "test_tool",
            "label": "测试工具",
            "description": "Test tool",
            "method": "POST",
            "url_template": "https://example.com",
            "parameters_schema": {"type": "object"},
            "headers": None,
            "auth_type": "bearer",
            "auth_config_masked": {"token": "****"},
            "query_params": None,
            "body_template": None,
            "response_extract": None,
            "timeout": 30,
            "is_active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        out = RemoteToolOut(**data)
        assert out.name == "test_tool"
        assert out.auth_config_masked == {"token": "****"}


class TestRemoteToolTestRequest:
    """Test RemoteToolTestRequest schema."""

    def test_with_arguments(self):
        """Request with test arguments."""
        data = {"arguments": {"arg1": "value1", "arg2": 42}}
        req = RemoteToolTestRequest(**data)
        assert req.arguments == {"arg1": "value1", "arg2": 42}

    def test_empty_arguments(self):
        """Request with empty arguments."""
        req = RemoteToolTestRequest(arguments={})
        assert req.arguments == {}


class TestMaskAuthConfig:
    """Test auth config masking utility."""

    def test_none_config(self):
        """None config should return None."""
        assert _mask_auth_config(None) is None

    def test_empty_config(self):
        """Empty config should return None."""
        assert _mask_auth_config({}) is None

    def test_mask_token(self):
        """Token should be masked."""
        config = {"token": "secret123"}
        masked = _mask_auth_config(config)
        assert masked == {"token": "****"}

    def test_mask_api_key(self):
        """API key should be masked."""
        config = {"header_name": "X-API-Key", "key": "secret"}
        masked = _mask_auth_config(config)
        assert masked == {"header_name": "****", "key": "****"}

    def test_mask_basic_auth(self):
        """Basic auth credentials should be masked."""
        config = {"username": "user", "password": "pass"}
        masked = _mask_auth_config(config)
        assert masked == {"username": "****", "password": "****"}

    def test_mask_custom_headers(self):
        """Custom headers dict should be masked."""
        config = {"headers": {"X-Token": "t1", "X-Org": "org1"}}
        masked = _mask_auth_config(config)
        assert masked == {"headers": {"X-Token": "****", "X-Org": "****"}}

    def test_mask_mixed_types(self):
        """Mixed value types should be handled."""
        config = {
            "token": "secret",
            "enabled": True,
            "count": 42,
            "nested": {"key": "value"}
        }
        masked = _mask_auth_config(config)
        assert masked["token"] == "****"
        assert masked["enabled"] is True  # non-string preserved
        assert masked["count"] == 42  # non-string preserved
        assert masked["nested"] == {"key": "****"}  # nested dict masked
