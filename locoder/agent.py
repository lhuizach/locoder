"""The core agent loop: send conversation + tools to the model, execute any
tool calls it asks for, feed results back, repeat until it gives a plain
text answer.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import Optional

from locoder.context import ContextManager
from locoder.providers.base import Message, ModelProvider
from locoder.sandbox import Sandbox
from locoder.session import SessionStore
from locoder.tools import registry as tools_registry
from locoder.tools.base import ToolContext, ToolResult
from locoder.ui import UI

SYSTEM_PROMPT = """\
You are locoder, a careful local coding agent running in a user's terminal.

You have tools to read, write, and edit files, list directories, search file \
contents, and run shell commands. All file operations are sandboxed to the \
current project directory.

Guidelines:
- Read relevant files before editing them; don't guess at their contents.
- Prefer edit_file for small, targeted changes to existing files. Only use \
write_file for new files or full rewrites.
- Keep changes minimal and focused on what was asked.
- Explain your plan briefly before making non-trivial changes.
- After making changes, verify them (e.g. re-read the file or run relevant \
commands/tests) when practical.
- Shell commands and file writes require user confirmation; if the user \
declines, stop and ask how they'd like to proceed instead of retrying blindly.
"""

MAX_TOOL_ITERATIONS = 25


class AgentMode(str, Enum):
    BUILD = "build"
    ASK = "ask"
    PLAN = "plan"


MODE_ORDER = [AgentMode.BUILD, AgentMode.ASK, AgentMode.PLAN]
READ_ONLY_TOOLS = {"read_file", "list_dir", "search_files"}

MODE_COLORS = {
    AgentMode.BUILD: "#4a9eff",
    AgentMode.ASK: "#4caf50",
    AgentMode.PLAN: "#e0954f",
}

MODE_INSTRUCTIONS = {
    AgentMode.ASK: (
        "\n\nYou are currently in ASK mode (read-only). You may read files, list "
        "directories, and search code to answer questions, but you must NOT write "
        "or edit files or run commands - those tools are unavailable right now. If "
        "the user asks for a change, explain what you'd do instead of doing it, "
        "and mention they can press Tab to switch to Build mode to have you make it."
    ),
    AgentMode.PLAN: (
        "\n\nYou are currently in PLAN mode (read-only). Investigate the relevant "
        "files - reading, listing, and searching only, no writes or commands - then "
        "respond with a clear, concrete step-by-step plan for the requested change: "
        "which files to touch and what to change in each. Do not attempt to carry "
        "out the plan yourself; the user will review it and press Tab to switch to "
        "Build mode to have you execute it."
    ),
}


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        ui: UI,
        sandbox: Sandbox,
        ctx_manager: ContextManager,
        session: SessionStore,
        *,
        yolo: bool = False,
    ) -> None:
        self.provider = provider
        self.ui = ui
        self.sandbox = sandbox
        self.ctx_manager = ctx_manager
        self.session = session
        self.yolo = yolo
        self.mode = AgentMode.BUILD
        self.messages: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]
        self.tool_ctx = ToolContext(
            sandbox=sandbox,
            yolo=yolo,
            confirm=self._confirm,
            notify=ui.info,
        )
        self._cancel_requested = threading.Event()

    def _confirm(self, title: str, preview: str) -> bool:
        if self.yolo:
            return True
        return self.ui.confirm(title, preview)

    def set_yolo(self, value: bool) -> None:
        self.yolo = value
        self.tool_ctx.yolo = value

    def set_mode(self, mode: AgentMode) -> None:
        self.mode = mode

    def set_provider(self, provider: ModelProvider) -> None:
        self.provider = provider

    def clear(self) -> None:
        self.messages = [Message(role="system", content=SYSTEM_PROMPT)]
        self.session.start_new()

    def token_usage(self) -> tuple[int, int]:
        return self.ctx_manager.total_tokens(self.messages), self.ctx_manager.num_ctx

    def request_cancel(self) -> None:
        """Ask the in-flight turn (if any) to stop as soon as it next checks."""
        self._cancel_requested.set()

    def _tool_schemas(self) -> list[dict]:
        schemas = tools_registry.all_schemas()
        if self.mode == AgentMode.BUILD:
            return schemas
        return [s for s in schemas if s["function"]["name"] in READ_ONLY_TOOLS]

    def _effective_messages(self) -> list[Message]:
        instructions = MODE_INSTRUCTIONS.get(self.mode)
        if not instructions:
            return self.messages
        mode_note = Message(role="system", content=instructions.strip())
        return [self.messages[0], mode_note, *self.messages[1:]]

    def run_turn(self, user_input: str) -> None:
        self._cancel_requested.clear()
        self.messages.append(Message(role="user", content=user_input))

        for _ in range(MAX_TOOL_ITERATIONS):
            if self._cancel_requested.is_set():
                self.ui.warn("interrupted")
                self.session.save(self.messages)
                return

            if self.ctx_manager.should_compact(self.messages):
                self.ui.info("Context window is getting full - summarizing older turns...")
                self.messages = self.ctx_manager.compact(self.messages, self.provider)

            final_message, error, interrupted = self._stream_completion()
            if interrupted:
                self.session.save(self.messages)
                return
            if error is not None:
                self.ui.error(str(error))
                return
            if final_message is None:
                self.ui.error("model returned an empty response")
                return

            self.messages.append(final_message)

            if not final_message.tool_calls:
                self.session.save(self.messages)
                return

            for tool_call in final_message.tool_calls:
                self.ui.tool_call(tool_call.name, tool_call.arguments)
                if self.mode != AgentMode.BUILD and tool_call.name not in READ_ONLY_TOOLS:
                    result = ToolResult(
                        output=(
                            f"'{tool_call.name}' is not available in {self.mode.value} mode "
                            "(read-only) - switch to Build mode (Tab) to make changes"
                        ),
                        is_error=True,
                    )
                else:
                    result = tools_registry.dispatch(tool_call.name, tool_call.arguments, self.tool_ctx)
                self.ui.tool_result(tool_call.name, result)
                self.messages.append(
                    Message(
                        role="tool",
                        content=result.output,
                        tool_call_id=tool_call.id,
                        name=tool_call.name,
                    )
                )

            self.session.save(self.messages)

        self.ui.warn(
            f"stopped after {MAX_TOOL_ITERATIONS} tool-call iterations in this turn "
            "(possible loop) - try rephrasing your request"
        )
        self.session.save(self.messages)

    def _stream_completion(self) -> tuple[Optional[Message], Optional[BaseException], bool]:
        final_message: Optional[Message] = None
        error: Optional[BaseException] = None
        gen = self.provider.chat(self._effective_messages(), tools=self._tool_schemas(), stream=True)
        try:
            for event in gen:
                if self._cancel_requested.is_set():
                    gen.close()
                    self.ui.warn("interrupted")
                    return None, None, True
                if event.type == "token":
                    self.ui.stream_token(event.token)
                elif event.type == "thinking":
                    self.ui.thinking_token(event.token)
                elif event.type == "error":
                    error = event.error
                elif event.type == "done":
                    final_message = event.message
        except KeyboardInterrupt:
            gen.close()
            self.ui.warn("interrupted")
            return None, None, True
        finally:
            self.ui.end_stream()
        return final_message, error, False
