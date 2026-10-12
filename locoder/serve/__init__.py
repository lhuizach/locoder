"""``locoder serve``: use locoder from your phone.

Runs the same agent as the CLI behind a small HTTP API on your local
network, plus the phone web app. Pair a phone with the QR code or 6-digit
code it prints. With --tunnel the app can also reach it from anywhere,
end-to-end encrypted, through Cloudflare. See mobile/PLAN.md for the design.
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from locoder.display import FAINT, FG, LOGO, MUTED, SECONDARY, pixels_to_text, wordmark_bitmap
from locoder.serve.pairing import CODE_TTL, Pairing
from locoder.serve.qr import pair_url, qr_text

ACCENT = "#4a9eff"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="locoder serve", description="Use locoder from your phone.")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on (default: every network this PC is on)")
    parser.add_argument("--address",
                        help="the address phones should use, if the one locoder picks (first listed) is wrong")
    parser.add_argument("--tunnel", action="store_true",
                        help="also reachable from anywhere through Cloudflare (needs cloudflared), end-to-end encrypted")
    parser.add_argument("--tunnel-url",
                        help="the public address of a Cloudflare tunnel you run yourself, pointing at this server")
    parser.add_argument("--model", help="override the model name from config")
    parser.add_argument("--provider", help="override provider kind")
    parser.add_argument("--base-url", help="override the backend base URL")
    parser.add_argument("--num-ctx", type=int, help="override the context window size (tokens)")
    parser.add_argument("--yolo", action="store_true", help="start with confirmations off")
    parser.add_argument("--unpair-all", action="store_true", help="forget every paired phone and exit")
    return parser


def serve_main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console(highlight=False)

    if args.unpair_all:
        count = Pairing(on_new_code=lambda code: None).unpair_all()
        console.print(f"unpaired {count} device{'s' if count != 1 else ''}")
        return 0

    from locoder.serve.hub import Hub
    from locoder.serve.server import LocoderServer, lan_addresses, start_discovery

    overrides = {
        "model_override": args.model,
        "base_url_override": args.base_url,
        "provider_override": args.provider,
        "num_ctx_override": args.num_ctx,
        "yolo_override": args.yolo,
    }
    try:
        hub = Hub(Path.cwd(), overrides)
    except ValueError as exc:
        console.print(Text(f"locoder: {exc}", style="#e5534b"))
        return 1

    ready = threading.Event()
    addresses: list[str] = []

    def show_code(code: str) -> None:
        if ready.is_set():
            console.print()
            console.print(Text("new pairing code (the last one was used or expired)", style=MUTED))
            console.print(_pairing_block(addresses[0], args.port, code, secret=pairing.pair_secret,
                                         remote=hub.remote, fancy=_can_show_qr(console)))
            console.print()

    def log(msg: str) -> None:
        console.print(Text.assemble(("· ", FAINT), (msg, SECONDARY)))

    pairing = Pairing(on_new_code=show_code)
    try:
        server = LocoderServer((args.host, args.port), hub, pairing, log=log)
    except OSError as exc:
        console.print(Text(f"locoder: can't listen on port {args.port} ({exc}) - try --port 8766", style="#e5534b"))
        return 1
    stop = threading.Event()
    start_discovery(args.port, hub.computer, stop)

    addresses[:] = lan_addresses() if args.host in ("0.0.0.0", "") else [args.host]
    if args.address:
        addresses[:] = [args.address, *[a for a in addresses if a != args.address]]

    tunnel = None
    if args.tunnel_url:
        hub.remote = args.tunnel_url.rstrip("/")
    elif args.tunnel:
        tunnel = _start_tunnel(console, args.port, log)
        hub.remote = tunnel.url if tunnel else None

    _banner(console, hub, addresses, args.port, pairing.current_code(), len(pairing.devices),
            secret=pairing.pair_secret)
    ready.set()

    # Expired codes are replaced (and the new one shown) without waiting for a phone to ask.
    def rotate() -> None:
        while not stop.wait(5):
            pairing.current_code()

    threading.Thread(target=rotate, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if tunnel:
            tunnel.stop()
        server.server_close()
        console.print(Text("stopped", style=MUTED))
    return 0


def _start_tunnel(console: Console, port: int, log):
    from locoder.serve.tunnel import DOWNLOAD_PAGE, BIN_DIR, QuickTunnel, find_cloudflared

    binary = find_cloudflared()
    if binary is None:
        console.print(Text.assemble(
            ("no tunnel: cloudflared isn't installed. ", "#d7a84a"),
            (f"Get cloudflared-windows-amd64.exe from {DOWNLOAD_PAGE}, save it as "
             f"{BIN_DIR / 'cloudflared.exe'}, and run locoder serve --tunnel again.", MUTED)))
        return None
    console.print(Text("starting the encrypted tunnel...", style=MUTED))
    tunnel = QuickTunnel(port, log=log)
    if tunnel.start(binary) is None:
        tunnel.stop()
        console.print(Text("no tunnel: cloudflared didn't come up (no internet?) - home Wi-Fi only for now", style="#d7a84a"))
        return None
    return tunnel


def _fancy(console: Console) -> bool:
    """Block characters (logo, QR code) need a console that can encode them."""
    return (console.encoding or "").lower().replace("-", "").startswith("utf")


def _can_show_qr(console: Console) -> bool:
    """The QR code is drawn with colours; without them it's just a block."""
    return _fancy(console) and console.color_system is not None


def _banner(console: Console, hub, addresses: list[str], port: int, code: str, paired: int,
            *, secret: bytes = b"") -> None:
    console.print()
    if _fancy(console):
        lockup = Table.grid(padding=(0, 3))
        lockup.add_column(vertical="middle")
        lockup.add_column(vertical="middle")
        lockup.add_row(pixels_to_text(LOGO, ACCENT), pixels_to_text(wordmark_bitmap("locoder"), FG))
        console.print(lockup)
    else:
        console.print(Text("locoder", style=f"bold {ACCENT}"))
    console.print()
    console.print(Text.assemble(("serving ", MUTED), (hub.project_info()["label"], FG),
                                ("  ·  ", FAINT), (hub.provider.model, SECONDARY)))
    console.print()
    console.print(Text("on your phone (same Wi-Fi), open the locoder app or go to", style=MUTED))
    console.print(Text(f"  http://{addresses[0]}:{port}", style=f"bold {FG}"))
    for addr in addresses[1:]:
        # VPN / VM / WSL adapters: usually not the one the phone can see.
        console.print(Text.assemble((f"  http://{addr}:{port}", SECONDARY), ("  other network", FAINT)))
    if hub.remote:
        console.print()
        console.print(Text("anywhere else, the app connects through", style=MUTED))
        console.print(Text.assemble((f"  {hub.remote}", f"bold {FG}"),
                                    ("  end-to-end encrypted, Cloudflare can't read it", FAINT)))
    console.print()
    console.print(_pairing_block(addresses[0], port, code, secret=secret, remote=hub.remote,
                                 fancy=_can_show_qr(console)))
    if paired:
        console.print(Text(f"{paired} phone{'s are' if paired != 1 else ' is'} already paired", style=MUTED))
    console.print()
    console.print(Text("first time? if Windows asks, allow Python on private networks.  ctrl+c stops", style=FAINT))
    console.print()


def _pairing_block(address: str, port: int, code: str, *, secret: bytes = b"", remote: Optional[str] = None,
                   fancy: bool = True):
    """The QR code, with the code to type next to it."""
    if not fancy:
        return Text.assemble(("pairing code  ", MUTED), (f"{code[:3]} {code[3:]}", f"bold {ACCENT}"),
                             (f"   (works once, for {CODE_TTL // 60} minutes)", FAINT))
    info = Text.assemble(
        ("scan with your phone's camera\n", FG),
        ("or with scan QR code in the app\n\n", MUTED),
        ("or type the code  ", MUTED), (f"{code[:3]} {code[3:]}", f"bold {ACCENT}"), ("\n", ""),
        (f"works once, for {CODE_TTL // 60} minutes", FAINT),
    )
    block = Table.grid(padding=(0, 3))
    block.add_column(no_wrap=True)
    block.add_column(vertical="middle")
    block.add_row(qr_text(pair_url(address, port, code, secret=secret, remote=remote)), info)
    return block
