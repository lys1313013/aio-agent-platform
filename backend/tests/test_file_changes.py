"""Focused tests for automatic workspace file-change tracking."""

from aio_agent_platform.interface.routes.chat import _merge_file_changes
from aio_agent_platform.tools.executor import ToolExecutor


def test_workspace_diff_detects_create_modify_delete() -> None:
    before = {
        "changed.md": (10, 100),
        "deleted.txt": (4, 100),
        "same.py": (7, 100),
    }
    after = {
        "changed.md": (12, 200),
        "created.csv": (8, 200),
        "same.py": (7, 100),
    }

    changes = ToolExecutor._diff_workspace_files(before, after, "workspace-1")

    assert [(item["path"], item["action"]) for item in changes] == [
        ("changed.md", "modified"),
        ("created.csv", "created"),
        ("deleted.txt", "deleted"),
    ]
    assert all(item["workspace_id"] == "workspace-1" for item in changes)


def test_turn_merge_keeps_created_after_later_modification() -> None:
    created = [{
        "action": "created", "workspace_id": "w", "path": "report.md",
        "filename": "report.md", "mime_type": "text/markdown", "size": 10,
    }]
    modified = [{**created[0], "action": "modified", "size": 20}]

    merged = _merge_file_changes(created, modified)

    assert merged == [{**modified[0], "action": "created"}]


def test_turn_merge_removes_file_created_then_deleted() -> None:
    created = [{
        "action": "created", "workspace_id": "w", "path": "temp.txt",
        "filename": "temp.txt", "mime_type": "text/plain", "size": 1,
    }]
    deleted = [{**created[0], "action": "deleted"}]

    assert _merge_file_changes(created, deleted) == []
