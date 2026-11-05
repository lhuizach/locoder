"""Pairing phones with this PC.

``locoder serve`` shows a 6-digit code. A phone that sends it back gets a
long random key, which it then sends with every request. The code works
once, expires after a few minutes, and is thrown away after a few wrong
guesses. On disk (``~/.locoder/devices.json``) each phone has only a hash
of its key - enough to check it, not to use it - and its end-to-end
encryption secret (see e2e.py), which only matters to this PC.

Each code also comes with a random pairing secret, shown only in the QR code,
which lets a phone pair through the encrypted tunnel.

Pairing can be closed (``closed``): then no code works at all, so once your
phone is paired nobody else can pair, even if they see a code. ``locoder
serve`` closes it whenever a phone is already paired, and right after a phone
pairs; ``locoder serve --add-phone`` opens it for one more.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from locoder.serve.e2e import device_secret, pair_id

DEVICES_FILE = Path.home() / ".locoder" / "devices.json"
CODE_TTL = 10 * 60  # seconds
MAX_CODE_ATTEMPTS = 5


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass
class Device:
    key_hash: str
    name: str
    paired_at: float
    last_seen: float = 0.0
    secret: str = ""  # hex end-to-end secret; "" for phones paired before encryption

    @property
    def id(self) -> str:
        return self.key_hash[:32]


class Pairing:
    def __init__(
        self,
        store: Path = DEVICES_FILE,
        *,
        clock: Callable[[], float] = time.time,
        on_new_code: Callable[[str], None] = lambda code: None,
        close_after_pair: bool = False,
    ) -> None:
        self.store = store
        self.clock = clock
        self.on_new_code = on_new_code
        self._lock = threading.Lock()
        self.devices: list[Device] = self._load()
        self.code = ""
        self.pair_secret = b""
        self._code_expires = 0.0
        self._attempts = 0
        self.close_after_pair = close_after_pair
        self.closed = False
        self.new_code()

    # -- the one-time code ---------------------------------------------------

    def new_code(self) -> str:
        self.code = f"{secrets.randbelow(1_000_000):06d}"
        self.pair_secret = secrets.token_bytes(32)
        self._code_expires = self.clock() + CODE_TTL
        self._attempts = 0
        self.on_new_code(self.code)
        return self.code

    def current_code(self) -> str:
        """The code to show, replaced first if it has expired."""
        with self._lock:
            if self.clock() >= self._code_expires:
                self.new_code()
            return self.code

    def pair(self, code: str, device_name: str) -> Optional[str]:
        """Trade a correct code for a new key (None if the code is wrong)."""
        with self._lock:
            if self.closed:
                return None
            if self.clock() >= self._code_expires:
                self.new_code()
                return None
            if not secrets.compare_digest(str(code).strip(), self.code):
                self._attempts += 1
                if self._attempts >= MAX_CODE_ATTEMPTS:
                    self.new_code()
                return None
            key = secrets.token_urlsafe(32)
            now = self.clock()
            name = (device_name or "phone").strip()[:60] or "phone"
            self.devices.append(Device(_hash(key), name, paired_at=now, last_seen=now,
                                       secret=device_secret(key).hex()))
            self._save()
            if self.close_after_pair:
                self.closed = True  # before new_code, so it isn't shown as a new code to use
            self.new_code()  # one use only
            return key

    # -- keys ------------------------------------------------------------------

    def check(self, key: Optional[str]) -> Optional[Device]:
        if not key:
            return None
        wanted = _hash(key)
        for device in self.devices:
            if secrets.compare_digest(device.key_hash, wanted):
                device.last_seen = self.clock()
                if not device.secret:
                    # Paired before encryption existed: the key is right here, so catch up.
                    with self._lock:
                        device.secret = device_secret(key).hex()
                        self._save()
                return device
        return None

    def device(self, id_: str) -> Optional[Device]:
        """The phone with this end-to-end id, if it has a secret."""
        for device in self.devices:
            if device.secret and secrets.compare_digest(device.id, str(id_)):
                device.last_seen = self.clock()
                return device
        return None

    def pair_secret_for(self, pid: str) -> Optional[bytes]:
        """The current code's pairing secret, if ``pid`` names it and it hasn't expired."""
        with self._lock:
            if self.closed or self.clock() >= self._code_expires:
                return None
            return self.pair_secret if secrets.compare_digest(pair_id(self.pair_secret), str(pid)) else None

    def unpair(self, key: str) -> None:
        self.unpair_device(_hash(key)[:32])

    def unpair_device(self, id_: str) -> None:
        with self._lock:
            self.devices = [d for d in self.devices if d.id != id_]
            self._save()

    def unpair_all(self) -> int:
        with self._lock:
            count = len(self.devices)
            self.devices = []
            self._save()
            return count

    def _load(self) -> list[Device]:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        devices = []
        for d in data if isinstance(data, list) else []:
            try:
                devices.append(Device(str(d["key_hash"]), str(d.get("name", "phone")), float(d.get("paired_at", 0)),
                                      secret=str(d.get("secret", ""))))
            except (KeyError, TypeError, ValueError):
                continue
        return devices

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        data = [{"key_hash": d.key_hash, "name": d.name, "paired_at": d.paired_at, "secret": d.secret}
                for d in self.devices]
        self.store.write_text(json.dumps(data, indent=2), encoding="utf-8")
