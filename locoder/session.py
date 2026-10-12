"""Per-project persistence: `.locoder/sessions/*.json` conversation logs and
a `.locoder/history` file for the REPL's input history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from locoder.providers.base import Message

TITLE_PREVIEW_LEN = 60


@dataclass
class SessionSummary:
    path: Path
    session_id: str
    updated_at: str
    title: str
    message_count: int


class SessionStore:
    def __init__(self, project_root: Path) -> None:
        self.locoder_dir = project_root / ".locoder"
        self.sessions_dir = self.locoder_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._new_session_id()

    def _new_session_id(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        # The clock can tick slower than chats get started (Windows), so make
        # sure a new id never lands on an existing or the current chat.
        session_id, n = stamp, 1
        while (self.sessions_dir / f"{session_id}.json").exists() or session_id == getattr(self, "session_id", None):
            session_id, n = f"{stamp}-{n}", n + 1
        self.session_id = session_id
        self.path = self.sessions_dir / f"{self.session_id}.json"
        # Kept across saves: a title the user gave the chat, for one.
        self.meta: dict[str, Any] = {}

    @property
    def history_file(self) -> Path:
        return self.locoder_dir / "history"

    def start_new(self) -> None:
        self._new_session_id()

    def save(self, messages: list[Message], *, meta: Optional[dict[str, Any]] = None) -> None:
        data = {
            "session_id": self.session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "meta": {**self.meta, **(meta or {})},
            "messages": [m.to_dict() for m in messages],
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_sessions(self) -> list[Path]:
        return sorted(self.sessions_dir.glob("*.json"))

    def latest_session(self) -> Optional[Path]:
        sessions = self.list_sessions()
        return sessions[-1] if sessions else None

    def resume(self, path: Path) -> list[Message]:
        """Make ``path`` the active session, so future saves overwrite it."""
        messages = self.load(path)
        self.session_id = path.stem
        self.path = path
        self.meta = self.load_meta(path)
        return messages

    def chat_path(self, session_id: str) -> Optional[Path]:
        """The file for ``session_id``, if it exists (ids come from outside, so
        anything that isn't a plain file name in sessions/ is refused)."""
        if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
            return None
        path = self.sessions_dir / f"{session_id}.json"
        return path if path.is_file() else None

    def rename(self, path: Path, title: str) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("meta", {})["title"] = title
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if path == self.path:
            self.meta["title"] = title

    def delete(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def summarize_all(self) -> list[SessionSummary]:
        summaries = [self._summarize(path) for path in self.list_sessions()]
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
        return summaries

    @staticmethod
    def _summarize(path: Path) -> SessionSummary:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return SessionSummary(path=path, session_id=path.stem, updated_at="", title="(unreadable session)", message_count=0)

        messages = data.get("messages", [])
        title = "(empty conversation)"
        custom = (data.get("meta") or {}).get("title")
        custom = custom.strip() if isinstance(custom, str) else ""
        if custom:
            title = custom
        for m in [] if custom else messages:
            if m.get("role") == "user" and m.get("content", "").strip():
                text = m["content"].strip().replace("\n", " ")
                title = text[:TITLE_PREVIEW_LEN] + ("..." if len(text) > TITLE_PREVIEW_LEN else "")
                break

        return SessionSummary(
            path=path,
            session_id=data.get("session_id", path.stem),
            updated_at=data.get("updated_at", ""),
            title=title,
            message_count=len(messages),
        )

    @staticmethod
    def load_meta(path: Path) -> dict[str, Any]:
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta")
        except (json.JSONDecodeError, OSError):
            return {}
        return meta if isinstance(meta, dict) else {}

    @staticmethod
    def load(path: Path) -> list[Message]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Message.from_dict(m) for m in data.get("messages", [])]
