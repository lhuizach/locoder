"""Tools for talking to the user while working: a to-do list they can watch,
a question they can answer, and a notification for long tasks.

What each one looks like depends on where the user is (the TUI, the plain
REPL, the phone); the agent wires ToolContext's ask / notify_user /
show_todos to the current UI.
"""

from __future__ import annotations

import time
from typing import Any

from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register

STATUSES = ("pending", "in_progress", "done")
_STATUS_ALIASES = {"todo": "pending", "not_started": "pending", "doing": "in_progress", "active": "in_progress",
                   "started": "in_progress", "complete": "done", "completed": "done", "finished": "done"}
MAX_TODOS = 30
NOTIFY_EVERY = 60  # seconds: at most one notification a minute
MARKS = {"pending": "[ ]", "in_progress": "[>]", "done": "[x]"}


def render_todos(items: list[dict[str, str]]) -> str:
    done = sum(item["status"] == "done" for item in items)
    lines = [f"{MARKS[item['status']]} {item['text']}" for item in items]
    return "\n".join(lines) + f"\n({done} of {len(items)} done)"


def _update_todos(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    raw = args.get("todos")
    if not isinstance(raw, list) or not raw:
        raise ToolError("todos must be a list of {text, status} items - the whole list, every time")
    items = []
    for i, item in enumerate(raw[:MAX_TODOS], start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise ToolError(f"item {i} should be an object with text and status")
        text = str(item.get("text") or item.get("content") or item.get("task") or "").strip()
        if not text:
            raise ToolError(f"item {i} has no text")
        status = str(item.get("status") or "pending").strip().lower().replace(" ", "_").replace("-", "_")
        status = _STATUS_ALIASES.get(status, status)
        if status not in STATUSES:
            raise ToolError(f"item {i}: status must be one of {', '.join(STATUSES)}")
        items.append({"text": text[:200], "status": status})
    ctx.todos = items
    ctx.show_todos(items)
    return ToolResult(output=render_todos(items), meta={"todos": items})


def _ask_user(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    question = str(args.get("question") or "").strip()
    if not question:
        raise ToolError("question is empty")
    options = [str(o).strip() for o in (args.get("options") or []) if str(o).strip()][:6]
    answer = ctx.ask(question, options)
    if answer is None or not str(answer).strip():
        return ToolResult(output=(
            "The user didn't answer (they may be away from the computer). Make the most sensible choice "
            "yourself, carry on, and tell them which choice you made."
        ))
    return ToolResult(output=f"The user answered: {str(answer).strip()}")


def _notify_user(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    title = " ".join(str(args.get("title") or "").split())[:80]
    message = " ".join(str(args.get("message") or "").split())[:300]
    if not title or not message:
        raise ToolError("a notification needs a title and a message")
    now = time.monotonic()
    if ctx.last_notify is not None and now - ctx.last_notify < NOTIFY_EVERY:
        raise ToolError(f"the last notification went out {now - ctx.last_notify:.0f}s ago - send at most one a "
                        "minute, for real milestones")
    ctx.last_notify = now
    ctx.notify_user(title, message)
    return ToolResult(output=f"notification sent: {title}", meta={"title": title, "message": message})


register(
    Tool(
        name="update_todos",
        description=(
            "Keep a short checklist for tasks with several steps, which the user sees update as you work. "
            "Send the whole list every time, marking each item pending, in_progress or done. "
            "Skip it for quick one-step tasks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "What to do, in a few words"},
                            "status": {"type": "string", "enum": list(STATUSES)},
                        },
                        "required": ["text", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
        handler=_update_todos,
    )
)

register(
    Tool(
        name="ask_user",
        description=(
            "Ask the user a question and wait for the answer. Only for decisions you can't sensibly make "
            "yourself (which of two designs, whether to delete something they may want); for small details, "
            "pick a sensible default and carry on. Offer options when there are a few clear choices."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question, short and specific"},
                "options": {"type": "array", "items": {"type": "string"},
                            "description": "Up to 6 answers to choose from (the user can still type their own)"},
            },
            "required": ["question"],
        },
        handler=_ask_user,
    )
)

register(
    Tool(
        name="notify_user",
        description=(
            "Send the user a notification on their PC and phone. For long tasks only: when a big step is done, "
            "when you're finished, or when you're stuck and need them. At most one a minute."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "A few words, e.g. 'Tests passing'"},
                "message": {"type": "string", "description": "One or two sentences"},
            },
            "required": ["title", "message"],
        },
        handler=_notify_user,
    )
)
