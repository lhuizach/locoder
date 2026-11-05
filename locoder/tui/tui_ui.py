"""Duck-types locoder.ui.UI's interface, but renders into Textual widgets
instead of a Rich console.

Agent.run_turn() always executes inside a worker thread (see LocoderApp),
so every method here marshals back onto the app's event loop via
``call_from_thread`` before touching any widget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Sequence

from locoder import desktop_notify
from locoder.tools.base import ToolResult

if TYPE_CHECKING:
    from locoder.tui.app import LocoderApp


class TuiUI:
    def __init__(self, app: "LocoderApp") -> None:
        self.app = app

    def banner(self, **kwargs: Any) -> None:
        pass  # the prompt box's border already shows the mode and model

    def help(self) -> None:
        pass  # the TUI opens HelpModal directly instead of routing through ui.help()

    def stream_token(self, token: str) -> None:
        self.app.call_from_thread(self.app.chat_log.append_stream_token, token)

    def thinking_token(self, token: str) -> None:
        self.app.call_from_thread(self.app.chat_log.append_thinking_token, token)

    def end_stream(self) -> None:
        self.app.call_from_thread(self.app.chat_log.end_stream)

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self.app.call_from_thread(self.app.chat_log.add_tool_call, name, arguments)

    def tool_result(self, name: str, result: ToolResult) -> None:
        def _update() -> None:
            self.app.chat_log.add_tool_result(name, result)
            # A tool result is always followed by another round of model
            # output (more text, or another tool call) - re-arm the
            # thinking placeholder so there's no dead gap in the log.
            self.app.chat_log.start_thinking()

        self.app.call_from_thread(_update)

    def confirm(self, title: str, preview: str, *, lang: str = "diff", reasons: Sequence[str] = ()) -> bool:
        return self.app.call_from_thread(self.app.request_confirm, title, preview, lang, list(reasons))

    def ask_user(self, question: str, options: Sequence[str] = ()) -> Optional[str]:
        return self.app.call_from_thread(self.app.request_answer, question, list(options))

    def notify_user(self, title: str, message: str) -> None:
        self.app.call_from_thread(self.app.show_notification, title, message)
        desktop_notify.notify(title, message)

    def show_todos(self, items: Sequence[dict[str, str]]) -> None:
        self.app.call_from_thread(self.app.sidebar.set_todos, list(items))

    def info(self, msg: str) -> None:
        self.app.call_from_thread(self.app.chat_log.add_info, msg)

    def notice(self, msg: str) -> None:
        """For tool notifications, which can repeat on every call."""
        self.app.call_from_thread(self.app.chat_log.add_notice, msg)

    def warn(self, msg: str) -> None:
        self.app.call_from_thread(self.app.chat_log.add_warn, msg)

    def error(self, msg: str) -> None:
        self.app.call_from_thread(self.app.chat_log.add_error, msg)

    def system(self, msg: str) -> None:
        self.app.call_from_thread(self.app.chat_log.add_system, msg)

    def token_usage(self, *, used: int, limit: int) -> None:
        self.app.call_from_thread(self.app.status_bar.set_usage, used=used, limit=limit)
