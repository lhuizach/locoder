"""Shell command execution tool.

Every invocation goes through confirmation (unless --yolo is set); commands
locoder.safety finds risky are always confirmed, yolo or not, with the
reasons shown so the user notices before approving.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

from locoder.safety import command_risks
from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

DEFAULT_TIMEOUT = 120
OUTPUT_CAP = 10_000

def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and everything it spawned. With shell=True, ``proc`` is
    just the shell - killing only it would leave the real command running.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    proc.kill()


def _run_command(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = args["command"]
    cwd = ctx.sandbox.root
    if args.get("cwd"):
        cwd = ctx.sandbox.resolve(args["cwd"], must_exist=True)
        if not cwd.is_dir():
            raise ToolError(f"'{args['cwd']}' is not a directory")

    timeout = int(args.get("timeout", DEFAULT_TIMEOUT))
    label = "Run shell command"
    preview = f"$ {command}\n(cwd: {ctx.sandbox.display_path(cwd)})"
    reasons = command_risks(command, ctx.sandbox.root, cwd, ctx.written)

    if not ctx.confirm(label, preview, reasons):
        return ToolResult(output="user declined to run the command", is_error=True)

    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Decode explicitly: the locale default (cp1252 on Windows) raises on
        # any byte it can't map, which would turn ordinary output into a crash.
        text=True,
        encoding="utf-8",
        errors="replace",
        # Own process group, so a timeout can kill the whole tree on POSIX.
        start_new_session=(sys.platform != "win32"),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_tree(proc)
        proc.communicate()
        raise ToolError(f"command timed out after {timeout}s (process tree killed)") from exc

    parts = [f"exit code: {proc.returncode}"]
    if stdout:
        parts.append(f"stdout:\n{stdout[:OUTPUT_CAP]}")
    if stderr:
        parts.append(f"stderr:\n{stderr[:OUTPUT_CAP]}")
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
