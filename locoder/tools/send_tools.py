"""The send_file tool: lets the model hand a project file to the user's phone.

Only does something in a session a phone is attached to (``locoder serve``);
the file itself is fetched by the phone app through the server's download
endpoint, which applies the same checks (locoder.safety.sendable_problem).
"""

from __future__ import annotations

from typing import Any

from locoder.safety import MAX_SEND_BYTES, sendable_problem
from locoder.tools.base import Tool, ToolContext, ToolError, ToolResult, register


def _send_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    if not ctx.can_send_files:
        raise ToolError("no phone is connected to this session, so there is nobody to send it to - "
                        "tell the user where the file is instead")
    path = ctx.sandbox.resolve(args["path"], must_exist=True)
    problem = sendable_problem(ctx.sandbox.root, path)
    if problem:
        raise ToolError(problem)
    display = ctx.sandbox.display_path(path)
    size = path.stat().st_size
    return ToolResult(
        output=f"'{display}' ({size:,} bytes) is ready on the user's phone: they can tap Save to download it"
               + (" or Open to try it." if path.suffix.lower() in (".html", ".htm") else "."),
        meta={"file": display, "size": size},
    )


register(
    Tool(
        name="send_file",
        description=(
            "Send a file from the project to the user's phone, where they can download it "
            "(HTML files can also be opened and tried right in the app). Use it when the user asks to be "
            f"sent a file. Max {MAX_SEND_BYTES // 1_000_000} MB; secrets (.env, keys) are refused."
        ),
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path of the file, relative to the project root"}},
            "required": ["path"],
        },
        handler=_send_file,
    )
)
