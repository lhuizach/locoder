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
# After compaction, the verbatim recent history may use at most this share of
# num_ctx. Leaving real headroom below the compaction threshold means we don't
# re-summarize on every following model call.
KEEP_RECENT_FRACTION = 0.5
SUMMARY_PREFIX = "[Summary of earlier conversation]"
# Per-message cap inside the summarization request, so that request itself
# fits in the context window.
TRANSCRIPT_MESSAGE_CHARS = 2_000


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

    def split_index(self, rest: list[Message]) -> int:
        """Index into ``rest`` (non-system messages) where the verbatim recent
        tail starts: as many trailing messages as fit in the keep-recent
        budget (always at least the last one), never starting on a tool
        result - its assistant tool-call message must stay with it, or
        OpenAI-compatible servers reject the orphaned result.
        """
        budget = self.num_ctx * KEEP_RECENT_FRACTION
        split = len(rest)
        used = 0
        while split > 0:
            cost = self.count_message(rest[split - 1])
            if used + cost > budget and split < len(rest):
                break
            used += cost
            split -= 1
        while 0 < split < len(rest) and rest[split].role == "tool":
            split -= 1
        return split

    def compact(self, messages: list[Message], provider: "ModelProvider") -> list[Message]:
        """Collapse older turns (and any earlier summaries) into a single
        summary message, keeping the system prompt and the recent tail verbatim.
        """
        has_prompt = bool(messages) and messages[0].role == "system"
        system_prompt = messages[:1] if has_prompt else []
        body = messages[1:] if has_prompt else messages
        prior_summaries = [m for m in body if m.role == "system"]
        rest = [m for m in body if m.role != "system"]

        split = self.split_index(rest)
        if split == 0:
            return messages

        to_summarize = rest[:split]
        recent = rest[split:]

        transcript = "\n\n".join(
            line for line in (_transcript_entry(m) for m in prior_summaries + to_summarize) if line
        )
        if not transcript:
            return system_prompt + prior_summaries + recent

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
            return system_prompt + prior_summaries + recent

        summary_message = Message(role="system", content=f"{SUMMARY_PREFIX}\n{summary_text}")
        return system_prompt + [summary_message] + recent


def _transcript_entry(message: Message) -> str:
    parts = []
    content = message.content.strip()
    if content:
        if len(content) > TRANSCRIPT_MESSAGE_CHARS:
            content = content[:TRANSCRIPT_MESSAGE_CHARS] + " [...]"
        parts.append(content)
    for tc in message.tool_calls:
        args = str(tc.arguments)
        if len(args) > 300:
            args = args[:300] + "...}"
        parts.append(f"-> called {tc.name}({args})")
    if not parts:
        return ""
    role = f"tool:{message.name}" if message.role == "tool" and message.name else message.role
    return f"[{role}] " + "\n".join(parts)
