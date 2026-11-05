"""``lochat serve``: locoder serve with a chat-only agent, its own folder and its own page."""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from locoder.lochat import LOCHAT, WEB_DIR, ChatAgent, LochatHub
from locoder.providers.base import Message, ModelProvider, StreamEvent, ToolCall
from locoder.serve.hub import HubError
from locoder.serve.pairing import Pairing
from locoder.serve.server import LocoderServer
from test_agent_loop import RecordingUI


class ToolRecordingProvider(ModelProvider):
    """Replies in order (the last one repeats) and notes which tools it was offered."""

    def __init__(self, replies: list[Message]) -> None:
        super().__init__(model="fake", base_url="http://fake")
        self.replies = replies
        self.requests: list[list[Message]] = []
        self.offered: list[list[str]] = []
        self.title_requests: list[str] = []

    def chat(self, messages: list[Message], tools: Optional[list[dict[str, Any]]] = None, *, stream: bool = True) -> Iterator[StreamEvent]:
        if len(messages) == 1 and "short title" in messages[0].content:
            self.title_requests.append(messages[0].content)
            yield StreamEvent(type="done", message=Message(role="assistant", content='"Saying hello."'))
            return
        self.requests.append(list(messages))
        self.offered.append(sorted(t["function"]["name"] for t in tools or []))
        reply = self.replies[min(len(self.requests) - 1, len(self.replies) - 1)]
        yield StreamEvent(type="done", message=reply)

    def list_models(self) -> list[str]:
        return ["fake"]


def wait_for_end(hub: LochatHub, since: int, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in hub.events_after(since, timeout=0.2) or []:
            if event["type"] == "run_end":
                return event
    raise AssertionError("the run never ended")


@pytest.fixture
def lochat(tmp_path: Path):
    write = ToolCall(id="w1", name="write_file", arguments={"path": "x.txt", "content": "hi\n"})
    provider = ToolRecordingProvider([
        Message(role="assistant", content="", tool_calls=[write]),  # a model trying a file tool anyway
        Message(role="assistant", content="Hello! How can I help?"),
    ])
    hub = LochatHub(tmp_path / "lochat", provider_factory=lambda **kwargs: provider)
    hub.web = False  # not whatever this PC's config says
    return hub, provider, tmp_path


def test_lochat_is_chat_only(lochat):
    hub, provider, tmp_path = lochat
    seq = hub.last_seq
    chat = hub.send(None, "hi there")["id"]
    assert wait_for_end(hub, seq)["status"] == "done"

    assert provider.offered[0] == []  # no tools at all while Web is off
    assert not list(tmp_path.rglob("x.txt"))  # the file tool the model named didn't run
    system = provider.requests[0][0].content
    assert system.startswith("You are lochat") and "Project root" not in system

    # Its own place, not the folder it was started in.
    assert (tmp_path / "lochat" / ".locoder" / "sessions" / f"{chat}.json").is_file()
    assert hub.sandbox.root == (tmp_path / "lochat" / "workspace").resolve()
    with pytest.raises(HubError):
        hub.read_file("../.locoder/sessions")
    assert [p["name"] for p in hub.projects()] == ["lochat"]


def test_a_new_chat_is_named_by_the_model_first(lochat):
    hub, provider, _ = lochat
    seq = hub.last_seq
    chat = hub.send(None, "hi there")["id"]
    wait_for_end(hub, seq)
    events = hub.events_after(seq)
    kinds = [e["type"] for e in events if e.get("chat") == chat]
    assert kinds.index("title") < kinds.index("run_end")
    assert next(e for e in events if e["type"] == "title")["title"] == "Saying hello"
    assert [c["title"] for c in hub.chats()] == ["Saying hello"]  # kept through the chat's saves

    seq = hub.last_seq
    hub.send(chat, "and again")  # not a new chat: no new name
    wait_for_end(hub, seq)
    assert len(provider.title_requests) == 1


def test_titles_are_cleaned_up():
    from locoder.lochat import clean_title

    assert clean_title("<think>hmm</think>\nTitle: **Dinner ideas.**") == "Dinner ideas"
    assert clean_title("") == ""


def test_web_on_offers_only_web_tools(lochat):
    hub, provider, _ = lochat
    hub.update({"web": True})
    seq = hub.last_seq
    hub.send(None, "what's new?")
    wait_for_end(hub, seq)
    assert provider.offered[0] == ["web_fetch", "web_search"]
    assert "You can search the web" in provider.requests[0][0].content


def test_chat_agent_refuses_everything_but_web(lochat):
    hub, _, _ = lochat
    agent = hub._new_agent(RecordingUI(), hub.store)
    assert isinstance(agent, ChatAgent)
    assert "chat only" in agent._refuse_tool("run_command")
    assert "switched off" in agent._refuse_tool("web_search")
    agent.set_web(True)
    assert agent._refuse_tool("web_search") is None and agent._refuse_tool("read_file")


def test_the_server_hands_out_lochat_page(lochat):
    hub, _, tmp_path = lochat
    server = LocoderServer(("127.0.0.1", 0), hub, Pairing(tmp_path / "devices.json"),
                           web_dir=WEB_DIR, app_name="lochat")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as res:
            assert "lochat serve" in res.read().decode("utf-8")
        with urllib.request.urlopen(base + "/api/hello") as res:
            assert json.loads(res.read())["app"] == "lochat"
    finally:
        server.shutdown()
        server.server_close()


def test_lochat_keeps_to_itself():
    assert LOCHAT.port != 8765 and LOCHAT.devices_file.parent.name == ".lochat"
    assert not LOCHAT.discovery and not LOCHAT.serves_folder
