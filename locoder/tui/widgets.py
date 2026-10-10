"""Passive display widgets: the header bar, the scrolling chat transcript,
and the token-usage status bar. All chat content is wrapped in Rich ``Text``/
``Markdown`` objects rather than raw strings, so model or tool output that
happens to contain ``[...]`` (type hints, list indexing, etc.) can never be
misparsed as Textual/Rich markup.
"""

from __future__ import annotations

import time
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from locoder.tools.base import ToolResult

MAX_TOOL_RESULT_PREVIEW = 2_000
# ASCII-only: non-ASCII glyphs (braille spinners, etc.) have crashed on
# Windows' legacy cp1252 console encoding elsewhere in this project - keep
# every rendered frame safe for that codec.
THINKING_FRAMES = ["|", "/", "-", "\\"]
SPINNER_INTERVAL = 1 / 6
ACCENT = "#e0954f"


def _short(value: Any, limit: int = 60) -> str:
    s = value if isinstance(value, str) else repr(value)
    s = s.replace("\n", "\\n")
    return s if len(s) <= limit else s[: limit - 1] + "..."


class TopBar(Static):
    """A compact one-line model/mode reminder, docked just above the input."""

    def set_info(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        yolo: bool,
        project_root: str,
        mode_name: str,
        accent: str = ACCENT,
    ) -> None:
        confirm_mode = f"[bold {accent}]yolo[/]" if yolo else "confirm"
        self.update(f"[bold {accent}]{mode_name}[/]  [{accent}]{provider}:{model}[/]  [dim]- {confirm_mode}[/]")


class StatusBar(Static):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._usage_text = ""
        self._spinner_timer = None
        self._spinner_frame = 0
        self._busy_label = "thinking..."
        self._accent = ACCENT
        self.set_usage(used=0, limit=1)

    def set_accent(self, accent: str) -> None:
        self._accent = accent

    def set_usage(self, *, used: int, limit: int) -> None:
        pct = (used / limit * 100) if limit else 0.0
        color = "green" if pct < 70 else ("yellow" if pct < 90 else "red")
        self._usage_text = f"[{color}]{used:,} tokens[/] [dim]({pct:.0f}% of {limit:,})[/]"
        if self._spinner_timer is None:
            self.update(self._usage_text)

    def set_busy(self, busy: bool, label: str = "thinking...") -> None:
        if busy:
            self._busy_label = label
            if self._spinner_timer is None:
                self._spinner_frame = 0
                self._spinner_timer = self.set_interval(SPINNER_INTERVAL, self._tick_spinner)
                self._tick_spinner()
        elif self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
            self.update(self._usage_text)

    def _tick_spinner(self) -> None:
        frame = THINKING_FRAMES[self._spinner_frame % len(THINKING_FRAMES)]
        self._spinner_frame += 1
        self.update(f"[{self._accent}]{frame}[/] [dim]{self._busy_label} (esc to cancel)[/]")


class Sidebar(Static):
    """Right-hand panel: context/token usage and a couple of static facts."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._used = 0
        self._limit = 1
        self._model = ""
        self._provider = ""
        self._yolo = False
        self._mode_name = "Build"
        self._accent = ACCENT
        self._refresh_display()

    def set_model_info(self, *, provider: str, model: str, yolo: bool, mode_name: str, accent: str = ACCENT) -> None:
        self._provider = provider
        self._model = model
        self._yolo = yolo
        self._mode_name = mode_name
        self._accent = accent
        self._refresh_display()

    def set_usage(self, *, used: int, limit: int) -> None:
        self._used = used
        self._limit = limit
        self._refresh_display()

    def _refresh_display(self) -> None:
        pct = (self._used / self._limit * 100) if self._limit else 0.0
        color = "green" if pct < 70 else ("yellow" if pct < 90 else "red")
        confirm_mode = f"[bold {self._accent}]yolo[/]" if self._yolo else "confirm"
        lines = [
            "[bold]Session[/]",
            "",
            "[bold]Mode[/]",
            f"[bold {self._accent}]{self._mode_name}[/]  [dim](tab to cycle)[/]",
            "",
            "[bold]Model[/]",
            f"{self._provider}:{self._model}",
            f"writes: {confirm_mode}",
            "",
            "[bold]Context[/]",
            f"[{color}]{self._used:,} tokens[/]",
            f"{pct:.0f}% used",
            "",
            "[dim]/help for commands[/]",
        ]
        self.update("\n".join(lines))


class ThoughtWidget(Static):
    """A collapsed '+ Thought: 682ms' summary; click to expand the reasoning
    trace the model produced before its actual answer.
    """

    def __init__(self, *, accent: str = ACCENT, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.thought_text = ""
        self.elapsed_ms = 0
        self.expanded = False
        self._accent = accent

    def finalize(self, elapsed_ms: int) -> None:
        self.elapsed_ms = elapsed_ms
        self._refresh_display()

    def _refresh_display(self) -> None:
        prefix = "-" if self.expanded else "+"
        header = Text(f"{prefix} Thought: {self.elapsed_ms}ms", style=f"italic {self._accent}")
        if self.expanded and self.thought_text.strip():
            self.update(Group(header, Text(self.thought_text.strip(), style="dim italic")))
        else:
            self.update(header)

    def on_click(self) -> None:
        if not self.thought_text.strip():
            return
        self.expanded = not self.expanded
        self._refresh_display()


class ChatLog(VerticalScroll):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stream_header: Static | None = None
        self._stream_content: Static | None = None
        self._stream_text: str = ""
        self._thinking: bool = False
        self._thinking_timer = None
        self._thinking_frame = 0
        self._thinking_started_at: float = 0.0
        self._thinking_buffer: str = ""
        self._model_name: str = ""
        self._mode_name: str = ""
        self._thinking_label: str = "thinking..."
        self._accent: str = ACCENT

    def set_model_name(self, name: str) -> None:
        self._model_name = name

    def set_mode_name(self, name: str) -> None:
        self._mode_name = name

    def set_accent(self, accent: str) -> None:
        self._accent = accent

    def _mount_line(self, renderable: Any, classes: str = "") -> Static:
        widget = Static(renderable, classes=classes)
        self.mount(widget)
        self.call_after_refresh(self.scroll_end, animate=False)
        return widget

    def add_user_message(self, text: str) -> None:
        widget = self._mount_line(Text(text), classes="msg-user")
        widget.styles.border_left = ("thick", self._accent)

    def replay_assistant_message(self, text: str) -> None:
        """Render a past assistant message (e.g. when resuming a saved
        conversation) without the live-turn timing/footer machinery, which
        has no meaningful elapsed time to report here.
        """
        self._mount_line(Text("locoder", style=f"bold {self._accent}"), classes="msg-header")
        self._mount_line(Markdown(text), classes="msg-assistant")

    def start_thinking(self, label: str = "thinking...") -> None:
        """Show an animated placeholder while waiting for the model's next
        round of output (tokens or a tool call) to start arriving.
        """
        if self._stream_content is not None:
            return  # already showing something for this round
        self._stream_text = ""
        self._thinking = True
        self._thinking_label = label
        self._thinking_started_at = time.monotonic()
        self._thinking_buffer = ""
        self._stream_header = self._mount_line(Text("locoder", style=f"bold {self._accent}"), classes="msg-header")
        self._stream_content = self._mount_line(Text(""), classes="msg-assistant")
        self._thinking_frame = 0
        self._thinking_timer = self.set_interval(SPINNER_INTERVAL, self._tick_thinking)
        self._tick_thinking()

    def append_thinking_token(self, token: str) -> None:
        self._thinking_buffer += token

    def _finalize_thought(self) -> None:
        if not self._thinking_buffer.strip():
            self._thinking_buffer = ""
            return
        elapsed_ms = int((time.monotonic() - self._thinking_started_at) * 1000)
        widget = ThoughtWidget(classes="msg-thought", accent=self._accent)
        widget.thought_text = self._thinking_buffer
        widget.finalize(elapsed_ms)
        if self._stream_header is not None:
            self.mount(widget, before=self._stream_header)
        else:
            self.mount(widget)
        self._thinking_buffer = ""

    def _tick_thinking(self) -> None:
        frame = THINKING_FRAMES[self._thinking_frame % len(THINKING_FRAMES)]
        self._thinking_frame += 1
        if self._stream_content is not None:
            self._stream_content.update(Text(f"{frame} {self._thinking_label}", style="dim italic"))

    def _stop_thinking(self) -> None:
        if self._thinking_timer is not None:
            self._thinking_timer.stop()
            self._thinking_timer = None
        self._thinking = False

    def start_stream(self) -> None:
        self._finalize_thought()
        self._stop_thinking()
        self._stream_text = ""
        if self._stream_header is None:
            self._stream_header = self._mount_line(Text("locoder", style=f"bold {self._accent}"), classes="msg-header")
        if self._stream_content is None:
            self._stream_content = self._mount_line(Text(""), classes="msg-assistant")
        else:
            self._stream_content.update(Text(""))

    def append_stream_token(self, token: str) -> None:
        if self._thinking or self._stream_content is None:
            self.start_stream()
        self._stream_text += token
        self._stream_content.update(Text(self._stream_text))
        self.call_after_refresh(self.scroll_end, animate=False)

    def end_stream(self) -> None:
        # In case the round had thinking tokens but no content at all (went
        # straight to a tool call) - finalize the thought line either way.
        self._finalize_thought()
        elapsed_s = time.monotonic() - self._thinking_started_at
        self._stop_thinking()
        if self._stream_content is not None:
            if self._stream_text.strip():
                self._stream_content.update(Markdown(self._stream_text))
                parts = [p for p in (self._mode_name, self._model_name) if p]
                parts.append(f"{elapsed_s:.1f}s")
                self._mount_line(Text(" - ".join(parts), style="dim"), classes="msg-footer")
            else:
                # Nothing was actually said this round (e.g. the model went
                # straight to a tool call) - drop the empty placeholder
                # instead of leaving a blank "locoder" header behind.
                if self._stream_header is not None:
                    self._stream_header.remove()
                self._stream_content.remove()
        self._stream_header = None
        self._stream_content = None
        self._stream_text = ""

    def add_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        preview = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        self._mount_line(Text(f"-> {name}({preview})", style="dim"), classes="msg-tool")

    def add_tool_result(self, name: str, result: ToolResult) -> None:
        text = result.output
        if len(text) > MAX_TOOL_RESULT_PREVIEW:
            text = text[:MAX_TOOL_RESULT_PREVIEW] + "... [truncated]"
        style = "red" if result.is_error else "dim"
        prefix = "x" if result.is_error else "ok"
        self._mount_line(Text(f"{prefix} {name}: {text}", style=style), classes="msg-tool")

    def add_info(self, msg: str) -> None:
        self._mount_line(Text(msg, style="dim"), classes="msg-status")

    def add_warn(self, msg: str) -> None:
        self._mount_line(Text(f"warning: {msg}", style="yellow"), classes="msg-status")

    def add_error(self, msg: str) -> None:
        self._mount_line(Text(f"error: {msg}", style="bold red"), classes="msg-status")

    def add_system(self, msg: str) -> None:
        self._mount_line(Text(msg, style=self._accent), classes="msg-status")
