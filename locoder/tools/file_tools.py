"""File read/write/edit/list tools, all sandboxed to the project root."""

from __future__ import annotations

import difflib
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from locoder.safety import delete_risks, file_change_risks, path_risks
from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

MAX_READ_BYTES = 2_000_000
MAX_FILES_PER_READ = 10
MAX_MULTI_READ_CHARS = 18_000  # under the dispatcher's 20k cap, so every file keeps its share


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


def _read_one(raw_path: str, ctx: ToolContext, start_line: Any = None, end_line: Any = None) -> str:
    path = ctx.sandbox.resolve(raw_path, must_exist=True)
    if path.is_dir():
        raise ToolError(f"'{raw_path}' is a directory, not a file")
    if path.stat().st_size > MAX_READ_BYTES:
        raise ToolError(f"'{raw_path}' is too large to read ({path.stat().st_size} bytes)")

    text = _read_text(path)
    lines = text.splitlines()

    start = max(int(start_line or 1), 1)
    end = min(int(end_line), len(lines)) if end_line else len(lines)

    numbered = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(lines[start - 1 : end], start=start))
    return numbered or "(empty file)"


def _read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    paths = args.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    if paths:
        # Several files in one call: each gets a fair share of the output cap,
        # and one missing file doesn't sink the rest.
        paths = [str(p) for p in paths[:MAX_FILES_PER_READ]]
        share = MAX_MULTI_READ_CHARS // len(paths)
        parts = []
        for raw_path in paths:
            try:
                text = _read_one(raw_path, ctx)
            except Exception as exc:  # noqa: BLE001 - reported per file
                text = f"(could not read it: {exc})"
            if len(text) > share:
                text = text[:share] + f"\n... [cut short: read '{raw_path}' on its own, with start_line/end_line, for the rest]"
            parts.append(f"=== {raw_path} ===\n{text}")
        return ToolResult(output="\n\n".join(parts))
    if not args.get("path"):
        raise ToolError("give 'path' for one file, or 'paths' for several")
    return ToolResult(output=_read_one(args["path"], ctx, args.get("start_line"), args.get("end_line")))


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
    reasons = file_change_risks(ctx.sandbox.root, path, old_content, content)
    if not ctx.confirm(f"{verb} {display}", diff, reasons):
        return ToolResult(output="user declined the write; file not changed", is_error=True)

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content, newline)
    ctx.written.add(path.resolve())
    return ToolResult(output=f"wrote {len(content)} bytes to '{display}'")


def _apply_edit(content: str, old: str, new: str, replace_all: bool, display: str, label: str = "") -> tuple[str, int]:
    """``content`` with ``old`` replaced; (new content, replacements made)."""
    if not old:
        raise ToolError(f"{label}old_string is empty - give the exact text to replace")
    count = content.count(old)
    if count == 0:
        raise ToolError(f"{label}old_string not found in '{display}' - it must match the file's current contents exactly")
    if count > 1 and not replace_all:
        raise ToolError(
            f"{label}old_string is not unique in '{display}' ({count} occurrences) - include more surrounding "
            "context, or set replace_all to change every one"
        )
    return (content.replace(old, new) if replace_all else content.replace(old, new, 1)), (count if replace_all else 1)


def _edit_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.sandbox.resolve(args["path"], must_exist=True)
    display = ctx.sandbox.display_path(path)
    edits = args.get("edits")
    if not edits:
        if "old_string" not in args or "new_string" not in args:
            raise ToolError("give old_string and new_string, or a list of edits")
        edits = [{"old_string": args["old_string"], "new_string": args["new_string"],
                  "replace_all": args.get("replace_all", False)}]
    if not isinstance(edits, list) or not all(isinstance(e, dict) for e in edits):
        raise ToolError("edits must be a list of {old_string, new_string} objects")

    raw = _read_text(path)
    newline = _detect_newline(raw)
    content = _to_lf(raw)
    # Every edit applies to the result of the one before; if any fails, none
    # are written. Match against LF-normalized text so the model's LF-only
    # strings still find their target in CRLF files; endings are restored on write.
    new_content, replacements = content, 0
    for i, edit in enumerate(edits, start=1):
        label = f"edit {i} of {len(edits)}: " if len(edits) > 1 else ""
        new_content, n = _apply_edit(new_content, _to_lf(str(edit.get("old_string", ""))),
                                     _to_lf(str(edit.get("new_string", ""))),
                                     bool(edit.get("replace_all")), display, label)
        replacements += n
    if new_content == content:
        return ToolResult(output=f"'{display}' already matches; no changes made")

    diff = _unified_diff(content, new_content, display)
    reasons = file_change_risks(ctx.sandbox.root, path, content, new_content)
    if not ctx.confirm(f"Edit {display}", diff, reasons):
        return ToolResult(output="user declined the edit; file not changed", is_error=True)

    _write_text(path, new_content, newline)
    ctx.written.add(path.resolve())
    if len(edits) == 1 and replacements == 1:
        return ToolResult(output=f"applied edit to '{display}'")
    return ToolResult(output=f"applied {len(edits)} edit{'s' if len(edits) > 1 else ''} to '{display}' "
                             f"({replacements} replacement{'s' if replacements != 1 else ''})")


def _remove_readonly(func, path, _exc) -> None:
    # Windows marks some files read-only (git objects, for one), which rmtree can't delete.
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _move_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    source = ctx.sandbox.resolve(args["source"], must_exist=True)
    raw_dest = str(args["destination"])
    dest = ctx.sandbox.resolve(raw_dest)
    if source == ctx.sandbox.root:
        raise ToolError("can't move the project folder itself")
    # Into a folder: an existing one, or any destination ending in / (made if needed).
    if dest.is_dir() or raw_dest.endswith(("/", "\\")):
        dest = dest / source.name
    if dest.exists():
        raise ToolError(f"'{ctx.sandbox.display_path(dest)}' already exists - delete it first or pick another name")
    if source.is_dir() and (dest == source or source in dest.parents):
        raise ToolError("can't move a folder into itself")

    a, b = ctx.sandbox.display_path(source), ctx.sandbox.display_path(dest)
    reasons = list(dict.fromkeys(path_risks(ctx.sandbox.root, source) + path_risks(ctx.sandbox.root, dest)))
    if not ctx.confirm(f"Move {a} to {b}", f"{a}{'/' if source.is_dir() else ''}\n  -> {b}", reasons):
        return ToolResult(output="user declined the move; nothing changed", is_error=True)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    if source.resolve() in ctx.written:
        ctx.written.discard(source.resolve())
        ctx.written.add(dest.resolve())
    return ToolResult(output=f"moved '{a}' to '{b}'")


def _delete_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = ctx.sandbox.resolve(args["path"], must_exist=True)
    if path == ctx.sandbox.root:
        raise ToolError("refusing to delete the whole project folder")
    display = ctx.sandbox.display_path(path)
    if path.is_dir():
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
        shown = "\n".join(f"  {e}" for e in entries[:20]) + (f"\n  ... and {len(entries) - 20} more" if len(entries) > 20 else "")
        preview = f"{display}/ and everything in it:\n{shown or '  (empty folder)'}"
    else:
        preview = f"{display} ({path.stat().st_size:,} bytes)"
    if not ctx.confirm(f"Delete {display}", preview, delete_risks(ctx.sandbox.root, path)):
        return ToolResult(output="user declined the delete; nothing changed", is_error=True)

    was_dir = path.is_dir()
    if was_dir:
        shutil.rmtree(path, onerror=_remove_readonly)
    else:
        path.unlink()
    ctx.written.discard(path.resolve())
    return ToolResult(output=f"deleted the folder '{display}' and everything in it" if was_dir else f"deleted '{display}'")


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
        description=(
            "Read a file's contents with line numbers. Use start_line/end_line for large files. "
            "To read several files at once, pass paths instead of path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root"},
                "start_line": {"type": "integer", "description": "1-indexed first line to include"},
                "end_line": {"type": "integer", "description": "1-indexed last line to include"},
                "paths": {"type": "array", "items": {"type": "string"},
                          "description": f"Several files to read in one go (up to {MAX_FILES_PER_READ}), instead of path"},
            },
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
            "Fails if old_string is not found or matches more than once (set replace_all to change every match). "
            "For several changes to one file, pass edits instead: they apply in order, all or nothing."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the project root"},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
                "new_string": {"type": "string", "description": "Text to replace it with"},
                "replace_all": {"type": "boolean", "description": "Replace every match instead of exactly one"},
                "edits": {
                    "type": "array",
                    "description": "Several edits to this file, instead of old_string/new_string",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {"type": "string"},
                            "new_string": {"type": "string"},
                            "replace_all": {"type": "boolean"},
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
            },
            "required": ["path"],
        },
        handler=_edit_file,
        requires_confirmation=True,
    )
)

register(
    Tool(
        name="move_path",
        description=(
            "Move or rename a file or folder inside the project. To move it into a folder, end destination "
            "with / (e.g. 'docs/'); the folder is created if needed. Refuses to overwrite."
        ),
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "File or folder to move, relative to the project root"},
                "destination": {"type": "string", "description": "New path (or an existing folder to move it into)"},
            },
            "required": ["source", "destination"],
        },
        handler=_move_path,
        requires_confirmation=True,
    )
)

register(
    Tool(
        name="delete_path",
        description="Delete a file, or a folder and everything in it. This can't be undone, so be sure it's meant to go.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "File or folder, relative to the project root"}},
            "required": ["path"],
        },
        handler=_delete_path,
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
