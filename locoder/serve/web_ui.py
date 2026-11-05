"""Duck-types locoder.ui.UI's interface, but instead of drawing anything it
publishes each call as an event for the phone. ``confirm`` blocks the agent
thread until the phone answers (or the run is stopped).
"""

from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence

from locoder import desktop_notify
from locoder.display import TOOL_VERBS, tool_target
from locoder.serve.transcript import tool_entry
from locoder.tools.base import ToolResult

if TYPE_CHECKING:
    from locoder.serve.hub import Hub

DESTRUCTIVE_MARK = "[DESTRUCTIVE - double check this]"
_ids = itertools.count(1)


@dataclass
class ConfirmRequest:
    id: str
    chat: str
    title: str
    preview: str
    danger: bool
    chat_title: str = ""
    reasons: list[str] = field(default_factory=list)
    answered: threading.Event = field(default_factory=threading.Event)
    approved: bool = False

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "chat": self.chat, "chat_title": self.chat_title, "title": self.title,
                "preview": self.preview, "danger": self.danger, "reasons": self.reasons}


@dataclass
class QuestionRequest:
    """The model's ask_user question, waiting for the phone to answer it."""

    id: str
    chat: str
    question: str
    options: list[str] = field(default_factory=list)
    chat_title: str = ""
    answered: threading.Event = field(default_factory=threading.Event)
    answer: Optional[str] = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "chat": self.chat, "chat_title": self.chat_title, "question": self.question,
                "options": self.options}


class WebUI:
    can_send_files = True  # the phone app can download what send_file offers

    def __init__(self, hub: "Hub", chat_id: str) -> None:
        self.hub = hub
        self.chat_id = chat_id
        self.failed = False
        self._last_args: dict[str, dict[str, Any]] = {}

    def _emit(self, type_: str, **data: Any) -> None:
        self.hub.publish(type_, chat=self.chat_id, **data)

    def banner(self, **kwargs: Any) -> None:
        pass

    def help(self) -> None:
        pass

    def stream_token(self, token: str) -> None:
        self._emit("token", text=token)

    def thinking_token(self, token: str) -> None:
        self._emit("thinking", text=token)

    def end_stream(self) -> None:
        self._emit("end_stream")

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self._last_args[name] = arguments
        self._emit("tool_call", name=name, verb=TOOL_VERBS.get(name, name), target=tool_target(name, arguments))

    def tool_result(self, name: str, result: ToolResult) -> None:
        self._emit("tool_result", entry=tool_entry(name, self._last_args.get(name, {}), result))

    def confirm(self, title: str, preview: str, *, lang: str = "diff", reasons: Sequence[str] = ()) -> bool:
        request = ConfirmRequest(
            id=str(next(_ids)),
            chat=self.chat_id,
            title=title.replace(DESTRUCTIVE_MARK, "").strip(),
            preview=preview,
            danger=bool(reasons) or DESTRUCTIVE_MARK in title,
            reasons=list(reasons),
        )
        return self.hub.ask(request)

    def ask_user(self, question: str, options: Sequence[str] = ()) -> Optional[str]:
        return self.hub.ask_question(QuestionRequest(id=f"q{next(_ids)}", chat=self.chat_id, question=question,
                                                     options=list(options)))

    def notify_user(self, title: str, message: str) -> None:
        self._emit("notify", title=title, message=message)
        desktop_notify.notify(title, message)

    def show_todos(self, items: Sequence[dict[str, str]]) -> None:
        self._emit("todos", items=list(items))

    def info(self, msg: str) -> None:
        self._emit("note", level="info", text=msg)

    def notice(self, msg: str) -> None:
        self.info(msg)

    def system(self, msg: str) -> None:
        self.info(msg)

    def warn(self, msg: str) -> None:
        self._emit("note", level="warn", text=msg)

    def error(self, msg: str) -> None:
        self.failed = True
        self._emit("note", level="error", text=msg)

    def token_usage(self, *, used: int, limit: int) -> None:
        self._emit("usage", used=used, limit=limit)


def preview_text(text: Optional[str], limit: int = 140) -> str:
    """A notification-sized line of an answer: no code blocks or markdown."""
    plain = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    plain = re.sub(r"[*`#>_]", "", plain)
    plain = " ".join(plain.split())
    return plain if len(plain) <= limit else plain[: limit - 1] + "…"
