"""A model that takes a long time to start answering (a big prompt, thinking,
part of it on the CPU) must not be given up on after `timeout` seconds."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from locoder.providers.base import Message
from locoder.providers.ollama import OllamaProvider
from locoder.providers.openai_compat import OpenAICompatProvider


@pytest.fixture
def slow_server():
    delay = {"seconds": 1.2}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            time.sleep(delay["seconds"])  # silent while "reading the prompt"
            if self.path.endswith("/api/chat"):
                lines = [{"message": {"role": "assistant", "content": "slow but sure"}, "done": False},
                         {"message": {"role": "assistant", "content": ""}, "done": True}]
                body = "".join(json.dumps(line) + "\n" for line in lines).encode()
            else:
                chunk = {"choices": [{"delta": {"content": "slow but sure"}, "finish_reason": None}]}
                body = (f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", delay
    server.shutdown()
    server.server_close()


def answer(provider) -> tuple[str, list]:
    events = list(provider.chat([Message(role="user", content="hi")], stream=True))
    done = [e for e in events if e.type == "done"]
    return (done[0].message.content if done else ""), [e for e in events if e.type == "error"]


@pytest.mark.parametrize("make", [
    lambda url: OllamaProvider(model="m", base_url=url, timeout=0.4),
    lambda url: OpenAICompatProvider(model="m", base_url=url + "/v1", timeout=0.4),
])
def test_waits_longer_than_timeout_for_the_first_word(slow_server, make):
    url, _ = slow_server
    provider = make(url)
    assert provider.timeout < 1.2  # the old behaviour would have given up
    assert answer(provider) == ("slow but sure", [])


@pytest.mark.parametrize("make", [
    lambda url: OllamaProvider(model="m", base_url=url, timeout=0.2),
    lambda url: OpenAICompatProvider(model="m", base_url=url + "/v1", timeout=0.2),
])
def test_gives_up_with_a_helpful_message_when_the_model_stays_silent(slow_server, make):
    url, delay = slow_server
    delay["seconds"] = 1.5
    provider = make(url)
    provider.STREAM_PATIENCE = 0.4  # shortened so the test doesn't wait 15 minutes
    text, errors = answer(provider)
    assert text == "" and len(errors) == 1
    assert "num_ctx" in str(errors[0].error) and "said nothing" in str(errors[0].error)
