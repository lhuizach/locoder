"""``locoder serve``: pairing, the API over real HTTP, and the event stream,
with a scripted model standing in for Ollama."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import pytest

from locoder.providers.base import Message, ToolCall
from locoder.serve import hub as hub_module
from locoder.serve.hub import Hub
from locoder.serve.pairing import CODE_TTL, MAX_CODE_ATTEMPTS, Pairing
from locoder.serve.server import LocoderServer
from locoder.serve.transcript import to_entries
from test_agent_loop import ScriptedProvider

# -- pairing --------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_code_works_once_and_key_is_stored_hashed(tmp_path: Path):
    store = tmp_path / "devices.json"
    pairing = Pairing(store)
    code = pairing.current_code()

    key = pairing.pair(code, "Pixel")
    assert key and pairing.check(key).name == "Pixel"
    assert pairing.pair(code, "again") is None  # used up
    assert key not in store.read_text()
    assert Pairing(store).check(key) is not None  # survives a restart
    assert pairing.check("nope") is None and pairing.check(None) is None


def test_code_expires_and_wrong_guesses_burn_it(tmp_path: Path):
    clock = Clock()
    pairing = Pairing(tmp_path / "d.json", clock=clock)
    code = pairing.current_code()
    clock.now += CODE_TTL + 1
    assert pairing.pair(code, "late") is None

    code = pairing.current_code()
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(MAX_CODE_ATTEMPTS):
        assert pairing.pair(wrong, "guess") is None
    assert pairing.pair(code, "too late") is None  # burnt (a new code is 1-in-a-million the same)


def test_unpair(tmp_path: Path):
    pairing = Pairing(tmp_path / "d.json")
    a = pairing.pair(pairing.current_code(), "a")
    b = pairing.pair(pairing.current_code(), "b")
    pairing.unpair(a)
    assert pairing.check(a) is None and pairing.check(b) is not None
    assert pairing.unpair_all() == 1 and pairing.check(b) is None


# -- transcript -----------------------------------------------------------------


def test_saved_messages_become_phone_entries():
    call = ToolCall(id="c1", name="run_command", arguments={"command": "pytest"})
    entries = to_entries([
        Message(role="system", content="sys"),
        Message(role="user", content="run the tests"),
        Message(role="assistant", content="Running them.", tool_calls=[call]),
        Message(role="tool", content="exit code: 1\n\n1 failed", tool_call_id="c1", name="run_command"),
        Message(role="assistant", content="One test fails."),
    ])
    assert [e["type"] for e in entries] == ["user", "text", "tool", "text"]
    assert entries[1]["narration"] is True and entries[3]["narration"] is False
    tool = entries[2]
    assert (tool["verb"], tool["target"], tool["summary"], tool["status"]) == ("Run", "pytest", "exit 1", "err")


# -- the server, over HTTP --------------------------------------------------------


def write_then_answer() -> list[Message]:
    write = ToolCall(id="w1", name="write_file", arguments={"path": "hello.txt", "content": "hi\n"})
    return [
        Message(role="assistant", content="I'll create it.", tool_calls=[write]),
        Message(role="assistant", content="Created **hello.txt**."),
    ]


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.key: Optional[str] = None

    def call(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture
def served(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(hub_module, "record_project", lambda root: None)
    monkeypatch.setattr(hub_module, "recent_projects", lambda current, limit=6: [])
    project = tmp_path / "project"
    project.mkdir()
    replies = write_then_answer()

    def provider_factory(**kwargs):
        provider = ScriptedProvider(replies)
        provider.model = kwargs["model"]
        return provider

    hub = Hub(project, provider_factory=provider_factory)
    pairing = Pairing(tmp_path / "devices.json")
    server = LocoderServer(("127.0.0.1", 0), hub, pairing)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    client = Client(f"http://127.0.0.1:{server.server_address[1]}")
    yield client, hub, pairing, project
    server.shutdown()
    server.server_close()


def wait_for(hub: Hub, type_: str, since: int = 0, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in hub.events_after(since, timeout=0.2) or []:
            if event["type"] == type_:
                return event
    raise AssertionError(f"no {type_} event")


def test_api_needs_a_paired_key(served):
    client, hub, pairing, _ = served
    assert client.call("GET", "/api/hello")[1]["app"] == "locoder"
    assert client.call("GET", "/api/chats")[0] == 401
    assert client.call("POST", "/api/pair", {"code": "000000x"})[0] == 403

    status, body = client.call("POST", "/api/pair", {"code": pairing.current_code(), "device": "test"})
    assert status == 200 and body["key"]
    client.key = body["key"]
    assert client.call("GET", "/api/chats") == (200, {"chats": [], "project": hub.project_info()})


def test_a_whole_chat_from_the_phone(served):
    client, hub, pairing, project = served
    client.key = client.call("POST", "/api/pair", {"code": pairing.current_code()})[1]["key"]

    status, body = client.call("POST", "/api/chats", {"text": "make hello.txt"})
    assert status == 200
    chat = body["id"]

    # The write waits for the phone's OK.
    confirm = wait_for(hub, "confirm")["request"]
    assert confirm["title"].startswith("Create") or "hello.txt" in confirm["title"]
    assert client.call("POST", "/api/chats", {"text": "another"})[0] == 409  # one task at a time
    opened = client.call("GET", f"/api/chats/{chat}")[1]
    assert opened["live"]["pending"][0]["id"] == confirm["id"]
    assert opened["entries"] == [{"type": "user", "text": "make hello.txt"}]

    assert client.call("POST", f"/api/confirm/{confirm['id']}", {"approve": True})[0] == 200
    end = wait_for(hub, "run_end")
    assert end["status"] == "done" and end["preview"] == "Created hello.txt."
    assert (project / "hello.txt").read_text() == "hi\n"

    chats = client.call("GET", "/api/chats")[1]["chats"]
    assert [c["title"] for c in chats] == ["make hello.txt"]
    entries = client.call("GET", f"/api/chats/{chat}")[1]["entries"]
    assert [e["type"] for e in entries] == ["user", "text", "tool", "text"]
    assert entries[2]["verb"] == "Write" and entries[2]["status"] == "ok"

    assert client.call("PATCH", f"/api/chats/{chat}", {"title": "  greeting   file "})[1]["title"] == "greeting file"
    assert client.call("GET", "/api/chats")[1]["chats"][0]["title"] == "greeting file"
    assert client.call("DELETE", f"/api/chats/{chat}")[0] == 200
    assert client.call("GET", "/api/chats")[1]["chats"] == []
    assert client.call("GET", f"/api/chats/{chat}")[0] == 404


def test_stop_declines_the_waiting_approval(served):
    client, hub, pairing, project = served
    client.key = client.call("POST", "/api/pair", {"code": pairing.current_code()})[1]["key"]
    chat = client.call("POST", "/api/chats", {"text": "make hello.txt"})[1]["id"]
    wait_for(hub, "confirm")
    client.call("POST", f"/api/chats/{chat}/stop")
    assert wait_for(hub, "run_end")["status"] == "stopped"
    assert not (project / "hello.txt").exists()
    assert hub.run is None and hub.pending == {}


def test_settings_and_bad_ids(served):
    client, hub, pairing, project = served
    client.key = client.call("POST", "/api/pair", {"code": pairing.current_code()})[1]["key"]
    state = client.call("PATCH", "/api/state", {"mode": "plan", "model": "other:1b", "yolo": True})[1]
    assert (state["mode"], state["model"], state["yolo"]) == ("plan", "other:1b", True)
    assert "other:1b" in (project / ".locoder" / "config.yaml").read_text()
    assert client.call("PATCH", "/api/state", {"mode": "nope"})[0] == 400
    assert client.call("GET", "/api/chats/..%2F..%2Fsecret")[0] == 404
    assert client.call("GET", "/api/models")[1]["models"] == ["fake"]
    assert client.call("GET", "/../pyproject.toml")[0] == 404


def test_event_stream_replays_from_seq(served):
    client, hub, pairing, _ = served
    client.key = client.call("POST", "/api/pair", {"code": pairing.current_code()})[1]["key"]
    first = hub.publish("note", text="one")
    hub.publish("note", text="two")

    req = urllib.request.Request(f"{client.base}/api/events?since={first['seq']}",
                                 headers={"Authorization": f"Bearer {client.key}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        lines = []
        while len(lines) < 2:
            line = resp.readline().decode().strip()
            if line.startswith("data: "):
                lines.append(json.loads(line[6:]))
    assert lines[0]["type"] == "hello"
    assert lines[1]["text"] == "two"


def test_renamed_title_survives_more_messages(tmp_path: Path):
    from locoder.session import SessionStore

    store = SessionStore(tmp_path)
    store.save([Message(role="system", content="s"), Message(role="user", content="first")])
    store.rename(store.path, "my title")
    again = SessionStore(tmp_path)
    messages = again.resume(store.path)
    again.save(messages + [Message(role="assistant", content="more")])
    assert SessionStore._summarize(store.path).title == "my title"
    assert again.chat_path("../x") is None and again.chat_path(store.path.stem) == store.path


# -- the pairing QR code ----------------------------------------------------------


def test_pairing_link_round_trips():
    from locoder.serve.qr import pair_url, parse_pair_url

    url = pair_url("192.168.1.24", 8765, "012345")
    assert url == "http://192.168.1.24:8765/#pair=012345"
    assert parse_pair_url(url) == ("http://192.168.1.24:8765", "012345")
    assert parse_pair_url("http://192.168.1.24:8765/") is None
    assert parse_pair_url("http://x/#pair=12ab56") is None
    assert parse_pair_url("javascript:alert(1)#pair=123456") is None


def test_terminal_qr_matches_the_real_code():
    import segno

    from locoder.serve.qr import DARK, qr_text

    data = "http://192.168.1.24:8765/#pair=012345"
    text = qr_text(data)
    # Rebuild the module grid from the half blocks: foreground = top, background = bottom.
    rows: list[list[bool]] = []
    for line in text.divide([i for i, ch in enumerate(text.plain) if ch == "\n"]):
        top, bottom = [], []
        for span in line.spans:
            fg, bg = str(span.style).split(" on ")
            top.append(fg == DARK)
            bottom.append(bg == DARK)
        if top:
            rows += [top, bottom]
    expected = [[bool(v) for v in row] for row in segno.make(data, error="m", micro=False).matrix]
    border = 2
    inner = [row[border:-border] for row in rows[border:border + len(expected)]]
    assert inner == expected
    assert not any(rows[0]) and not any(row[0] for row in rows)  # light quiet zone


def test_no_qr_without_colours():
    import io

    from rich.console import Console

    from locoder.serve import _can_show_qr, _pairing_block

    plain = Console(file=io.StringIO(), color_system=None)
    assert not _can_show_qr(plain)
    out = Console(file=io.StringIO(), color_system=None, width=80)
    out.print(_pairing_block("1.2.3.4", 8765, "123456", fancy=False))
    assert "123 456" in out.file.getvalue() and "▀" not in out.file.getvalue()
    assert _can_show_qr(Console(file=io.StringIO(), color_system="truecolor", force_terminal=True))


# -- end-to-end encryption (the tunnel) ---------------------------------------------


class SealedClient:
    """Talks to /api/e2e the way the app does over the tunnel."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.channel = None
        self.sender = ""
        self.pairing = False

    def post(self, envelope: dict, headers: Optional[dict] = None) -> tuple[int, Any]:
        req = urllib.request.Request(self.client.base + "/api/e2e", data=json.dumps(envelope).encode(),
                                     method="POST", headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def call(self, method: str, path: str, body: Any = None, *, t: Optional[float] = None) -> tuple[int, Any]:
        from locoder.serve.e2e import RESPONSE, unb64

        message = {"m": method, "p": path, "b": body, "t": (time.time() if t is None else t) * 1000}
        envelope = self.channel.seal_request(message, self.sender, pair=self.pairing)
        status, data = self.post(envelope, {"Cf-Ray": "test"})  # as if through Cloudflare
        if status != 200:
            return status, data
        reply = self.channel.open(data, RESPONSE, unb64(envelope["n"]))
        return reply["s"], reply["b"]


def pair_sealed(client: Client, pairing: Pairing) -> SealedClient:
    from locoder.serve.e2e import Channel, device_id, device_secret, pair_id

    pairing.current_code()
    sealed = SealedClient(client)
    sealed.channel, sealed.sender, sealed.pairing = Channel(pairing.pair_secret), pair_id(pairing.pair_secret), True
    status, body = sealed.call("POST", "/api/pair", {"code": pairing.code, "device": "remote phone"})
    assert status == 200, body
    key = body["key"]
    sealed.channel, sealed.sender, sealed.pairing = Channel(device_secret(key)), device_id(key), False
    return sealed


def test_pairing_and_calls_through_the_tunnel_are_sealed(served):
    client, hub, pairing, project = served
    sealed = pair_sealed(client, pairing)
    assert pairing.devices[-1].name == "remote phone"
    assert sealed.call("GET", "/api/chats") == (200, {"chats": [], "project": hub.project_info()})
    assert sealed.call("PATCH", "/api/state", {"mode": "ask"})[1]["mode"] == "ask"
    assert sealed.call("GET", "/api/nope")[0] == 404


def test_sealed_requests_cant_be_replayed_tampered_or_stale(served):
    from locoder.serve.e2e import b64, unb64

    client, hub, pairing, project = served
    sealed = pair_sealed(client, pairing)
    envelope = sealed.channel.seal_request({"m": "GET", "p": "/api/chats", "t": time.time() * 1000}, sealed.sender)
    assert sealed.post(envelope)[0] == 200
    assert sealed.post(envelope)[0] == 403  # replay

    envelope = sealed.channel.seal_request({"m": "GET", "p": "/api/chats", "t": time.time() * 1000}, sealed.sender)
    broken = bytearray(unb64(envelope["c"]))
    broken[0] ^= 1
    assert sealed.post({**envelope, "c": b64(bytes(broken))})[0] == 403
    assert sealed.call("GET", "/api/chats", t=time.time() - 3600)[0] == 403  # an hour old
    assert sealed.post({**envelope, "d": "0" * 32})[0] == 401  # unknown phone


def test_the_tunnel_only_takes_sealed_requests(served):
    client, hub, pairing, project = served
    client.key = client.call("POST", "/api/pair", {"code": pairing.current_code()})[1]["key"]
    req = urllib.request.Request(client.base + "/api/chats", headers={"Authorization": f"Bearer {client.key}",
                                                                      "Cf-Ray": "x"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 403
    page = urllib.request.urlopen(urllib.request.Request(client.base + "/", headers={"Cf-Ray": "x"}), timeout=5).read()
    assert b"only talks to the locoder app" in page and b"<script" not in page

    # A QR pairing secret works once.
    from locoder.serve.e2e import Channel, pair_id

    secret = pairing.pair_secret
    sealed = SealedClient(client)
    sealed.channel, sealed.sender, sealed.pairing = Channel(secret), pair_id(secret), True
    assert sealed.call("POST", "/api/pair", {"code": pairing.code})[0] == 200
    assert sealed.call("POST", "/api/pair", {"code": pairing.code})[0] == 403


def test_sealed_event_stream(served):
    from locoder.serve.e2e import EVENT, unb64

    client, hub, pairing, project = served
    sealed = pair_sealed(client, pairing)
    first = hub.publish("note", text="one")
    hub.publish("note", text="secret text")
    envelope = sealed.channel.seal_request(
        {"m": "GET", "p": f"/api/events?since={first['seq']}", "t": time.time() * 1000}, sealed.sender)
    req = urllib.request.Request(client.base + "/api/e2e", data=json.dumps(envelope).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Cf-Ray": "x"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        events, raw = [], b""
        while len(events) < 2:
            line = resp.readline()
            raw += line
            if line.startswith(b"data: "):
                events.append(sealed.channel.open(json.loads(line[6:]), EVENT, unb64(envelope["n"])))
    assert [e["type"] for e in events] == ["hello", "note"] and events[1]["text"] == "secret text"
    assert b"secret text" not in raw


def test_phones_paired_before_encryption_get_a_secret(tmp_path: Path):
    store = tmp_path / "d.json"
    pairing = Pairing(store)
    key = pairing.pair(pairing.current_code(), "old")
    data = json.loads(store.read_text())
    data[0].pop("secret")
    store.write_text(json.dumps(data))

    old = Pairing(store)
    assert old.device(old.devices[0].id) is None  # no secret yet
    assert old.check(key) is not None
    assert old.device(old.devices[0].id) is not None
    assert json.loads(store.read_text())[0]["secret"]


def test_pairing_link_prints_on_one_line_when_there_is_no_qr():
    import io

    from rich.console import Console

    from locoder.serve import _print_pairing

    out = Console(file=io.StringIO(), color_system=None, width=40)
    _print_pairing(out, "192.168.1.158", 8765, "123456", secret=bytes(range(32)),
                   remote="https://some-long-tunnel-name-here.trycloudflare.com")
    lines = out.file.getvalue().splitlines()
    link = next(line for line in lines if "pairing link" in line).split()[-1]
    assert link.endswith("&r=https://some-long-tunnel-name-here.trycloudflare.com") and "#pair=123456&k=" in link
