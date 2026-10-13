"""send_file: the model hands a project file to the phone, which downloads it."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from locoder.agent import Agent
from locoder.context import ContextManager
from locoder.providers.base import Message, ToolCall
from locoder.safety import MAX_SEND_BYTES, sendable_problem
from locoder.sandbox import Sandbox
from locoder.serve.transcript import to_entries
from locoder.session import SessionStore
from locoder.tools import registry
from locoder.tools.base import ToolContext
from test_agent_loop import ScriptedProvider
from test_serve import Client, pair_sealed, served  # noqa: F401 - the `served` fixture


def ctx_for(root: Path, *, phone: bool) -> ToolContext:
    return ToolContext(sandbox=Sandbox(root), yolo=True, confirm=lambda t, p, reasons=(): True, can_send_files=phone)


# -- what may be sent ---------------------------------------------------------------


def test_sendable_problem(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "page.html").write_text("<p>hi</p>")
    (project / ".env").write_text("KEY=1")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("x")
    (project / "cert.pem").write_text("x")
    (project / "sub").mkdir()
    (tmp_path / "outside.txt").write_text("x")
    big = project / "big.bin"
    big.write_bytes(b"0" * (MAX_SEND_BYTES + 1))

    assert sendable_problem(project, project / "page.html") is None
    assert "passwords or keys" in sendable_problem(project, project / ".env")
    assert "passwords or keys" in sendable_problem(project, project / "cert.pem")
    assert ".git" in sendable_problem(project, project / ".git" / "config")
    assert "outside the project" in sendable_problem(project, tmp_path / "outside.txt")
    assert "not a file" in sendable_problem(project, project / "sub")
    assert "limit is 8 MB" in sendable_problem(project, big)


# -- the tool ---------------------------------------------------------------------------


def test_send_file_tool(tmp_path: Path):
    (tmp_path / "todo.html").write_text("<h1>todo</h1>")
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / ".env").write_text("SECRET=1")

    html = registry.dispatch("send_file", {"path": "todo.html"}, ctx_for(tmp_path, phone=True))
    assert not html.is_error and html.meta == {"file": "todo.html", "size": 13}
    assert "tap Save" in html.output and "Open" in html.output
    assert "Open" not in registry.dispatch("send_file", {"path": "notes.txt"}, ctx_for(tmp_path, phone=True)).output

    secret = registry.dispatch("send_file", {"path": ".env"}, ctx_for(tmp_path, phone=True))
    assert secret.is_error and "passwords or keys" in secret.output
    missing = registry.dispatch("send_file", {"path": "nope.html"}, ctx_for(tmp_path, phone=True))
    assert missing.is_error and "no such file" in missing.output

    nobody = registry.dispatch("send_file", {"path": "todo.html"}, ctx_for(tmp_path, phone=False))
    assert nobody.is_error and "no phone is connected" in nobody.output


def test_only_phone_sessions_are_offered_the_tool(tmp_path: Path):
    class PhoneUI:
        can_send_files = True

        def __getattr__(self, name: str):
            return lambda *args, **kwargs: None

    class TerminalUI:
        def __getattr__(self, name: str):
            return lambda *args, **kwargs: None

    def names(ui) -> set[str]:
        agent = Agent(ScriptedProvider([Message(role="assistant", content="x")]), ui, Sandbox(tmp_path),
                      ContextManager(num_ctx=8192), SessionStore(tmp_path))
        return {s["function"]["name"] for s in agent._tool_schemas()}

    assert "send_file" in names(PhoneUI())
    assert "send_file" not in names(TerminalUI())


# -- the transcript shows a file card --------------------------------------------------


def test_transcript_gives_sent_files_a_card_and_not_refused_ones():
    ok_call = ToolCall(id="1", name="send_file", arguments={"path": "web/todo.html"})
    bad_call = ToolCall(id="2", name="send_file", arguments={"path": ".env"})
    entries = to_entries([
        Message(role="user", content="send me it"),
        Message(role="assistant", content="", tool_calls=[ok_call, bad_call]),
        Message(role="tool", content="'web/todo.html' (13 bytes) is ready on the user's phone: they can tap Save",
                tool_call_id="1", name="send_file"),
        Message(role="tool", content="'.env' may hold passwords or keys, so it isn't sent", tool_call_id="2",
                name="send_file"),
    ])
    sent, refused = entries[1], entries[2]
    assert sent["verb"] == "Send" and sent["status"] == "ok" and sent["summary"] == "sent"
    assert sent["file"]["path"] == "web/todo.html" and sent["file"]["name"] == "todo.html"
    assert refused["status"] == "err" and "file" not in refused


# -- the download endpoint ----------------------------------------------------------------


def test_phone_downloads_a_project_file(served):  # noqa: F811
    client, hub, pairing, project = served
    client.key = client.call("POST", "/api/pair", {"code": pairing.current_code()})[1]["key"]
    (project / "todo.html").write_bytes(b"<h1>todo</h1>\n")
    (project / ".env").write_text("SECRET=1")

    status, body = client.call("GET", "/api/files/get?path=todo.html")
    assert status == 200 and (body["name"], body["size"], body["mime"]) == ("todo.html", 14, "text/html")
    assert base64.b64decode(body["data"]) == b"<h1>todo</h1>\n"
    assert client.call("GET", "/api/files/get?path=.env")[0] == 403
    assert client.call("GET", "/api/files/get?path=..%2Fsecret.txt")[0] in (403, 404)
    assert client.call("GET", "/api/files/get?path=missing.html")[0] == 404
    assert client.call("GET", "/api/files/get")[0] == 400

    client.key = None
    assert client.call("GET", "/api/files/get?path=todo.html")[0] == 401


def test_download_through_the_encrypted_route(served):  # noqa: F811
    client, hub, pairing, project = served
    (project / "todo.html").write_text("<h1>todo</h1>")
    sealed = pair_sealed(client, pairing)
    status, body = sealed.call("GET", "/api/files/get?path=todo.html")
    assert status == 200 and base64.b64decode(body["data"]) == b"<h1>todo</h1>"
    assert sealed.call("GET", "/api/files/get?path=.env")[0] in (403, 404)
