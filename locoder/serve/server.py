"""The HTTP side of ``locoder serve``: the phone web app, a JSON API, and a
live event stream (server-sent events) - all on Python's standard library.

Everything under /api/ except pairing needs ``Authorization: Bearer <key>``.
CORS is open because the key, not the origin, is what's checked: the Android
app's bundled page calls this from a file:// origin.

A tiny UDP responder lets the Android app find the PC on the local network
without typing an address.
"""

from __future__ import annotations

import json
import mimetypes
import re
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from locoder import __version__
from locoder.serve.e2e import EVENT, RESPONSE, Channel, E2eError, ReplayGuard
from locoder.serve.hub import Hub, HubError
from locoder.serve.pairing import Pairing

WEB_DIR = Path(__file__).parent / "web"
DEFAULT_PORT = 8765
DISCOVERY_MAGIC = b"locoder?"
HEARTBEAT = 15.0  # seconds between keep-alive comments on the event stream
MAX_BODY = 1_000_000


class Route:
    def __init__(self, method: str, pattern: str, handler: Callable[..., Any], *, auth: bool = True) -> None:
        self.method = method
        self.regex = re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", pattern) + "$")
        self.handler = handler
        self.auth = auth


class LocoderServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], hub: Hub, pairing: Pairing,
                 log: Callable[[str], None] = lambda msg: None) -> None:
        self.hub = hub
        self.pairing = pairing
        self.log = log
        self.closing = threading.Event()
        self.replay = ReplayGuard()
        super().__init__(address, Handler)

    def shutdown(self) -> None:
        self.closing.set()
        super().shutdown()


class Handler(BaseHTTPRequestHandler):
    server: LocoderServer
    protocol_version = "HTTP/1.1"

    # -- plumbing ----------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # quiet; notable things are logged by the routes themselves

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")

    def _json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _read_body(self) -> None:
        # Always drained, even for requests that get refused, so the next
        # request on a kept-alive connection starts at the right byte.
        length = int(self.headers.get("Content-Length") or 0)
        self._too_big = length > MAX_BODY
        if self._too_big:
            self.close_connection = True
        self._raw = self.rfile.read(length) if length and not self._too_big else b""

    def _body(self) -> dict[str, Any]:
        if self._too_big:
            raise HubError(413, "request too large")
        raw = self._raw
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise HubError(400, "body must be JSON") from None
        if not isinstance(data, dict):
            raise HubError(400, "body must be a JSON object")
        return data

    def _key(self) -> Optional[str]:
        header = self.headers.get("Authorization", "")
        return header[7:].strip() if header.lower().startswith("bearer ") else None

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        self._read_body()
        self._device = None  # set when a request arrived end-to-end encrypted
        self._seal_event = None
        if url.path == "/api/e2e":
            return self._e2e() if method == "POST" else self._error(405, "POST only")
        # Through the Cloudflare tunnel (it adds these headers): only sealed
        # requests from the app. The web app itself isn't served either, as
        # Cloudflare could change it on the way.
        if "Cf-Ray" in self.headers or "Cf-Connecting-Ip" in self.headers:
            if url.path.startswith("/api/"):
                return self._error(403, "over the internet, locoder only takes encrypted requests from its app")
            return self._tunnel_page()
        if not url.path.startswith("/api/"):
            if method == "GET":
                return self._static(url.path)
            return self._error(404, "not found")
        status, data = self._call(method, url.path, url.query)
        if data is not _STREAMED:
            self._json(status, data)

    def _call(self, method: str, path: str, query: str) -> tuple[int, Any]:
        """Runs one API call: (status, JSON body), or _STREAMED if it wrote its own response."""
        self.query = parse_qs(query)
        for route in ROUTES:
            match = route.regex.match(path)
            if not match or route.method != method:
                continue
            if route.auth and self._device is None and self.server.pairing.check(self._key()) is None:
                return 401, {"error": "not paired - pair this device again"}
            try:
                result = route.handler(self, **match.groupdict())
            except HubError as exc:
                return exc.status, {"error": str(exc)}
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return 0, _STREAMED
            except Exception as exc:  # noqa: BLE001 - one bad request shouldn't kill the server
                self.server.log(f"error handling {method} {path}: {exc}")
                return 500, {"error": f"server error: {exc}"}
            return 200, ({} if result is None else result)
        return 404, {"error": "no such API call"}

    def _e2e(self) -> None:
        """An end-to-end encrypted request (see e2e.py): unseal, run, seal the reply."""
        pairing = self.server.pairing
        try:
            envelope = json.loads(self._raw or b"null")
            if not isinstance(envelope, dict):
                raise ValueError
        except ValueError:
            return self._error(400, "bad envelope")
        pairing_request = "pair" in envelope
        device = None
        if pairing_request:
            sender = str(envelope["pair"])
            secret = pairing.pair_secret_for(sender)
            if secret is None:
                return self._error(403, "that QR code has expired or was used - scan the new one")
        else:
            device = pairing.device(str(envelope.get("d", "")))
            if device is None:
                return self._error(401, "not paired - pair this device again")
            sender, secret = device.id, bytes.fromhex(device.secret)
        channel = Channel(secret)
        try:
            message, nonce = channel.open_request(envelope, sender)
            self.server.replay.check(nonce, message.get("t"))
        except E2eError as exc:
            return self._error(403, str(exc))

        method = str(message.get("m", "GET")).upper()
        target = urlparse(str(message.get("p", "")))
        if not target.path.startswith("/api/") or target.path == "/api/e2e":
            return self._error(404, "no such API call")
        if pairing_request and target.path != "/api/pair":
            return self._error(403, "pair first")
        body = message.get("b")
        self._raw = json.dumps(body).encode() if body is not None else b""
        self._too_big = False
        self._device = device
        self._seal_event = lambda event: channel.seal(event, EVENT, nonce)
        status, data = self._call(method, target.path, target.query)
        if data is not _STREAMED:
            self._json(200, channel.seal({"s": status, "b": data}, RESPONSE, nonce))

    def _tunnel_page(self) -> None:
        body = TUNNEL_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(404, "not found")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # -- routes ------------------------------------------------------------------

    def hello(self) -> dict[str, Any]:
        """Unauthenticated: just enough for the phone to show which PC this is."""
        return {"app": "locoder", "computer": self.server.hub.computer, "version": __version__}

    def pair(self) -> dict[str, Any]:
        body = self._body()
        device = str(body.get("device") or "phone")
        key = self.server.pairing.pair(str(body.get("code", "")), device)
        if key is None:
            raise HubError(403, "wrong or expired code - check the one locoder serve shows now")
        self.server.log(f"paired with {device}")
        return {"key": key, "computer": self.server.hub.computer}

    def unpair(self) -> None:
        key = self._key()
        if self._device is not None:
            self.server.pairing.unpair_device(self._device.id)
        elif key:
            self.server.pairing.unpair(key)
        else:
            return
        self.server.log("a phone unpaired itself")

    def state(self) -> dict[str, Any]:
        return self.server.hub.state()

    def patch_state(self) -> dict[str, Any]:
        return self.server.hub.update(self._body())

    def models(self) -> dict[str, Any]:
        return {"models": self.server.hub.models(), "current": self.server.hub.provider.model}

    def chats(self) -> dict[str, Any]:
        return {"chats": self.server.hub.chats(), "project": self.server.hub.project_info()}

    def new_chat(self) -> dict[str, Any]:
        body = self._body()
        return self.server.hub.send(None, str(body.get("text", "")), body.get("files") or [])

    def chat(self, chat: str) -> dict[str, Any]:
        return self.server.hub.chat(chat)

    def rename(self, chat: str) -> dict[str, Any]:
        return self.server.hub.rename(chat, str(self._body().get("title", "")))

    def delete(self, chat: str) -> None:
        self.server.hub.delete(chat)

    def send_message(self, chat: str) -> dict[str, Any]:
        body = self._body()
        return self.server.hub.send(chat, str(body.get("text", "")), body.get("files") or [])

    def stop(self, chat: str) -> None:
        self.server.hub.stop(chat)

    def confirm(self, request: str) -> None:
        self.server.hub.answer(request, bool(self._body().get("approve")))

    def projects(self) -> dict[str, Any]:
        return {"projects": self.server.hub.projects()}

    def switch_project(self) -> dict[str, Any]:
        return self.server.hub.switch_project(str(self._body().get("path", "")))

    def files(self) -> dict[str, Any]:
        return {"files": self.server.hub.recent_files()}

    def events(self) -> object:
        """Server-sent events: every event after ``?since=``, then live ones."""
        hub = self.server.hub
        try:
            seq = int(self.query.get("since", ["0"])[0])
        except ValueError:
            seq = 0
        if seq <= 0:
            seq = hub.last_seq
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def write(data: dict[str, Any]) -> None:
            if self._seal_event:
                data = self._seal_event(data)
            self.wfile.write(b"data: " + json.dumps(data).encode("utf-8") + b"\n\n")

        write({"type": "hello", "seq": seq, "state": hub.state()})
        self.wfile.flush()
        while not self.server.closing.is_set():
            batch = hub.events_after(seq, timeout=HEARTBEAT)
            if batch is None:
                seq = hub.last_seq
                write({"type": "resync", "seq": seq})
            elif batch:
                for event in batch:
                    write(event)
                seq = batch[-1]["seq"]
            else:
                self.wfile.write(b": ping\n\n")
            self.wfile.flush()
        return _STREAMED


_STREAMED = object()

TUNNEL_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>locoder</title><body style="background:#0d0d0d;color:#e8e8e8;font:16px system-ui;padding:32px;max-width:460px">
<h3 style="color:#4a9eff">this is your PC's locoder</h3>
<p>Over the internet it only talks to the locoder app, end-to-end encrypted, so nothing
here can be read on the way. Open the app to use it.</p>
<p style="color:#6a6a6a">At home, use the address <code>locoder serve</code> shows instead.</p></body>"""

ROUTES = [
    Route("GET", "/api/hello", Handler.hello, auth=False),
    Route("POST", "/api/pair", Handler.pair, auth=False),
    Route("POST", "/api/unpair", Handler.unpair),
    Route("GET", "/api/state", Handler.state),
    Route("PATCH", "/api/state", Handler.patch_state),
    Route("GET", "/api/models", Handler.models),
    Route("GET", "/api/chats", Handler.chats),
    Route("POST", "/api/chats", Handler.new_chat),
    Route("GET", "/api/chats/{chat}", Handler.chat),
    Route("PATCH", "/api/chats/{chat}", Handler.rename),
    Route("DELETE", "/api/chats/{chat}", Handler.delete),
    Route("POST", "/api/chats/{chat}/messages", Handler.send_message),
    Route("POST", "/api/chats/{chat}/stop", Handler.stop),
    Route("POST", "/api/confirm/{request}", Handler.confirm),
    Route("GET", "/api/projects", Handler.projects),
    Route("POST", "/api/projects/switch", Handler.switch_project),
    Route("GET", "/api/files/recent", Handler.files),
    Route("GET", "/api/events", Handler.events),
]


def start_discovery(port: int, computer: str, stop: threading.Event) -> Optional[threading.Thread]:
    """Answer "locoder?" broadcasts on UDP ``port`` with where to connect."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(1.0)
    except OSError:
        return None
    reply = json.dumps({"app": "locoder", "computer": computer, "port": port}).encode("utf-8")

    def loop() -> None:
        with sock:
            while not stop.is_set():
                try:
                    data, addr = sock.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if data.strip() == DISCOVERY_MAGIC:
                    try:
                        sock.sendto(reply, addr)
                    except OSError:
                        pass

    thread = threading.Thread(target=loop, name="locoder-discovery", daemon=True)
    thread.start()
    return thread


def lan_addresses() -> list[str]:
    """This PC's IPv4 addresses a phone could reach, best guess first."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # no packet is sent
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
            if addr not in found:
                found.append(addr)
    except OSError:
        pass
    return [a for a in found if not a.startswith(("127.", "169.254."))] or ["127.0.0.1"]
