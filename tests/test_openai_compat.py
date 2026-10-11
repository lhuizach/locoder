from __future__ import annotations

import json
from typing import Any

import pytest

from locoder.providers import openai_compat
from locoder.providers.base import Message
from locoder.providers.groq import GROQ_BASE_URL, GroqProvider
from locoder.providers.openai_compat import OpenAICompatProvider, ThinkSplitter
from locoder.providers.registry import create_provider


class FakeResponse:
    def __init__(self, status: int = 200, lines: list[str] | None = None, body: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._lines = lines or []
        self._body = body
        self.headers = headers or {}
        self.text = json.dumps(body) if body is not None else ""
        self.reason = "error"

    def iter_lines(self, decode_unicode: bool = True):
        yield from self._lines

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body

    def close(self) -> None:
        pass


def _sse(*deltas: dict) -> list[str]:
    return [f"data: {json.dumps({'choices': [{'delta': d}]})}" for d in deltas] + ["data: [DONE]"]


@pytest.fixture
def posts(monkeypatch):
    """Queue of responses for requests.post; records each payload sent."""
    queue: list[FakeResponse] = []
    sent: list[dict] = []

    def fake_post(url, json=None, headers=None, stream=None, timeout=None):  # noqa: A002
        sent.append({"url": url, "payload": json, "headers": headers})
        return queue.pop(0)

    monkeypatch.setattr(openai_compat.requests, "post", fake_post)
    monkeypatch.setattr(openai_compat.time, "sleep", lambda s: None)
    return queue, sent


def _events(provider, messages=None):
    return list(provider.chat(messages or [Message(role="user", content="hi")]))


def test_think_splitter_handles_tags_split_across_chunks():
    s = ThinkSplitter()
    pieces = []
    for chunk in ["<thi", "nk>plan it", "</th", "ink>Answer", " here"]:
        pieces += s.feed(chunk)
    pieces += s.flush()
    thinking = "".join(t for k, t in pieces if k == "thinking")
    answer = "".join(t for k, t in pieces if k == "token")
    assert thinking == "plan it"
    assert answer == "Answer here"


def test_inline_think_tags_stay_out_of_the_saved_answer(posts):
    queue, _ = posts
    queue.append(FakeResponse(lines=_sse({"content": "<think>secret"}, {"content": " plan</think>Done."})))
    provider = OpenAICompatProvider(model="m", base_url="http://x/v1")

    events = _events(provider)

    done = events[-1]
    assert done.type == "done"
    assert done.message.content == "Done."
    assert "".join(e.token for e in events if e.type == "thinking") == "secret plan"


def test_separate_reasoning_field_is_streamed_as_thinking(posts):
    queue, _ = posts
    queue.append(FakeResponse(lines=_sse({"reasoning": "hmm"}, {"content": "Hi"})))

    events = _events(OpenAICompatProvider(model="m", base_url="http://x/v1"))

    assert [e.type for e in events] == ["thinking", "token", "done"]
    assert events[-1].message.content == "Hi"


def test_rate_limit_is_retried_using_retry_after(posts):
    queue, sent = posts
    queue.append(FakeResponse(status=429, body={"error": {"message": "slow down"}}, headers={"retry-after": "2"}))
    queue.append(FakeResponse(lines=_sse({"content": "ok"})))

    events = _events(OpenAICompatProvider(model="m", base_url="http://x/v1"))

    assert len(sent) == 2
    assert events[-1].message.content == "ok"


def test_http_error_includes_the_servers_message(posts):
    queue, _ = posts
    queue.append(FakeResponse(status=413, body={"error": {"message": "Request too large for model: TPM limit"}}))

    events = _events(OpenAICompatProvider(model="m", base_url="http://x/v1"))

    assert events[-1].type == "error"
    assert "HTTP 413" in str(events[-1].error)
    assert "TPM limit" in str(events[-1].error)


def test_groq_defaults(monkeypatch, posts):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    queue, sent = posts
    queue.append(FakeResponse(lines=_sse({"content": "ok"})))

    # The config's default base_url is Ollama's; groq must replace it.
    provider = create_provider(kind="groq", model="qwen/qwen3.8-27b", base_url="http://localhost:11434")
    _events(provider)

    assert isinstance(provider, GroqProvider)
    assert sent[0]["url"] == f"{GROQ_BASE_URL}/chat/completions"
    assert sent[0]["headers"]["Authorization"] == "Bearer gsk_test"
    assert sent[0]["payload"]["reasoning_format"] == "parsed"


def test_groq_skips_reasoning_format_for_models_that_reject_it(posts):
    queue, sent = posts
    queue.append(FakeResponse(lines=_sse({"content": "ok"})))

    _events(GroqProvider(model="llama-3.3-70b-versatile", base_url="", api_key="k"))

    assert "reasoning_format" not in sent[0]["payload"]
