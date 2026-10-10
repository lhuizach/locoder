from __future__ import annotations

from typing import Any, Iterator, Optional

from locoder.context import SUMMARY_PREFIX, ContextManager
from locoder.providers.base import Message, ModelProvider, StreamEvent, ToolCall


class CharCounter:
    """Deterministic stand-in for tiktoken: one token per character."""

    def count(self, text: str) -> int:
        return len(text)


class FakeProvider(ModelProvider):
    def __init__(self, reply: str = "summary text") -> None:
        super().__init__(model="fake", base_url="http://fake")
        self.reply = reply
        self.requests: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: Optional[list[dict[str, Any]]] = None, *, stream: bool = True) -> Iterator[StreamEvent]:
        self.requests.append(messages)
        yield StreamEvent(type="done", message=Message(role="assistant", content=self.reply))

    def list_models(self) -> list[str]:
        return ["fake"]


def _tool_exchange(i: int, size: int) -> list[Message]:
    call = ToolCall(name="read_file", arguments={"path": f"f{i}.py"}, id=f"call_{i}")
    return [
        Message(role="assistant", content="", tool_calls=[call]),
        Message(role="tool", content="x" * size, tool_call_id=f"call_{i}", name="read_file"),
    ]


def _manager(num_ctx: int = 1000) -> ContextManager:
    return ContextManager(num_ctx=num_ctx, threshold=0.85, counter=CharCounter())


def test_recent_tail_never_starts_with_orphaned_tool_result():
    mgr = _manager()
    messages = [Message(role="system", content="prompt"), Message(role="user", content="go")]
    for i in range(6):
        messages += _tool_exchange(i, 150)

    compacted = mgr.compact(messages, FakeProvider())

    rest = [m for m in compacted if m.role != "system"]
    assert rest, "recent tail should not be empty"
    assert rest[0].role != "tool"
    call_ids = {tc.id for m in rest for tc in m.tool_calls}
    for m in rest:
        if m.role == "tool":
            assert m.tool_call_id in call_ids


def test_compaction_gets_well_under_threshold():
    mgr = _manager()
    messages = [Message(role="system", content="prompt"), Message(role="user", content="go")]
    for i in range(10):
        messages += _tool_exchange(i, 100)
    assert mgr.should_compact(messages)

    compacted = mgr.compact(messages, FakeProvider())

    assert mgr.total_tokens(compacted) <= mgr.num_ctx * 0.6
    assert compacted[0].content == "prompt"
    assert compacted[1].content.startswith(SUMMARY_PREFIX)


def test_summary_request_includes_tool_calls_and_prior_summary():
    mgr = _manager()
    provider = FakeProvider()
    messages = [
        Message(role="system", content="prompt"),
        Message(role="system", content=f"{SUMMARY_PREFIX}\nearlier facts"),
        Message(role="user", content="go"),
    ]
    for i in range(10):
        messages += _tool_exchange(i, 100)

    compacted = mgr.compact(messages, provider)

    transcript = provider.requests[0][-1].content
    assert "earlier facts" in transcript
    assert "-> called read_file(" in transcript
    # Old summaries are folded into the new one rather than piling up.
    assert sum(m.content.startswith(SUMMARY_PREFIX) for m in compacted) == 1


def test_failed_summary_falls_back_to_truncation():
    mgr = _manager()
    messages = [Message(role="system", content="prompt"), Message(role="user", content="go")]
    for i in range(10):
        messages += _tool_exchange(i, 100)

    compacted = mgr.compact(messages, FakeProvider(reply=""))

    assert compacted[0].content == "prompt"
    assert mgr.total_tokens(compacted) < mgr.total_tokens(messages)
    assert [m for m in compacted if m.role != "system"][0].role != "tool"


def test_nothing_to_compact_returns_messages_unchanged():
    mgr = _manager()
    messages = [Message(role="system", content="prompt"), Message(role="user", content="y" * 2000)]

    assert mgr.compact(messages, FakeProvider()) == messages
