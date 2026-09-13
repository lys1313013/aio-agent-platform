"""Trace summaries retain the executing agent on both exit paths."""

import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from aio_agent_platform.core import agent as agent_module
from aio_agent_platform.observation.recorder import (
    ObsContext,
    get_obs_context,
    set_obs_context,
)


@pytest.mark.parametrize("failed", [False, True])
def test_trace_keeps_agent_identity(monkeypatch, failed):
    recorder = Mock()
    monkeypatch.setattr(agent_module, "get_recorder", lambda: recorder)
    monkeypatch.setattr(agent_module, "get_hook_manager", lambda: Mock())
    loop = object.__new__(agent_module.AgentLoop)
    loop.provider = SimpleNamespace(model="test-model")
    ctx = ObsContext(
        trace_id=uuid4(), session_id=uuid4(), user_id=uuid4(),
        tenant_id=uuid4(), agent_id=uuid4(),
    )
    set_obs_context(ctx)
    try:
        if failed:
            loop.finalize_trace_error()
        else:
            loop._finalize_trace(
                ctx.trace_id, time.monotonic(), ctx.session_id,
                ctx.user_id, ctx.tenant_id,
                status="completed", agent_id=ctx.agent_id,
            )
        recorder.record_trace.assert_called_once()
        fields = recorder.record_trace.call_args.kwargs
        assert fields["agent_id"] == ctx.agent_id
        assert fields["trace_id"] == ctx.trace_id
        assert fields["status"] == ("error" if failed else "completed")
        assert get_obs_context() is None
    finally:
        set_obs_context(None)
