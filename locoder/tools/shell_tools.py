"""Shell command execution tool.

Every invocation goes through confirmation (unless --yolo is set); commands
that match a known-destructive pattern get flagged in the confirmation
prompt so the user notices before approving.
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

DEFAULT_TIMEOUT = 120
OUTPUT_CAP = 10_000

DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f\b",
    r"\brm\s+-[a-z]*f[a-z]*r\b",
    r"\bgit\s+push\b.*(--force|-f)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-z]*f\b",
    r"\bgit\s+branch\s+-D\b",
    r"\bdrop\s+(table|database)\b",
    r"\btruncate\s+table\b",
    r"\bdel\s+/[fsq]",
    r"\brmdir\s+/s\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*{\s*:\|:&\s*};",  # fork bomb
    r"\bformat\s+[a-z]:",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bchmod\s+-R\s+777\b",
]
_DESTRUCTIVE_RE = re.compile("|".join(DESTRUCTIVE_PATTERNS), re.IGNORECASE)


def _looks_destructive(command: str) -> bool:
    return bool(_DESTRUCTIVE_RE.search(command))


def _run_command(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = args["command"]
    cwd = ctx.sandbox.root
    if args.get("cwd"):
        cwd = ctx.sandbox.resolve(args["cwd"], must_exist=True)
        if not cwd.is_dir():
            raise ToolError(f"'{args['cwd']}' is not a directory")

    timeout = int(args.get("timeout", DEFAULT_TIMEOUT))
    label = "Run shell command"
    if _looks_destructive(command):
        label += "  [DESTRUCTIVE - double check this]"
    preview = f"$ {command}\n(cwd: {ctx.sandbox.display_path(cwd)})"

    if not ctx.confirm(label, preview):
        return ToolResult(output="user declined to run the command", is_error=True)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out after {timeout}s") from exc

    parts = [f"exit code: {proc.returncode}"]
    if proc.stdout:
        parts.append(f"stdout:\n{proc.stdout[:OUTPUT_CAP]}")
    if proc.stderr:
        parts.append(f"stderr:\n{proc.stderr[:OUTPUT_CAP]}")
    return ToolResult(output="\n\n".join(parts), is_error=(proc.returncode != 0))


register(
    Tool(
        name="run_command",
        description=(
            "Execute a shell command in the project directory and return stdout, stderr, and exit code. "
            "Always requires user confirmation unless --yolo is set."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {"type": "string", "description": "Directory to run the command in, defaults to the project root"},
                "timeout": {"type": "integer", "description": "Timeout in seconds, defaults to 120"},
            },
            "required": ["command"],
        },
        handler=_run_command,
        requires_confirmation=True,
    )
)
