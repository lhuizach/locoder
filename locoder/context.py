"""Approximate token accounting and automatic history compaction.

Local models rarely expose their exact tokenizer over the wire, so we count
tokens with tiktoken's cl100k_base encoding as a stand-in — close enough for
budgeting purposes across most modern tokenizers. If tiktoken's encoding
files aren't available (e.g. no network on first run), we fall back to a
chars/4 heuristic instead of failing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from locoder.providers.base import Message

if TYPE_CHECKING:
    from locoder.providers.base import ModelProvider

PER_MESSAGE_OVERHEAD = 4  # rough allowance for role/formatting tokens
DEFAULT_KEEP_RECENT = 8


class TokenCounter:
    def __init__(self) -> None:
        self._enc = None
        try:
            import tiktoken

            self._enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._enc = None

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass
        return max(1, len(text) // 4)


class ContextManager:
    def __init__(self, num_ctx: int, threshold: float = 0.85, counter: Optional[TokenCounter] = None) -> None:
        self.num_ctx = num_ctx
        self.threshold = threshold
        self.counter = counter or TokenCounter()

    def count_message(self, message: Message) -> int:
        text = message.content
        for tc in message.tool_calls:
            text += tc.name + str(tc.arguments)
        return self.counter.count(text) + PER_MESSAGE_OVERHEAD

    def total_tokens(self, messages: list[Message]) -> int:
        return sum(self.count_message(m) for m in messages)

    def should_compact(self, messages: list[Message]) -> bool:
        return self.total_tokens(messages) >= self.num_ctx * self.threshold

    def compact(
        self,
        messages: list[Message],
        provider: "ModelProvider",
        *,
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ) -> list[Message]:
        """Collapse older turns into a single summary message, keeping the
        system prompt and the most recent ``keep_recent`` messages verbatim.
        """
        system_msgs = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]

        if len(rest) <= keep_recent:
            return messages

        to_summarize = rest[:-keep_recent]
        recent = rest[-keep_recent:]

        transcript = "\n\n".join(f"[{m.role}] {m.content}" for m in to_summarize if m.content.strip())
        if not transcript.strip():
            return system_msgs + recent

        summary_request = [
            Message(
                role="system",
                content=(
                    "Summarize the following coding-session conversation concisely. "
                    "Preserve concrete facts: file paths touched, decisions made, "
                    "commands run, and any outstanding TODOs. Bullet points, no preamble."
                ),
            ),
            Message(role="user", content=transcript),
        ]

        summary_text = ""
        for event in provider.chat(summary_request, tools=None, stream=False):
            if event.type == "done" and event.message:
                summary_text = event.message.content
            elif event.type == "error":
                # Summarization failing shouldn't crash the session - just
                # fall back to a hard truncation instead.
                summary_text = ""

        if not summary_text:
            return system_msgs + recent

        summary_message = Message(role="system", content=f"[Summary of earlier conversation]\n{summary_text}")
        return system_msgs + [summary_message] + recent
