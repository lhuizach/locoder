from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Optional

from locoder.agent import EMPTY_REPLY_NUDGE, MAX_IDENTICAL_FAILURES, Agent
from locoder.context import ContextManager
from locoder.providers.base import Message, ModelProvider, StreamEvent, ToolCall
from locoder.sandbox import Sandbox
from locoder.session import SessionStore


class ScriptedProvider(ModelProvider):
    """Replies with the given assistant messages in order (the last one repeats)."""

    def __init__(self, replies: list[Message]) -> None:
        super().__init__(model="fake", base_url="http://fake")
        self.replies = replies
        self.requests: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: Optional[list[dict[str, Any]]] = None, *, stream: bool = True) -> Iterator[StreamEvent]:
        self.requests.append(list(messages))
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        yield StreamEvent(type="done", message=reply)

    def list_models(self) -> list[str]:
        return ["fake"]


class RecordingUI:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.results: list[str] = []

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def tool_result(self, name: str, result) -> None:  # noqa: ANN001
        self.results.append(result.output)

    def __getattr__(self, name: str):
        return lambda *args, **kwargs: None


def _agent(tmp_path: Path, provider: ModelProvider, ui: RecordingUI) -> Agent:
    return Agent(provider, ui, Sandbox(tmp_path), ContextManager(num_ctx=32768), SessionStore(tmp_path), yolo=True)


def test_system_prompt_tells_model_the_project_root(tmp_path: Path):
    provider = ScriptedProvider([Message(role="assistant", content="hi")])
    _agent(tmp_path, provider, RecordingUI()).run_turn("hello")

    system = provider.requests[0][0]
    assert system.role == "system"
    assert str(tmp_path.resolve()) in system.content
    assert "don't cd" in system.content


def test_empty_reply_gets_one_transient_nudge(tmp_path: Path):
    provider = ScriptedProvider([
        Message(role="assistant", content=""),
        Message(role="assistant", content="Done: added the command."),
    ])
    ui = RecordingUI()
    agent = _agent(tmp_path, provider, ui)

    agent.run_turn("do it")

    assert provider.requests[1][-1].content == EMPTY_REPLY_NUDGE
    assert [m.content for m in agent.messages if m.role == "assistant"] == ["Done: added the command."]
    assert all(m.content != EMPTY_REPLY_NUDGE for m in agent.messages)
    assert ui.warnings == []


def test_empty_reply_twice_warns_instead_of_looping(tmp_path: Path):
    provider = ScriptedProvider([Message(role="assistant", content="")])
    ui = RecordingUI()

    _agent(tmp_path, provider, ui).run_turn("do it")

    assert len(provider.requests) == 2
    assert any("without a reply" in w for w in ui.warnings)


def test_repeated_identical_failure_is_flagged_then_stops_the_turn(tmp_path: Path):
    failing = ToolCall(name="read_file", arguments={"path": "missing.txt"})
    provider = ScriptedProvider([Message(role="assistant", tool_calls=[failing])])
    ui = RecordingUI()

    _agent(tmp_path, provider, ui).run_turn("read it")

    assert len(provider.requests) == MAX_IDENTICAL_FAILURES
    assert "already failed the same way" not in ui.results[0]
    assert "already failed the same way" in ui.results[1]
    assert any("same failing tool call" in w for w in ui.warnings)
