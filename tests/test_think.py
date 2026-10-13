"""The thinking switch: off asks Ollama to skip the model's thinking step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from locoder import cli
from locoder.config import AppConfig
from locoder.providers import ollama as ollama_module
from locoder.providers.base import Message
from locoder.providers.ollama import OllamaProvider
from locoder.providers.registry import create_provider
from locoder.serve import hub as hub_module
from locoder.serve.hub import Hub
from test_agent_loop import ScriptedProvider


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def iter_lines(self, decode_unicode=True):
        yield json.dumps({"message": {"role": "assistant", "content": "ok"}, "done": True})

    def close(self) -> None:
        pass


@pytest.fixture
def payloads(monkeypatch):
    sent: list[dict] = []

    def fake_post(url, json=None, **kwargs):
        sent.append(json)
        return FakeResponse()

    monkeypatch.setattr(ollama_module.requests, "post", fake_post)
    return sent


def ask(provider) -> None:
    list(provider.chat([Message(role="user", content="hi")], stream=True))


def test_thinking_is_only_switched_off_when_asked(payloads):
    ask(OllamaProvider(model="m", base_url="http://x"))
    ask(OllamaProvider(model="m", base_url="http://x", think=False))
    assert "think" not in payloads[0]  # default: the field isn't sent, so non-thinking models are untouched
    assert payloads[1]["think"] is False


def test_provider_copies_and_registry(payloads):
    on = create_provider(kind="ollama", model="m", base_url="http://x")
    off = create_provider(kind="ollama", model="m", base_url="http://x", think=False)
    assert on.think is True and off.think is False
    assert on.with_think(False).think is False and on.think is True  # a copy
    assert off.with_model("other").think is False  # survives a model switch


def test_config_round_trip(tmp_path: Path):
    config = AppConfig.load(tmp_path)
    assert config.think is True and config.provider_kwargs()["think"] is True
    config.think = False
    config.save(tmp_path)
    assert "think: false" in (tmp_path / ".locoder" / "config.yaml").read_text()
    assert AppConfig.load(tmp_path).think is False


def test_phone_switch_saves_and_applies(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(hub_module, "record_project", lambda root: None)
    made: list[bool] = []

    def factory(**kwargs):
        made.append(kwargs["think"])
        provider = ScriptedProvider([Message(role="assistant", content="x")])
        provider.think = kwargs["think"]
        return provider

    project = tmp_path / "p"
    project.mkdir()
    hub = Hub(project, provider_factory=factory)
    assert hub.state()["think"] is True
    state = hub.update({"think": False})
    assert state["think"] is False and hub.provider.think is False and made[-1] is False
    assert AppConfig.load(project).think is False  # kept for next time
    assert hub.update({"think": True})["think"] is True


def test_think_slash_command_toggles(tmp_path: Path):
    class Agent:
        def __init__(self):
            self.provider = OllamaProvider(model="m", base_url="http://x")

        def set_provider(self, provider):
            self.provider = provider

    class UI:
        def __init__(self):
            self.said: list[str] = []

        def system(self, msg):
            self.said.append(msg)

    agent, ui = Agent(), UI()
    cli._handle_slash_command("/think", agent, ui)
    assert agent.provider.think is False and "off" in ui.said[-1]
    cli._handle_slash_command("/think", agent, ui)
    assert agent.provider.think is True and "on" in ui.said[-1]
