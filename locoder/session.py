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
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = self.sessions_dir / f"{self.session_id}.json"

    @property
    def history_file(self) -> Path:
        return self.locoder_dir / "history"

    def start_new(self) -> None:
        self._new_session_id()

    def save(self, messages: list[Message], *, meta: Optional[dict[str, Any]] = None) -> None:
        data = {
            "session_id": self.session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "meta": meta or {},
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
        return messages

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
        for m in messages:
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
    def load(path: Path) -> list[Message]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Message.from_dict(m) for m in data.get("messages", [])]
