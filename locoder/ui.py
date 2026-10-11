"""All terminal rendering for the plain (--plain) REPL: the banner, streamed
tokens, tool lines, diffs and confirmation prompts. Keeping this separate
from agent.py means the agent loop never touches rich/prompt_toolkit
directly.

It follows the TUI's look (locoder/display.py): the logo and wordmark, the
mode colour as the only accent, one line per tool call, rounded boxes. On a
console that can't encode block characters (legacy cp1252 Windows consoles)
it falls back to plain ASCII.
"""

from __future__ import annotations

from typing import Any, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from locoder.display import (
    ERROR,
    FAINT,
    FG,
    ICON_DECLINED,
    ICON_ERROR,
    ICON_OK,
    LOGO,
    MUTED,
    SECONDARY,
    SEP,
    TOOL_VERBS,
    WARN,
    context_bar,
    pixels_to_text,
    tool_summary,
    tool_target,
    usage_color,
    where_it_runs,
    wordmark_bitmap,
)
from locoder.tools.base import ToolResult

ACCENT = "#4a9eff"  # Build mode's colour (agent.MODE_COLORS)
DESTRUCTIVE_MARK = "[DESTRUCTIVE - double check this]"


class UI:
    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console(highlight=False)
        self._streaming = False
        self.accent = ACCENT
        # Block characters and the mark need a Unicode-capable console.
        self.fancy = (self.console.encoding or "").lower().replace("-", "").startswith("utf")

    @property
    def prompt(self) -> str:
        return "› " if self.fancy else "> "

    # -- lifecycle / chrome -------------------------------------------------

    def banner(self, *, model: str, base_url: str, provider: str, yolo: bool, project_root: str) -> None:
        if self.fancy:
            lockup = Table.grid(padding=(0, 3))
            lockup.add_column(vertical="middle")
            lockup.add_column(vertical="middle")
            lockup.add_row(pixels_to_text(LOGO, self.accent), pixels_to_text(wordmark_bitmap("locoder"), FG))
            self.console.print(lockup)
        else:
            self.console.print(Text("locoder", style=f"bold {self.accent}"))
        self.console.print()
        writes = Text("yolo - no confirmations", style=f"bold {self.accent}") if yolo else Text(
            "asks before writes and commands", style=MUTED)
        self.console.print(Text.assemble((model, FG), (SEP, FAINT), (provider, SECONDARY), (SEP, FAINT)), writes,
                           sep="")
        if self.fancy:
            self.console.print(where_it_runs(base_url))
        self.console.print(Text(project_root, style=MUTED))
        self.console.print(Text.assemble(("/help", SECONDARY), (" for commands", MUTED)))
        self.console.print()

    def help(self) -> None:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True)
        grid.add_column()
        for cmd, what in [
            ("/help", "show this message"),
            ("/clear", "clear the conversation and start a new session"),
            ("/model <name>", "switch the active model"),
            ("/yolo", "toggle confirmation prompts on/off"),
            ("/tokens", "show context/token usage"),
            ("/exit, /quit", "leave locoder"),
        ]:
            grid.add_row(Text(cmd, style=self.accent), Text(what, style=SECONDARY))
        self.console.print(Text("commands", style=MUTED))
        self.console.print(grid)

    # -- streaming assistant output -----------------------------------------

    def stream_token(self, token: str) -> None:
        if not self._streaming:
            self.console.print()
            self.console.print(Text("locoder", style=f"bold {self.accent}"))
            self._streaming = True
        self.console.print(token, end="", highlight=False, soft_wrap=True, markup=False)

    def end_stream(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def thinking_token(self, token: str) -> None:
        pass  # the plain REPL doesn't surface the model's reasoning trace

    # -- tool lines -----------------------------------------------------------

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        # Printed once the result is in, as one line (see tool_result).
        self.end_stream()
        self._pending = (name, arguments)

    def tool_result(self, name: str, result: ToolResult) -> None:
        pending_name, arguments = getattr(self, "_pending", (name, {}))
        if pending_name != name:
            arguments = {}
        summary = tool_summary(name, arguments, result)
        if result.is_error and "declined" in result.output:
            icon, color = ICON_DECLINED, WARN
        elif result.is_error:
            icon, color = ICON_ERROR, ERROR
        else:
            icon, color = ICON_OK, self.accent
        if not self.fancy:
            icon = {ICON_OK: "*", ICON_ERROR: "x"}.get(icon, icon)
        verb = TOOL_VERBS.get(name, name)
        self.console.print(Text.assemble(
            "  ", (icon, color), " ", (f"{verb:<7}", f"bold {FG}"), (tool_target(name, arguments), SECONDARY),
            (f"  {summary}", ERROR if result.is_error and icon != ICON_DECLINED else MUTED),
        ), no_wrap=True, overflow="ellipsis")

    # -- confirmation / diffs ------------------------------------------------

    def confirm(self, title: str, preview: str, *, lang: str = "diff") -> bool:
        self.end_stream()
        destructive = DESTRUCTIVE_MARK in title
        title = title.replace(DESTRUCTIVE_MARK, "").strip()
        color = ERROR if destructive else self.accent
        body = self._render_preview(preview, lang)
        if destructive:
            body = Table.grid()
            body.add_row(Text("this command looks destructive - read it carefully", style=f"bold {ERROR}"))
            body.add_row(Text(""))
            body.add_row(self._render_preview(preview, lang))
        self.console.print(Panel(body, title=Text(f" {title} ", style=f"bold {color}"), title_align="left",
                                 border_style=color, box=box.ROUNDED if self.fancy else box.ASCII, padding=(0, 1)))
        try:
            return Confirm.ask(Text.assemble(("proceed?", FG)), default=False, console=self.console)
        except (EOFError, KeyboardInterrupt):
            return False

    def _render_preview(self, preview: str, lang: str):
        preview = preview.rstrip("\n")
        if lang == "diff":
            return Syntax(preview, "diff", theme="ansi_dark", word_wrap=True, background_color="default")
        return Text(preview, style=FG)

    # -- misc status ----------------------------------------------------------

    def info(self, msg: str) -> None:
        self.end_stream()
        self.console.print(Text(msg, style=MUTED))

    def warn(self, msg: str) -> None:
        self.end_stream()
        self.console.print(Text.assemble(("warning ", f"bold {WARN}"), (msg, SECONDARY)))

    def error(self, msg: str) -> None:
        self.end_stream()
        self.console.print(Text.assemble(("error ", f"bold {ERROR}"), (msg, SECONDARY)))

    def system(self, msg: str) -> None:
        self.console.print(Text(msg, style=self.accent))

    def token_usage(self, *, used: int, limit: int) -> None:
        filled, empty, frac = context_bar(used, limit, width=20)
        if not self.fancy:
            filled, empty = "#" * len(filled), "-" * len(empty)
        self.console.print(Text.assemble(
            (filled, usage_color(frac, self.accent)), (empty, FAINT),
            (f"  {frac * 100:.0f}%", SECONDARY), (f"{SEP}{used:,} / {limit:,} tokens", MUTED),
        ))
