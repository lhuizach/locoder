"""File read/write/edit/list tools, all sandboxed to the project root."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

MAX_READ_BYTES = 2_000_000


def _read_text(path: Path) -> str:
    """Read ``path`` with its line endings untouched (no CRLF -> LF translation)."""
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return f.read()
    except UnicodeDecodeError as exc:
        raise ToolError(f"'{path.name}' does not look like a text file: {exc}") from exc


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _write_text(path: Path, text: str, newline: str) -> None:
    """Write ``text`` using ``newline`` for every line ending, regardless of
    platform - Python's default text mode would turn LF into CRLF on Windows.
    """
    data = _to_lf(text)
    if newline != "\n":
        data = data.replace("\n", newline)
    path.write_text(data, encoding="utf-8", newline="")


def _unified_diff(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def _read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.sandbox.resolve(args["path"], must_exist=True)
    if path.is_dir():
        raise ToolError(f"'{args['path']}' is a directory, not a file")
    if path.stat().st_size > MAX_READ_BYTES:
        raise ToolError(f"'{args['path']}' is too large to read ({path.stat().st_size} bytes)")

    text = _read_text(path)
    lines = text.splitlines()

    start = max(int(args.get("start_line", 1)), 1)
    end = int(args.get("end_line", len(lines))) if args.get("end_line") else len(lines)
    end = min(end, len(lines))

    numbered = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(lines[start - 1 : end], start=start))
    return ToolResult(output=numbered or "(empty file)")


def _write_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.sandbox.resolve(args["path"])
    content = args["content"]
    display = ctx.sandbox.display_path(path)

    raw_old = _read_text(path) if path.exists() else None
    # Keep an existing file's line-ending style; new files get what the model sent.
    newline = _detect_newline(raw_old if raw_old is not None else content)
    old_content = _to_lf(raw_old) if raw_old is not None else None
    content = _to_lf(content)
    if old_content == content:
        return ToolResult(output=f"'{display}' already matches the requested content; no changes made")

    diff = _unified_diff(old_content or "", content, display) or f"(new file) {display}\n{content}"
    verb = "Overwrite" if path.exists() else "Create"
    if not ctx.confirm(f"{verb} {display}", diff):
        return ToolResult(output="user declined the write; file not changed", is_error=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content, newline)
    return ToolResult(output=f"wrote {len(content)} bytes to '{display}'")


def _edit_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.sandbox.resolve(args["path"], must_exist=True)
    # Match against LF-normalized text so the model's LF-only strings still
    # find their target in CRLF files; the original endings are restored on write.
    old_string = _to_lf(args["old_string"])
    new_string = _to_lf(args["new_string"])
    display = ctx.sandbox.display_path(path)

    raw = _read_text(path)
    newline = _detect_newline(raw)
    content = _to_lf(raw)
    count = content.count(old_string)
    if count == 0:
        raise ToolError(f"old_string not found in '{display}' - it must match the file's current contents exactly")
    if count > 1:
        raise ToolError(
            f"old_string is not unique in '{display}' ({count} occurrences) - include more surrounding context"
        )

    new_content = content.replace(old_string, new_string, 1)
    diff = _unified_diff(content, new_content, display)
    if not ctx.confirm(f"Edit {display}", diff):
        return ToolResult(output="user declined the edit; file not changed", is_error=True)

    _write_text(path, new_content, newline)
    return ToolResult(output=f"applied edit to '{display}'")


def _list_dir(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.sandbox.resolve(args.get("path", "."), must_exist=True)
    if not path.is_dir():
        raise ToolError(f"'{args.get('path', '.')}' is not a directory")

    entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines = []
    for entry in entries:
        kind = "d" if entry.is_dir() else "f"
        size = "" if entry.is_dir() else f"{entry.stat().st_size}b"
        lines.append(f"{kind}  {entry.name}{'/' if entry.is_dir() else ''}  {size}".rstrip())
    return ToolResult(output="\n".join(lines) or "(empty directory)")


register(
    Tool(
        name="read_file",
        description="Read a file's contents with line numbers. Use start_line/end_line for large files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root"},
                "start_line": {"type": "integer", "description": "1-indexed first line to include"},
                "end_line": {"type": "integer", "description": "1-indexed last line to include"},
            },
            "required": ["path"],
        },
        handler=_read_file,
    )
)

register(
    Tool(
        name="write_file",
        description="Create a new file or overwrite an existing one with the given content. Shows a diff and asks for confirmation first.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root"},
                "content": {"type": "string", "description": "Full file contents to write"},
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
        requires_confirmation=True,
    )
)

register(
    Tool(
        name="edit_file",
        description=(
            "Replace an exact, unique substring of a file with a new string. "
            "Fails if old_string is not found or matches more than once."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root"},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
                "new_string": {"type": "string", "description": "Text to replace it with"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=_edit_file,
        requires_confirmation=True,
    )
)

register(
    Tool(
        name="list_dir",
        description="List the contents of a directory (non-recursive).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root, defaults to '.'"},
            },
        },
        handler=_list_dir,
    )
)
