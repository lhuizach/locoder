"""End-to-end encryption between a paired phone and this PC.

Used when the phone reaches ``locoder serve`` through a tunnel (Cloudflare):
the tunnel only ever sees sealed envelopes, never your code, chats or keys.
The phone app does the same steps (locoder/serve/web/index.html and the
Android app's E2e.kt), so the details here are the protocol:

- Every paired phone has a 32-byte secret ``S = HMAC-SHA256("locoder e2e",
  key)`` derived from its pairing key; the PC keeps ``S`` (not the key).
- Pairing over the tunnel uses the 32-byte secret in the QR code instead;
  only someone who saw your screen has it.
- Two AES-256-GCM keys per secret, one per direction:
  ``HMAC-SHA256(S, "c2s")`` and ``HMAC-SHA256(S, "s2c")``.
- A request is ``{"v": 1, "d": device_id, "n": nonce, "c": ciphertext}``
  (or ``"pair": pair_id`` instead of ``"d"``) with associated data
  ``"locoder-req:" + id``; the plaintext is ``{"m", "p", "b", "t"}``:
  method, path, JSON body and the phone's clock in milliseconds.
- The reply is ``{"n", "c"}`` holding ``{"s": status, "b": body}``, bound to
  the request by associated data ``"locoder-res:" + request nonce``; event
  stream lines are the same with ``"locoder-evt:" + request nonce``.
- A request is refused if its clock is more than 5 minutes off or its nonce
  was already used (no replays).

Base64 is standard with padding.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_SKEW_MS = 5 * 60 * 1000


class E2eError(Exception):
    """A sealed message that can't be trusted: wrong key, tampered, replayed or stale."""


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise E2eError("bad base64") from exc


def device_secret(key: str) -> bytes:
    return hmac.new(b"locoder e2e", key.encode("utf-8"), hashlib.sha256).digest()


def device_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def pair_id(pair_secret: bytes) -> str:
    return hashlib.sha256(pair_secret).hexdigest()[:32]


class Channel:
    """The two directional keys for one secret."""

    def __init__(self, secret: bytes) -> None:
        self.c2s = AESGCM(hmac.new(secret, b"c2s", hashlib.sha256).digest())
        self.s2c = AESGCM(hmac.new(secret, b"s2c", hashlib.sha256).digest())

    # phone -> PC
    def open_request(self, envelope: dict[str, Any], sender: str) -> tuple[dict[str, Any], bytes]:
        nonce = unb64(str(envelope.get("n", "")))
        if len(nonce) != 12:
            raise E2eError("bad nonce")
        try:
            plain = self.c2s.decrypt(nonce, unb64(str(envelope.get("c", ""))), b"locoder-req:" + sender.encode())
        except InvalidTag as exc:
            raise E2eError("can't decrypt (wrong key or tampered)") from exc
        try:
            message = json.loads(plain)
        except ValueError as exc:
            raise E2eError("bad message") from exc
        if not isinstance(message, dict):
            raise E2eError("bad message")
        return message, nonce

    def seal_request(self, message: dict[str, Any], sender: str, *, pair: bool = False) -> dict[str, Any]:
        """The phone's side, for tests (and anything else that talks to serve)."""
        nonce = os.urandom(12)
        sealed = self.c2s.encrypt(nonce, json.dumps(message).encode(), b"locoder-req:" + sender.encode())
        return {"v": 1, ("pair" if pair else "d"): sender, "n": b64(nonce), "c": b64(sealed)}

    # PC -> phone
    def seal(self, data: dict[str, Any], label: bytes, request_nonce: bytes) -> dict[str, Any]:
        nonce = os.urandom(12)
        sealed = self.s2c.encrypt(nonce, json.dumps(data).encode(), label + request_nonce)
        return {"n": b64(nonce), "c": b64(sealed)}

    def open(self, envelope: dict[str, Any], label: bytes, request_nonce: bytes) -> dict[str, Any]:
        try:
            plain = self.s2c.decrypt(unb64(envelope["n"]), unb64(envelope["c"]), label + request_nonce)
        except (InvalidTag, KeyError) as exc:
            raise E2eError("can't decrypt reply") from exc
        return json.loads(plain)


RESPONSE = b"locoder-res:"
EVENT = b"locoder-evt:"


class ReplayGuard:
    """Remembers recent nonces so a captured request can't be sent again."""

    def __init__(self) -> None:
        self._seen: dict[bytes, float] = {}
        self._lock = threading.Lock()

    def check(self, nonce: bytes, sent_ms: Any, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not isinstance(sent_ms, (int, float)) or abs(now * 1000 - sent_ms) > MAX_SKEW_MS:
            raise E2eError("the phone's clock is off by more than 5 minutes (or the request is old)")
        with self._lock:
            cutoff = now - 2 * MAX_SKEW_MS / 1000
            if len(self._seen) > 10_000 or any(t < cutoff for t in list(self._seen.values())[:1]):
                self._seen = {n: t for n, t in self._seen.items() if t >= cutoff}
            if nonce in self._seen:
                raise E2eError("replayed request")
            self._seen[nonce] = now
