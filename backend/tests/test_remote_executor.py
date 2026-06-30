"""Tests for RemoteToolExecutor."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from uuid import uuid4

import httpx

from aio_agent_platform.tools.remote.executor import RemoteToolExecutor, _split_path
from aio_agent_platform.tools.remote.manager import RemoteToolManager, RemoteToolConfig
from aio_agent_platform.tools.registry import ToolRegistry


def make_config(**kwargs):
    """Helper to create a RemoteToolConfig with defaults."""
    defaults = {
        "id": uuid4(),
        "name": "test_tool",
        "label": "测试工具",
        "description": "Test tool",
        "method": "POST",
        "url_template": "https://api.example.com/v1/test",
        "parameters_schema": {"type": "object", "properties": {}},
        "headers": None,
        "auth_type": "none",
        "auth_config": None,
        "query_params": None,
        "body_template": None,
        "response_extract": None,
        "timeout": 30,
        "is_active": True,
    }
    defaults.update(kwargs)
    return RemoteToolConfig(**defaults)


class TestSplitPath:
    """Test JSONPath splitting utility."""

    def test_simple_key(self):
        assert _split_path("key") == ["key"]

    def test_dotted_path(self):
        assert _split_path("a.b.c") == ["a", "b", "c"]

    def test_array_index(self):
        assert _split_path("choices[0]") == ["choices", "[0]"]

    def test_array_index_with_dots(self):
        assert _split_path("choices[0].message.content") == ["choices", "[0]", "message", "content"]

    def test_wildcard(self):
        assert _split_path("items[*]") == ["items", "[*]"]

    def test_wildcard_with_dots(self):
        assert _split_path("data.items[*].name") == ["data", "items", "[*]", "name"]


class TestExtractResponse:
    """Test response extraction via JSONPath."""

    @pytest.fixture
    def executor(self):
        registry = ToolRegistry()
        manager = RemoteToolManager(registry)
        return RemoteToolExecutor(manager)

    def test_root(self, executor):
        data = {"a": 1, "b": 2}
        assert executor._extract_response(data, "$") == {"a": 1, "b": 2}

    def test_simple_key(self, executor):
        data = {"message": "hello"}
        assert executor._extract_response(data, "$.message") == "hello"

    def test_nested_key(self, executor):
        data = {"user": {"name": "Alice"}}
        assert executor._extract_response(data, "$.user.name") == "Alice"

    def test_array_index(self, executor):
        data = {"choices": [{"id": 1}, {"id": 2}]}
        assert executor._extract_response(data, "$.choices[0]") == {"id": 1}
        assert executor._extract_response(data, "$.choices[1]") == {"id": 2}

    def test_array_index_nested(self, executor):
        data = {"choices": [{"message": {"content": "hello"}}]}
        assert executor._extract_response(data, "$.choices[0].message.content") == "hello"

    def test_wildcard(self, executor):
        data = {"items": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}
        result = executor._extract_response(data, "$.items[*].name")
        assert result == ["a", "b", "c"]

    def test_missing_key(self, executor):
        data = {"a": 1}
        assert executor._extract_response(data, "$.b") is None

    def test_out_of_bounds(self, executor):
        data = {"items": [{"id": 1}]}
        assert executor._extract_response(data, "$.items[5]") is None


class TestBuildAuthHeaders:
    """Test authentication header building."""

    @pytest.fixture
    def executor(self):
        registry = ToolRegistry()
        manager = RemoteToolManager(registry)
        return RemoteToolExecutor(manager)

    def test_none_auth(self, executor):
        headers = executor._build_auth_headers("none", None)
        assert headers == {}

    def test_bearer_auth(self, executor):
        headers = executor._build_auth_headers("bearer", {"token": "abc123"})
        assert headers == {"Authorization": "Bearer abc123"}

    def test_api_key_auth(self, executor):
        headers = executor._build_auth_headers("api_key", {"header_name": "X-API-Key", "key": "secret"})
        assert headers == {"X-API-Key": "secret"}

    def test_api_key_default_header(self, executor):
        headers = executor._build_auth_headers("api_key", {"key": "secret"})
        assert headers == {"X-API-Key": "secret"}

    def test_basic_auth(self, executor):
        headers = executor._build_auth_headers("basic", {"username": "user", "password": "pass"})
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")

    def test_custom_header(self, executor):
        headers = executor._build_auth_headers("custom_header", {"headers": {"X-Token": "t1", "X-Org": "org1"}})
        assert headers == {"X-Token": "t1", "X-Org": "org1"}


class TestBuildBody:
    """Test request body building."""

    @pytest.fixture
    def executor(self):
        registry = ToolRegistry()
        manager = RemoteToolManager(registry)
        return RemoteToolExecutor(manager)

    def test_get_returns_none(self, executor):
        config = make_config(method="GET")
        body = executor._build_body(config, {"foo": "bar"})
        assert body is None

    def test_post_with_template(self, executor):
        config = make_config(
            method="POST",
            body_template={"model": "test", "input": "{{text}}"}
        )
        body = executor._build_body(config, {"text": "hello"})
        assert body == {"model": "test", "input": "hello"}

    def test_post_without_template_uses_remaining(self, executor):
        config = make_config(method="POST", body_template=None)
        body = executor._build_body(config, {"key": "value"})
        assert body == {"key": "value"}

    def test_post_without_template_empty_args(self, executor):
        config = make_config(method="POST", body_template=None)
        body = executor._build_body(config, {})
        assert body is None


class TestBuildQueryParams:
    """Test query parameter building."""

    @pytest.fixture
    def executor(self):
        registry = ToolRegistry()
        manager = RemoteToolManager(registry)
        return RemoteToolExecutor(manager)

    def test_get_auto_query(self, executor):
        config = make_config(method="GET", query_params=None)
        query = executor._build_query_params(config, {"page": 1, "limit": 10})
        assert query == {"page": 1, "limit": 10}

    def test_post_no_auto_query(self, executor):
        config = make_config(method="POST", query_params=None)
        query = executor._build_query_params(config, {"page": 1})
        assert query == {}

    def test_explicit_mapping(self, executor):
        config = make_config(
            method="GET",
            query_params={"page_num": "page", "page_size": "size"}
        )
        query = executor._build_query_params(config, {"page_num": 2, "page_size": 20, "extra": "ignored"})
        assert query == {"page": 2, "size": 20}


class TestCall:
    """Test the full call flow."""

    @pytest.fixture
    def setup(self):
        registry = ToolRegistry()
        manager = RemoteToolManager(registry)
        executor = RemoteToolExecutor(manager)
        return manager, executor

    @pytest.mark.asyncio
    async def test_successful_call(self, setup):
        manager, executor = setup
        config = make_config(
            name="my_tool",
            url_template="https://api.example.com/test",
            body_template={"input": "{{text}}"},
            auth_type="bearer",
            auth_config={"token": "mytoken"},
        )
        manager._tools["my_tool"] = config

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": "success"}
        mock_response.text = '{"result": "success"}'

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor.call("my_tool", {"text": "hello"})

            mock_client.request.assert_called_once()
            call_kwargs = mock_client.request.call_args
            assert call_kwargs.kwargs["url"] == "https://api.example.com/test"
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer mytoken"
            assert call_kwargs.kwargs["json"] == {"input": "hello"}
            assert result == '{\n  "result": "success"\n}'

    @pytest.mark.asyncio
    async def test_response_extract(self, setup):
        manager, executor = setup
        config = make_config(
            name="extract_tool",
            response_extract="$.choices[0].message.content",
        )
        manager._tools["extract_tool"] = config

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "extracted text"}}]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor.call("extract_tool", {})
            assert result == "extracted text"

    @pytest.mark.asyncio
    async def test_http_error(self, setup):
        manager, executor = setup
        config = make_config(name="error_tool")
        manager._tools["error_tool"] = config

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            from aio_agent_platform.tools.remote.executor import RemoteToolError
            with pytest.raises(RemoteToolError, match="HTTP 404"):
                await executor.call("error_tool", {})

    @pytest.mark.asyncio
    async def test_timeout(self, setup):
        manager, executor = setup
        config = make_config(name="timeout_tool", timeout=5)
        manager._tools["timeout_tool"] = config

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            from aio_agent_platform.tools.remote.executor import RemoteToolError
            with pytest.raises(RemoteToolError, match="timed out"):
                await executor.call("timeout_tool", {})

    @pytest.mark.asyncio
    async def test_tool_not_found(self, setup):
        manager, executor = setup

        from aio_agent_platform.tools.remote.executor import RemoteToolError
        with pytest.raises(RemoteToolError, match="not found"):
            await executor.call("nonexistent", {})

    @pytest.mark.asyncio
    async def test_url_template_rendering(self, setup):
        manager, executor = setup
        config = make_config(
            name="path_tool",
            url_template="https://api.example.com/users/{user_id}/posts/{post_id}",
        )
        manager._tools["path_tool"] = config

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await executor.call("path_tool", {"user_id": "u1", "post_id": "p2"})

            call_kwargs = mock_client.request.call_args
            assert call_kwargs.kwargs["url"] == "https://api.example.com/users/u1/posts/p2"

    @pytest.mark.asyncio
    async def test_non_json_response(self, setup):
        manager, executor = setup
        config = make_config(name="text_tool")
        manager._tools["text_tool"] = config

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = Exception("not json")
        mock_response.text = "plain text response"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await executor.call("text_tool", {})
            assert result == "plain text response"
