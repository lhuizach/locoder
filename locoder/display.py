"""Shared look and wording for locoder's terminal UIs (the Textual TUI and
the plain --plain REPL): the palette, the logo mark, and how a tool call is
summarised in one line.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from rich.text import Text

from locoder.tools.base import ToolResult

# Palette (dark only). The accent is the current mode's colour (agent.MODE_COLORS).
BG = "#0d0d0d"
FG = "#e8e8e8"
SECONDARY = "#a8a8a8"
MUTED = "#6a6a6a"
FAINT = "#3a3a3a"
BORDER = "#2a2a2a"
OK = "#5fb85f"
WARN = "#d7a84a"
ERROR = "#e5534b"

# The loading animation: a three-square snake running clockwise around a
# 4 x 2 loop of quarter-cell squares, drawn two characters wide.
_SNAKE_PATH = [(0, 0), (1, 0), (2, 0), (3, 0), (3, 1), (2, 1), (1, 1), (0, 1)]
_SNAKE_LENGTH = 3
# Quarter blocks indexed by bits: 1 top-left, 2 top-right, 4 bottom-left, 8 bottom-right.
_QUADRANTS = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"
SNAKE_FRAMES = len(_SNAKE_PATH)


def snake_frame(i: int) -> str:
    """Frame ``i`` of the loading animation (wraps around)."""
    cells = [0, 0]
    head = i % len(_SNAKE_PATH)
    for k in range(_SNAKE_LENGTH):
        x, y = _SNAKE_PATH[(head - k) % len(_SNAKE_PATH)]
        cells[x // 2] |= 1 << (y * 2 + x % 2)
    return _QUADRANTS[cells[0]] + _QUADRANTS[cells[1]]


def _short(value: Any, limit: int = 60) -> str:
    s = value if isinstance(value, str) else repr(value)
    s = s.replace("\n", "\\n")
    return s if len(s) <= limit else s[: limit - 1] + "..."


def context_bar(used: int, limit: int, width: int = 16) -> tuple[str, str, float]:
    """(filled, empty, fraction) segments of a context-usage bar."""
    frac = min(1.0, used / limit) if limit else 0.0
    filled = round(frac * width)
    if used and not filled:
        filled = 1
    return "━" * filled, "━" * (width - filled), frac


def usage_color(frac: float, accent: str) -> str:
    return accent if frac < 0.7 else (WARN if frac < 0.9 else ERROR)


TOOL_VERBS = {
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "list_dir": "List",
    "search_files": "Search",
    "run_command": "Run",
    "send_file": "Send",
    "find_files": "Find",
    "move_path": "Move",
    "delete_path": "Delete",
    "start_process": "Start",
    "read_process": "Output",
    "stop_process": "Stop",
    "update_todos": "Plan",
    "ask_user": "Ask",
    "notify_user": "Notify",
    "web_search": "Search",
    "web_fetch": "Fetch",
}

# All cp1252-safe.
ICON_PENDING = "·"  # middle dot
ICON_OK = "•"  # bullet
ICON_ERROR = "×"  # multiplication sign
ICON_DECLINED = "!"
SEP = " · "


def tool_target(name: str, args: dict[str, Any]) -> str:
    """The one thing a tool call is about: a path, a command, a pattern."""
    if name == "search_files":
        target = f'"{_short(args.get("pattern", ""), 40)}"'
        path = args.get("path")
        return f"{target} in {path}" if path and path != "." else target
    if name == "run_command":
        return _short(args.get("command", ""), 70)
    if name == "list_dir":
        return str(args.get("path") or ".")
    if name == "read_file" and args.get("paths"):
        paths = args["paths"] if isinstance(args["paths"], list) else [args["paths"]]
        return _short(", ".join(str(p) for p in paths), 70)
    if name == "find_files":
        path = args.get("path")
        return f"{args.get('pattern', '')}" + (f" in {path}" if path and path != "." else "")
    if name == "move_path":
        return f"{args.get('source', '')} -> {args.get('destination', '')}"
    if name == "delete_path":
        return str(args.get("path", ""))
    if name == "start_process":
        return _short(args.get("command", ""), 70)
    if name in ("read_process", "stop_process"):
        return f"process {args['id']}" if args.get("id") not in (None, "") else "all"
    if name == "update_todos":
        todos = args.get("todos") or []
        return f"{len(todos)} step{'s' if len(todos) != 1 else ''}" if isinstance(todos, list) else ""
    if name == "ask_user":
        return _short(args.get("question", ""), 70)
    if name == "notify_user":
        return _short(args.get("title", ""), 60)
    if name == "web_search":
        return f'"{_short(args.get("query", ""), 60)}"'
    if name == "web_fetch":
        return _short(args.get("url", ""), 70)
    if name in ("read_file", "write_file", "edit_file", "send_file"):
        target = str(args.get("path", ""))
        start, end = args.get("start_line"), args.get("end_line")
        if name == "read_file" and (start or end):
            target += f":{start or 1}-{end or ''}"
        return target
    return ", ".join(f"{k}={_short(v, 30)}" for k, v in args.items())


def _lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def tool_summary(name: str, args: dict[str, Any], result: ToolResult) -> str:
    """A few words on how a tool call went, e.g. "171 lines" or "exit 1"."""
    out = result.output.strip()
    if result.is_error:
        if "declined" in out:
            return "declined"
        first = out.splitlines()[0] if out else "failed"
        if first.startswith("exit code: "):
            return "exit " + first[len("exit code: "):]
        # First clause only; the full message is one click away.
        return _short(first.split(" - ")[0], 60)
    if name == "read_file" and args.get("paths"):
        return f"{sum(line.startswith('=== ') for line in out.splitlines())} files"
    if name == "read_file":
        return "empty" if out == "(empty file)" else f"{_lines(out)} lines"
    if name == "find_files":
        if out == "no files found":
            return "none found"
        hits = [line for line in out.splitlines() if not line.startswith("...")]
        return f"{len(hits)}{'+' if len(hits) < _lines(out) else ''} found"
    if name in ("move_path", "delete_path", "notify_user"):
        return {"move_path": "moved", "delete_path": "deleted", "notify_user": "sent"}[name]
    if name in ("start_process", "read_process", "stop_process"):
        first = out.splitlines()[0] if out else ""
        if first.startswith("stopped"):
            return "stopped"
        return first.split(": ", 1)[1] if first.startswith("process") and ": " in first else "done"
    if name == "update_todos":
        last = out.splitlines()[-1] if out else ""
        return last.strip("()") if last.startswith("(") else "updated"
    if name == "ask_user":
        return "answered" if out.startswith("The user answered") else "no answer"
    if name == "web_search":
        return f"{result.meta.get('results', 0)} results" if result.meta.get("results") else "no results"
    if name == "web_fetch":
        return f"{len(out):,} chars"
    if name == "list_dir":
        return "empty" if out == "(empty directory)" else f"{_lines(out)} entries"
    if name == "search_files":
        if out == "no matches found":
            return "no matches"
        hits = [line for line in out.splitlines() if not line.startswith("...")]
        more = "+" if len(hits) < _lines(out) else ""
        return f"{len(hits)}{more} match{'' if len(hits) == 1 and not more else 'es'}"
    if name == "send_file":
        return "sent" if not result.is_error else "failed"
    if name == "run_command":
        first = out.splitlines()[0] if out else ""
        return "exit " + first[len("exit code: "):] if first.startswith("exit code: ") else "done"
    if name == "edit_file":
        if args.get("edits"):
            edits = args["edits"] if isinstance(args["edits"], list) else []
            return f"{len(edits)} edits"
        return f"+{_lines(args.get('new_string', ''))} -{_lines(args.get('old_string', ''))}"
    if name == "write_file":
        if "no changes" in out:
            return "unchanged"
        return f"{_lines(args.get('content', ''))} lines"
    return "done"


# 14 x 14, a compact cut of the logo: a bar across the top with two square
# holes on the left, and an arm down the right side as thick as the bar.
LOGO = [
    "##############",
    "#.############",
    "##############",
    "#.############",
    "##############",
] + [".........#####"] * 9

# 7 rows: two for ascenders (l, d), five for the x-height.
GLYPHS = {
    "l": ["#.", "#.", "#.", "#.", "#.", "#.", "##"],
    "o": ["....", "....", "####", "#..#", "#..#", "#..#", "####"],
    "c": ["....", "....", "####", "#...", "#...", "#...", "####"],
    "d": ["...#", "...#", "####", "#..#", "#..#", "#..#", "####"],
    "e": ["....", "....", "####", "#..#", "####", "#...", "####"],
    "r": ["....", "....", "####", "#...", "#...", "#...", "#..."],
}

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def pixels_to_text(rows: list[str], style: str) -> Text:
    """Render a '#'/'.' bitmap two rows per line with half blocks."""
    rows = rows + ["." * len(rows[0])] if len(rows) % 2 else rows
    text = Text(no_wrap=True, style=style)
    for y in range(0, len(rows), 2):
        top, bottom = rows[y], rows[y + 1]
        for t, b in zip(top, bottom):
            text.append({(True, True): "█", (True, False): "▀", (False, True): "▄"}.get(
                (t == "#", b == "#"), " "))
        if y + 2 < len(rows):
            text.append("\n")
    return text


def wordmark_bitmap(word: str) -> list[str]:
    rows = ["" for _ in range(7)]
    for i, ch in enumerate(word):
        for r in range(7):
            rows[r] += GLYPHS[ch][r] + ("." if i < len(word) - 1 else "")
    return rows


def where_it_runs(base_url: str) -> Text:
    """One line under the prompt box: local, or which host the code goes to."""
    host = urlparse(base_url).hostname or base_url
    if host in LOCAL_HOSTS:
        return Text.assemble(("● ", OK), ("runs locally — your code stays on this machine", MUTED))
    return Text.assemble(("● ", WARN), (f"connected to {host} — prompts and files are sent there", MUTED))
