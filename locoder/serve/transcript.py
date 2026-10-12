"""Saved conversations, as the phone draws them: your messages, the model's
text, and one line per tool call (verb, target, how it went, output).
"""

from __future__ import annotations

from typing import Any

from locoder.display import TOOL_VERBS, tool_summary, tool_target
from locoder.providers.base import Message
from locoder.tools.base import ToolResult

# What the phone gets of a tool's output; the model saw all of it.
MAX_OUTPUT = 4_000
_ERROR_PREFIXES = ("unknown tool", "not found:", "permission denied:", "unexpected error", "user declined")


def tool_entry(name: str, args: dict[str, Any], result: ToolResult) -> dict[str, Any]:
    summary = tool_summary(name, args, result)
    if summary == "declined":
        status = "declined"
    else:
        status = "err" if result.is_error else "ok"
    output = result.output
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + f"\n... ({len(result.output) - MAX_OUTPUT:,} more characters)"
    return {
        "type": "tool",
        "name": name,
        "verb": TOOL_VERBS.get(name, name),
        "target": tool_target(name, args),
        "summary": summary,
        "status": status,
        "output": output,
    }


def _looks_failed(name: str, output: str) -> bool:
    """Saved tool results don't record whether they failed, so guess."""
    text = output.lstrip()
    if "This exact call already failed" in text:
        return True
    if name == "run_command" and text.startswith("exit code: "):
        return not text.startswith("exit code: 0")
    return text.lower().startswith(_ERROR_PREFIXES)


def to_entries(messages: list[Message]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for m in messages:
        if m.role == "user" and m.content.strip():
            entries.append({"type": "user", "text": m.content})
        elif m.role == "assistant":
            if m.content.strip():
                entries.append({"type": "text", "text": m.content, "narration": bool(m.tool_calls)})
            for tc in m.tool_calls:
                calls[tc.id] = (tc.name, tc.arguments)
        elif m.role == "tool":
            name, args = calls.get(m.tool_call_id or "", (m.name or "tool", {}))
            result = ToolResult(output=m.content, is_error=_looks_failed(name, m.content))
            entries.append(tool_entry(name, args, result))
    return entries
