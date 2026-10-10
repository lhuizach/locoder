"""Tool registration framework.

Each tool module (file_tools, search_tools, shell_tools) registers one or
more ``Tool`` instances at import time. The agent loop never calls a tool
implementation directly — it goes through ``dispatch`` in registry.py so
confirmation, sandboxing, and error-wrapping stay consistent across tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # Shows `preview` (a diff, a shell command, ...) under `title` and asks
    # the user to approve. Always returns True when yolo is set by the caller
    # that constructs this context, but tools should call it unconditionally
    # and let the context decide.
    confirm: Callable[[str, str], bool]
    notify: Callable[[str], None] = field(default=lambda msg: None)


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
