"""Pairing phones with this PC.

``locoder serve`` shows a 6-digit code. A phone that sends it back gets a
long random key, which it then sends with every request. The code works
once, expires after a few minutes, and is thrown away after a few wrong
guesses. Only a hash of each key is kept on disk
(``~/.locoder/devices.json``), so that file is no use to anyone who reads it.
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


class Pairing:
    def __init__(
        self,
        store: Path = DEVICES_FILE,
        *,
        clock: Callable[[], float] = time.time,
        on_new_code: Callable[[str], None] = lambda code: None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.on_new_code = on_new_code
        self._lock = threading.Lock()
        self.devices: list[Device] = self._load()
        self.code = ""
        self._code_expires = 0.0
        self._attempts = 0
        self.new_code()

    # -- the one-time code ---------------------------------------------------

    def new_code(self) -> str:
        self.code = f"{secrets.randbelow(1_000_000):06d}"
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
            self.devices.append(Device(_hash(key), name, paired_at=now, last_seen=now))
            self._save()
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
                return device
        return None

    def unpair(self, key: str) -> None:
        with self._lock:
            wanted = _hash(key)
            self.devices = [d for d in self.devices if d.key_hash != wanted]
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
                devices.append(Device(str(d["key_hash"]), str(d.get("name", "phone")), float(d.get("paired_at", 0))))
            except (KeyError, TypeError, ValueError):
                continue
        return devices

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        data = [{"key_hash": d.key_hash, "name": d.name, "paired_at": d.paired_at} for d in self.devices]
        self.store.write_text(json.dumps(data, indent=2), encoding="utf-8")
