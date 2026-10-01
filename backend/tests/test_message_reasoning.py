"""Regression coverage for persisted assistant reasoning."""

from aio_agent_platform.core.chat_history import _append_reasoning_chunk


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


def test_recorder_keeps_reasoning_between_parallel_tool_batches():
    from uuid import uuid4

    from aio_agent_platform.core.chat_history import ChatTurnRecorder

    turn = ChatTurnRecorder(uuid4(), uuid4())
    for event in [
        'reasoning:先搜索', 'tool_call:a:web_search:{}', 'tool_call:b:web_search:{}',
        'tool_result:b:web_search:ok:"b"', 'tool_result:a:web_search:ok:"a"',
        'reasoning:再读取', 'tool_call:c:web_fetch:{}', 'reasoning:最后总结',
    ]:
        turn.record(event)
    assert [chunk['tool_call_index'] for chunk in turn.reasoning] == [0, 2, 3]


def test_old_reasoning_order_is_restored_only_with_complete_event_evidence():
    from aio_agent_platform.core.chat_history import restore_reasoning_order

    old = [{'id': 'thinking-0', 'content': '先搜索'}, {'id': 'thinking-1', 'content': '再读取'}]
    events = [
        {'type': 'thinking', 'content': '先'}, {'type': 'thinking', 'content': '搜索'},
        {'type': 'tool_call'}, {'type': 'tool_call'},
        {'type': 'thinking', 'content': '再读取'},
    ]
    restored = restore_reasoning_order(old, events)
    assert [chunk['tool_call_index'] for chunk in restored] == [0, 2]
    assert old[0] == {'id': 'thinking-0', 'content': '先搜索'}
    assert restore_reasoning_order(old, events[:-1]) == old


def test_snapshot_retains_positions_for_interruption_and_legacy_done():
    from aio_agent_platform.core.chat_runs import snapshot_event

    snapshot = {}
    for event in [
        {'type': 'thinking', 'content': '先搜索'},
        {'type': 'tool_call', 'id': 'a', 'name': 'web_search', 'arguments': {}},
        {'type': 'thinking', 'content': '再'},
        {'type': 'file_changes', 'file_changes': []},
        {'type': 'thinking', 'content': '读取'},
    ]:
        snapshot = snapshot_event(snapshot, event)
    assert [chunk['tool_call_index'] for chunk in snapshot['reasoning']] == [0, 1]
    old_reasoning = [{key: chunk[key] for key in ('id', 'content')} for chunk in snapshot['reasoning']]
    snapshot = snapshot_event(snapshot, {'type': 'done', 'reasoning': old_reasoning})
    assert [chunk['tool_call_index'] for chunk in snapshot['reasoning']] == [0, 1]
