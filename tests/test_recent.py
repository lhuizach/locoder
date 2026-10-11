from __future__ import annotations

import os
import time
from pathlib import Path

from locoder.providers.base import Message
from locoder.recent import (
    record_project,
    recent_conversations,
    recent_files,
    recent_projects,
    relative_time,
)
from locoder.session import SessionStore


def test_relative_time():
    now = 1_000_000.0
    assert relative_time(now - 5, now) == "just now"
    assert relative_time(now - 300, now) == "5m ago"
    assert relative_time(now - 3 * 3600, now) == "3h ago"
    assert relative_time(now - 30 * 3600, now) == "yesterday"
    assert relative_time(now - 4 * 86400, now) == "4d ago"


def test_recent_files_newest_first_and_skips_ignored_dirs(tmp_path: Path):
    for i, name in enumerate(["old.py", "pkg/mid.py", "new.md"]):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        os.utime(path, (time.time() - 1000 + i * 100,) * 2)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "newest.py").write_text("x")

    labels = [item.label for item in recent_files(tmp_path, limit=5)]

    assert labels == ["new.md", "pkg/mid.py", "old.py"]


def test_recent_conversations_skip_empty_ones(tmp_path: Path):
    store = SessionStore(tmp_path)
    store.save([Message(role="system", content="prompt"), Message(role="user", content="fix the tests")])
    store.start_new()
    store.save([Message(role="system", content="prompt")])

    items = recent_conversations(store)

    assert [i.label for i in items] == ["fix the tests"]
    assert "2 messages" in items[0].detail


def test_recent_projects_most_recent_first_excluding_current(tmp_path: Path):
    store = tmp_path / "recent.json"
    a, b, gone = tmp_path / "a", tmp_path / "b", tmp_path / "gone"
    a.mkdir()
    b.mkdir()
    gone.mkdir()
    for project in (gone, a, b):
        record_project(project, store)
    gone.rmdir()

    labels = [Path(i.value).name for i in recent_projects(b, store=store)]

    assert labels == ["a"]  # b is current, gone no longer exists
    record_project(a, store)
    assert [Path(i.value).name for i in recent_projects(tmp_path, store=store)] == ["a", "b"]
