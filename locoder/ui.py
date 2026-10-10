"""All terminal rendering lives here: banners, streamed tokens, tool call
call-outs, diffs, and confirmation prompts. Keeping this separate from
agent.py means the agent loop never touches rich/prompt_toolkit directly.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.text import Text

from locoder.tools.base import ToolResult

MAX_TOOL_RESULT_PREVIEW = 2_000


class UI:
    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console()
        self._streaming = False

    # -- lifecycle / chrome -------------------------------------------------

    def banner(self, *, model: str, base_url: str, provider: str, yolo: bool, project_root: str) -> None:
        mode = "[bold red]YOLO (no confirmations)[/]" if yolo else "[green]confirm before write/run[/]"
        self.console.print(
            Panel.fit(
                f"[bold cyan]locoder[/]\n"
                f"provider: [bold]{provider}[/]  model: [bold]{model}[/]  backend: {base_url}\n"
                f"project: {project_root}\n"
                f"mode: {mode}",
                border_style="cyan",
            )
        )
        self.console.print("Type your request, or [bold]/help[/] for slash commands.\n")

    def help(self) -> None:
        self.console.print(
            "\n".join(
                [
                    "[bold]Slash commands[/]",
                    "  /help            show this message",
                    "  /clear           clear the conversation and start a new session",
                    "  /model <name>    switch the active model",
                    "  /yolo            toggle confirmation prompts on/off",
                    "  /tokens          show context/token usage",
                    "  /exit, /quit     leave locoder",
                ]
            )
        )

    # -- streaming assistant output -----------------------------------------

    def stream_token(self, token: str) -> None:
        if not self._streaming:
            self.console.print("[bold cyan]locoder[/] ", end="")
            self._streaming = True
        self.console.print(token, end="", highlight=False, soft_wrap=True)

    def end_stream(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def thinking_token(self, token: str) -> None:
        pass  # the plain REPL doesn't surface the model's reasoning trace

    # -- tool call-outs -------------------------------------------------------

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self.end_stream()
        args_preview = ", ".join(f"{k}={_short(v)}" for k, v in arguments.items())
        self.console.print(f"[dim]-> {name}({args_preview})[/]")

    def tool_result(self, name: str, result: ToolResult) -> None:
        text = result.output
        truncated = len(text) > MAX_TOOL_RESULT_PREVIEW
        preview = text[:MAX_TOOL_RESULT_PREVIEW] + ("... [truncated]" if truncated else "")
        style = "red" if result.is_error else "dim"
        prefix = "x" if result.is_error else "ok"
        self.console.print(f"[{style}]{prefix} {name}: {preview}[/]")

    # -- confirmation / diffs ------------------------------------------------

    def confirm(self, title: str, preview: str, *, lang: str = "diff") -> bool:
        self.end_stream()
        self.console.print(Panel(self._render_preview(preview, lang), title=title, border_style="yellow"))
        try:
            return Confirm.ask("Proceed?", default=False)
        except (EOFError, KeyboardInterrupt):
            return False

    def _render_preview(self, preview: str, lang: str):
        if lang == "diff":
            return Syntax(preview, "diff", theme="ansi_dark", word_wrap=True)
        return Text(preview)

    # -- misc status ----------------------------------------------------------

    def info(self, msg: str) -> None:
        self.end_stream()
        self.console.print(f"[dim]{msg}[/]")

    def warn(self, msg: str) -> None:
        self.end_stream()
        self.console.print(f"[yellow]warning:[/] {msg}")

    def error(self, msg: str) -> None:
        self.end_stream()
        self.console.print(f"[bold red]error:[/] {msg}")

    def system(self, msg: str) -> None:
        self.console.print(f"[magenta]{msg}[/]")

    def token_usage(self, *, used: int, limit: int) -> None:
        pct = (used / limit * 100) if limit else 0
        color = "green" if pct < 70 else ("yellow" if pct < 90 else "red")
        self.console.print(f"[{color}]tokens: ~{used:,} / {limit:,} ({pct:.0f}%)[/]")


def _short(value: Any, limit: int = 60) -> str:
    s = value if isinstance(value, str) else json.dumps(value)
    s = s.replace("\n", "\\n")
    return s if len(s) <= limit else s[: limit - 1] + "..."
