"""Tool registration framework.

Each tool module (file_tools, search_tools, shell_tools) registers one or
more ``Tool`` instances at import time. The agent loop never calls a tool
implementation directly — it goes through ``dispatch`` in registry.py so
confirmation, sandboxing, and error-wrapping stay consistent across tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from locoder.sandbox import Sandbox


@dataclass
class ToolResult:
    output: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Expected/handled failure (file not found, ambiguous match, etc.).

    Raised by tool implementations; the dispatcher turns it into a
    ``ToolResult(is_error=True)`` instead of letting it crash the agent loop.
    """


@dataclass
class ToolContext:
    sandbox: Sandbox
    yolo: bool
    # confirm(title, preview, reasons): shows `preview` (a diff, a shell
    # command, ...) under `title` and asks the user to approve. In yolo mode
    # it approves by itself - unless `reasons` (from locoder.safety) says the
    # action is risky, which always asks. Tools call it unconditionally and
    # let the context decide.
    confirm: Callable[..., bool]
    notify: Callable[[str], None] = field(default=lambda msg: None)
    # Files written this session, so running a script locoder just wrote
    # can be checked like any other command.
    written: set[Path] = field(default_factory=set)
    # True when a phone is attached (locoder serve): send_file only works then.
    can_send_files: bool = False
    # ask(question, options): the user's answer, or None when nobody answers
    # (no one at the keyboard, a batch run, the question dismissed).
    ask: Callable[[str, list[str]], Optional[str]] = field(default=lambda question, options: None)
    # notify_user(title, message): a desktop / phone notification.
    notify_user: Callable[[str, str], None] = field(default=lambda title, message: None)
    # show_todos(items): redraw the model's checklist wherever the user is looking.
    show_todos: Callable[[list[dict[str, str]]], None] = field(default=lambda items: None)
    todos: list[dict[str, str]] = field(default_factory=list)
    last_notify: Optional[float] = None
    # Background processes (start_process); a ProcessManager, made on first use.
    # A long-lived owner (locoder serve) passes its own so servers outlive one run.
    processes: Any = None
    web_enabled: bool = False
    brave_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], ToolResult]
    requires_confirmation: bool = False

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    _REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> Optional[Tool]:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())
