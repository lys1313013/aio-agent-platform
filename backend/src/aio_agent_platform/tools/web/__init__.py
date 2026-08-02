"""Web tools — web_search / web_fetch handler registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aio_agent_platform.tools.web.config import web_config
from aio_agent_platform.tools.web.fetch import WebFetcher
from aio_agent_platform.tools.web.search import SearchRouter

if TYPE_CHECKING:
    from aio_agent_platform.tools.executor import ToolExecutor


def register_handlers(tool_executor: ToolExecutor) -> None:
    """Register web_search / web_fetch direct handlers.

    Always registered — the enabled flag is read from runtime config
    (DB over env) on every call, so admins can toggle without restart.
    """
    fetcher = WebFetcher(web_config)
    searcher = SearchRouter(web_config)
    tool_executor.register_direct_handler("web_search", searcher.handle)
    tool_executor.register_direct_handler("web_fetch", fetcher.handle)
