"""Content search tool. Prefers ripgrep; falls back to a pure-Python walk
when `rg` isn't on PATH so locoder still works on a bare system.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from locoder.sandbox import IGNORED_DIRS  # noqa: F401 - also re-exported for tui.commands
from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

MAX_MATCHES = 200
MAX_LINE_CHARS = 300  # minified/generated files can have megabyte-long lines


def _search_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    pattern = args["pattern"]
    rel_path = args.get("path", ".")
    search_root = ctx.sandbox.resolve(rel_path, must_exist=True)

    rg = shutil.which("rg")
    if rg:
        return _search_with_ripgrep(rg, pattern, search_root, ctx)
    return _search_with_python(pattern, search_root, ctx)


def _search_with_ripgrep(rg: str, pattern: str, search_root: Path, ctx: ToolContext) -> ToolResult:
    display_root = ctx.sandbox.display_path(search_root)
    cmd = [
        rg,
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--max-count",
        str(MAX_MATCHES),
        "--max-columns",
        str(MAX_LINE_CHARS),
        "--max-columns-preview",
        "--path-separator",
        "/",
        # -e/-- so patterns or paths starting with "-" aren't parsed as flags
        "-e",
        pattern,
        "--",
        display_root,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            cwd=ctx.sandbox.root,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("search timed out after 30s") from exc

    if proc.returncode == 1:
        return ToolResult(output="no matches found")
    if proc.returncode not in (0, 1):
        raise ToolError(f"ripgrep failed: {proc.stderr.strip()}")

    lines = proc.stdout.strip().splitlines()
    truncated = len(lines) > MAX_MATCHES
    output = "\n".join(lines[:MAX_MATCHES])
    if truncated:
        output += f"\n... truncated at {MAX_MATCHES} matches"
    return ToolResult(output=output or "no matches found")


def _search_with_python(pattern: str, search_root: Path, ctx: ToolContext) -> ToolResult:
    ctx.notify("ripgrep not found on PATH; falling back to a slower built-in search")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regex pattern: {exc}") from exc

    matches: list[str] = []
    files = [search_root] if search_root.is_file() else _walk(search_root)
    for file_path in files:
        if len(matches) >= MAX_MATCHES:
            break
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                if len(line) > MAX_LINE_CHARS:
                    line = line[:MAX_LINE_CHARS] + " [... line truncated]"
                matches.append(f"{ctx.sandbox.display_path(file_path)}:{lineno}:{line}")
                if len(matches) >= MAX_MATCHES:
                    break

    if not matches:
        return ToolResult(output="no matches found")
    output = "\n".join(matches)
    if len(matches) >= MAX_MATCHES:
        output += f"\n... truncated at {MAX_MATCHES} matches"
    return ToolResult(output=output)


def _walk(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not any(part in IGNORED_DIRS for part in path.parts):
            yield path


MAX_FOUND = 200
MAX_FIND_SCAN = 100_000


def _find_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Files (and folders) by name or path pattern. Pure Python, so it
    behaves the same on Windows and Linux; matching ignores case."""
    pattern = str(args["pattern"]).strip().replace("\\", "/").lower()
    if not pattern:
        raise ToolError("pattern is empty - e.g. '*.py', 'config.*' or 'src/**/*.test.js'")
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    pattern = pattern.replace("**/", "*")
    root = ctx.sandbox.resolve(args.get("path") or ".", must_exist=True)
    if not root.is_dir():
        raise ToolError(f"'{args.get('path')}' is not a folder")
    by_path = "/" in pattern

    found: list[str] = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        here = Path(dirpath)
        for name, is_dir in [(d, True) for d in dirnames] + [(f, False) for f in sorted(filenames)]:
            scanned += 1
            subject = (here / name).relative_to(root).as_posix().lower() if by_path else name.lower()
            if fnmatch.fnmatchcase(subject, pattern):
                found.append(ctx.sandbox.display_path(here / name) + ("/" if is_dir else ""))
        if len(found) > MAX_FOUND or scanned > MAX_FIND_SCAN:
            break

    if not found:
        return ToolResult(output="no files found")
    found.sort()
    output = "\n".join(found[:MAX_FOUND])
    if len(found) > MAX_FOUND or scanned > MAX_FIND_SCAN:
        output += f"\n... stopped after {MAX_FOUND} results - narrow the pattern or path"
    return ToolResult(output=output)


register(
    Tool(
        name="search_files",
        description="Search file contents for a regex pattern (ripgrep-backed). Returns matching file:line:text.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search, defaults to '.'"},
            },
            "required": ["pattern"],
        },
        handler=_search_files,
    )
)

register(
    Tool(
        name="find_files",
        description=(
            "Find files and folders by name, e.g. '*.py', 'package.json', 'test_*'. Patterns with a / match the "
            "path instead, e.g. 'src/**/*.ts'. Case doesn't matter. Use search_files to search inside files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Name or path pattern with * and ? wildcards"},
                "path": {"type": "string", "description": "Folder to search in, defaults to the project root"},
            },
            "required": ["pattern"],
        },
        handler=_find_files,
    )
)
