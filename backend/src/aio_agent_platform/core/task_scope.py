"""Execution-local child tasks and previously completed calls for safe continuation."""
import asyncio
import json
from contextvars import ContextVar

child_tasks: ContextVar[set[asyncio.Task] | None] = ContextVar("chat_child_tasks", default=None)
completed_calls: ContextVar[dict[str, dict] | None] = ContextVar("chat_completed_calls", default=None)


def call_key(name: str, arguments: dict) -> str:
    return json.dumps([name, arguments], ensure_ascii=False, sort_keys=True)


def track_task(coroutine) -> asyncio.Task:
    task = asyncio.create_task(coroutine)
    tasks = child_tasks.get()
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return task
