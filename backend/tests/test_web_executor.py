"""web_search / web_fetch executor-integration tests.

Regression: the executor invokes every direct handler with extra kwargs
(delegation=, tool_executor=, event_queue=, ...). Handler signatures must
tolerate them — these tests exercise the full executor.execute() path,
not just the handler internals.
"""

from uuid import uuid4

import pytest

from aio_agent_platform.core.config import WebSettings
from aio_agent_platform.tools.builtin import register_builtin_tools
from aio_agent_platform.tools.executor import ToolExecutor
from aio_agent_platform.tools.registry import ToolRegistry
from aio_agent_platform.tools.web.fetch import WebFetcher
from aio_agent_platform.tools.web.search import SearchRouter


class FakeConfig:
    def __init__(self, settings: WebSettings):
        self._settings = settings

    async def get(self) -> WebSettings:
        return self._settings


def _executor(enabled: bool) -> ToolExecutor:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ex = ToolExecutor(registry=registry, sandbox_mgr=None)
    config = FakeConfig(WebSettings(enabled=enabled, cache_ttl_seconds=0))
    ex.register_direct_handler("web_search", SearchRouter(config).handle)
    ex.register_direct_handler("web_fetch", WebFetcher(config).handle)
    return ex


@pytest.mark.asyncio
async def test_web_search_via_executor_disabled():
    ex = _executor(enabled=False)
    result = await ex.execute(
        "web_search",
        {"query": "test"},
        tool_call_id="tc1",
        user_id=str(uuid4()),
        session_id=str(uuid4()),
    )
    assert result.success
    assert "未启用" in result.output or "disabled" in result.output.lower()


@pytest.mark.asyncio
async def test_web_fetch_via_executor_disabled():
    ex = _executor(enabled=False)
    result = await ex.execute(
        "web_fetch",
        {"url": "https://example.com"},
        tool_call_id="tc1",
        user_id=str(uuid4()),
        session_id=str(uuid4()),
    )
    assert result.success
    assert "未启用" in result.output or "disabled" in result.output.lower()


@pytest.mark.asyncio
async def test_web_search_via_executor_missing_query():
    ex = _executor(enabled=True)
    result = await ex.execute(
        "web_search",
        {},
        tool_call_id="tc1",
        user_id=str(uuid4()),
        session_id=str(uuid4()),
    )
    assert result.success
    assert "query" in result.output


@pytest.mark.asyncio
async def test_web_fetch_via_executor_missing_url():
    ex = _executor(enabled=True)
    result = await ex.execute(
        "web_fetch",
        {},
        tool_call_id="tc1",
        user_id=str(uuid4()),
        session_id=str(uuid4()),
    )
    assert result.success
    assert "url" in result.output


@pytest.mark.asyncio
async def test_web_fetch_via_executor_ssrf_blocked():
    ex = _executor(enabled=True)
    result = await ex.execute(
        "web_fetch",
        {"url": "http://127.0.0.1/secret"},
        tool_call_id="tc1",
        user_id=str(uuid4()),
        session_id=str(uuid4()),
    )
    assert result.success
    assert "Error" in result.output
