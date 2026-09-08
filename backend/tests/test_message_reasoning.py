"""Regression coverage for persisted assistant reasoning."""

from aio_agent_platform.interface.routes.chat import _append_reasoning_chunk


def test_reasoning_chunks_keep_iteration_order() -> None:
    chunks: list[dict] = []

    _append_reasoning_chunk(chunks, "先查询数据")
    _append_reasoning_chunk(chunks, "再核对结果")

    assert chunks == [
        {"id": "thinking-0", "content": "先查询数据"},
        {"id": "thinking-1", "content": "再核对结果"},
    ]


def test_empty_reasoning_chunk_is_ignored() -> None:
    chunks: list[dict] = []

    _append_reasoning_chunk(chunks, "")

    assert chunks == []
