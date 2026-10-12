"""Reaching ``locoder serve`` from outside your Wi-Fi, through Cloudflare.

``cloudflared`` (Cloudflare's tunnel program) connects *out* from this PC, so
no firewall or router change and no admin rights are needed, and gives a
public https address that forwards to the local server. Everything the app
sends through it is end-to-end encrypted (e2e.py); Cloudflare only carries
sealed envelopes.

A "quick tunnel" needs no Cloudflare account, but its address changes every
time it starts; the app learns the new one whenever it's connected (the
``remote`` field of /api/state), and from the QR code.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

BIN_DIR = Path.home() / ".locoder" / "bin"
DOWNLOAD_PAGE = "https://github.com/cloudflare/cloudflared/releases/latest"
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> Optional[str]:
    name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
    local = BIN_DIR / name
    if local.is_file():
        return str(local)
    return shutil.which("cloudflared")


class QuickTunnel:
    def __init__(self, port: int, log: Callable[[str], None] = lambda msg: None) -> None:
        self.port = port
        self.log = log
        self.url: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._found = threading.Event()

    def start(self, binary: str, timeout: float = 40.0) -> Optional[str]:
        """Starts cloudflared; returns the public address, or None if it didn't come up."""
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
            env={**os.environ, "NO_COLOR": "1"},
        )
        threading.Thread(target=self._read, name="cloudflared-log", daemon=True).start()
        self._found.wait(timeout)
        return self.url

    def _read(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            if self.url is None:
                match = _URL.search(line)
                if match:
                    self.url = match.group(0)
                    self._found.set()
        if self.url is None:
            self._found.set()  # exited without an address
        else:
            self.log("the encrypted tunnel closed (cloudflared stopped)")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
