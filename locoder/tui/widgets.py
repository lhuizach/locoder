"""Passive display widgets: the scrolling chat transcript, the sidebar and
the bottom status bar. All chat content is wrapped in Rich ``Text``/
``Markdown`` objects rather than raw strings, so model or tool output that
happens to contain ``[...]`` (type hints, list indexing, etc.) can never be
misparsed as Textual/Rich markup.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from locoder.display import (
    FAINT,
    FG,
    ICON_DECLINED,
    ICON_ERROR,
    ICON_OK,
    ICON_PENDING,
    MARK,
    MUTED,
    SECONDARY,
    SEP,
    TOOL_VERBS,
    context_bar,
    tool_summary,
    tool_target,
    usage_color,
)
from locoder.tools.base import ToolResult

MAX_TOOL_RESULT_PREVIEW = 2_000
# ASCII-only: non-ASCII glyphs (braille spinners, etc.) have crashed on
# Windows' legacy cp1252 console encoding elsewhere in this project - keep
# every rendered frame safe for that codec.
THINKING_FRAMES = ["|", "/", "-", "\\"]
SPINNER_INTERVAL = 1 / 6
ACCENT = "#4a9eff"


def _home_relative(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.lower().startswith(home.lower()) else path


class StatusBar(Static):
    """Bottom line: the project folder (or what's running) on the left,
    context usage on the right - the chat view's version of the home footer.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._project = ""
        self._used = 0
        self._limit = 1
        self._spinner_timer = None
        self._spinner_frame = 0
        self._busy_label = "thinking..."
        self._accent = ACCENT

    def set_accent(self, accent: str) -> None:
        self._accent = accent
        self._redraw()

    def set_project(self, path: str) -> None:
        self._project = _home_relative(path)
        self._redraw()

    def set_usage(self, *, used: int, limit: int) -> None:
        self._used, self._limit = used, limit
        self._redraw()

    def set_busy(self, busy: bool, label: str = "thinking...") -> None:
        if busy:
            self._busy_label = label
            if self._spinner_timer is None:
                self._spinner_frame = 0
                self._spinner_timer = self.set_interval(SPINNER_INTERVAL, self._redraw)
        elif self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._redraw()

    def _redraw(self) -> None:
        if self._spinner_timer is not None:
            frame = THINKING_FRAMES[self._spinner_frame % len(THINKING_FRAMES)]
            self._spinner_frame += 1
            # Text, not markup: a "/" or "\" frame would be parsed as a closing tag.
            left = Text.assemble((frame, self._accent), " ", (self._busy_label, SECONDARY),
                                 (SEP + "esc to cancel", MUTED))
        else:
            left = Text(self._project, style=MUTED)
        filled, empty, frac = context_bar(self._used, self._limit, width=10)
        right = Text.assemble(
            (filled, usage_color(frac, self._accent)), (empty, FAINT),
            (f"  {frac * 100:.0f}%", SECONDARY), (f"{SEP}{self._used:,} tokens", MUTED),
        )
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(left, right)
        self.update(grid)


class Sidebar(Static):
    """Right-hand panel (a rounded outline titled "session" in app.tcss):
    mode, model and context usage.
    """

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

    def on_mount(self) -> None:
        self.border_title = Text(" session ", style=MUTED)

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
        filled, empty, frac = context_bar(self._used, self._limit, width=24)
        writes = Text("yolo - no confirmations", style=f"bold {self._accent}") if self._yolo else Text(
            "asks before writes and commands", style=MUTED)
        blocks = [
            Text.assemble((MARK + " ", self._accent), ("locoder", f"bold {FG}")),
            Text(""),
            Text("mode", style=MUTED),
            Text.assemble((self._mode_name, f"bold {self._accent}"), ("  tab to switch", MUTED)),
            Text(""),
            Text("model", style=MUTED),
            Text(self._model, style=FG),
            Text.assemble((self._provider, SECONDARY)),
            writes,
            Text(""),
            Text("context", style=MUTED),
            Text.assemble((filled, usage_color(frac, self._accent)), (empty, FAINT)),
            Text.assemble((f"{self._used:,}", FG), (f" / {self._limit:,}", MUTED), (f"{SEP}{frac * 100:.0f}%", SECONDARY)),
            Text(""),
            Text.assemble(("/help", SECONDARY), ("  commands", MUTED)),
        ]
        self.update(Group(*blocks))


class ThoughtLine(Static):
    """'Thought for 17.8s': every reasoning trace from one turn, collapsed
    into a single line. Click to read it.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.thought_text = ""
        self.seconds = 0.0
        self.expanded = False

    def add(self, text: str, seconds: float) -> None:
        text = text.strip()
        if text:
            self.thought_text = f"{self.thought_text}\n\n{text}" if self.thought_text else text
        self.seconds += seconds
        self.display = bool(self.thought_text)
        self._refresh_line()

    def _refresh_line(self) -> None:
        header = Text(f"Thought for {self.seconds:.1f}s", style="italic")
        if self.expanded:
            self.update(Group(header, Padding(Text(self.thought_text, style="italic"), (0, 0, 0, 2))))
        else:
            self.update(header)

    def on_click(self) -> None:
        self.expanded = not self.expanded
        self._refresh_line()


class ToolLine(Static):
    """One tool call as a single line - icon, verb, target, outcome. The
    output is hidden; click to show it.
    """

    def __init__(self, name: str, arguments: dict[str, Any], accent: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_name = name
        self.arguments = arguments
        self.result: ToolResult | None = None
        self.expanded = False
        self._accent = accent
        self._refresh_line()

    def set_result(self, result: ToolResult) -> None:
        self.result = result
        self._refresh_line()

    def _refresh_line(self) -> None:
        verb = TOOL_VERBS.get(self.tool_name, self.tool_name)
        if self.result is None:
            icon, icon_style, summary = ICON_PENDING, "dim", ""
        elif self.result.is_error and "declined" in self.result.output:
            icon, icon_style = ICON_DECLINED, "yellow"
            summary = tool_summary(self.tool_name, self.arguments, self.result)
        elif self.result.is_error:
            icon, icon_style = ICON_ERROR, "red"
            summary = tool_summary(self.tool_name, self.arguments, self.result)
        else:
            icon, icon_style = ICON_OK, self._accent
            summary = tool_summary(self.tool_name, self.arguments, self.result)
        line = Text.assemble(
            (icon, icon_style),
            " ",
            (f"{verb:<7}", "bold"),
            (tool_target(self.tool_name, self.arguments), SECONDARY),
            (f"  {summary}" if summary else "", "red" if icon == ICON_ERROR else "dim"),
        )
        line.no_wrap = True
        line.overflow = "ellipsis"
        if self.expanded and self.result is not None:
            output = self.result.output
            if len(output) > MAX_TOOL_RESULT_PREVIEW:
                output = output[:MAX_TOOL_RESULT_PREVIEW] + "\n... [truncated]"
            self.update(Group(line, Padding(Text(output, style="dim"), (0, 0, 1, 2))))
        else:
            self.update(line)

    def on_click(self) -> None:
        if self.result is None:
            return
        self.expanded = not self.expanded
        # Collapsed lines are cut off with "..."; the expanded output wraps.
        self.set_class(self.expanded, "expanded")
        self._refresh_line()


class ChatLog(VerticalScroll):
    """The transcript. Each assistant turn is one block:

        locoder
        Thought for 17.8s              (collapsed reasoning, all rounds)
        * Read  path  171 lines        (one line per tool call)
          narration between tools      (dimmed)
        The final answer.              (full brightness)
        model - 24.3s - 4 tools

    Everything is mounted flat; a live status line stays at the bottom of the
    turn while it runs and new blocks are inserted above it.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model_name = ""
        self._mode_name = ""
        self._accent = ACCENT
        # Current turn (None between turns).
        self._in_turn = False
        self._turn_started = 0.0
        self._thought: ThoughtLine | None = None
        self._live: Static | None = None
        self._live_timer = None
        self._live_label = ""
        self._live_since = 0.0
        self._live_frame = 0
        self._tools: list[ToolLine] = []
        self._infos: set[str] = set()
        self._notices: set[str] = set()
        # Current round of model output.
        self._round_started = 0.0
        self._round_thinking = ""
        self._round_thinking_closed = False
        self._text_widget: Static | None = None
        self._text = ""
        self._last_text: tuple[Static, str] | None = None

    def on_mount(self) -> None:
        # Stay pinned to the newest output, unless the user scrolls up to read.
        self.anchor()

    def set_model_name(self, name: str) -> None:
        self._model_name = name

    def set_mode_name(self, name: str) -> None:
        self._mode_name = name

    def set_accent(self, accent: str) -> None:
        self._accent = accent

    # -- mounting -------------------------------------------------------------

    def _add(self, widget: Static) -> Static:
        """Mount inside the current turn (above its live line) or at the end."""
        if self._live is not None and self._live.parent is self:
            self.mount(widget, before=self._live)
        else:
            self.mount(widget)
        return widget

    def _mount_line(self, renderable: Any, classes: str = "") -> Static:
        return self._add(Static(renderable, classes=classes))

    def add_user_message(self, text: str) -> None:
        # A rounded outline titled "you", in the same family as the prompt box.
        widget = Static(Text(text, style=FG), classes="msg-user")
        widget.border_title = Text(" you ", style=MUTED)
        self.mount(widget)
        self.scroll_end(animate=False)
        self.anchor()

    # -- turns ----------------------------------------------------------------

    def begin_turn(self, *, live: bool = True) -> None:
        if self._in_turn:
            return
        self._in_turn = True
        self._turn_started = time.monotonic()
        self._tools = []
        self._infos = set()
        self._last_text = None
        self.mount(Static(Text.assemble((MARK + " ", self._accent), ("locoder", f"bold {self._accent}")),
                          classes="turn-header"))
        self._thought = ThoughtLine(classes="turn-thought")
        self._thought.display = False
        self.mount(self._thought)
        if live:
            self._live = Static("", classes="turn-live")
            self.mount(self._live)
            self._live_timer = self.set_interval(SPINNER_INTERVAL, self._tick_live)

    def finish_turn(self, *, footer: str | None = None) -> None:
        """End the current turn: drop the live line and add the footer."""
        if not self._in_turn:
            return
        self._close_round()
        if self._live_timer is not None:
            self._live_timer.stop()
            self._live_timer = None
        if self._live is not None:
            self._live.remove()
            self._live = None
        if footer is None:
            parts = [p for p in (self._model_name,) if p]
            parts.append(f"{time.monotonic() - self._turn_started:.1f}s")
            if self._tools:
                parts.append(f"{len(self._tools)} tool{'s' if len(self._tools) != 1 else ''}")
            footer = SEP.join(parts)
        if footer:
            self.mount(Static(Text(footer), classes="turn-footer"))
        self._in_turn = False
        self._thought = None

    def _set_live(self, label: str) -> None:
        if label != self._live_label:
            self._live_label = label
            self._live_since = time.monotonic()
        if self._live is not None:
            self._live.display = True
            self._tick_live()

    def _hide_live(self) -> None:
        if self._live is not None:
            self._live.display = False

    def _tick_live(self) -> None:
        if self._live is None or not self._live.display:
            return
        frame = THINKING_FRAMES[self._live_frame % len(THINKING_FRAMES)]
        self._live_frame += 1
        elapsed = time.monotonic() - self._live_since
        self._live.update(Text(f"{frame} {self._live_label} {elapsed:.0f}s", style="dim italic"))

    # -- the agent's UI calls (via TuiUI) ------------------------------------

    def start_thinking(self, label: str = "Thinking") -> None:
        """The model is about to produce its next round of output."""
        self.begin_turn()
        self._round_started = time.monotonic()
        self._round_thinking = ""
        self._round_thinking_closed = False
        self._set_live(label.rstrip(". ").capitalize())

    def append_thinking_token(self, token: str) -> None:
        self._round_thinking += token

    def _close_thinking(self) -> None:
        """The round's reasoning is over (text or a tool call started)."""
        if self._round_thinking_closed:
            return
        self._round_thinking_closed = True
        if self._round_thinking.strip() and self._thought is not None:
            self._thought.add(self._round_thinking, time.monotonic() - self._round_started)
        self._round_thinking = ""

    def append_stream_token(self, token: str) -> None:
        self.begin_turn()
        self._close_thinking()
        if self._text_widget is None:
            self._text = ""
            self._text_widget = self._mount_line(Text(""), classes="turn-text")
            self._hide_live()
        self._text += token
        self._text_widget.update(Text(self._text))

    def end_stream(self) -> None:
        self._close_round()

    def _close_round(self) -> None:
        self._close_thinking()
        if self._text_widget is not None:
            if self._text.strip():
                self._text_widget.update(Markdown(self._text))
                self._last_text = (self._text_widget, self._text)
            else:
                self._text_widget.remove()
        self._text_widget = None
        self._text = ""

    def add_tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self.begin_turn()
        self._close_round()
        # Text that led into a tool call is narration, not the answer: dim it.
        if self._last_text is not None:
            widget, text = self._last_text
            widget.update(Markdown(text, style="dim"))
            widget.add_class("narration")
            self._last_text = None
        line = ToolLine(name, arguments, self._accent, classes="turn-tool")
        self._tools.append(line)
        self._add(line)
        self._set_live(f"{TOOL_VERBS.get(name, name)} {tool_target(name, arguments)}".strip())

    def add_tool_result(self, name: str, result: ToolResult) -> None:
        for line in reversed(self._tools):
            if line.tool_name == name and line.result is None:
                line.set_result(result)
                return

    def add_info(self, msg: str) -> None:
        # Tools can repeat the same notice every call (e.g. "ripgrep not found").
        if self._in_turn:
            if msg in self._infos:
                return
            self._infos.add(msg)
        self._mount_line(Text(msg, style="dim"), classes="msg-status")

    def add_notice(self, msg: str) -> None:
        """A tool's side remark (e.g. "ripgrep not found"): shown once per session."""
        if msg not in self._notices:
            self._notices.add(msg)
            self._mount_line(Text(msg, style="dim"), classes="msg-status")

    def add_warn(self, msg: str) -> None:
        self._mount_line(Text(f"warning: {msg}", style="yellow"), classes="msg-status")

    def add_error(self, msg: str) -> None:
        self._mount_line(Text(f"error: {msg}", style="bold red"), classes="msg-status")

    def add_system(self, msg: str) -> None:
        self._mount_line(Text(msg, style=self._accent), classes="msg-status")

    # -- replaying a saved conversation ---------------------------------------

    def replay_text(self, text: str, *, narration: bool) -> None:
        self.begin_turn(live=False)
        widget = self._mount_line(Markdown(text, style="dim" if narration else ""), classes="turn-text")
        if narration:
            widget.add_class("narration")

    def replay_tool(self, name: str, arguments: dict[str, Any], result: ToolResult | None) -> None:
        self.begin_turn(live=False)
        line = ToolLine(name, arguments, self._accent, classes="turn-tool")
        if result is not None:
            line.set_result(result)
        self._tools.append(line)
        self._add(line)
