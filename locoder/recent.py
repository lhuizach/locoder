"""What the home screen offers to pick up again: recent conversations in
this project, recently edited files, and other project folders locoder has
been run in (kept in ``~/.locoder/recent_projects.json``).
"""

from __future__ import annotations

import heapq
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from locoder.sandbox import IGNORED_DIRS
from locoder.session import SessionStore

RECENT_PROJECTS_FILE = Path.home() / ".locoder" / "recent_projects.json"
MAX_RECENT_PROJECTS = 20
# Bound on how many files the "recently edited" scan looks at.
MAX_FILE_SCAN = 5_000


@dataclass(frozen=True)
class RecentItem:
    kind: str  # "conversation", "file" or "project"
    label: str
    detail: str
    value: str  # session path, project-relative file path, or project folder


def relative_time(timestamp: float, now: Optional[float] = None) -> str:
    seconds = max(0, (now or time.time()) - timestamp)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    days = int(seconds // 86400)
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _home_relative(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.lower().startswith(home.lower()) else path


def recent_conversations(session: SessionStore, limit: int = 6) -> list[RecentItem]:
    items = []
    for s in session.summarize_all():
        if not s.message_count or s.title == "(empty conversation)":
            continue
        try:
            when = relative_time(datetime.fromisoformat(s.updated_at).timestamp())
        except ValueError:
            when = "?"
        items.append(RecentItem("conversation", s.title, f"{when} · {s.message_count} messages", str(s.path)))
        if len(items) >= limit:
            break
    return items


def recent_files(root: Path, limit: int = 6) -> list[RecentItem]:
    found: list[tuple[float, str]] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for name in filenames:
            scanned += 1
            path = Path(dirpath) / name
            try:
                found.append((path.stat().st_mtime, path.relative_to(root).as_posix()))
            except (OSError, ValueError):
                continue
        if scanned >= MAX_FILE_SCAN:
            break
    return [RecentItem("file", rel, f"edited {relative_time(mtime)}", rel)
            for mtime, rel in heapq.nlargest(limit, found)]


def _load_projects(store: Path) -> list[dict]:
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data if isinstance(p, dict) and isinstance(p.get("path"), str)] if isinstance(data, list) else []


def record_project(root: Path, store: Path = RECENT_PROJECTS_FILE) -> None:
    """Remember that locoder was opened in ``root`` (most recent first)."""
    path = str(root.resolve())
    projects = [p for p in _load_projects(store) if p["path"].lower() != path.lower()]
    projects.insert(0, {"path": path, "opened_at": time.time()})
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps(projects[:MAX_RECENT_PROJECTS], indent=2), encoding="utf-8")
    except OSError:
        pass  # a missing history is not worth failing startup over


def recent_projects(current: Path, limit: int = 6, store: Path = RECENT_PROJECTS_FILE) -> list[RecentItem]:
    """Other folders locoder was opened in, that still exist."""
    current_path = str(current.resolve()).lower()
    items = []
    for p in _load_projects(store):
        if p["path"].lower() == current_path or not Path(p["path"]).is_dir():
            continue
        opened = p.get("opened_at")
        detail = f"opened {relative_time(opened)}" if isinstance(opened, (int, float)) else ""
        items.append(RecentItem("project", _home_relative(p["path"]), detail, p["path"]))
        if len(items) >= limit:
            break
    return items
