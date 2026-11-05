"""Background processes: start something that keeps running (a dev server,
a watcher), read what it prints, stop it.

run_command waits for a command to finish, so a server would just hang it
until the timeout. These tools start the process, return after a few
seconds with whatever it printed so far, and keep collecting its output.
Every process still running when locoder exits is stopped.
"""

from __future__ import annotations

import atexit
import itertools
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Any, Optional

from locoder.safety import command_risks
from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register
from locoder.tools.shell_tools import _kill_tree

MAX_PROCESSES = 5
KEEP_LINES = 2_000
START_WAIT = 4.0  # seconds to collect a new process's first output
MAX_WAIT = 30
OUTPUT_CAP = 8_000


class BackgroundProcess:
    def __init__(self, number: int, name: str, command: str, proc: subprocess.Popen) -> None:
        self.number, self.name, self.command, self.proc = number, name, command, proc
        self.lines: deque[str] = deque(maxlen=KEEP_LINES)
        self.total = 0  # lines seen so far, including any that fell out of `lines`
        self.read_to = 0  # how far the model has read
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            with self.lock:
                self.lines.append(line.rstrip("\n"))
                self.total += 1

    @property
    def status(self) -> str:
        code = self.proc.poll()
        return "running" if code is None else f"exited with code {code}"

    def new_output(self) -> str:
        with self.lock:
            first_kept = self.total - len(self.lines)
            start = max(self.read_to, first_kept)
            fresh = list(self.lines)[start - first_kept:]
            skipped = start - self.read_to
            self.read_to = self.total
        text = "\n".join(fresh)
        if len(text) > OUTPUT_CAP:
            text = "... [earlier output cut]\n" + text[-OUTPUT_CAP:]
        elif skipped:
            text = f"... [{skipped} older lines no longer kept]\n" + text
        return text

    def wait_for_output(self, seconds: float) -> None:
        """Return once something new is printed or the process ends, or after ``seconds``."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self.lock:
                fresh = self.total > self.read_to
            if fresh:
                time.sleep(0.3)  # let a burst of lines land together
                return
            if self.proc.poll() is not None:
                time.sleep(0.2)
                return
            time.sleep(0.1)

    def header(self) -> str:
        return f"process {self.number} ({self.name}): {self.status}"


class ProcessManager:
    def __init__(self) -> None:
        self.processes: dict[int, BackgroundProcess] = {}
        self._numbers = itertools.count(1)
        atexit.register(self.stop_all)

    def running(self) -> list[BackgroundProcess]:
        return [p for p in self.processes.values() if p.proc.poll() is None]

    def start(self, command: str, cwd: Any, name: str) -> BackgroundProcess:
        if len(self.running()) >= MAX_PROCESSES:
            raise ToolError(f"{MAX_PROCESSES} background processes are already running - stop one first")
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=(sys.platform != "win32"),
        )
        process = BackgroundProcess(next(self._numbers), name, command, proc)
        self.processes[process.number] = process
        return process

    def get(self, number: Any) -> BackgroundProcess:
        try:
            return self.processes[int(str(number).strip().lstrip("#"))]
        except (KeyError, ValueError):
            known = ", ".join(str(n) for n in self.processes) or "none started yet"
            raise ToolError(f"there's no background process {number!r} (processes: {known})") from None

    def stop(self, process: BackgroundProcess) -> None:
        if process.proc.poll() is None:
            _kill_tree(process.proc)
            try:
                process.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def stop_all(self) -> None:
        for process in list(self.processes.values()):
            self.stop(process)


def _manager(ctx: ToolContext) -> ProcessManager:
    if ctx.processes is None:
        ctx.processes = ProcessManager()
    return ctx.processes


def _start_process(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    command = str(args["command"]).strip()
    if not command:
        raise ToolError("command is empty")
    cwd = ctx.sandbox.root
    if args.get("cwd"):
        cwd = ctx.sandbox.resolve(args["cwd"], must_exist=True)
        if not cwd.is_dir():
            raise ToolError(f"'{args['cwd']}' is not a directory")
    name = str(args.get("name") or command.split()[0])[:40]
    preview = f"$ {command}\n(cwd: {ctx.sandbox.display_path(cwd)}; keeps running in the background)"
    if not ctx.confirm("Start background process", preview, command_risks(command, ctx.sandbox.root, cwd, ctx.written)):
        return ToolResult(output="user declined to start the process", is_error=True)

    process = _manager(ctx).start(command, cwd, name)
    # Servers print their address a moment after their first line, so give
    # it a little while before reporting, but stop early if it exits.
    deadline = time.monotonic() + START_WAIT
    while time.monotonic() < deadline and process.proc.poll() is None:
        time.sleep(0.2)
    time.sleep(0.2)
    output = process.new_output()
    lines = [process.header(), output or "(no output yet)"]
    if process.proc.poll() is not None:
        lines.append("It already finished - for commands that finish, run_command is simpler.")
    else:
        lines.append(f"Use read_process with id {process.number} to see new output, stop_process to stop it.")
    return ToolResult(output="\n".join(lines), is_error=process.proc.poll() not in (None, 0),
                      meta={"process": process.number})


def _read_process(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    manager = _manager(ctx)
    if args.get("id") in (None, ""):
        if not manager.processes:
            return ToolResult(output="no background processes have been started")
        return ToolResult(output="\n".join(f"{p.header()}: {p.command}" for p in manager.processes.values()))
    process = manager.get(args["id"])
    wait = min(max(float(args.get("wait_seconds") or 0), 0), MAX_WAIT)
    if wait:
        process.wait_for_output(wait)
    output = process.new_output()
    return ToolResult(output=f"{process.header()}\n{output or '(no new output)'}")


def _stop_process(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    manager = _manager(ctx)
    process = manager.get(args["id"])
    was_running = process.proc.poll() is None
    manager.stop(process)
    output = process.new_output()
    tail = "\n".join(output.splitlines()[-20:])
    head = f"stopped process {process.number} ({process.name})" if was_running else process.header()
    return ToolResult(output=head + (f"\nlast output:\n{tail}" if tail else ""))


register(
    Tool(
        name="start_process",
        description=(
            "Start a command that keeps running in the background, like a dev server (npm run dev, "
            "python -m http.server) or a watcher, and return its first few seconds of output. "
            "Use run_command instead for commands that finish on their own."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to start"},
                "name": {"type": "string", "description": "Short label for it, e.g. 'dev server'"},
                "cwd": {"type": "string", "description": "Directory to run it in, defaults to the project root"},
            },
            "required": ["command"],
        },
        handler=_start_process,
        requires_confirmation=True,
    )
)

register(
    Tool(
        name="read_process",
        description=(
            "Show what a background process printed since you last looked, and whether it's still running. "
            "Without an id, lists every background process."
        ),
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "The process number start_process gave"},
                "wait_seconds": {"type": "integer", "description": f"Wait up to this long (max {MAX_WAIT}) for new output"},
            },
        },
        handler=_read_process,
    )
)

register(
    Tool(
        name="stop_process",
        description="Stop a background process (and anything it started).",
        parameters={
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "The process number start_process gave"}},
            "required": ["id"],
        },
        handler=_stop_process,
    )
)


def stop_all(processes: Optional[ProcessManager]) -> None:
    if processes is not None:
        processes.stop_all()
