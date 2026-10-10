"""Content search tool. Prefers ripgrep; falls back to a pure-Python walk
when `rg` isn't on PATH so locoder still works on a bare system.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

MAX_MATCHES = 200
MAX_LINE_CHARS = 300  # minified/generated files can have megabyte-long lines
IGNORED_DIRS = {".git", ".locoder", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}


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
