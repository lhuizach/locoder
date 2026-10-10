"""Aggregates every tool module and dispatches calls from the agent loop."""

from __future__ import annotations

from typing import Any

from locoder.tools import base
from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult

# Import for registration side effects (each module calls base.register(...)
# at import time).
from locoder.tools import file_tools, search_tools, shell_tools  # noqa: F401


def all_schemas() -> list[dict[str, Any]]:
    return [tool.to_schema() for tool in base.all_tools()]


def dispatch(name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
    tool = base.get(name)
    if tool is None:
        return ToolResult(output=f"unknown tool '{name}'", is_error=True)
    try:
        return tool.handler(arguments, ctx)
    except ToolError as exc:
        return ToolResult(output=str(exc), is_error=True)
    except FileNotFoundError as exc:
        return ToolResult(output=f"not found: {exc}", is_error=True)
    except PermissionError as exc:
        return ToolResult(output=f"permission denied: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, not a crash
        return ToolResult(output=f"unexpected error running '{name}': {exc}", is_error=True)


__all__ = ["all_schemas", "dispatch", "Tool", "ToolContext", "ToolError", "ToolResult"]
