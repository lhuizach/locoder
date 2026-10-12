"""The pairing QR code ``locoder serve`` prints.

It holds a plain link, ``http://<pc>:<port>/#pair=<code>&k=<secret>&r=<tunnel>``:
a phone's camera app opens it in the browser, which pairs straight away, and
the Android app's scanner reads it all out of it. With a tunnel, ``k`` (the
code's one-time pairing secret) lets the app pair through it encrypted, and
``r`` is where. Everything sits after ``#``, so it's never sent in a request.
"""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, quote, urlparse

import segno
from rich.text import Text

DARK = "#000000"
LIGHT = "#ffffff"


def pair_url(address: str, port: int, code: str, *, secret: bytes = b"", remote: str | None = None) -> str:
    url = f"http://{address}:{port}/#pair={code}"
    if remote and secret:
        url += "&k=" + base64.urlsafe_b64encode(secret).decode().rstrip("=") + "&r=" + quote(remote, safe=":/")
    return url


def parse_pair_url(url: str) -> tuple[str, str] | None:
    """(server origin, code) from a pairing link, or None if it isn't one."""
    parsed = urlparse(url.strip())
    code = parse_qs(parsed.fragment).get("pair", [""])[0]
    if parsed.scheme not in ("http", "https") or not parsed.hostname or not (code.isdigit() and len(code) == 6):
        return None
    return f"{parsed.scheme}://{parsed.netloc}", code


def qr_text(data: str) -> Text:
    """The QR code as half-block characters, two module rows per line.

    Dark-on-light with a quiet zone, whatever the terminal's own colours,
    since many scanners can't read an inverted code.
    """
    matrix = [list(row) for row in segno.make(data, error="m", micro=False).matrix]
    border = 2
    size = len(matrix) + 2 * border
    padded = [[False] * size for _ in range(border)]
    padded += [[False] * border + [bool(v) for v in row] + [False] * border for row in matrix]
    padded += [[False] * size for _ in range(border)]
    if len(padded) % 2:
        padded.append([False] * size)

    text = Text(no_wrap=True, overflow="crop")
    for top, bottom in zip(padded[0::2], padded[1::2]):
        for upper, lower in zip(top, bottom):
            text.append("▀", style=f"{DARK if upper else LIGHT} on {DARK if lower else LIGHT}")
        text.append("\n")
    text.rstrip()
    return text
